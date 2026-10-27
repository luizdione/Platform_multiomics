"""Builder dos workflows do n8n (F5 genômica, especificação G1 §4).

O que se confere aqui:
  - o JSON da transcriptômica sai BYTE A BYTE igual (o workflow importado e publicado não é
    reimportado nem tem o E2E refeito por causa da genômica);
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
import shutil
import subprocess
from pathlib import Path

import pytest

from omica.config import REPO_ROOT
from omica.pipelines import carregar_pipelines, envio_formulario
from test_runs import api  # noqa: F401 (fixture)

_ESPEC = importlib.util.spec_from_file_location("build_workflows", REPO_ROOT / "n8n" / "build_workflows.py")
bw = importlib.util.module_from_spec(_ESPEC)
_ESPEC.loader.exec_module(bw)

# sha256 do texto (LF, como o json.dumps gera) do workflow da transcriptômica importado e
# publicado no n8n em 2026-10-26 (E2E pelo formulário). Mudou? Então ele precisa ser
# reimportado, publicado e ter o E2E refeito — não é efeito colateral aceitável da genômica.
SHA_TRANSCRIPTOMICA = "f9bd2f2034827c3a23ee7721dc66ca3a6e441146030236e8c86cc85be2b41d5e"
GENOMICA = ("genomica_painel", "genomica_regioes")


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
def test_transcriptomica_sai_byte_a_byte_igual(validos):
    texto = _texto(bw.construir(validos["transcriptomica_de"]))
    assert hashlib.sha256(texto.encode("utf-8")).hexdigest() == SHA_TRANSCRIPTOMICA


def test_dist_em_dia_e_todo_pipeline_tem_workflow(validos):
    """O main PULA (só imprime) pipeline sem id em IDS_WORKFLOW: um pipeline novo ficaria sem
    formulário em silêncio. E o dist versionado é o que se importa: tem de ser o gerado."""
    assert set(validos) <= set(bw.IDS_WORKFLOW), set(validos) - set(bw.IDS_WORKFLOW)
    for pid in bw.IDS_WORKFLOW:
        wf = bw.construir(validos[pid])
        assert bw.conferir(wf) == [], pid
        assert (bw.DIST / f"{pid}.json").read_text(encoding="utf-8") == _texto(wf), \
            f"dist/{pid}.json desatualizado: rode python n8n/build_workflows.py"
    assert bw.IDS_WORKFLOW["genomica_painel"] == "omicaGenomPain01"
    assert bw.IDS_WORKFLOW["genomica_regioes"] == "omicaGenomRegi01"


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


def _plano(entradas: dict) -> dict:
    return {"statusCode": 200, "body": {
        "passos": [{"id": "extracao", "titulo": "Extração", "fila": "bioinfo", "mem_gb": 4, "tempo_estimado": "1 min"}],
        "entradas": entradas, "escolhas": {}, "recursos": {"pico_mem_gb": 4}, "plano_sha256": "a" * 64}}


def _ler_run(perfil: dict) -> dict:
    return {"Ler run (escolhas)": [{"json": {"statusCode": 200, "body": {"entradas": {
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
    traga modo/irmaos (o código dos extras nem existe no workflow)."""
    js = _js(validos, "transcriptomica_de", "Resumo do plano")
    html = _html(_rodar(tmp_path, js, [{"json": _plano({"counts": {
        "formato": "contagens_genes", "bytes": 10, "sha256": "e" * 64, "modo": "copia", "irmaos": {".x": {}}}})}]))
    assert "<li><b>counts</b>: contagens_genes, 10 bytes, sha256 eeeeeeeeeeee…</li>" in html


@precisa_node
def test_js_separar_arquivos_so_pega_upload(validos, tmp_path):
    js = _js(validos, "genomica_regioes", "Separar arquivos")
    binario = {"regioes": {"fileName": "regioes.bed"}}
    r = _rodar(tmp_path, js, [{"json": {"statusCode": 201, "body": {"run_id": "x"}}}],
               {"Arquivos do experimento": [{"json": {"vcf": "x"}, "binary": binario}]})
    assert r == {"ok": [{"json": {"nome": "regioes"}, "binary": {"arquivo": {"fileName": "regioes.bed"}}}]}


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
