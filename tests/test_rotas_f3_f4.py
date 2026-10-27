"""Rotas da F3 (adaptar uma entrada do run) e da F4 (relatório) pela API v1, e a página
/relatorios/<run>/. O LLM é FALSO (motor_local.chat_json trocado) e o redator também (o JSON
que o Claude devolveria): nada aqui depende de Ollama nem da API da Anthropic.
"""
from __future__ import annotations

from pathlib import Path

from omica import runs
from omica.agentes import motor_local
from test_adaptador import COUNTS, _demo_baguncado, _gravar, _ler
from test_relatorio_servico import BOM, _concluido
from test_runs import DEMO, REFERENCIA, _aprovado, _novo, api, lancados  # noqa: F401 (fixtures)

AMOSTRAS = _ler(COUNTS)[0][2:]
OPS_CERTAS = [{"op": "renomear", "origem": "feature", "destino": "gene_id"},
              {"op": "selecionar", "colunas": ["gene_id", "gene_name", *AMOSTRAS]}]


def _sobe(api, rid: str, nome: str, arq: Path) -> dict:
    r = api.put(f"/api/v1/runs/{rid}/entradas/{nome}", content=arq.read_bytes(),
                headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 200, r.text
    return r.json()


def _com_counts_baguncado(api, tmp_path) -> str:
    rid = _novo(api)
    _sobe(api, rid, "counts", _demo_baguncado(tmp_path))
    r = api.put(f"/api/v1/runs/{rid}/entradas/coldata", json={"caminho": f"{DEMO}/coldata_granulosa.tsv"})
    assert r.status_code == 200, r.text
    return rid


def _llm(monkeypatch, dados=None, efeito=None) -> list:
    chamadas: list = []

    def falso(sistema, usuario, esquema, **_):
        chamadas.append(usuario)
        if efeito:
            efeito()
        return {"dados": {"ops": OPS_CERTAS, "excecoes": []} if dados is None else dados,
                "modelo": "falso:1b"}

    monkeypatch.setattr(motor_local, "chat_json", falso)
    return chamadas


def _plano(api, rid: str):
    return api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {"referencia": REFERENCIA}})


# ------------------------------------------------------------------------- F3
def test_entrada_baguncada_bloqueia_o_plano_ate_ser_adaptada(api, monkeypatch, tmp_path):
    rid = _com_counts_baguncado(api, tmp_path)
    assert _plano(api, rid).json()["detail"]["erro"] == "entradas_reprovadas"
    chamadas = _llm(monkeypatch)

    r = api.post(f"/api/v1/runs/{rid}/entradas/counts/adaptar")
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["ok"] and corpo["modo"] == "llm" and len(chamadas) == 1
    reg = corpo["run"]["entradas"]["counts"]
    adaptado = Path(reg["caminho"])
    assert adaptado.name == "counts.adaptado.tsv" and reg["veredito"]["ok"]
    assert adaptado.read_bytes() == COUNTS.read_bytes().replace(b"\r\n", b"\n")
    assert reg["original"]["sha256"] != reg["sha256"] and Path(reg["original"]["caminho"]).exists()

    plano = _plano(api, rid)
    assert plano.status_code == 200, plano.text
    p = plano.json()
    # a adaptação entra no plano — logo no sha que o autor aprova — e o script lê o adaptado
    assert p["entradas"]["counts"]["adaptacao"]["ops"] == OPS_CERTAS
    assert p["entradas"]["counts"]["adaptacao"]["modelo"] == "falso:1b"
    assert adaptado.as_posix() in p["passos"][0]["cmd"]


def test_sinonimo_inequivoco_nao_consulta_o_modelo(api, monkeypatch, tmp_path):
    linhas = _ler(COUNTS)
    linhas[0][0] = "Gene ID"
    rid = _novo(api)
    _sobe(api, rid, "counts", _gravar(tmp_path / "c.csv", linhas, delim=","))

    def nunca(*a, **k):
        raise AssertionError("o modelo não devia ser chamado")

    monkeypatch.setattr(motor_local, "chat_json", nunca)
    r = api.post(f"/api/v1/runs/{rid}/entradas/counts/adaptar").json()
    assert r["ok"] and r["modo"] == "deterministico"


