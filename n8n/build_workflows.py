"""build_workflows.py — gera os workflows do n8n que orquestram os pipelines da ômica (F5).

Um workflow por pipeline (`pipelines/<id>.yaml`), todos com o mesmo desenho:

  Formulário (arquivos e/ou caminhos no servidor) → contexto do run (subprojeto escolhido da
  lista que a ômica devolve NA HORA, sem privados; conferido antes do POST) → cria o run com
  {pipeline, especie, subprojeto} → envia as entradas (upload
  cru; depois, em série, o {"caminho"} das que têm índice ao lado ou aceitam referência)
  → adapta as que não passam
  (agente local; se ele duvidar, o AUTOR mapeia num formulário só com cabeçalhos reais)
  → escolhas do autor (dropdown só com valores reais) → plano → APROVAÇÃO ÚNICA
  → executa os passos em ordem (espera longa na API, RAM cheia espera e repete, falha
  pergunta ao autor) → relatório escrito pelo Claude e conferido pela ômica → link.

O n8n só ORQUESTRA: sanitização, plano congelado por sha256, portões, envelope e a
conferência de cada número do relatório são da ômica. O n8n nunca fala com o Ollama nem
com o Redis; a chave da Anthropic fica só na credencial dele.

Credenciais referenciadas por ID (não são segredo; os segredos ficam cifrados no n8n):
  - omica Bearer        (httpHeaderAuth: Authorization = Bearer <OMICA_API_TOKEN>)
  - Anthropic x-api-key (httpHeaderAuth: x-api-key = <chave>)

Uso (no env `datascience` ou no container web):
  python n8n/build_workflows.py            # gera n8n/dist/<id>.json para cada pipeline
  python n8n/build_workflows.py --checar   # só confere que o dist está em dia (CI)
Importar: ver n8n/README.md.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Optional

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent
sys.path.insert(0, str(RAIZ))

from omica.contrato.formatos import DIRETORIOS  # noqa: E402
from omica.pipelines import Entrada, Pipeline, carregar_pipelines, envio_formulario  # noqa: E402

DIST = AQUI / "dist"
API = "http://omica-web:8710/api/v1"       # rede interna omica_api (o n8n nunca vê o Redis)
UI = "http://127.0.0.1:8710"                # o navegador do autor, no host
CRED_OMICA = {"id": "7VePH8cFDSRysYq9", "name": "omica Bearer"}
CRED_ANTHROPIC = {"id": "cmbUV99kDWwkyosz", "name": "Anthropic x-api-key"}
MODELO_REDATOR = "claude-opus-5-5"
ESPERA_S = 110                              # < ESPERA_MAX_S da API (120)
TETO_PASSO_H = 12                           # um passo esperando mais que isso vira falha
TETO_FILA_H = 2                             # RAM cheia / fila fora por mais que isso: falha
IDS_WORKFLOW = {"transcriptomica_de": "omicaTranscrDE01",
                "genomica_painel": "omicaGenomPain01", "genomica_regioes": "omicaGenomRegi01",
                # F6 (ESPEC §4 item 1): também com 16 caracteres; fixos, como os de cima
                "ml_aims_vcf": "omicaMLAimsVcf01",
                "epigenomica_dmc": "omicaEpigDMC0001", "epigenomica_mofa": "omicaEpigMOFA001"}
TIMEOUT_CAMINHO_S = 600                     # PUT por caminho: sha completo de um BCF de ~1,9 GB
# Extensões do campo de upload por formato. O navegador só filtra a janela de escolha (não é
# portão: quem decide é o validador da ômica), mas um BED com outra extensão seria lido
# 1-based pelo bcftools — o formulário não deve nem oferecer o .txt. O padrão é o de sempre.
ACEITA_POR_FORMATO = {"painel_simbolos": ".txt", "bed_regioes": ".bed",
                      "painel_genes_json": ".json", "rotulos_amostras": ".tsv,.txt"}
ACEITA_PADRAO = ".tsv,.csv,.txt"


def campo_do_formulario(nome: str, e: Entrada) -> dict[str, Any]:
    """Campo do gatilho para uma entrada. O TIPO vem de `pipelines.envio_formulario`, a mesma
    função que a API expõe em GET /pipelines: "caminho" (VCF com índice ao lado, ou entrada
    que aceita referência) vira campo de TEXTO com o exemplo do contrato como dica — o índice
    não vai por upload (422 entrada_exige_indice) e um BCF de 1,9 GB não cabe no formulário."""
    if envio_formulario(e) == "caminho":
        return {"fieldLabel": e.rotulo, "fieldName": nome, "fieldType": "text", "requiredField": True,
                "placeholder": e.exemplo}
    return {"fieldLabel": e.rotulo, "fieldName": nome, "fieldType": "file", "requiredField": True,
            "multipleFiles": False, "acceptFileTypes": ACEITA_POR_FORMATO.get(e.formato, ACEITA_PADRAO)}


# --------------------------------------------------------------------- nós
class Grafo:
    def __init__(self, nome_wf: str) -> None:
        self.nome_wf = nome_wf
        self.nos: list[dict[str, Any]] = []
        self.arestas: dict[str, dict[str, list[list[dict[str, Any]]]]] = {}

    def no(self, nome: str, tipo: str, versao: float, pos: tuple[int, int],
           params: dict[str, Any], **extra: Any) -> str:
        assert all(n["name"] != nome for n in self.nos), f"nó repetido: {nome}"
        self.nos.append({"parameters": params, "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.nome_wf}/{nome}")),
                         "name": nome, "type": tipo, "typeVersion": versao, "position": list(pos), **extra})
        return nome

    def liga(self, de: str, para: str, saida: int = 0) -> None:
        saidas = self.arestas.setdefault(de, {}).setdefault("main", [])
        while len(saidas) <= saida:
            saidas.append([])
        saidas[saida].append({"node": para, "type": "main", "index": 0})

    # atalhos -----------------------------------------------------------
    def code(self, nome: str, pos: tuple[int, int], js: str) -> str:
        return self.no(nome, "n8n-nodes-base.code", 2, pos, {"jsCode": js.strip() + "\n"})

    def se(self, nome: str, pos: tuple[int, int], expr: str) -> str:
        """IF booleano: saída 0 = verdadeiro, 1 = falso."""
        return self.no(nome, "n8n-nodes-base.if", 2.2, pos, {
            "conditions": {
                "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 2},
                "conditions": [{"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.nome_wf}/{nome}/c")),
                                "leftValue": "={{ " + expr + " }}", "rightValue": "",
                                "operator": {"type": "boolean", "operation": "true", "singleValue": True}}],
                "combinator": "and"},
            "looseTypeValidation": True, "options": {}})

    def http(self, nome: str, pos: tuple[int, int], metodo: str, url: str, *,
             corpo: Optional[str] = None, binario: Optional[str] = None, timeout_s: int = 60,
             cred: dict[str, str] = CRED_OMICA, cabecalhos: Optional[dict[str, str]] = None) -> str:
        """Resposta COMPLETA e sem estourar: quem decide pelo status é o nó seguinte."""
        p: dict[str, Any] = {"method": metodo, "url": url, "authentication": "genericCredentialType",
                             "genericAuthType": "httpHeaderAuth",
                             "options": {"timeout": timeout_s * 1000,
                                         "response": {"response": {"fullResponse": True, "neverError": True}}}}
        if cabecalhos:
            p["sendHeaders"] = True
            p["headerParameters"] = {"parameters": [{"name": k, "value": v} for k, v in cabecalhos.items()]}
        if corpo is not None:
            p.update(sendBody=True, specifyBody="json", jsonBody=corpo)
        if binario is not None:
            p.update(sendBody=True, contentType="binaryData", inputDataFieldName=binario)
        return self.no(nome, "n8n-nodes-base.httpRequest", 4.2, pos, p,
                       credentials={"httpHeaderAuth": dict(cred)})

    def pagina(self, nome: str, pos: tuple[int, int], titulo: str, campos_expr: str,
               descricao: str = "", botao: str = "Continuar") -> str:
        """Página de formulário com campos definidos em JSON (montados por um nó anterior)."""
        return self.no(nome, "n8n-nodes-base.form", 2.5, pos, {
            "operation": "page", "defineForm": "json", "jsonOutput": campos_expr,
            "options": {"formTitle": titulo, "formDescription": descricao, "buttonLabel": botao}})

    def fim(self, nome: str, pos: tuple[int, int], titulo: str, mensagem: str) -> str:
        return self.no(nome, "n8n-nodes-base.form", 2.5, pos, {
            "operation": "completion", "respondWith": "text",
            "completionTitle": titulo, "completionMessage": mensagem})

    def espera(self, nome: str, pos: tuple[int, int], segundos: int) -> str:
        return self.no(nome, "n8n-nodes-base.wait", 1.1, pos, {"amount": segundos, "unit": "seconds"},
                       webhookId=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.nome_wf}/{nome}/wh")))


RUN = "$('Criar run').first().json.body.run_id"
JS_ESCAPAR = r"""
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
"""
# X5′ (F6, crítica M4): o resumo do plano mostra os MESMOS avisos do subprojeto que o relatório
# declara (servico.avisos_que_valem): o prefixo antes do primeiro ':', normalizado, é o módulo
# do plano ou não é módulo nenhum (aviso geral). Sem isso, um run da transcriptômica mostrava o
# aviso "epigenômica: painel_108 v1 … 13 loci errados" do demo. A normalização é a do Python
# (_normalizar), não uma parecida: o JS não expõe a classe canônica (unicodedata.combining), mas
# a reordenação do NFD a denuncia — marca de classe ≠ 0 troca de lugar com U+0334 (classe 1) ou
# com U+0301 (classe 230); a de classe 0 (seletor de variação, sinal vocálico índico) não se
# move, e o Python a mantém. `aparar` tira só o que o str.strip() tira (o trim() do JS difere em
# \x1c-\x1f, \x85 e \ufeff), e ß → ss é a única diferença entre casefold e toLowerCase que dá
# ASCII. MODULOS vem do builder, já normalizado (modulos_dos_avisos).
JS_AVISOS = r"""
const marcaCombinante = (c) => ('a' + c + '\u0334').normalize('NFD') !== 'a' + c + '\u0334' ||
  ('a\u0301' + c).normalize('NFD') !== 'a\u0301' + c;
