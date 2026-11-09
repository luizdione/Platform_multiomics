"""Builder dos workflows do n8n (F5 genômica, especificação G1 §4; F6, ESPEC §4 + adendo §A10).

O que se confere aqui:
  - cada JSON do dist sai BYTE A BYTE igual ao sha fixado (mudou? reimportar, publicar e
    refazer o E2E daquele workflow — não é efeito colateral aceitável);
  - D-10: entre o gatilho e o `Criar run` há a escolha do contexto (lista da ômica NA HORA,
    sem privados, conferida antes do POST); o corpo do POST leva pipeline, especie e
    subprojeto; o resumo do plano mostra o contexto e os avisos que valem para o módulo (os
    MESMOS que o relatório declara, servico.avisos_que_valem) e recusa um plano que voltou
    para outro contexto; nenhum id ou título de subprojeto vai embutido no dist;
  - o tipo do campo do formulário vem de `pipelines.envio_formulario`, a MESMA função que a
    API expõe em GET /pipelines (upload só para quem não tem índice nem referência);
  - a entrada por caminho tem um segmento próprio (PUT JSON, timeout de 600 s, caminho
    recusado vai a uma página final e cancela o run) e o resumo do plano mostra modo, índice
    e os contigs com variantes;
  - o JS gerado é EXECUTADO num harness de node (quando há node no ambiente): conferir só o
    texto do JS deixaria passar um nó que lança exceção ou monta a linha errada.
Cada canário tem um controle irmão que passa.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from omica import contexto
from omica.config import REPO_ROOT
from omica.pipelines import carregar_pipelines, envio_formulario
from test_runs import api  # noqa: F401 (fixture)

_ESPEC = importlib.util.spec_from_file_location("build_workflows", REPO_ROOT / "n8n" / "build_workflows.py")
bw = importlib.util.module_from_spec(_ESPEC)
_ESPEC.loader.exec_module(bw)

# sha256 do texto (LF, como o json.dumps gera) de cada workflow do dist. O da transcriptômica
# (antes f9bd2f20…, publicado em 2026-10-26) e os da genômica mudaram na F6 com a D-10 (página
# de contexto e corpo do POST): os 3 precisam ser reimportados e publicados (as URLs
# /form/<webhookId> não mudam) e ter o E2E-0 refeito. Mudou de novo? Mesma coisa — não é
# efeito colateral aceitável de mexer em outro pipeline. Os 6 mudaram juntos com o filtro dos
# avisos no 'Resumo do plano' (X5′, crítica M4: antes cdb0fa0f…, 69d0196d…, c36d74e6…,
# 9decc93c…, 648f7e04…, 1a03bfc0…): os 6 precisam ser reimportados e publicados.
SHAS_DIST = {
    "transcriptomica_de": "bad34544ee2250eaabd7be1adbdb706393b078270ace8ccfb447bde3edc0b2c0",
    "genomica_painel": "d914c13feda33e408e401e59d5804820fc8a7643491705c1fa8a8afd2838c4af",
    "genomica_regioes": "020ee67636cadf5dda0427ddc8df386ad6e80170990471fd52d31c7b90e57ad6",
    "ml_aims_vcf": "357c19472f4b9d74a16ee4ba8a9677166c28a2ae88d52cfb7562d1ffd6ac3d27",
    "epigenomica_dmc": "41b18a491de8a06b3c518f990be987be793e488f2288135cc33838c1402635b7",
    "epigenomica_mofa": "49b9154cbafe199709d0c64fb691936524a786390c51fc0406a5a9f88be9d333",
}
SHA_TRANSCRIPTOMICA = SHAS_DIST["transcriptomica_de"]
GENOMICA = ("genomica_painel", "genomica_regioes")
EPIGENOMICA = ("epigenomica_dmc", "epigenomica_mofa")
# o nó que confere o contexto, como o 'Resumo do plano' o lê (par escolhido no formulário)
CONFERIDO = {"Conferir contexto": [{"json": {"ok": True, "especie": "bos_taurus", "subprojeto": "demo"}}]}


@pytest.fixture(scope="module")
def validos():
    v, erros = carregar_pipelines()
    assert erros == {}, erros
    return v


def _texto(wf: dict) -> str:
    return json.dumps(wf, ensure_ascii=False, indent=2) + "\n"


def _nos(wf: dict) -> dict[str, dict]:
    return {n["name"]: n for n in wf["nodes"]}


def _saidas(wf: dict, de: str) -> list[list[str]]:
    return [[a["node"] for a in lista] for lista in wf["connections"].get(de, {}).get("main", [])]


# ------------------------------------------------------------- dist e ids
def _sha(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("pid", sorted(SHAS_DIST))
def test_dist_sai_byte_a_byte_igual_ao_sha_fixado(validos, pid):
    assert _sha(_texto(bw.construir(validos[pid]))) == SHAS_DIST[pid], pid


def test_sha_fixado_morde_uma_mudanca_so_na_genomica(validos):
    """Canário do byte a byte (no lugar do antigo, que só olhava a transcriptômica): trocar o
    aviso de alcance de UM pipeline da genômica muda o sha dele e só dele. Controle: o
    workflow sem mudança bate."""
    wf = bw.construir(validos["genomica_regioes"])
    assert _sha(_texto(wf)) == SHAS_DIST["genomica_regioes"]
    mexido = copy.deepcopy(wf)
    no = next(n for n in mexido["nodes"] if n["name"] == "Resumo do plano")
    no["parameters"]["jsCode"] = no["parameters"]["jsCode"].replace("regiões em outros", "regiões noutros")
    assert _sha(_texto(mexido)) != SHAS_DIST["genomica_regioes"]
    assert _sha(_texto(bw.construir(validos["genomica_painel"]))) == SHAS_DIST["genomica_painel"]


def test_dist_em_dia_e_todo_pipeline_tem_workflow(validos):
    """O main PULA (só imprime) pipeline sem id em IDS_WORKFLOW: um pipeline novo ficaria sem
    formulário em silêncio. E o dist versionado é o que se importa: tem de ser o gerado."""
    assert set(validos) <= set(bw.IDS_WORKFLOW), set(validos) - set(bw.IDS_WORKFLOW)
    assert set(bw.IDS_WORKFLOW) == set(SHAS_DIST)
    for pid in bw.IDS_WORKFLOW:
        wf = bw.construir(validos[pid])
        assert bw.conferir(wf) == [], pid
        assert (bw.DIST / f"{pid}.json").read_text(encoding="utf-8") == _texto(wf), \
            f"dist/{pid}.json desatualizado: rode python n8n/build_workflows.py"
    assert sorted(p.name for p in bw.DIST.glob("*.json")) == sorted(f"{pid}.json" for pid in bw.IDS_WORKFLOW)
    assert bw.IDS_WORKFLOW["genomica_painel"] == "omicaGenomPain01"
    assert bw.IDS_WORKFLOW["genomica_regioes"] == "omicaGenomRegi01"
    # F6 (ESPEC §4 item 1): ids fixos de 16 caracteres, como os atuais, e todos distintos
    assert bw.IDS_WORKFLOW["ml_aims_vcf"] == "omicaMLAimsVcf01"
    assert bw.IDS_WORKFLOW["epigenomica_dmc"] == "omicaEpigDMC0001"
    assert bw.IDS_WORKFLOW["epigenomica_mofa"] == "omicaEpigMOFA001"
    assert {len(i) for i in bw.IDS_WORKFLOW.values()} == {16}
    assert len(set(bw.IDS_WORKFLOW.values())) == len(bw.IDS_WORKFLOW)


def _rodar_main(monkeypatch, dist: Path, *args: str) -> int:
    monkeypatch.setattr(bw, "DIST", dist)
    monkeypatch.setattr(sys, "argv", ["build_workflows.py", *args])
    return bw.main()


def test_checar_sai_com_0_e_morde_um_dist_velho(monkeypatch, tmp_path, capsys):
    """Aceite da F4: `--checar` com código 0 sobre o dist versionado (processo de verdade, como
    o CI roda). Canário: numa cópia com UM arquivo velho, o código é 1 e ele sai marcado;
    controle: a cópia intacta dá 0."""
    r = subprocess.run([sys.executable, str(REPO_ROOT / "n8n" / "build_workflows.py"), "--checar"],
                       capture_output=True, text=True, encoding="utf-8", timeout=300, cwd=REPO_ROOT,
                       env={**os.environ, "PYTHONUTF8": "1"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "DESATUALIZADO" not in r.stdout and r.stdout.count("[ok]") == len(bw.IDS_WORKFLOW)
    copia = tmp_path / "dist"
    shutil.copytree(bw.DIST, copia)
    assert _rodar_main(monkeypatch, copia, "--checar") == 0
    velho = copia / "ml_aims_vcf.json"
    velho.write_text(velho.read_text(encoding="utf-8").replace("privados=false", "privados=true"),
                     encoding="utf-8", newline="\n")
    capsys.readouterr()
    assert _rodar_main(monkeypatch, copia, "--checar") == 1
    assert "[DESATUALIZADO] ml_aims_vcf.json" in capsys.readouterr().out


# ------------------------------------------------------- campos do formulário
def test_campo_do_formulario_segue_envio_formulario(validos):
    for pid, p in validos.items():
        campos = {c["fieldName"]: c for c in
                  _nos(bw.construir(p))["Arquivos do experimento"]["parameters"]["formFields"]["values"]}
        assert set(campos) == set(p.entradas), pid
        for nome, e in p.entradas.items():
            c = campos[nome]
            assert c["fieldLabel"] == e.rotulo and c["requiredField"] is True
            if envio_formulario(e) == "caminho":
                assert c["fieldType"] == "text" and c["placeholder"] == e.exemplo, (pid, nome)
                assert "acceptFileTypes" not in c and "multipleFiles" not in c
            else:
                assert c["fieldType"] == "file" and c["multipleFiles"] is False, (pid, nome)


def test_extensoes_do_upload_por_formato(validos):
    """O BED só .bed (com outro nome o bcftools lê 1-based); o painel só .txt; o resto como
    sempre. Canário de fato: com o padrão, o formulário oferecia .txt para o BED."""
    def aceita(pid: str, nome: str) -> str:
        campos = _nos(bw.construir(validos[pid]))["Arquivos do experimento"]["parameters"]["formFields"]["values"]
        return next(c for c in campos if c["fieldName"] == nome)["acceptFileTypes"]
    assert aceita("genomica_regioes", "regioes") == ".bed"
    assert aceita("genomica_painel", "painel") == ".txt"
    assert aceita("transcriptomica_de", "counts") == aceita("transcriptomica_de", "coldata") == ".tsv,.csv,.txt"
    # F6 (ESPEC §4 item 2): o painel da epigenômica é JSON; os rótulos do ML, .tsv/.txt; o
    # coldata da epigenômica continua no padrão
    for pid in EPIGENOMICA:
        assert aceita(pid, "painel") == ".json"
        assert aceita(pid, "amostras") == ".tsv,.csv,.txt"
    assert aceita("ml_aims_vcf", "rotulos") == ".tsv,.txt"


def test_pasta_e_vcf_do_ml_sao_caminho(validos):
    """F6 §4 item 3: nada muda em campo_do_formulario — a pasta de coverages e o VCF do ML
    viram campo de TEXTO com o exemplo do contrato (envio_formulario devolve 'caminho')."""
    for pid, nome in [*((p, "coverages") for p in EPIGENOMICA), ("ml_aims_vcf", "vcf")]:
        campos = _nos(bw.construir(validos[pid]))["Arquivos do experimento"]["parameters"]["formFields"]["values"]
        c = next(x for x in campos if x["fieldName"] == nome)
        assert c["fieldType"] == "text" and c["placeholder"] == validos[pid].entradas[nome].exemplo, (pid, nome)


def test_escolhas_diferentes_avisam_no_formulario(validos):
    """F6 §4 item 5: com `diferente_de` (B ≠ A), a página das escolhas avisa; o 422
    escolhas_iguais cai em 'Fim: plano recusado'. Controle: a transcriptômica (escolha sem
    diferente_de) não ganha a frase; o MOFA (sem escolhas) segue pelo 'Sem escolhas'."""
    frase = "Os dois grupos têm de ser diferentes."
    for pid in ("ml_aims_vcf", "epigenomica_dmc"):
        assert frase in _nos(bw.construir(validos[pid]))["Escolhas do autor"]["parameters"]["options"]["formDescription"]
    assert frase not in _nos(bw.construir(validos["transcriptomica_de"]))["Escolhas do autor"]["parameters"]["options"]["formDescription"]
    mofa = _nos(bw.construir(validos["epigenomica_mofa"]))
    assert "Sem escolhas" in mofa and "Escolhas do autor" not in mofa


# ------------------------------------------------------- contexto do run (D-10)
CADEIA_CONTEXTO = ["Arquivos do experimento", "Contextos do pipeline", "Campos do contexto", "Contexto do run",
                   "Conferir contexto", "Contexto válido?"]


def test_cadeia_do_contexto_nos_6_workflows(validos):
    """Adendo §A10 item 0: gatilho → lista da ômica → página → conferência → IF; só o ramo
    verdadeiro chega ao POST (a aresta antiga gatilho → Criar run saiu) e o falso termina sem
    POST. A URL pede a regra do POST /runs SEM privados."""
    assert set(bw.IDS_WORKFLOW) >= {"ml_aims_vcf", *EPIGENOMICA}
    for pid in bw.IDS_WORKFLOW:
        wf = bw.construir(validos[pid])
        nos = _nos(wf)
        for de, para in zip(CADEIA_CONTEXTO, CADEIA_CONTEXTO[1:]):
            assert _saidas(wf, de) == [[para]], (pid, de)
        assert _saidas(wf, "Contexto válido?") == [["Criar run"], ["Fim: contexto recusado"]], pid
        quem_cria = sorted(de for de in wf["connections"] if any("Criar run" in s for s in _saidas(wf, de)))
        assert quem_cria == ["Contexto válido?"], pid
        assert "Fim: contexto recusado" not in wf["connections"]  # termina ali, sem POST
        http = nos["Contextos do pipeline"]["parameters"]
        assert http["method"] == "GET" and "sendBody" not in http
        assert http["url"] == f"{bw.API}/subprojetos?pipeline={pid}&privados=false"
        fim = nos["Fim: contexto recusado"]["parameters"]
        assert fim["operation"] == "completion"
        assert fim["completionMessage"] == "O subprojeto escolhido não está na lista desta ômica."
        pagina = nos["Contexto do run"]["parameters"]
        assert pagina["operation"] == "page" and pagina["jsonOutput"] == "={{ JSON.stringify($json.campos) }}"
        assert pagina["options"]["formTitle"] == "Onde este run vai morar"


def test_conferir_pega_a_conferencia_do_contexto_desligada(validos):
    """Canário do portão do dist sobre a cadeia nova: sem a aresta da conferência, o IF fica
    solto (o POST nunca rodaria). Controle: o workflow inteiro passa."""
    wf = bw.construir(validos["ml_aims_vcf"])
    assert bw.conferir(wf) == []
    quebrado = copy.deepcopy(wf)
    del quebrado["connections"]["Conferir contexto"]
    assert "nó sem entrada: Contexto válido?" in bw.conferir(quebrado)


def _embutidos(texto: str, regs) -> list[str]:
    """Ids e títulos de subprojeto que aparecem no texto de um workflow (deve ser nenhum)."""
    achados = ['"bos_taurus/'] if '"bos_taurus/' in texto else []
    for r in regs:
        achados += [x for x in (r.sub.id, r.sub.titulo) if x and x in texto]
    return achados


def test_dist_nao_embute_subprojeto(validos):
    """Adendo §A10: a lista de subprojetos nunca é embutida no dist (versionado): nem um
    `"bos_taurus/…`, nem id, nem título de subprojeto. Canário: um workflow com a lista do
    catálogo gravada no dropdown é pego; controle: o dist real passa."""
    regs = list(contexto.catalogo().subprojetos.values())
    assert any(r.sub.id == "bos_taurus/demo" for r in regs)
    for arq in sorted(bw.DIST.glob("*.json")):
        assert _embutidos(arq.read_text(encoding="utf-8"), regs) == [], arq.name
    wf = bw.construir(validos["transcriptomica_de"])
    no = next(n for n in wf["nodes"] if n["name"] == "Campos do contexto")
    no["parameters"]["jsCode"] = no["parameters"]["jsCode"].replace(
        "const ids = lista.map", "const fixos = " + json.dumps([r.sub.id for r in regs]) + ";\nconst ids = lista.map")
    achados = _embutidos(_texto(wf), regs)
    assert '"bos_taurus/' in achados and "bos_taurus/demo" in achados, achados


def test_vcf_e_caminho_nos_dois_pipelines_da_genomica(validos):
    for pid in GENOMICA:
        campos = _nos(bw.construir(validos[pid]))["Arquivos do experimento"]["parameters"]["formFields"]["values"]
        vcf = next(c for c in campos if c["fieldName"] == "vcf")
        assert vcf["fieldType"] == "text" and vcf["placeholder"].endswith("demo_bta14_inicio.vcf.gz")
        # o rótulo cita o caminho do CONTAINER para a caixa (crítica #12): relativo resolve no repo
        assert "/data/omica/entradas/" in vcf["fieldLabel"]


# ------------------------------------------------------------ segmento de caminhos
def test_cadeia_de_envio_da_genomica(validos):
    for pid in GENOMICA:
        wf = bw.construir(validos[pid])
        nos = _nos(wf)
        cadeia = ["Criar run", "Separar arquivos", "Enviar entrada", "Conferir envios", "Separar caminhos",
                  "Enviar caminhos", "Conferir caminhos", "Caminhos aceitos?", "Ler run"]
        for de, para in zip(cadeia, cadeia[1:]):
            assert _saidas(wf, de)[0] == [para], (pid, de)
        assert _saidas(wf, "Caminhos aceitos?")[1] == ["Cancelar run (caminho)"]
        assert _saidas(wf, "Cancelar run (caminho)") == [["Fim: caminho recusado"]]
        assert "/abortar" in nos["Cancelar run (caminho)"]["parameters"]["url"]
        # upload só do que é arquivo; o VCF nunca vai pelo binário
        upload = next(n for n in validos[pid].entradas if n != "vcf")
        js = nos["Separar arquivos"]["parameters"]["jsCode"]
        assert f'return ["{upload}"].map' in js and '"vcf"' not in js
        put = nos["Enviar caminhos"]["parameters"]
        assert put["method"] == "PUT" and put["specifyBody"] == "json"
        assert put["jsonBody"] == "={{ JSON.stringify({ caminho: $json.caminho }) }}"
        assert put["options"]["timeout"] == 600_000 and "binaryData" not in json.dumps(put)
        assert put["url"] == nos["Enviar entrada"]["parameters"]["url"]


def test_transcriptomica_nao_tem_segmento_de_caminho(validos):
    nos = _nos(bw.construir(validos["transcriptomica_de"]))
    assert not {"Separar caminhos", "Enviar caminhos", "Conferir caminhos", "Caminhos aceitos?",
                "Fim: caminho recusado"} & set(nos)
    assert "contigs_com_dados" not in nos["Resumo do plano"]["parameters"]["jsCode"]


def test_pipeline_so_com_caminho_confere_o_201_no_segmento(validos):
    """Sem upload, o 'Separar arquivos' (que conferia o 201 do Criar run) não existe: o
    segmento de caminhos passa a ser o primeiro e tem de conferir o 201 ele mesmo."""
    p = validos["genomica_painel"]
    so_vcf = p.model_copy(update={"entradas": {"vcf": p.entradas["vcf"]}})
    wf = bw.construir(so_vcf)
    nos = _nos(wf)
    assert "Separar arquivos" not in nos and "Enviar entrada" not in nos
    assert _saidas(wf, "Criar run") == [["Separar caminhos"]]
    assert "r.statusCode !== 201" in nos["Separar caminhos"]["parameters"]["jsCode"]
    assert bw.conferir(wf) == []
    # controle: com upload antes, quem confere o 201 é o Separar arquivos
    com = _nos(bw.construir(p))
    assert "r.statusCode !== 201" not in com["Separar caminhos"]["parameters"]["jsCode"]
    assert "r.statusCode !== 201" in com["Separar arquivos"]["parameters"]["jsCode"]


def test_conferir_pega_o_segmento_desligado(validos):
    """Canário do portão do dist sobre o segmento novo: um IF solto (aresta esquecida) seria
    importado e nunca rodaria. Controle: o workflow inteiro passa."""
    wf = bw.construir(validos["genomica_painel"])
    assert bw.conferir(wf) == []
    quebrado = copy.deepcopy(wf)
    del quebrado["connections"]["Conferir caminhos"]
    assert "nó sem entrada: Caminhos aceitos?" in bw.conferir(quebrado)


def test_aviso_de_alcance_por_pipeline(validos):
    painel = _nos(bw.construir(validos["genomica_painel"]))["Resumo do plano"]["parameters"]["jsCode"]
    regioes = _nos(bw.construir(validos["genomica_regioes"]))["Resumo do plano"]["parameters"]["jsCode"]
    assert "genes do painel em outros cromossomos não terão variantes extraídas" in painel
    assert "regiões em outros cromossomos não terão variantes extraídas" in regioes
    for js in (painel, regioes):
        assert "$('Ler run (escolhas)').last().json.body.entradas" in js and "+ extras(n, e) +" in js


# ------------------------------------------------- JS gerado, executado no node
NODE = shutil.which("node")
precisa_node = pytest.mark.skipif(NODE is None, reason="sem node neste ambiente (a imagem web não tem)")

HARNESS = r"""
const fs = require('fs');
const ctx = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const codigo = fs.readFileSync(process.argv[3], 'utf8');
const entrada = ctx.entrada || [];
const $input = { first: () => entrada[0], last: () => entrada[entrada.length - 1], all: () => entrada };
const $ = (nome) => {
  const xs = (ctx.nos || {})[nome];
  if (!xs) return { isExecuted: false, first: () => { throw new Error('nó não executado: ' + nome); },
                    last: () => { throw new Error('nó não executado: ' + nome); }, all: () => [] };
  return { isExecuted: true, first: () => xs[0], last: () => xs[xs.length - 1], all: () => xs };
};
const fn = new Function('$input', '$', 'return (async () => {\n' + codigo + '\n})();');
fn($input, $).then((r) => process.stdout.write(JSON.stringify({ ok: r })),
                   (e) => process.stdout.write(JSON.stringify({ erro: String(e && e.message) })));