def test_duvida_do_modelo_volta_ao_autor_com_cabecalhos_reais(api, monkeypatch, tmp_path):
    rid = _com_counts_baguncado(api, tmp_path)
    _llm(monkeypatch, dados={"ops": [], "excecoes": ["não sei qual coluna é o gene"]})
    r = api.post(f"/api/v1/runs/{rid}/entradas/counts/adaptar")
    assert r.status_code == 200  # dúvida não é erro HTTP: é o caso de levar ao autor
    corpo = r.json()
    assert not corpo["ok"] and corpo["opcoes"]["cabecalho"][0] == "feature"
    reg = corpo["run"]["entradas"]["counts"]
    assert "adaptacao" not in reg and not reg["veredito"]["ok"]
    assert corpo["run"]["adaptacoes"]["counts"]["excecoes"] == ["não sei qual coluna é o gene"]

    # o autor escolhe nos dropdowns: mesmas conferências, modo "autor", e o plano destrava
    r = api.post(f"/api/v1/runs/{rid}/entradas/counts/adaptar", json={"ops": OPS_CERTAS}).json()
    assert r["ok"] and r["modo"] == "autor"
    assert _plano(api, rid).status_code == 200


def test_operacao_do_autor_que_nao_ancora_e_recusada(api, tmp_path):
    rid = _com_counts_baguncado(api, tmp_path)
    ops = [{"op": "renomear", "origem": "coluna_que_nao_existe", "destino": "gene_id"}]
    r = api.post(f"/api/v1/runs/{rid}/entradas/counts/adaptar", json={"ops": ops}).json()
    assert not r["ok"] and r["descartes"] and "adaptacao" not in r["run"]["entradas"]["counts"]


def test_gpu_reservada_e_503_e_nada_muda(api, monkeypatch, tmp_path):
    rid = _com_counts_baguncado(api, tmp_path)

    def reservada(*a, **k):
        raise motor_local.MotorIndisponivel("gpu_reservada", "MD em andamento")

    monkeypatch.setattr(motor_local, "chat_json", reservada)
    r = api.post(f"/api/v1/runs/{rid}/entradas/counts/adaptar")
    assert r.status_code == 503 and r.json()["detail"]["codigo"] == "gpu_reservada"
    assert "adaptacoes" not in api.get(f"/api/v1/runs/{rid}").json()


def test_entrada_trocada_durante_a_inferencia_nao_e_sobrescrita(api, monkeypatch, tmp_path):
    """O LLM roda FORA da trava do run: se a entrada mudar no meio, o resultado velho não grava."""
    rid = _com_counts_baguncado(api, tmp_path)
    _llm(monkeypatch, efeito=lambda: runs.registrar_entrada(
        rid, "counts", caminho=f"{DEMO}/counts_granulosa.tsv"))
    r = api.post(f"/api/v1/runs/{rid}/entradas/counts/adaptar")
    assert r.status_code == 409 and r.json()["detail"]["erro"] == "entrada_mudou_durante_adaptacao"
    reg = api.get(f"/api/v1/runs/{rid}").json()["entradas"]["counts"]
    assert reg["origem"].startswith("caminho:") and "adaptacao" not in reg
    sobras = list(Path(reg["caminho"]).parent.glob("*.adaptando.*"))
    assert sobras == []  # o temporário da tentativa perdida não fica para trás


def test_adaptar_depois_de_aprovar_e_409(api, lancados):
    rid, _ = _aprovado(api)
    r = api.post(f"/api/v1/runs/{rid}/entradas/counts/adaptar")
    assert r.status_code == 409 and r.json()["detail"]["erro"] == "entradas_congeladas"


def test_adaptar_entrada_nao_registrada_e_404(api):
    rid = _novo(api)
    r = api.post(f"/api/v1/runs/{rid}/entradas/counts/adaptar")
    assert r.status_code == 404 and r.json()["detail"]["erro"] == "entrada_nao_registrada"


def test_original_alterado_depois_da_aprovacao_bloqueia_execucao(api, monkeypatch, tmp_path, lancados):
    rid = _com_counts_baguncado(api, tmp_path)
    _llm(monkeypatch)
    reg = api.post(f"/api/v1/runs/{rid}/entradas/counts/adaptar").json()["run"]["entradas"]["counts"]
    plano = _plano(api, rid).json()
    assert api.post(f"/api/v1/runs/{rid}/aprovar", json={"plano_sha256": plano["plano_sha256"]}).status_code == 200
    Path(reg["original"]["caminho"]).write_bytes(b"feature,x\n1,2\n")  # a ORIGEM da adaptação
    r = api.post(f"/api/v1/runs/{rid}/passos/de/executar")
    assert r.status_code == 409 and r.json()["detail"]["erro"] == "entrada_alterada"
    assert lancados == []