const ESPACOS_DO_PYTHON = '[\\t\\n\\v\\f\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000]+';
const aparar = (s) => s.replace(new RegExp('^' + ESPACOS_DO_PYTHON + '|' + ESPACOS_DO_PYTHON + '$', 'g'), '');
const normalizar = (s) => aparar(Array.from(s.normalize('NFKD')).filter((c) => !marcaCombinante(c)).join('')
  .toLowerCase().replace(/\u00df/g, 'ss'));
const avisosQueValem = (avisos, modulo) => {
  const proprio = normalizar(String(modulo || ''));
  return avisos.filter((a) => {
    if (typeof a !== 'string' || aparar(a) === '') return false;
    const prefixo = normalizar(a.split(':')[0]);
    return prefixo === proprio || !MODULOS.includes(prefixo);
  });
};
"""


def modulos_dos_avisos() -> list[str]:
    """Os ids de módulo que o prefixo de um aviso pode citar, já normalizados e em ordem (o dist
    sai igual byte a byte): a MESMA lista do relatório — servico._ids_de_modulo, isto é, o
    load_registry mais as pastas de modules/ com manifest (um manifest inválido não transforma
    o aviso dele em aviso geral)."""
    from omica.relatorio.servico import _ids_de_modulo, _normalizar

    return sorted({_normalizar(m) for m in _ids_de_modulo()})


# --------------------------------------------------------------- pipeline
def construir(p: Pipeline) -> dict[str, Any]:
    g = Grafo(f"omica_{p.id}")
    formatos_entrada = {n: e.formato for n, e in p.entradas.items()}
    adaptaveis = [n for n, e in p.entradas.items() if e.adaptavel]
    escolhas = {n: e.rotulo for n, e in p.escolhas_autor.items()}

    # ---- 1. arquivos → run → entradas ---------------------------------
    # upload (arquivo) ou caminho no servidor: a decisão é do contrato (envio_formulario)
    arquivos = [n for n, e in p.entradas.items() if envio_formulario(e) == "arquivo"]
    caminhos = [n for n, e in p.entradas.items() if envio_formulario(e) == "caminho"]
    campos = [campo_do_formulario(n, e) for n, e in p.entradas.items()]
    g.no("Arquivos do experimento", "n8n-nodes-base.formTrigger", 2.6, (-1320, 300), {
        "formTitle": f"Ômica · {p.titulo}", "formDescription": p.descricao,
        "formFields": {"values": campos}, "options": {}},
        webhookId=str(uuid.uuid5(uuid.NAMESPACE_URL, f"omica/{p.id}/form")))
    # o n8n 2.34.5 registra o formulário publicado pelo webhookId (o `path` só vale no editor):
    # a URL de produção é /form/<webhookId>, que o main() imprime
    g.nos[-1]["parameters"]["path"] = f"omica-{p.id.replace('_', '-')}"

    # ---- 0. contexto do run (D-10; adendo F6 §A10, item 0) ----------------
    # A lista vem da ômica NA HORA: GET /subprojetos?pipeline=&privados=false aplica a MESMA
    # regra do POST /runs (contexto.execucao_permitida) e tira os privados. Ela nunca é
    # embutida no dist, que é versionado: subprojeto novo não exige regerar o workflow, e
    # slug/título de privado não passam pelo formulário (que não tem login) nem pelos registros
    # de execução do n8n (com privados=false, os `erros` da resposta só trazem os "repo:*": o
    # erro de carga de um subprojeto de <dados> traria slug e título). Só o id vai para a
    # opção. O par escolhido é conferido contra a lista antes do POST: fora dela, o run nem é
    # criado.
    g.http("Contextos do pipeline", (-1100, 300), "GET", f"{API}/subprojetos?pipeline={p.id}&privados=false")
    g.code("Campos do contexto", (-880, 300), f"""
const r = $input.first().json;
if (r.statusCode !== 200) throw new Error('A ômica recusou listar os subprojetos (' + r.statusCode + '): ' + JSON.stringify(r.body).slice(0, 400));
const lista = Array.isArray(r.body?.subprojetos) ? r.body.subprojetos : [];
if (!lista.length) throw new Error('nenhum subprojeto aceita este pipeline: ' + {json.dumps(p.id)});
const ids = lista.map((s) => String(s.id));
return [{{ json: {{ campos: [{{ fieldLabel: 'Subprojeto', fieldName: 'contexto', fieldType: 'dropdown', requiredField: true,
  fieldOptions: {{ values: ids.map((id) => ({{ option: id }})) }} }}], ids }} }}];
""")
    g.pagina("Contexto do run", (-660, 300), "Onde este run vai morar", "={{ JSON.stringify($json.campos) }}",
             descricao="Só aparecem subprojetos em que a ômica aceita este pipeline; o demo é o dos selos.")
    g.code("Conferir contexto", (-440, 300), """