"""


def _rodar(tmp_path: Path, codigo: str, entrada=None, nos=None) -> dict:
    (tmp_path / "ctx.json").write_text(json.dumps({"entrada": entrada or [], "nos": nos or {}}), encoding="utf-8")
    (tmp_path / "codigo.js").write_text(codigo, encoding="utf-8")
    (tmp_path / "harness.js").write_text(HARNESS, encoding="utf-8")
    r = subprocess.run([NODE, str(tmp_path / "harness.js"), str(tmp_path / "ctx.json"), str(tmp_path / "codigo.js")],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _js(validos, pid: str, no: str) -> str:
    return _nos(bw.construir(validos[pid]))[no]["parameters"]["jsCode"]


@precisa_node
def test_js_separar_caminhos(validos, tmp_path):
    js = _js(validos, "genomica_painel", "Separar caminhos")
    rotulo = validos["genomica_painel"].entradas["vcf"].rotulo
    ok = _rodar(tmp_path, js, [{"json": {}}],
                {"Arquivos do experimento": [{"json": {"vcf": "  /data/1000bg_vcf/Chr14-Run8-TAUIND-public.bcf \n",
                                                       "painel": "ignorado"}}]})
    assert ok == {"ok": [{"json": {"nome": "vcf", "caminho": "/data/1000bg_vcf/Chr14-Run8-TAUIND-public.bcf"}}]}
    # o gatilho que devolvesse pelo rótulo (e não pelo fieldName) ainda funciona
    pelo_rotulo = _rodar(tmp_path, js, [{"json": {}}], {"Arquivos do experimento": [{"json": {rotulo: "x.vcf.gz"}}]})
    assert pelo_rotulo["ok"][0]["json"]["caminho"] == "x.vcf.gz"
    # canários: vazio, só espaço e não-texto lançam, em vez de mandar {"caminho": ""} à ômica
    for valor in ("", "   \n", None, ["a"]):
        r = _rodar(tmp_path, js, [{"json": {}}], {"Arquivos do experimento": [{"json": {"vcf": valor}}]})
        assert r.get("erro", "").startswith("Caminho vazio no formulário: vcf"), (valor, r)


@precisa_node
def test_js_conferir_caminhos(validos, tmp_path):
    js = _js(validos, "genomica_regioes", "Conferir caminhos")
    separados = {"Separar caminhos": [{"json": {"nome": "vcf", "caminho": "x"}}]}
    ok = _rodar(tmp_path, js, [{"json": {"statusCode": 200, "body": {"veredito": {"ok": False}}}}], separados)
    # 200 com veredito reprovado segue (quem decide é a 'Fila de adaptação' → 'Fim: entrada inválida')
    assert ok == {"ok": [{"json": {"rota": "ok"}}]}
    corpo = {"detail": {"erro": "caminho_recusado", "detalhe": "entrada por caminho só de ..."}}
    r = _rodar(tmp_path, js, [{"json": {"statusCode": 422, "body": corpo}}], separados)
    assert r["ok"][0]["json"]["rota"] == "recusado"
    assert r["ok"][0]["json"]["detalhe"].startswith("vcf (422): ") and "caminho_recusado" in r["ok"][0]["json"]["detalhe"]


CONTEXTO_DEMO = {"especie": "bos_taurus", "subprojeto": "demo", "assembly": "ARS-UCD1.2", "sha256": "f" * 64,
                 "avisos": []}


def _plano(entradas: dict, contexto: dict | None = CONTEXTO_DEMO, passos: list | None = None,
           modulo: str | None = None) -> dict:
    corpo = {
        "passos": passos or [{"id": "extracao", "titulo": "Extração", "fila": "bioinfo", "mem_gb": 4, "tempo_estimado": "1 min"}],
        "entradas": entradas, "escolhas": {}, "recursos": {"pico_mem_gb": 4}, "plano_sha256": "a" * 64}
    if contexto is not None:
        corpo["contexto"] = contexto
    if modulo is not None:  # o plano real traz o módulo (runs.propor_plano); o filtro dos avisos o lê
        corpo["modulo"] = modulo
    return {"statusCode": 200, "body": corpo}


def _ler_run(perfil: dict) -> dict:
    return {**CONFERIDO, "Ler run (escolhas)": [{"json": {"statusCode": 200, "body": {"entradas": {
        "painel": {"veredito": {"ok": True, "perfil": {"n_simbolos": 14}}},
        "vcf": {"veredito": {"ok": True, "perfil": perfil}}}}}}]}


def _html(r: dict) -> str:
    return r["ok"][0]["json"]["campos"][0]["html"]


@precisa_node
def test_js_resumo_do_plano_mostra_modo_indice_e_contigs(validos, tmp_path):
    js = _js(validos, "genomica_painel", "Resumo do plano")
    base = {"formato": "vcf_indexado", "bytes": 940_000_000, "sha256": "b" * 64}
    painel = {"formato": "painel_simbolos", "bytes": 120, "sha256": "c" * 64, "modo": "copia"}
    ref = _rodar(tmp_path, js, [{"json": _plano({"painel": painel, "vcf": {
        **base, "modo": "referencia", "irmaos": {".csi": {"sha256": "d" * 64}}}})}],
        _ler_run({"contigs_com_dados": ["14"], "n_contigs_com_dados": 1}))
    html = _html(ref)
    linha_vcf = html.split("<li><b>vcf</b>")[1].split("</li>")[0]
    assert "por referência, sem cópia" in linha_vcf and "+ índice .csi" in linha_vcf
    assert "contigs com variantes: <b>14</b>" in linha_vcf
    assert "genes do painel em outros cromossomos não terão variantes extraídas" in linha_vcf
    # a linha do painel (upload, sem contigs no perfil) não ganha nada disso
    linha_painel = html.split("<li><b>painel</b>")[1].split("</li>")[0]
    assert "referência" not in linha_painel and "contigs" not in linha_painel and "índice" not in linha_painel
    # controle: cópia com .tbi não diz "por referência"
    copia = _html(_rodar(tmp_path, js, [{"json": _plano({"vcf": {**base, "modo": "copia",
                                                                 "irmaos": {".tbi": {}}}})}],
                         _ler_run({"contigs_com_dados": ["14"], "n_contigs_com_dados": 1})))
    assert "por referência" not in copia and "+ índice .tbi" in copia


@precisa_node
def test_js_resumo_do_plano_trunca_e_escapa_contigs(validos, tmp_path):
    js = _js(validos, "genomica_regioes", "Resumo do plano")
    contigs = ["<img src=x onerror=alert(1)>"] + [str(i) for i in range(1, 50)]
    html = _html(_rodar(tmp_path, js, [{"json": _plano({"vcf": {"formato": "vcf_indexado", "bytes": 1,
                                                                "sha256": "b" * 64, "modo": "copia"}})}],
                        _ler_run({"contigs_com_dados": contigs, "n_contigs_com_dados": 87})))
    assert "(os 50 primeiros de 87)" in html
    assert "<img" not in html and "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "regiões em outros cromossomos não terão variantes extraídas" in html


@precisa_node
def test_js_resumo_do_plano_da_transcriptomica_continua_igual(validos, tmp_path):
    """Controle: sem entrada por caminho, a linha da entrada é a de sempre, mesmo que o plano
    traga modo/irmaos (o código dos extras nem existe no workflow). O HTML novo abre com a
    linha do contexto (adendo §A10 item 8)."""
    js = _js(validos, "transcriptomica_de", "Resumo do plano")
    html = _html(_rodar(tmp_path, js, [{"json": _plano({"counts": {
        "formato": "contagens_genes", "bytes": 10, "sha256": "e" * 64, "modo": "copia", "irmaos": {".x": {}}}})}],
        CONFERIDO))
    assert "<li><b>counts</b>: contagens_genes, 10 bytes, sha256 eeeeeeeeeeee…</li>" in html
    assert html.startswith("<p>Contexto: <b>bos_taurus/demo</b> · assembly ARS-UCD1.2 · sha ffffffffffff…</p>"
                           "<p>Confira tudo antes de aprovar.")
    assert "Avisos do subprojeto" not in html  # sem avisos, sem a lista


@precisa_node
@pytest.mark.parametrize("pid", sorted(SHAS_DIST))
def test_js_resumo_do_plano_mostra_contexto_e_avisos_escapados(validos, tmp_path, pid):
    """Adendo §A10 item 8, nos 6: contexto (espécie/subprojeto, assembly, sha curto) e a lista
    'Avisos do subprojeto', tudo escapado — os avisos vêm de um YAML editável."""
    js = _js(validos, pid, "Resumo do plano")
    ctx = {"especie": "bos_taurus", "subprojeto": "x<y", "assembly": "ARS<UCD>", "sha256": "0123456789ab" + "c" * 52,
           "avisos": ["epigenômica: painel <b>v1</b> & 13 loci", "sem prefixo <i>x</i> & y"]}
    nos = {**_ler_run({}), "Conferir contexto": [{"json": {"ok": True, "especie": "bos_taurus", "subprojeto": "x<y"}}]}
    html = _html(_rodar(tmp_path, js, [{"json": _plano({}, ctx, modulo=validos[pid].modulo)}], nos))
    assert html.startswith("<p>Contexto: <b>bos_taurus/x&lt;y</b> · assembly ARS&lt;UCD&gt; · sha 0123456789ab…</p>")
    # X5′: o aviso com prefixo de módulo só aparece no módulo dele; o sem prefixo, em todos
    geral = "<li>sem prefixo &lt;i&gt;x&lt;/i&gt; &amp; y</li></ul>"
    if pid in EPIGENOMICA:
        assert ("<h4>Avisos do subprojeto</h4><ul><li>epigenômica: painel &lt;b&gt;v1&lt;/b&gt; &amp; 13 loci</li>"
                + geral) in html
    else:
        assert "<h4>Avisos do subprojeto</h4><ul>" + geral in html and "13 loci" not in html
    assert "<b>v1</b>" not in html and "<i>x</i>" not in html


# X5′ (crítica M4): bateria da sincronia com servico.avisos_que_valem. Os casos de borda são os
# que uma normalização "parecida" erraria: \p{M} no lugar da classe canônica (seletor de
# variação e sinal índico têm classe 0, o Python os mantém), trim() no lugar do str.strip()
# (\x85 e \x1f o Python tira; o BOM, não), ':' largo (o Python parte no ':' ASCII ANTES do NFKD).
_LARGO = "".join(chr(ord(c) + 0xFEE0) for c in "genomica")
_AVISOS_SINCRONIA = [
    "epigenômica: painel_108 v1 tem 13 loci errados",
    "Epigenômica : maiúscula e espaço antes dos dois-pontos",
    "EPIGENOMICA: sem acento",
    "  genômica: espaço na frente",
    "Genômica:",
    "transcriptômica: só da transcriptômica",
    "ML: só do ML",
    "ml : com espaço",
    "ml: a: b (só o primeiro ':' conta)",
    "sem prefixo",
    "nota: prefixo que não é módulo",
    "genômica, epigenômica: lista não é módulo",
    ": prefixo vazio",
    _LARGO + ": letras largas viram genomica no NFKD",
    _LARGO + chr(0xFF1A) + " dois-pontos largo não parte",
    "genômica" + chr(0xFE0F) + ": seletor de variação, classe 0",
    "geno" + chr(0x0901) + "mica: sinal índico, classe 0",
    "genomica" + chr(0x0334) + ": marca de classe 1, o Python tira",
    "epigenomica" + chr(0x1F) + ": separador que o strip() tira",
    "epigenomica" + chr(0xFEFF) + ": BOM, que o strip() não tira",
    "epigenomica" + chr(0x3000) + ": espaço ideográfico",
    "", "   ", chr(0x1F), chr(0x85), chr(0xFEFF),
    42, None, ["epigenômica: dentro de lista"],
]


def _avisos_do_html(html_: str) -> list[str]:
    """Os avisos que o resumo mostrou, desescapados, na ordem."""
    from html import unescape
    if "<h4>Avisos do subprojeto</h4>" not in html_:
        return []
    lista = html_.split("<h4>Avisos do subprojeto</h4><ul>")[1].split("</ul>")[0]
    return [unescape(x) for x in lista.removeprefix("<li>").removesuffix("</li>").split("</li><li>")]


def _mostrados(tmp_path: Path, js: str, modulo: str, avisos: list, contexto_base: dict = CONTEXTO_DEMO) -> list[str]:
    plano = _plano({}, {**contexto_base, "avisos": avisos}, modulo=modulo)
    # _ler_run: genômica e ML leem o veredito das entradas (alcance do VCF); já traz o CONFERIDO
    return _avisos_do_html(_html(_rodar(tmp_path, js, [{"json": plano}], _ler_run({}))))


@precisa_node
@pytest.mark.parametrize("pid", sorted(SHAS_DIST))
def test_js_avisos_do_resumo_sao_os_do_relatorio(validos, tmp_path, pid):
    """X5′ (crítica M4): o resumo do plano mostra EXATAMENTE os avisos que o relatório declara
    (servico.avisos_que_valem, sobre o mesmo plano), nos 6 workflows. A bateria só discrimina
    se o filtro tira algo e deixa algo: as duas coisas são conferidas."""
    from omica.relatorio import servico
    modulo = validos[pid].modulo
    js = _js(validos, pid, "Resumo do plano")
    esperado = servico.avisos_que_valem({"modulo": modulo, "contexto": {"avisos": _AVISOS_SINCRONIA}})
    textos = [a for a in _AVISOS_SINCRONIA if isinstance(a, str) and a.strip()]
    assert esperado and len(esperado) < len(textos), (pid, esperado)
    assert _mostrados(tmp_path, js, modulo, _AVISOS_SINCRONIA) == esperado
    # os casos de borda caem do lado que o Python decide, não do que o JS ingênuo decidiria
    assert ("geno" + chr(0x0901) + "mica: sinal índico, classe 0") in esperado
    assert chr(0xFEFF) in esperado and chr(0x85) not in esperado
    assert (("genomica" + chr(0x0334) + ": marca de classe 1, o Python tira") in esperado) == (modulo == "genomica")


@precisa_node
def test_js_avisos_canarios_divergem_do_relatorio(validos, tmp_path):
    """Canários do filtro: cada regra desligada numa CÓPIA do JS diverge do relatório pelo motivo
    previsto. Sem o filtro (a linha de antes) ou sem a lista de módulos, um run da
    transcriptômica mostra o aviso da epigenômica; \\p{M} no lugar da classe canônica esconde o
    aviso geral do sinal índico; trim() no lugar do strip() do Python mostra o \\x85 e esconde o
    BOM. Controle: o JS versionado bate com o relatório."""
    from omica.relatorio import servico
    js = _js(validos, "transcriptomica_de", "Resumo do plano")
    esperado = servico.avisos_que_valem({"modulo": "transcriptomica", "contexto": {"avisos": _AVISOS_SINCRONIA}})
    assert _mostrados(tmp_path, js, "transcriptomica", _AVISOS_SINCRONIA) == esperado
    epi = "epigenômica: painel_108 v1 tem 13 loci errados"
    indico = "geno" + chr(0x0901) + "mica: sinal índico, classe 0"
    modulos = "const MODULOS = " + json.dumps(bw.modulos_dos_avisos()) + ";"
    mutacoes = [
        ("avisosQueValem(Array.isArray(ctx.avisos) ? ctx.avisos : [], p.modulo)",
         "(Array.isArray(ctx.avisos) ? ctx.avisos : [])", lambda m: epi in m and epi not in esperado),
        (modulos, "const MODULOS = [];", lambda m: epi in m and epi not in esperado),
        ("filter((c) => !marcaCombinante(c))", "filter((c) => !/\\p{M}/u.test(c))",
         lambda m: indico in esperado and indico not in m),
        ("const aparar = (s) => s.replace(", "const aparar = (s) => s.trim();\nconst _strip = (s) => s.replace(",
         lambda m: chr(0x85) in m and chr(0xFEFF) not in m),
    ]
    for de, para, motivo in mutacoes:
        assert js.count(de) == 1, de
        mostrado = _mostrados(tmp_path, js.replace(de, para), "transcriptomica", _AVISOS_SINCRONIA)
        assert mostrado != esperado and motivo(mostrado), (para, mostrado)


@precisa_node
def test_js_avisos_casefold_de_modulo_com_ss(validos, tmp_path, monkeypatch):
    """ß → ss é a regra do casefold que o toLowerCase do JS não tem. Nenhum id atual tem "ss",
    então o caso só aparece com um módulo assim na lista (a do relatório, que o builder lê):
    "Straße: …" é aviso do módulo strasse, fora da transcriptômica. Controle: "Strase: …" não é
    módulo nenhum e aparece."""
    from omica.relatorio import servico
    original = servico._ids_de_modulo
    monkeypatch.setattr(servico, "_ids_de_modulo", lambda: set(original()) | {"strasse"})
    js = _js(validos, "transcriptomica_de", "Resumo do plano")
    assert '"strasse"' in js
    avisos = ["Stra" + chr(0xDF) + "e: só do módulo strasse", "STRA" + chr(0x1E9E) + "E: idem, maiúsculo", "Strase: geral"]
    esperado = servico.avisos_que_valem({"modulo": "transcriptomica", "contexto": {"avisos": avisos}})
    assert esperado == ["Strase: geral"]
    assert _mostrados(tmp_path, js, "transcriptomica", avisos) == esperado


@precisa_node
@pytest.mark.parametrize("pid", sorted(SHAS_DIST))
def test_js_avisos_do_demo_por_modulo(validos, tmp_path, pid):
    """O caso do achado, com o contexto REAL do demo (contexto.demo().para_plano()): o aviso
    "epigenômica: painel_108 v1 … 13 loci errados" aparece nos 2 da epigenômica e em nenhum
    outro (antes, um run da transcriptômica o mostrava), e o resumo bate com o relatório."""
    from omica.relatorio import servico
    ctx = contexto.demo().para_plano()
    assert any("13 loci errados" in a for a in ctx["avisos"])  # o demo ainda tem o aviso
    modulo = validos[pid].modulo
    mostrados = _mostrados(tmp_path, _js(validos, pid, "Resumo do plano"), modulo, ctx["avisos"], ctx)
    assert mostrados == servico.avisos_que_valem({"modulo": modulo, "contexto": ctx})
    assert any("13 loci errados" in a for a in mostrados) == (pid in EPIGENOMICA), (pid, mostrados)


def test_modulos_dos_avisos_sao_os_do_relatorio(validos, monkeypatch):
    """A lista embutida no JS é a do relatório (servico._ids_de_modulo, via load_registry),
    normalizada e em ordem, e cobre o módulo de todo pipeline. Canário: um módulo a mais na
    lista do relatório muda a lista do builder (ela não é uma cópia fixa); controle: sem a
    mudança, a lista volta a ser a de sempre."""
    from omica.relatorio import servico
    lista = bw.modulos_dos_avisos()
    assert lista == sorted({servico._normalizar(m) for m in servico._ids_de_modulo()})
    assert {p.modulo for p in validos.values()} <= set(lista)
    for pid in SHAS_DIST:
        assert _js(validos, pid, "Resumo do plano").count("const MODULOS = " + json.dumps(lista) + ";") == 1
    original = servico._ids_de_modulo
    monkeypatch.setattr(servico, "_ids_de_modulo", lambda: set(original()) | {"Proteômica"})
    assert bw.modulos_dos_avisos() == sorted(lista + ["proteomica"])
    monkeypatch.undo()
    assert bw.modulos_dos_avisos() == lista


@precisa_node
@pytest.mark.parametrize("pid", ["transcriptomica_de", "ml_aims_vcf", "epigenomica_dmc"])
def test_js_resumo_do_plano_recusa_plano_de_outro_contexto(validos, tmp_path, pid):
    """D-10, falha fechada: sem especie/subprojeto no corpo do POST, a ômica cria o run no demo
    (com aviso que ninguém vê). O resumo confere o contexto do PLANO contra o par escolhido e,
    se divergem (ou o plano não traz contexto), vai a 'Fim: plano recusado' sem a página de
    aprovação. Controle: o mesmo par passa."""
    js = _js(validos, pid, "Resumo do plano")
    aula = {"Conferir contexto": [{"json": {"ok": True, "especie": "bos_taurus", "subprojeto": "aula_granulosa_calor"}}]}
    for contexto_do_plano in (CONTEXTO_DEMO, None):
        r = _rodar(tmp_path, js, [{"json": _plano({}, contexto_do_plano)}], {**_ler_run({}), **aula})
        saida = r["ok"][0]["json"]
        assert saida["rota"] == "recusado" and "campos" not in saida, (pid, contexto_do_plano)
        assert "não para o escolhido no formulário" in saida["detalhe"]
        assert '"bos_taurus/aula_granulosa_calor"' in saida["detalhe"]
    ok = _rodar(tmp_path, js, [{"json": _plano({}, {**CONTEXTO_DEMO, "subprojeto": "aula_granulosa_calor"})}],
                {**_ler_run({}), **aula})
    assert ok["ok"][0]["json"]["rota"] == "aprovar"


@precisa_node
def test_js_resumo_do_ml_le_a_regiao_do_plano(validos, tmp_path):
    """Adendo §A10 item 4 (crítica M3/C11): o aviso de alcance do ML diz a região do PLANO
    (parâmetro do dado, que o subprojeto sobrescreve), nunca o default do YAML."""
    js = _js(validos, "ml_aims_vcf", "Resumo do plano")
    assert "14:1-2500000" not in js and "outros cromossomos" not in js
    vcf = {"formato": "vcf_indexado", "bytes": 1, "sha256": "b" * 64, "modo": "referencia", "irmaos": {".csi": {}}}
    passos = [{"id": "contraste", "titulo": "c", "fila": "ml", "mem_gb": 1, "tempo_estimado": "1 min", "valores": {}},
              {"id": "dataset", "titulo": "d", "fila": "bioinfo", "mem_gb": 2, "tempo_estimado": "1 min",
               "valores": {"regiao": "7:1-<100>"}}]
    html = _html(_rodar(tmp_path, js, [{"json": _plano({"vcf": vcf}, passos=passos)}],
                        _ler_run({"contigs_com_dados": ["14"], "n_contigs_com_dados": 1})))
    linha = html.split("<li><b>vcf</b>")[1].split("</li>")[0]
    assert ("contigs com variantes: <b>14</b> — o dataset usa só a região 7:1-&lt;100&gt; (parâmetro do "
            "subprojeto): um VCF sem esse contig dá 0 marcadores") in linha
    assert "por referência, sem cópia + índice .csi" in linha
    # canário: plano sem o passo do dataset não inventa região
    sem = _html(_rodar(tmp_path, js, [{"json": _plano({"vcf": vcf}, passos=passos[:1])}],
                       _ler_run({"contigs_com_dados": ["14"], "n_contigs_com_dados": 1})))
    assert "o dataset usa só a região (ausente no plano)" in sem


@precisa_node
@pytest.mark.parametrize("pid", EPIGENOMICA)
def test_js_resumo_da_epigenomica_mostra_a_pasta_sem_contigs(validos, tmp_path, pid):
    """Adendo §A10 item 4 (C11): a epigenômica não herda o aviso de contigs da genômica; a
    entrada de pasta diz quantos arquivos entraram. Controle: o painel (upload) não ganha nada,
    e a genômica (sem pasta) nem tem a linha no código."""
    js = _js(validos, pid, "Resumo do plano")
    assert "contigs_com_dados" not in js and "outros cromossomos" not in js and "Ler run (escolhas)" not in js
    ents = {"coverages": {"formato": "coverages_bismark", "bytes": 439723, "sha256": "1" * 64, "modo": "copia",
                          "tipo": "diretorio", "n_membros": 6},
            "painel": {"formato": "painel_genes_json", "bytes": 10, "sha256": "2" * 64, "modo": "copia"}}
    html = _html(_rodar(tmp_path, js, [{"json": _plano(ents)}], CONFERIDO))
    assert "<li><b>coverages</b>: coverages_bismark, 439723 bytes, sha256 111111111111… — pasta com 6 arquivos</li>" in html
    assert "<li><b>painel</b>: painel_genes_json, 10 bytes, sha256 222222222222…</li>" in html
    for g in GENOMICA:
        assert "pasta com" not in _js(validos, g, "Resumo do plano")


@precisa_node
def test_js_campos_do_contexto(validos, tmp_path):
    """Adendo §A10 item 0, nó 'Campos do contexto': 2 ids viram 2 opções, na ordem da ômica, e
    SÓ o id vai para a opção (título e pergunta não). Canários: lista vazia, 404 e corpo sem
    lista lançam (a execução para antes de qualquer POST)."""
    js = _js(validos, "ml_aims_vcf", "Campos do contexto")
    corpo = {"subprojetos": [{"id": "bos_taurus/demo", "titulo": "Título secreto A", "especie": "bos_taurus"},
                             {"id": "bos_taurus/pesquisa_x", "titulo": "Título secreto B", "especie": "bos_taurus"}],
             "erros": {}}
    r = _rodar(tmp_path, js, [{"json": {"statusCode": 200, "body": corpo}}])
    saida = r["ok"][0]["json"]
    assert saida["ids"] == ["bos_taurus/demo", "bos_taurus/pesquisa_x"]
    assert saida["campos"] == [{"fieldLabel": "Subprojeto", "fieldName": "contexto", "fieldType": "dropdown",
                                "requiredField": True, "fieldOptions": {"values": [
                                    {"option": "bos_taurus/demo"}, {"option": "bos_taurus/pesquisa_x"}]}}]
    assert "secreto" not in json.dumps(r)
    for resposta, motivo in [({"statusCode": 200, "body": {"subprojetos": [], "erros": {}}},
                              "nenhum subprojeto aceita este pipeline: ml_aims_vcf"),
                             ({"statusCode": 200, "body": {}}, "nenhum subprojeto aceita este pipeline"),
                             ({"statusCode": 404, "body": {"detail": {"erro": "pipeline_inexistente"}}},
                              "A ômica recusou listar os subprojetos (404)")]:
        r = _rodar(tmp_path, js, [{"json": resposta}])
        assert motivo in r.get("erro", ""), (resposta, r)


@precisa_node
def test_js_campos_do_contexto_com_a_resposta_real_da_api(api, validos, tmp_path):
    """Contrato API ↔ JS: a resposta de verdade do GET /subprojetos?pipeline=&privados=false
    (a URL do nó) vira as opções, o demo primeiro."""
    js = _js(validos, "transcriptomica_de", "Campos do contexto")
    url = _nos(bw.construir(validos["transcriptomica_de"]))["Contextos do pipeline"]["parameters"]["url"]
    resp = api.get(url.replace(bw.API, "/api/v1"))
    assert resp.status_code == 200
    ids = [s["id"] for s in resp.json()["subprojetos"]]
    assert ids and ids[0] == "bos_taurus/demo"
    r = _rodar(tmp_path, js, [{"json": {"statusCode": resp.status_code, "body": resp.json()}}])
    assert r["ok"][0]["json"]["ids"] == ids
    # pipeline inexistente: o 404 de verdade também faz o nó lançar
    r404 = api.get("/api/v1/subprojetos?pipeline=nao_existe&privados=false")
    assert r404.status_code == 404
    r = _rodar(tmp_path, js, [{"json": {"statusCode": 404, "body": r404.json()}}])
    assert "pipeline_inexistente" in r.get("erro", "")


@precisa_node
def test_js_conferir_contexto(validos, tmp_path):
    """Nó 'Conferir contexto': só passa um id que a ômica listou AGORA, partido no primeiro '/'.
    Canários: fora da lista, não-texto e id sem '/' dão ok false (→ 'Fim: contexto recusado',
    sem POST)."""
    js = _js(validos, "epigenomica_dmc", "Conferir contexto")
    lista = {"Campos do contexto": [{"json": {"ids": ["bos_taurus/demo", "semBarra", "a/b/c", "x/"]}}]}

    def conferir(valor):
        return _rodar(tmp_path, js, [{"json": {"contexto": valor}}], lista)["ok"][0]["json"]

    assert conferir("bos_taurus/demo") == {"ok": True, "especie": "bos_taurus", "subprojeto": "demo"}
    assert conferir("a/b/c") == {"ok": True, "especie": "a", "subprojeto": "b/c"}
    for ruim in ("bos_taurus/privado_x", "bos_taurus/demo ", ["bos_taurus/demo"], None, "semBarra", "x/"):
        assert conferir(ruim) == {"ok": False}, ruim


def _corpo_do_criar_run(validos, tmp_path: Path, pid: str, item: dict) -> dict:
    """Avalia no node a expressão do corpo do `Criar run` com o item que sai do IF."""
    corpo = _nos(bw.construir(validos[pid]))["Criar run"]["parameters"]["jsonBody"]
    assert corpo.startswith("={{ ") and corpo.endswith(" }}")
    (tmp_path / "expr.js").write_text(
        "const $json = JSON.parse(process.argv[2]);\nprocess.stdout.write(" + corpo[4:-3] + ");\n", encoding="utf-8")
    r = subprocess.run([NODE, str(tmp_path / "expr.js"), json.dumps(item)], capture_output=True, text=True,
                       encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@precisa_node
@pytest.mark.parametrize("pid", sorted(SHAS_DIST))
def test_corpo_do_criar_run_leva_os_3_campos(validos, tmp_path, pid):
    """O POST /runs leva pipeline, especie e subprojeto (antes só o pipeline: todo run do
    formulário caía no demo). A expressão do n8n é avaliada no node com o item do IF."""
    item = {"ok": True, "especie": "bos_taurus", "subprojeto": "demo"}
    assert _corpo_do_criar_run(validos, tmp_path, pid, item) == {
        "pipeline": pid, "especie": "bos_taurus", "subprojeto": "demo"}


@precisa_node
def test_js_contexto_ponta_a_ponta_com_a_api_real(api, validos, tmp_path):
    """Contrato n8n ↔ API da D-10, com a ômica de verdade (TestClient): o corpo que o `Criar
    run` monta cria o run no subprojeto escolhido, e o `Resumo do plano` lê o `contexto` do
    plano REAL (as chaves de contexto.para_plano) e segue para a aprovação. Canário: o corpo
    antigo, só com o pipeline, cai no demo, e o resumo recusa o plano em vez de pedir a
    aprovação."""
    pid = "transcriptomica_de"
    js = _js(validos, pid, "Resumo do plano")
    aula = {"ok": True, "especie": "bos_taurus", "subprojeto": "aula_granulosa_calor"}

    def plano_com(corpo: dict) -> dict:
        r = api.post("/api/v1/runs", json=corpo)
        assert r.status_code == 201, r.text
        rid = r.json()["run_id"]
        for nome, arq in (("counts", "counts_granulosa.tsv"), ("coldata", "coldata_granulosa.tsv")):
            r = api.put(f"/api/v1/runs/{rid}/entradas/{nome}",
                        json={"caminho": f"modules/transcriptomica/demo_data/{arq}"})
            assert r.status_code == 200, r.text
        r = api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {"referencia": "winter_control"}})
        assert r.status_code == 200, r.text
        api.post(f"/api/v1/runs/{rid}/abortar")  # não deixa run aberto para o próximo
        return {"statusCode": r.status_code, "body": r.json()}

    real = plano_com(_corpo_do_criar_run(validos, tmp_path, pid, aula))
    ctx = real["body"]["contexto"]
    assert (ctx["especie"], ctx["subprojeto"]) == ("bos_taurus", "aula_granulosa_calor")
    r = _rodar(tmp_path, js, [{"json": real}], {"Conferir contexto": [{"json": aula}]})
    assert r["ok"][0]["json"]["rota"] == "aprovar", r
    assert _html(r).startswith(f"<p>Contexto: <b>bos_taurus/aula_granulosa_calor</b> · assembly {ctx['assembly']}"
                               f" · sha {ctx['sha256'][:12]}…</p>")
    velho = plano_com({"pipeline": pid})
    assert velho["body"]["contexto"]["subprojeto"] == "demo"
    r = _rodar(tmp_path, js, [{"json": velho}], {"Conferir contexto": [{"json": aula}]})
    assert r["ok"][0]["json"]["rota"] == "recusado"
    assert "não para o escolhido no formulário" in r["ok"][0]["json"]["detalhe"]


@precisa_node
def test_js_separar_arquivos_so_pega_upload(validos, tmp_path):
    js = _js(validos, "genomica_regioes", "Separar arquivos")
    binario = {"regioes": {"fileName": "regioes.bed"}}
    r = _rodar(tmp_path, js, [{"json": {"statusCode": 201, "body": {"run_id": "x"}}}],
               {"Arquivos do experimento": [{"json": {"vcf": "x"}, "binary": binario}]})
    assert r == {"ok": [{"json": {"nome": "regioes"}, "binary": {"arquivo": {"fileName": "regioes.bed"}}}]}


# ------------------------------------------------------------------ prompt
# o que a mensagem ao redator traz (servico._mensagem, F1b) e o prompt tem de explicar
_NO_PROMPT = ("A linha `Contexto:` diz a espécie e o subprojeto do run; não generalize para outra espécie.",
              "Se a mensagem trouxer ALERTAS ou ALERTAS DO SUBPROJETO, declare cada um em limitações",
              "Hiper = mais metilada no grupo A", "a classe positiva da ROC-AUC é `contraste.contraste_resumo.classe_y1`")


def _faltam_no_prompt(texto: str) -> list[str]:
    plano = " ".join(texto.split())  # o markdown quebra linha no meio da frase
    return [t for t in _NO_PROMPT if t not in plano]


def test_prompt_explica_contexto_alertas_e_direcoes():
    """ESPEC §4 item 6 + adendo §A10 item 6: o prompt (vale para todos os módulos) explica a
    linha Contexto e os dois blocos de alertas que a ômica manda, e a direção de metilação e
    ML. Os cabeçalhos citados são os que servico._mensagem escreve. Canário: sem a linha do
    contexto, falta; controle: o prompt versionado tem tudo."""
    from omica.relatorio import servico
    fonte = Path(servico.__file__).read_text(encoding="utf-8")
    assert 'f"Contexto: ' in fonte and "ALERTAS DO SUBPROJETO (" in fonte and "\\nALERTAS (" in fonte
    texto = bw.AQUI.joinpath("prompts", "relatorio_sistema.md").read_text(encoding="utf-8")
    assert _faltam_no_prompt(texto) == []
    sem = texto.replace("A linha `Contexto:`", "A linha de contexto")
    assert _faltam_no_prompt(sem) == [_NO_PROMPT[0]]


# ------------------------------------------------------------------- API
def test_api_expoe_modos_e_envio_iguais_ao_builder(api, validos):
    """A3 (G1 §1.13): GET /pipelines diz `modos` e `envio` por entrada, e o `envio` é o mesmo
    que o builder usou para o tipo do campo — fonte única."""
    corpo = api.get("/api/v1/pipelines").json()
    assert corpo["erros"] == {}
    por_id = {x["id"]: x for x in corpo["pipelines"]}
    for pid in GENOMICA:
        ents = por_id[pid]["entradas"]
        assert ents["vcf"]["modos"] == ["copia", "referencia"] and ents["vcf"]["envio"] == "caminho"
    assert por_id["genomica_painel"]["entradas"]["painel"]["envio"] == "arquivo"
    assert por_id["genomica_regioes"]["entradas"]["regioes"]["envio"] == "arquivo"
    for pid, p in validos.items():
        campos = {c["fieldName"]: c["fieldType"] for c in
                  _nos(bw.construir(p))["Arquivos do experimento"]["parameters"]["formFields"]["values"]}
        for nome, e in por_id[pid]["entradas"].items():
            assert e["envio"] == envio_formulario(p.entradas[nome])
            assert campos[nome] == ("text" if e["envio"] == "caminho" else "file"), (pid, nome)