# ------------------------------------------------------- espera longa (para o n8n)
def test_aguardar_devolve_assim_que_o_passo_termina(api, lancados):
    import json
    import threading
    import time

    from test_relatorio_servico import DE, RESUMO_DE
    from test_runs import _concluir

    rid, plano = _aprovado(api)
    api.post(f"/api/v1/runs/{rid}/passos/de/executar")
    job = lancados[0]["job_id"]
    threading.Timer(0.6, _concluir, args=(job, plano["passos"][0]["saida"],
                                          {"de_table.tsv": DE, "de_resumo.json": json.dumps(RESUMO_DE)})).start()
    t0 = time.monotonic()
    r = api.get(f"/api/v1/runs/{rid}/passos/de/aguardar", params={"segundos": 20})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["portao"]["ok"] and corpo["run_estado"] == "executando"
    assert time.monotonic() - t0 < 10  # voltou quando o job terminou, não no fim do prazo


def test_aguardar_vence_o_prazo_com_o_passo_pendente(api, lancados):
    rid, _ = _aprovado(api)
    api.post(f"/api/v1/runs/{rid}/passos/de/executar")
    corpo = api.get(f"/api/v1/runs/{rid}/passos/de/aguardar", params={"segundos": 0.5}).json()
    assert corpo["estado"] == "queued" and corpo["portao"]["pendente"]
    # passo não lançado não espera nada; prazo acima do teto é 422
    assert api.get(f"/api/v1/runs/{rid}/passos/volcano/aguardar").json()["estado"] == "nao_lancado"
    assert api.get(f"/api/v1/runs/{rid}/passos/de/aguardar", params={"segundos": 999}).status_code == 422


# ------------------------------------------------------------------------- F4
def test_relatorio_pela_api_ate_a_pagina(api, lancados):
    rid = _concluido(api, lancados)
    p = api.post(f"/api/v1/runs/{rid}/relatorio/pedido")
    assert p.status_code == 200 and p.json()["esquema"]["additionalProperties"] is False
    r = api.post(f"/api/v1/runs/{rid}/relatorio/rascunho",
                 json={"rascunho": BOM, "modelo": "claude-opus-5-5", "stop_reason": "end_turn"})
    assert r.status_code == 200 and r.json()["ok"], r.text
    url = r.json()["url"]
    assert url == f"/relatorios/{rid}/"

    pagina = api.get(url)
    assert pagina.status_code == 200 and "GENE1" in pagina.text
    assert "default-src 'none'" in pagina.headers["content-security-policy"]
    sem_barra = api.get(f"/relatorios/{rid}", follow_redirects=False)
    assert sem_barra.status_code == 307 and sem_barra.headers["location"] == url
    fig = api.get(f"{url}figuras/volcano.png")
    assert fig.status_code == 200 and fig.headers["content-type"] == "image/png"


def test_pagina_nao_serve_nada_fora_das_figuras(api, lancados):
    rid = _concluido(api, lancados)
    api.post(f"/api/v1/runs/{rid}/relatorio/pedido")
    api.post(f"/api/v1/runs/{rid}/relatorio/rascunho", json={"rascunho": BOM, "stop_reason": "end_turn"})
    for nome in ("..%2Frelatorio.html", "relatorio.html", "proveniencia.json", ".png"):
        r = api.get(f"/relatorios/{rid}/figuras/{nome}", follow_redirects=False)
        assert r.status_code in (303, 404), nome
    assert api.get("/relatorios/transcriptomica_de-20990101-000000-abcdef/",
                   follow_redirects=False).status_code == 303


def test_relatorio_pela_api_recusa_fora_de_ordem(api, lancados):
    rid, _ = _aprovado(api)
    r = api.post(f"/api/v1/runs/{rid}/relatorio/pedido")
    assert r.status_code == 409 and r.json()["detail"]["erro"] == "run_nao_concluido"
    api.post(f"/api/v1/runs/{rid}/abortar")  # libera a vaga (um run ativo por vez)
    rid = _concluido(api, lancados)
    r = api.post(f"/api/v1/runs/{rid}/relatorio/rascunho", json={"rascunho": BOM})
    assert r.status_code == 409 and r.json()["detail"]["erro"] == "ficha_ausente"
    r = api.post(f"/api/v1/runs/{rid}/relatorio/rascunho", json={"rascunho": BOM, "extra": 1})
    assert r.status_code == 422