// o valor volta do navegador: só vale se for um dos ids que a ômica listou agora
const escolhido = $input.first().json.contexto;
const ids = $('Campos do contexto').first().json.ids || [];
const c = typeof escolhido === 'string' ? escolhido : '';
const i = c.indexOf('/');
if (!ids.includes(c) || i <= 0 || i === c.length - 1) return [{ json: { ok: false } }];
return [{ json: { ok: true, especie: c.slice(0, i), subprojeto: c.slice(i + 1) } }];
""")
    g.se("Contexto válido?", (-220, 300), "$json.ok === true")
    g.fim("Fim: contexto recusado", (0, 520), "Contexto recusado",
          "O subprojeto escolhido não está na lista desta ômica.")
    for de, para in [("Arquivos do experimento", "Contextos do pipeline"), ("Contextos do pipeline", "Campos do contexto"),
                     ("Campos do contexto", "Contexto do run"), ("Contexto do run", "Conferir contexto"),
                     ("Conferir contexto", "Contexto válido?")]:
        g.liga(de, para)
    g.liga("Contexto válido?", "Fim: contexto recusado", 1)
    # sem os dois campos, o POST cairia no demo (com aviso, mas sem ninguém ver): o Resumo do
    # plano confere que o plano voltou para o par escolhido aqui
    g.http("Criar run", (220, 300), "POST", f"{API}/runs",
           corpo="={{ JSON.stringify({ pipeline: " + json.dumps(p.id)
           + ", especie: $json.especie, subprojeto: $json.subprojeto }) }}")
    checa_201 = """
const r = $input.first().json;
if (r.statusCode !== 201) throw new Error('A ômica recusou criar o run: ' + JSON.stringify(r.body).slice(0, 400));
"""
    if arquivos:
        g.code("Separar arquivos", (440, 300), f"""{checa_201}const bin = $('Arquivos do experimento').first().binary || {{}};
return {json.dumps(arquivos)}.map((nome) => {{
  if (!bin[nome]) throw new Error('Arquivo ausente no formulário: ' + nome);
  return {{ json: {{ nome }}, binary: {{ arquivo: bin[nome] }} }};
}});
""")
        g.http("Enviar entrada", (660, 300), "PUT", "={{ '" + API + "/runs/' + " + RUN + " + '/entradas/' + $json.nome }}",
               binario="arquivo", cabecalhos={"Content-Type": "application/octet-stream"}, timeout_s=300)
        g.code("Conferir envios", (880, 300), """
const ruins = $input.all().filter((i) => i.json.statusCode !== 200);
if (ruins.length) throw new Error('Envio recusado: ' + ruins.map((i) => JSON.stringify(i.json.body)).join(' | ').slice(0, 600));
return [{ json: {} }];
""")
    if caminhos:
        # Entrada por CAMINHO no servidor (G1 §4.3): PUT JSON {"caminho"}, em série depois dos
        # uploads. O servidor decide cópia ou referência pela raiz; o cliente nunca diz o modo
        # nem manda o índice. Caminho recusado (422 caminho_recusado, corpo fixo e sem eco) é o
        # erro mais comum de quem digita — vai a uma página final com o motivo, e o run é
        # cancelado (senão conta no limite de runs abertos), em vez de estourar a execução.
        rotulos = {n: p.entradas[n].rotulo for n in caminhos}
        g.code("Separar caminhos", (440, -160), f"""{checa_201 if not arquivos else ""}
const f = $('Arquivos do experimento').first().json;
// o gatilho devolve o texto pelo fieldName; o rótulo é só a rede de segurança
const rotulos = {json.dumps(rotulos, ensure_ascii=False)};
return {json.dumps(caminhos)}.map((nome) => {{
  const bruto = f[nome] ?? f[rotulos[nome]];
  const caminho = typeof bruto === 'string' ? bruto.trim() : '';
  if (!caminho) throw new Error('Caminho vazio no formulário: ' + nome);
  return {{ json: {{ nome, caminho }} }};
}});
""")
        g.http("Enviar caminhos", (660, -160), "PUT",
               "={{ '" + API + "/runs/' + " + RUN + " + '/entradas/' + $json.nome }}",
               corpo="={{ JSON.stringify({ caminho: $json.caminho }) }}", timeout_s=TIMEOUT_CAMINHO_S)
        g.code("Conferir caminhos", (880, -160), """
const nomes = $('Separar caminhos').all().map((i) => i.json.nome);
const ruins = $input.all().map((i, k) => [nomes[k], i.json]).filter(([, r]) => r.statusCode !== 200);
if (!ruins.length) return [{ json: { rota: 'ok' } }];
const det = ruins.map(([n, r]) => n + ' (' + r.statusCode + '): ' + JSON.stringify(r.body)).join(' | ');
return [{ json: { rota: 'recusado', detalhe: det.slice(0, 800) } }];
""")
        g.se("Caminhos aceitos?", (1100, -160), "$json.rota === 'ok'")
        g.http("Cancelar run (caminho)", (1320, -260), "POST", "={{ '" + API + "/runs/' + " + RUN + " + '/abortar' }}")
        # a página só afirma o cancelamento se o /abortar respondeu 200 (trava ocupada ou 5xx
        # deixariam o run aberto, contando no limite de runs abertos)
        g.fim("Fim: caminho recusado", (1540, -260), "Caminho recusado pela ômica",
              "={{ 'Nada foi executado. ' + ($json.statusCode === 200 ? 'O run foi cancelado. ' : "
              "'O run NÃO foi cancelado (status ' + $json.statusCode + '): aborte-o pela ômica. ') + "
              "'A ômica recusou: ' + $('Conferir caminhos').last().json.detalhe }}")
    g.http("Ler run", (1100, 300), "GET", "={{ '" + API + "/runs/' + " + RUN + " }}")
    g.code("Fila de adaptação", (1320, 300), f"""
const run = $input.first().json.body;
const adaptaveis = {json.dumps(adaptaveis)};
const ruins = Object.entries(run.entradas).filter(([, r]) => !r.veredito.ok).map(([n]) => n);
const travadas = ruins.filter((n) => !adaptaveis.includes(n));
if (travadas.length) {{
  const det = travadas.map((n) => n + ': ' + run.entradas[n].veredito.falhas.slice(0, 3).join('; ')).join(' | ');
  return [{{ json: {{ rota: 'invalida', detalhe: det }} }}];
}}
return [{{ json: {{ rota: 'adaptar', fila: ruins, i: 0 }} }}];
""")
    g.liga("Contexto válido?", "Criar run", 0)
    elos = ["Criar run"]
    if arquivos:
        elos += ["Separar arquivos", "Enviar entrada", "Conferir envios"]
    if caminhos:
        elos += ["Separar caminhos", "Enviar caminhos", "Conferir caminhos", "Caminhos aceitos?"]
    for de, para in zip(elos, [*elos[1:], "Ler run"]):
        g.liga(de, para)  # do IF "Caminhos aceitos?", a saída 0 (verdadeiro)
    if caminhos:
        g.liga("Caminhos aceitos?", "Cancelar run (caminho)", 1)
        g.liga("Cancelar run (caminho)", "Fim: caminho recusado")
    g.liga("Ler run", "Fila de adaptação")
    g.se("Entradas válidas?", (1540, 300), "$json.rota !== 'invalida'")
    g.fim("Fim: entrada inválida", (1760, 520), "Entrada fora do formato",
          "={{ 'Uma entrada que o pipeline não adapta veio fora do formato: ' + $json.detalhe }}")
    g.liga("Fila de adaptação", "Entradas válidas?")
    g.liga("Entradas válidas?", "Fim: entrada inválida", 1)

    # ---- 2. adaptação (uma entrada por vez) ---------------------------
    g.code("Entrada atual", (1760, 300), """
const e = $input.first().json;
const fila = e.fila ?? $('Fila de adaptação').first().json.fila;
const i = e.i ?? 0;
return [{ json: { fila, i, nome: fila[i] ?? null, rota: i < fila.length ? 'adaptar' : 'fim' } }];
""")
    g.se("Falta adaptar?", (1980, 300), "$json.rota === 'adaptar'")
    g.http("Adaptar (agente)", (2200, 200), "POST",
           "={{ '" + API + "/runs/' + " + RUN + " + '/entradas/' + $json.nome + '/adaptar' }}", timeout_s=900)
    g.code("Ler adaptação", (2420, 300), """
const r = $input.first().json;
const atual = $('Entrada atual').last().json;
let rota = 'erro';
if (r.statusCode === 200 && r.body.ok) rota = 'proxima';
else if (r.statusCode === 200) rota = 'autor';
else if (r.statusCode === 503) rota = 'motor';
return [{ json: { rota, corpo: r.body, status: r.statusCode, nome: atual.nome, fila: atual.fila, i: atual.i } }];
""")
    g.se("Adaptou?", (2640, 300), "$json.rota === 'proxima'")
    g.code("Avançar entrada", (2860, 120), "const e = $input.first().json;\nreturn [{ json: { fila: e.fila, i: e.i + 1 } }];")
    g.se("Levar ao autor?", (2860, 400), "$json.rota === 'autor'")
    g.code("Campos do mapeamento", (3080, 300), JS_ESCAPAR + f"""
const e = $input.first().json;
const op = e.corpo.opcoes || {{}};
const cab = op.cabecalho || [];
const formato = {json.dumps(formatos_entrada)}[e.nome];
const opcoes = cab.map((c) => ({{ option: c }}));
const falhas = (((e.corpo.run || {{}}).entradas || {{}})[e.nome] || {{}}).veredito?.falhas || [];
const li = (xs) => xs.map((x) => '<li>' + esc(x) + '</li>').join('');
const campos = [{{ fieldType: 'html', html: '<p>Entrada <b>' + esc(e.nome) + '</b> (' + esc(formato) + ').</p>' +
  '<p>O que o validador apontou no arquivo:</p><ul>' + li(falhas.slice(0, 8)) + '</ul>' +
  '<p>O que o agente não conseguiu resolver:</p><ul>' + li(e.corpo.excecoes || []) + '</ul>' +
  '<p>Se o problema é de NOME DE COLUNA, escolha abaixo as colunas reais (nada é criado: só renomeado, ' +
  'selecionado ou repetido). Se é de VALOR (amostra numérica, condição com acento ou espaço, número de ' +
  'condições), corrija o arquivo e envie de novo.</p>' }}];
for (const alvo of (op.alvos || [])) {{
  campos.push({{ fieldLabel: 'Coluna que é «' + alvo + '»', fieldName: 'alvo__' + alvo, fieldType: 'dropdown',
    fieldOptions: {{ values: opcoes }} }});
}}
// alvo que pode ser preenchido repetindo outra coluna (contagens: gene_name ← gene_id)
for (const [alvo, fonte] of Object.entries(op.copia_se_ausente || {{}})) {{
  campos.push({{ fieldLabel: 'Coluna que é «' + alvo + '» (sem ela: repetir o ' + fonte + ')', fieldName: 'copia__' + alvo,
    fieldType: 'dropdown', fieldOptions: {{ values: [{{ option: '(repetir o ' + fonte + ')' }}, ...opcoes] }} }});
}}
if (formato === 'contagens_genes') {{
  campos.push({{ fieldLabel: 'Colunas que são AMOSTRAS (contagens)', fieldName: 'amostras', fieldType: 'checkbox',
    fieldOptions: {{ values: opcoes }} }});
}}
campos.push({{ fieldLabel: 'O que fazer', fieldName: 'decisao', fieldType: 'radio', requiredField: true,
  fieldOptions: {{ values: [{{ option: 'Aplicar este mapeamento' }}, {{ option: 'Corrigir o arquivo e enviar de novo' }}] }} }});
return [{{ json: {{ campos, nome: e.nome, formato, copia_se_ausente: op.copia_se_ausente || {{}}, falhas }} }}];
""")
    g.pagina("Mapeamento pelo autor", (3300, 300), "Mapear colunas", "={{ JSON.stringify($json.campos) }}",
             botao="Aplicar mapeamento")
    g.code("Ops do autor", (3520, 300), """
// ordem: renomear (alvos e colunas escolhidas) → copiar (alvo ausente repete a fonte) → selecionar
const f = $input.first().json;
const ctx = $('Campos do mapeamento').last().json;
const ops = [];
const usadas = [];
for (const [k, v] of Object.entries(f)) {
  if (!k.startsWith('alvo__') || !v) continue;
  const alvo = k.slice(6);
  usadas.push(v);
  if (v !== alvo) ops.push({ op: 'renomear', origem: v, destino: alvo });
}
const copias = [];
for (const [alvo, fonte] of Object.entries(ctx.copia_se_ausente || {})) {
  const v = f['copia__' + alvo];
  // "repetir", vazio ou a MESMA coluna já usada (dois renomear sobre ela seriam descartados)
  if (!v || v.startsWith('(repetir') || usadas.includes(v)) { copias.push({ op: 'copiar', origem: fonte, destino: alvo }); continue; }
  usadas.push(v);
  if (v !== alvo) ops.push({ op: 'renomear', origem: v, destino: alvo });
}
ops.push(...copias);
if (ctx.formato === 'contagens_genes') {
  const amostras = [].concat(f.amostras || []).filter((c) => !usadas.includes(c));
  ops.push({ op: 'selecionar', colunas: ['gene_id', 'gene_name', ...amostras] });
}
return [{ json: { nome: ctx.nome, ops } }];
""")
    g.http("Adaptar (autor)", (3740, 300), "POST",
           "={{ '" + API + "/runs/' + " + RUN + " + '/entradas/' + $json.nome + '/adaptar' }}",
           corpo="={{ JSON.stringify({ ops: $json.ops }) }}", timeout_s=120)
    g.se("Agente indisponível?", (3080, 520), "$json.rota === 'motor'")
    g.pagina("Agente indisponível", (3300, 520), "Agente local indisponível",
             "={{ JSON.stringify([{ fieldType: 'html', html: '<p>O modelo local não pode rodar agora (GPU reservada pelo dono ou outra inferência na vez): '"
             " + String($json.corpo?.detail?.detalhe ?? '').replace(/[<>&]/g, '') + '</p>' }, { fieldLabel: 'O que fazer', fieldName: 'decisao', fieldType: 'radio', "
             "fieldOptions: { values: [{ option: 'Esperar 2 minutos e tentar de novo' }, { option: 'Mapear eu mesmo' }] }, requiredField: true }]) }}")
    g.se("Esperar o agente?", (3520, 520), "$json.decisao === 'Esperar 2 minutos e tentar de novo'")
    g.espera("Esperar o agente", (3740, 460), 120)
    g.code("Pedir opções", (3740, 620), "return [{ json: { nome: $('Entrada atual').last().json.nome, ops: [] } }];")
    g.fim("Fim: erro na adaptação", (3300, 760), "Erro ao adaptar a entrada",
          "={{ 'A ômica recusou a adaptação (' + $json.status + '): ' + JSON.stringify($json.corpo).slice(0, 500) }}")

    g.liga("Entradas válidas?", "Entrada atual", 0)
    g.liga("Entrada atual", "Falta adaptar?")
    g.liga("Falta adaptar?", "Adaptar (agente)", 0)
    g.liga("Adaptar (agente)", "Ler adaptação")
    g.liga("Ler adaptação", "Adaptou?")
    g.liga("Adaptou?", "Avançar entrada", 0)
    g.liga("Avançar entrada", "Entrada atual")
    g.liga("Adaptou?", "Levar ao autor?", 1)
    g.liga("Levar ao autor?", "Campos do mapeamento", 0)
    g.liga("Campos do mapeamento", "Mapeamento pelo autor")
    g.se("Aplicar mapeamento?", (3410, 160), "$json.decisao === 'Aplicar este mapeamento'")
    g.http("Cancelar run (arquivo)", (3630, 40), "POST", "={{ '" + API + "/runs/' + " + RUN + " + '/abortar' }}")
    g.fim("Fim: corrigir o arquivo", (3850, 40), "Corrija o arquivo e envie de novo",
          "={{ 'Nada foi executado. O que corrigir em «' + $('Campos do mapeamento').last().json.nome + '»: ' + "
          "$('Campos do mapeamento').last().json.falhas.slice(0, 8).join(' | ') }}")
    g.liga("Mapeamento pelo autor", "Aplicar mapeamento?")
    g.liga("Aplicar mapeamento?", "Ops do autor", 0)
    g.liga("Aplicar mapeamento?", "Cancelar run (arquivo)", 1)
    g.liga("Cancelar run (arquivo)", "Fim: corrigir o arquivo")
    g.liga("Ops do autor", "Adaptar (autor)")
    g.liga("Adaptar (autor)", "Ler adaptação")
    g.liga("Levar ao autor?", "Agente indisponível?", 1)
    g.liga("Agente indisponível?", "Agente indisponível", 0)
    g.liga("Agente indisponível", "Esperar o agente?")
    g.liga("Esperar o agente?", "Esperar o agente", 0)
    g.liga("Esperar o agente", "Entrada atual")  # volta com o mesmo i (Ler adaptação não avançou)
    g.liga("Esperar o agente?", "Pedir opções", 1)
    g.liga("Pedir opções", "Adaptar (autor)")
    g.liga("Agente indisponível?", "Fim: erro na adaptação", 1)
    # "Esperar o agente" devolve o item da página; a Entrada atual relê a fila e o índice
    # do último "Ler adaptação" quando o item não os traz
    g.nos[[n["name"] for n in g.nos].index("Entrada atual")]["parameters"]["jsCode"] = """
const e = $input.first().json;
let fila = e.fila, i = e.i;
if (fila === undefined) {
  const ultimo = $('Ler adaptação').isExecuted ? $('Ler adaptação').last().json : null;
  fila = ultimo ? ultimo.fila : $('Fila de adaptação').first().json.fila;
  i = ultimo ? ultimo.i : 0;
}
return [{ json: { fila, i, nome: fila[i] ?? null, rota: i < fila.length ? 'adaptar' : 'fim' } }];
"""

    # ---- 3. escolhas → plano → aprovação ------------------------------
    g.http("Ler run (escolhas)", (2200, 700), "GET", "={{ '" + API + "/runs/' + " + RUN + " }}")
    g.liga("Falta adaptar?", "Ler run (escolhas)", 1)
    if escolhas:
        g.code("Campos das escolhas", (2420, 900), f"""
const run = $input.first().json.body;
const rotulos = {json.dumps(escolhas, ensure_ascii=False)};
const campos = Object.entries(rotulos).map(([nome, rotulo]) => ({{
  fieldLabel: rotulo, fieldName: nome, fieldType: 'dropdown', requiredField: true,
  fieldOptions: {{ values: ((run.escolhas_opcoes[nome] || {{}}).valores || []).map((v) => ({{ option: String(v) }})) }} }}));
return [{{ json: {{ campos }} }}];
""")
        # F6 (ESPEC §4 item 5): A == B dá 422 escolhas_iguais no plano, que cai em "Fim: plano
        # recusado" com o detalhe; o formulário avisa antes
        diferentes = any(e.diferente_de for e in p.escolhas_autor.values())
        g.pagina("Escolhas do autor", (2640, 900), "Escolhas do autor",
                 "={{ JSON.stringify($json.campos) }}",
                 descricao="Só aparecem valores que existem nos seus arquivos."
                 + (" Os dois grupos têm de ser diferentes." if diferentes else ""))
        g.code("Montar escolhas", (2860, 900), f"""
const f = $input.first().json;
const nomes = {json.dumps(list(escolhas))};
return [{{ json: {{ escolhas: Object.fromEntries(nomes.map((n) => [n, f[n]])) }} }}];
""")
        g.liga("Ler run (escolhas)", "Campos das escolhas")
        g.liga("Campos das escolhas", "Escolhas do autor")
        g.liga("Escolhas do autor", "Montar escolhas")
        antes_do_plano = "Montar escolhas"
    else:
        g.code("Sem escolhas", (2420, 900), "return [{ json: { escolhas: {} } }];")
        g.liga("Ler run (escolhas)", "Sem escolhas")
        antes_do_plano = "Sem escolhas"
    g.http("Propor plano", (3080, 900), "POST", "={{ '" + API + "/runs/' + " + RUN + " + '/plano' }}",
           corpo="={{ JSON.stringify({ escolhas: $json.escolhas }) }}")
    g.liga(antes_do_plano, "Propor plano")
    extras_js, extras_chamada = "", ""
    if caminhos:
        # Só com entrada por caminho (G1 §4.4): quem só tem upload (a transcriptômica) não ganha
        # o código dos extras.
        if p.modulo == "genomica":
            aviso = "'" + ("genes do painel em outros cromossomos não terão variantes extraídas"
                           if any(e.formato == "painel_simbolos" for e in p.entradas.values())
                           else "regiões em outros cromossomos não terão variantes extraídas") + "'"
        elif p.modulo == "ml":
            # F6 (adendo §A10 item 4, crítica M3/C11): a região é do DADO (escopo subprojeto) e
            # vem do PLANO congelado, não do YAML — o subprojeto pode sobrescrevê-la
            passo_ds = next(x.id for x in p.passos if x.script == "ml_dataset")
            aviso = ("'o dataset usa só a região ' + esc(((p.passos.find((x) => x.id === "
                     + json.dumps(passo_ds) + ") || {}).valores || {}).regiao ?? '(ausente no plano)') + "
                     "' (parâmetro do subprojeto): um VCF sem esse contig dá 0 marcadores'")
        else:
            aviso = None  # epigenômica: sem bloco de contigs (o texto da genômica não vale aqui)
        bloco_contigs = "" if aviso is None else f"""
  const perfil = ((vistas[n] || {{}}).veredito || {{}}).perfil || {{}};
  if (Array.isArray(perfil.contigs_com_dados)) {{
    const lista = perfil.contigs_com_dados;
    const total = Number(perfil.n_contigs_com_dados ?? lista.length);
    s += '<br>contigs com variantes: <b>' + (lista.length ? esc(lista.join(', ')) : '(nenhum)') + '</b>' +
      (total > lista.length ? ' (os ' + esc(lista.length) + ' primeiros de ' + esc(total) + ')' : '') +
      ' — ' + aviso;
  }}"""
        # F6: entrada de PASTA (formato em formatos.DIRETORIOS) mostra quantos arquivos entraram
        linha_pasta = ("\n  if (e.tipo === 'diretorio') s += ' — pasta com ' + esc(e.n_membros) + ' arquivos';"
                       if any(e.formato in DIRETORIOS for e in p.entradas.values()) else "")
        cabeca = ("""
// Entrada por caminho: modo, índice e, se for pasta, quantos arquivos entraram."""
                  if aviso is None else """
// Entrada por caminho: modo, índice e o ALCANCE do VCF. Os contigs vêm do veredito da entrada
// (perfil do vcf_indexado, lido pelo índice), não do plano: um BCF do Run 8 tem um cromossomo
// só, e o portão confere números, não alcance — o autor tem de ver isso ANTES de aprovar.
const vistas = $('Ler run (escolhas)').last().json.body.entradas || {};
const aviso = """ + aviso + ";")
        extras_js = cabeca + f"""
const extras = (n, e) => {{
  let s = '';
  if (e.modo === 'referencia') s += ' — por referência, sem cópia';
  const indices = Object.keys(e.irmaos || {{}});
  if (indices.length) s += ' + índice ' + esc(indices.join(', '));{linha_pasta}{bloco_contigs}
  return s;
}};"""
        extras_chamada = " + extras(n, e)"
    js_avisos = "const MODULOS = " + json.dumps(modulos_dos_avisos()) + ";" + JS_AVISOS
    g.code("Resumo do plano", (3300, 900), JS_ESCAPAR + js_avisos + """
const r = $input.first().json;
if (r.statusCode !== 200) return [{ json: { rota: 'recusado', detalhe: JSON.stringify(r.body).slice(0, 600) } }];
const p = r.body;
// D-10: o plano tem de ter voltado para o contexto escolhido no formulário. Sem especie e
// subprojeto no corpo, o POST /runs cai no demo — aprovar isso rodaria no lugar errado.
const pedido = $('Conferir contexto').first().json;
const ctx = p.contexto || {};
if (ctx.especie !== pedido.especie || ctx.subprojeto !== pedido.subprojeto) {
  return [{ json: { rota: 'recusado', detalhe: 'O plano veio para o contexto ' + JSON.stringify(String(ctx.especie) + '/' + String(ctx.subprojeto)) +
    ', não para o escolhido no formulário (' + JSON.stringify(pedido.especie + '/' + pedido.subprojeto) + '). Nada foi aprovado.' } }];
}
// só os avisos que valem para o módulo do plano: os mesmos que o relatório vai declarar
const avisos = avisosQueValem(Array.isArray(ctx.avisos) ? ctx.avisos : [], p.modulo);
const htmlCtx = '<p>Contexto: <b>' + esc(ctx.especie + '/' + ctx.subprojeto) + '</b> · assembly ' + esc(ctx.assembly) +
  ' · sha ' + esc(String(ctx.sha256).slice(0, 12)) + '…</p>' +
  (avisos.length ? '<h4>Avisos do subprojeto</h4><ul>' + avisos.map((a) => '<li>' + esc(a) + '</li>').join('') + '</ul>' : '');""" + extras_js + """
const linhas = p.passos.map((x) => '<tr><td>' + esc(x.id) + '</td><td>' + esc(x.titulo) + '</td><td>' + esc(x.fila) +
  '</td><td>' + esc(x.mem_gb) + ' GB</td><td>' + esc(x.tempo_estimado) + '</td></tr>').join('');
const ents = Object.entries(p.entradas).map(([n, e]) => '<li><b>' + esc(n) + '</b>: ' + esc(e.formato) + ', ' + esc(e.bytes) +
  ' bytes, sha256 ' + esc(String(e.sha256).slice(0, 12)) + '…' + (e.adaptacao ? ' — adaptada (' + esc(e.adaptacao.modo) + ', ' +
  esc(e.adaptacao.ops.length) + ' operações' + (e.adaptacao.modelo ? ', ' + esc(e.adaptacao.modelo) : '') + ')' : '')""" + extras_chamada + """ + '</li>').join('');
const escs = Object.entries(p.escolhas).map(([k, v]) => '<li>' + esc(k) + ' = <b>' + esc(v) + '</b></li>').join('') || '<li>(nenhuma)</li>';
const html = htmlCtx + '<p>Confira tudo antes de aprovar. Depois de aprovado, NADA muda: cada passo roda exatamente com isto.</p>' +
  '<h4>Passos</h4><table><tr><th>passo</th><th>o quê</th><th>fila</th><th>RAM</th><th>tempo</th></tr>' + linhas + '</table>' +
  '<p>Pico de RAM: <b>' + esc(p.recursos.pico_mem_gb) + ' GB</b></p><h4>Entradas</h4><ul>' + ents + '</ul><h4>Escolhas</h4><ul>' + escs + '</ul>' +
  '<p><small>plano ' + esc(p.plano_sha256) + '</small></p>';
const campos = [{ fieldType: 'html', html }, { fieldLabel: 'Decisão', fieldName: 'decisao', fieldType: 'radio', requiredField: true,
  fieldOptions: { values: [{ option: 'Aprovar e executar' }, { option: 'Cancelar' }] } }];
return [{ json: { rota: 'aprovar', campos, plano_sha256: p.plano_sha256, passos: p.passos.map((x) => x.id) } }];
""")
    g.liga("Propor plano", "Resumo do plano")
    g.se("Plano aceito?", (3520, 900), "$json.rota === 'aprovar'")
    g.fim("Fim: plano recusado", (3740, 1100), "Plano recusado pela ômica", "={{ $json.detalhe }}")
    g.pagina("Aprovação do plano", (3740, 900), "Aprovar o plano", "={{ JSON.stringify($json.campos) }}",
             botao="Confirmar")
    g.se("Aprovado?", (3960, 900), "$json.decisao === 'Aprovar e executar'")
    g.http("Aprovar", (4180, 820), "POST", "={{ '" + API + "/runs/' + " + RUN + " + '/aprovar' }}",
           corpo="={{ JSON.stringify({ plano_sha256: $('Resumo do plano').last().json.plano_sha256 }) }}")
    g.http("Cancelar run", (4180, 1020), "POST", "={{ '" + API + "/runs/' + " + RUN + " + '/abortar' }}")
    g.fim("Fim: cancelado", (4400, 1020), "Run cancelado", "Nada foi executado.")
    g.se("Aprovação aceita?", (4400, 820), "$json.statusCode === 200")
    g.fim("Fim: aprovação recusada", (4620, 1000), "Aprovação recusada",
          "={{ 'A ômica recusou aprovar: ' + JSON.stringify($json.body).slice(0, 500) }}")
    for a, b, s in [("Plano aceito?", "Aprovação do plano", 0), ("Plano aceito?", "Fim: plano recusado", 1),
                    ("Aprovação do plano", "Aprovado?", 0), ("Aprovado?", "Aprovar", 0),
                    ("Aprovado?", "Cancelar run", 1), ("Cancelar run", "Fim: cancelado", 0),
                    ("Aprovar", "Aprovação aceita?", 0), ("Aprovação aceita?", "Fim: aprovação recusada", 1)]:
        g.liga(a, b, s)
    g.liga("Resumo do plano", "Plano aceito?")

    # ---- 4. execução, passo a passo ------------------------------------
    g.code("Passo atual", (4620, 820), f"""
const passos = $('Resumo do plano').last().json.passos;
const i = $input.first().json._proximo_i ?? 0;
return [{{ json: {{ passos, i, passo: passos[i] ?? null, rota: i < passos.length ? 'executar' : 'fim', inicio: Date.now() }} }}];
""")
    g.se("Falta executar?", (4840, 820), "$json.rota === 'executar'")
    g.http("Executar passo", (5060, 720), "POST",
           "={{ '" + API + "/runs/' + " + RUN + " + '/passos/' + $('Passo atual').last().json.passo + '/executar' }}")
    g.code("Ler execução", (5280, 720), f"""
const r = $input.first().json;
const p = $('Passo atual').last().json;
let rota = 'falhou';
if (r.statusCode === 202 || (r.statusCode === 409 && r.body?.detail?.erro === 'passo_ja_lancado')) rota = 'aguardar';
else if (r.statusCode === 429 || r.statusCode === 503) rota = (Date.now() - p.inicio) / 3.6e6 > {TETO_FILA_H} ? 'falhou' : 'esperar';
return [{{ json: {{ rota, status: r.statusCode, detalhe: JSON.stringify(r.body).slice(0, 600) }} }}];
""")
    g.se("Lançado?", (5500, 720), "$json.rota === 'aguardar'")
    g.se("RAM ou fila?", (5500, 920), "$json.rota === 'esperar'")
    g.espera("Esperar RAM/fila", (5720, 1000), 120)
    g.http("Aguardar passo", (5720, 620), "GET",
           "={{ '" + API + "/runs/' + " + RUN + " + '/passos/' + $('Passo atual').last().json.passo + '/aguardar?segundos=" + str(ESPERA_S) + "' }}",
           timeout_s=ESPERA_S + 30)
    g.code("Ler espera", (5940, 620), f"""
const r = $input.first().json;
const p = $('Passo atual').last().json;
if (r.statusCode !== 200) return [{{ json: {{ rota: 'falhou', status: r.statusCode, detalhe: JSON.stringify(r.body).slice(0, 600) }} }}];
const env = r.body;
if (['queued', 'running'].includes(env.estado)) {{
  const horas = (Date.now() - p.inicio) / 3.6e6;
  return [{{ json: {{ rota: horas > {TETO_PASSO_H} ? 'falhou' : 'esperar', status: 200,
    detalhe: horas > {TETO_PASSO_H} ? 'o passo passou de {TETO_PASSO_H} h sem terminar' : '' }} }}];
}}
if (env.portao.ok) return [{{ json: {{ rota: 'entregue', _proximo_i: p.i + 1 }} }}];
return [{{ json: {{ rota: 'falhou', status: 200, detalhe: (env.portao.falhas || []).join(' | ').slice(0, 600) }} }}];
""")
    g.se("Ainda rodando?", (6160, 620), "$json.rota === 'esperar'")
    g.se("Entregue?", (6380, 720), "$json.rota === 'entregue'")
    g.code("Campos da falha", (6600, 920), JS_ESCAPAR + """
const e = $input.first().json;
const p = $('Passo atual').last().json;
return [{ json: { campos: [
  { fieldType: 'html', html: '<p>O passo <b>' + esc(p.passo) + '</b> não entregou (' + esc(e.status ?? '') + '):</p><pre>' + esc(e.detalhe) + '</pre>' +
    '<p>O log do job está na ômica (' + esc('""" + UI + """/jobs') + ').</p>' },
  { fieldLabel: 'O que fazer', fieldName: 'decisao', fieldType: 'radio', requiredField: true,
    fieldOptions: { values: [{ option: 'Repetir este passo' }, { option: 'Abortar o run' }] } }] } }];
""")
    g.pagina("Passo falhou", (6820, 920), "Um passo não entregou", "={{ JSON.stringify($json.campos) }}")
    g.se("Repetir?", (7040, 920), "$json.decisao === 'Repetir este passo'")
    g.http("Abortar run", (7260, 1020), "POST", "={{ '" + API + "/runs/' + " + RUN + " + '/abortar' }}")
    g.fim("Fim: abortado", (7480, 1020), "Run abortado",
          "={{ 'Run ' + " + RUN + " + ' abortado. O que já foi entregue continua em " + UI + "/jobs.' }}")
    for a, b, s in [("Aprovação aceita?", "Passo atual", 0), ("Passo atual", "Falta executar?", 0),
                    ("Falta executar?", "Executar passo", 0), ("Executar passo", "Ler execução", 0),
                    ("Ler execução", "Lançado?", 0), ("Lançado?", "Aguardar passo", 0),
                    ("Lançado?", "RAM ou fila?", 1), ("RAM ou fila?", "Esperar RAM/fila", 0),
                    ("Esperar RAM/fila", "Executar passo", 0), ("RAM ou fila?", "Campos da falha", 1),
                    ("Aguardar passo", "Ler espera", 0), ("Ler espera", "Ainda rodando?", 0),
                    ("Ainda rodando?", "Aguardar passo", 0), ("Ainda rodando?", "Entregue?", 1),
                    ("Entregue?", "Passo atual", 0), ("Entregue?", "Campos da falha", 1),
                    ("Campos da falha", "Passo falhou", 0), ("Passo falhou", "Repetir?", 0),
                    ("Repetir?", "Executar passo", 0), ("Repetir?", "Abortar run", 1),
                    ("Abortar run", "Fim: abortado", 0)]:
        g.liga(a, b, s)

    # ---- 5. relatório (Claude escreve, a ômica confere) ----------------
    g.http("Pedido de relatório", (5060, 1300), "POST", "={{ '" + API + "/runs/' + " + RUN + " + '/relatorio/pedido' }}")
    g.liga("Falta executar?", "Pedido de relatório", 1)
    g.se("Ficha pronta?", (5280, 1300), "$json.statusCode === 200")
    g.fim("Fim: sem relatório", (5500, 1500), "Run concluído, relatório não gerado",
          "={{ 'Os passos terminaram, mas a ômica não montou a ficha de fatos: ' + JSON.stringify($json.body).slice(0, 500) }}")
    g.code("Montar chamada ao Claude", (5500, 1300), f"""
const p = $input.first().json.body;
return [{{ json: {{ esquema: p.esquema, body: {{
  model: {json.dumps(MODELO_REDATOR)}, max_tokens: 16000, fallbacks: 'default',
  output_config: {{ effort: 'high', format: {{ type: 'json_schema', schema: p.esquema }} }},
  system: p.sistema, messages: [{{ role: 'user', content: p.mensagem }}] }} }} }}];
""")
    g.http("Claude (redator)", (5720, 1300), "POST", "https://api.anthropic.com/v1/messages",
           corpo="={{ JSON.stringify($json.body) }}", timeout_s=600, cred=CRED_ANTHROPIC,
           cabecalhos={"anthropic-version": "2023-06-01", "anthropic-beta": "server-side-fallback-2026-07-01"})
    g.code("Ler resposta do Claude", (5940, 1300), """
const r = $input.first().json;
if (r.statusCode !== 200) return [{ json: { rota: 'erro_api', detalhe: JSON.stringify(r.body).slice(0, 600) } }];
const b = r.body;
// stop_reason ANTES do conteúdo; só blocos de texto (thinking/fallback ficam de fora)
const texto = (b.content || []).filter((c) => c.type === 'text').map((c) => c.text).join('');
let rascunho = null;
try { rascunho = JSON.parse(texto); } catch (e) { rascunho = null; }
return [{ json: { rota: 'entregar', rascunho, texto, modelo: b.model || '', stop_reason: b.stop_reason || '' } }];
""")
    g.se("Redator respondeu?", (6160, 1300), "$json.rota === 'entregar'")
    g.http("Entregar rascunho", (6380, 1200), "POST",
           "={{ '" + API + "/runs/' + " + RUN + " + '/relatorio/rascunho' }}",
           corpo="={{ JSON.stringify({ rascunho: $json.rascunho, modelo: $json.modelo, stop_reason: $json.stop_reason }) }}",
           timeout_s=120)
    g.code("Ler portão", (6600, 1300), """
const r = $input.first().json;
const b = r.body || {};
let rota = 'erro';
if (r.statusCode === 200 && b.ok) rota = 'pronto';
else if (r.statusCode === 200 && b.restam_automaticas > 0) rota = 'corrigir';
else if (r.statusCode === 200 || b?.detail?.erro === 'tentativas_esgotadas') rota = 'autor';
return [{ json: { rota, url: b.url, falhas: b.falhas || [], detalhe: JSON.stringify(b).slice(0, 600) } }];
""")
    g.se("Relatório aprovado?", (6820, 1300), "$json.rota === 'pronto'")
    g.fim("Fim: relatório pronto", (7040, 1200), "Relatório pronto",
          "={{ 'Relatório conferido número a número: " + UI + "' + $json.url }}")
    g.se("Nova tentativa automática?", (7040, 1400), "$json.rota === 'corrigir'")
    g.code("Pedir correção", (7260, 1300), """
const anterior = $('Pedir correção').isExecuted ? $('Pedir correção').last().json : $('Montar chamada ao Claude').last().json;
const resp = $('Ler resposta do Claude').last().json;
const falhas = $input.first().json.falhas;
const body = JSON.parse(JSON.stringify(anterior.body));
body.messages.push({ role: 'assistant', content: resp.texto || '(vazio)' });
body.messages.push({ role: 'user', content: 'O portão de números da ômica reprovou o rascunho. Falhas: ' + JSON.stringify(falhas) +
  '. Devolva o JSON INTEIRO de novo corrigindo só isso; todo número continua só como {{f:id}}.' });
return [{ json: { esquema: anterior.esquema, body } }];
""")
    g.se("Levar ao autor (relatório)?", (7260, 1500), "$json.rota === 'autor'")
    g.code("Campos da revisão", (7480, 1500), JS_ESCAPAR + """
const e = $input.first().json;
const resp = $('Ler resposta do Claude').last().json;
return [{ json: { campos: [
  { fieldType: 'html', html: '<p>O portão reprovou o rascunho e as tentativas automáticas acabaram. Falhas:</p><ul>' +
    (e.falhas || []).map((f) => '<li>' + esc(JSON.stringify(f)) + '</li>').join('') + '</ul>' +
    '<p>Corrija o JSON abaixo (números só como {{f:id}}). Ele passa pelo MESMO portão.</p>' },
  { fieldLabel: 'Rascunho (JSON)', fieldName: 'rascunho', fieldType: 'textarea', requiredField: true,
    defaultValue: JSON.stringify(resp.rascunho ?? {}, null, 2) }] } }];
""")
    g.pagina("Revisão do autor", (7700, 1500), "Revisar o relatório", "={{ JSON.stringify($json.campos) }}",
             botao="Enviar ao portão")
    g.code("Ler revisão", (7920, 1500), """
let rascunho = null;
try { rascunho = JSON.parse($input.first().json.rascunho); } catch (e) { rascunho = { erro: 'JSON inválido: ' + e.message }; }
return [{ json: { rascunho } }];
""")
    g.http("Entregar edição", (8140, 1500), "POST",
           "={{ '" + API + "/runs/' + " + RUN + " + '/relatorio/rascunho' }}",
           corpo="={{ JSON.stringify({ rascunho: $json.rascunho, autor: true }) }}", timeout_s=120)
    g.fim("Fim: erro no relatório", (7480, 1700), "Erro no relatório", "={{ $json.detalhe }}")
    g.pagina("Redator indisponível", (6380, 1500), "O redator não respondeu",
             "={{ JSON.stringify([{ fieldType: 'html', html: '<p>A API da Anthropic não respondeu como esperado.</p>' }, "
             "{ fieldLabel: 'O que fazer', fieldName: 'decisao', fieldType: 'radio', requiredField: true, "
             "fieldOptions: { values: [{ option: 'Tentar de novo' }, { option: 'Encerrar sem relatório' }] } }]) }}")
    g.se("Tentar o redator de novo?", (6600, 1600), "$json.decisao === 'Tentar de novo'")
    g.code("Repetir chamada", (6820, 1600), """
const anterior = $('Pedir correção').isExecuted ? $('Pedir correção').last().json : $('Montar chamada ao Claude').last().json;
return [{ json: anterior }];
""")
    g.fim("Fim: sem redator", (6820, 1800), "Run concluído, sem relatório",
          "={{ 'Os resultados estão em " + UI + "/jobs; o relatório pode ser pedido de novo depois.' }}")
    for a, b, s in [("Pedido de relatório", "Ficha pronta?", 0), ("Ficha pronta?", "Montar chamada ao Claude", 0),
                    ("Ficha pronta?", "Fim: sem relatório", 1), ("Montar chamada ao Claude", "Claude (redator)", 0),
                    ("Claude (redator)", "Ler resposta do Claude", 0), ("Ler resposta do Claude", "Redator respondeu?", 0),
                    ("Redator respondeu?", "Entregar rascunho", 0), ("Redator respondeu?", "Redator indisponível", 1),
                    ("Redator indisponível", "Tentar o redator de novo?", 0),
                    ("Tentar o redator de novo?", "Repetir chamada", 0), ("Tentar o redator de novo?", "Fim: sem redator", 1),
                    ("Repetir chamada", "Claude (redator)", 0),
                    ("Entregar rascunho", "Ler portão", 0), ("Ler portão", "Relatório aprovado?", 0),
                    ("Relatório aprovado?", "Fim: relatório pronto", 0), ("Relatório aprovado?", "Nova tentativa automática?", 1),
                    ("Nova tentativa automática?", "Pedir correção", 0), ("Pedir correção", "Claude (redator)", 0),
                    ("Nova tentativa automática?", "Levar ao autor (relatório)?", 1),
                    ("Levar ao autor (relatório)?", "Campos da revisão", 0),
                    ("Levar ao autor (relatório)?", "Fim: erro no relatório", 1),
                    ("Campos da revisão", "Revisão do autor", 0), ("Revisão do autor", "Ler revisão", 0),
                    ("Ler revisão", "Entregar edição", 0), ("Entregar edição", "Ler portão", 0)]:
        g.liga(a, b, s)

    return {
        "id": IDS_WORKFLOW[p.id], "name": f"Ômica · {p.titulo}", "nodes": g.nos, "connections": g.arestas,
        "active": False, "pinData": {},
        "settings": {"executionOrder": "v1", "saveDataErrorExecution": "all", "saveDataSuccessExecution": "all",
                     "saveManualExecutions": True, "callerPolicy": "workflowsFromSameOwner"},
        "meta": {"gerado_por": "omica/n8n/build_workflows.py", "pipeline": p.id},
        "tags": [],
    }


SEGREDOS = ("sk-ant-", "Bearer ey", "OMICA_API_TOKEN=", "x-api-key: sk", "-----BEGIN")


def conferir(wf: dict[str, Any]) -> list[str]:
    """Portão do dist: nada de segredo, toda aresta aponta para nó existente, sem nó solto."""
    problemas = []
    texto = json.dumps(wf, ensure_ascii=False)
    problemas += [f"padrão de segredo no JSON: {s!r}" for s in SEGREDOS if s in texto]
    nomes = {n["name"] for n in wf["nodes"]}
    destinos = set()
    for de, saidas in wf["connections"].items():
        if de not in nomes:
            problemas.append(f"aresta sai de nó inexistente: {de}")
        for lista in saidas["main"]:
            for a in lista:
                destinos.add(a["node"])
                if a["node"] not in nomes:
                    problemas.append(f"aresta para nó inexistente: {de} → {a['node']}")
    gatilhos = {n["name"] for n in wf["nodes"] if n["type"].endswith("Trigger")}
    soltos = nomes - destinos - gatilhos
    problemas += [f"nó sem entrada: {n}" for n in sorted(soltos)]
    return problemas


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--checar", action="store_true", help="só confere que dist/ está em dia")
    a = ap.parse_args()
    validos, erros = carregar_pipelines()
    if erros:
        print("pipelines com erro:", erros)
        return 1
    DIST.mkdir(exist_ok=True)
    falhou = False
    for pid, p in sorted(validos.items()):
        if pid not in IDS_WORKFLOW:
            print(f"[pula] {pid}: sem id de workflow em IDS_WORKFLOW")
            continue
        wf = construir(p)
        problemas = conferir(wf)
        if problemas:
            falhou = True
            print(f"[ERRO] {pid}:", *problemas, sep="\n  ")
            continue
        texto = json.dumps(wf, ensure_ascii=False, indent=2) + "\n"
        destino = DIST / f"{pid}.json"
        if a.checar:
            em_dia = destino.exists() and destino.read_text(encoding="utf-8") == texto
            print(f"[{'ok' if em_dia else 'DESATUALIZADO'}] {destino.name}")
            falhou |= not em_dia
        else:
            # LF em qualquer SO: o mesmo arquivo, byte a byte, no Windows e no Linux
            destino.write_text(texto, encoding="utf-8", newline="\n")
            gatilho = next(n for n in wf["nodes"] if n["type"].endswith("formTrigger"))
            print(f"[ok] {destino.relative_to(RAIZ)}: {len(wf['nodes'])} nós · formulário: "
                  f"http://127.0.0.1:5678/form/{gatilho['webhookId']}")
    return 1 if falhou else 0


if __name__ == "__main__":
    raise SystemExit(main())
