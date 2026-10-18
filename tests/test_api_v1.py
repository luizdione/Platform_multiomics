"""API JSON v1 (orquestração externa, ex.: n8n) — fase F1 da integração com o n8n.

Cada portão aqui tem canário (o que ele PRECISA recusar) e controle negativo (o que ele
precisa deixar passar). Um portão que só foi visto aprovando não está testado.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from omica.config import reset_config_cache
from omica.jobs import queue

TOKEN = "token-de-teste-nao-e-segredo"


def _cliente() -> TestClient:
    from omica.web.app import app

    return TestClient(app)


def _com_token(monkeypatch) -> dict[str, str]:
    monkeypatch.setenv("OMICA_API_TOKEN", TOKEN)
    reset_config_cache()
    return {"Authorization": f"Bearer {TOKEN}"}


# ---------------------------------------------------------------- autenticação
def test_sem_token_configurado_a_api_inteira_fica_fechada():
    """Falha fechada: sem OMICA_API_TOKEN, nem com cabeçalho a API responde."""
    r = _cliente().get("/api/v1/saude", headers={"Authorization": "Bearer qualquer"})
    assert r.status_code == 503
    assert r.json()["detail"]["erro"] == "api_desativada"


@pytest.mark.parametrize("cabecalho", [
    {},                                                   # sem cabeçalho
    {"Authorization": "Bearer errado"},                   # token errado
    {"Authorization": TOKEN},                             # sem o esquema Bearer
    {"Authorization": f"Basic {TOKEN}"},                  # esquema errado
    {"Authorization": "Bearer "},                         # Bearer vazio
    # não-ASCII em BYTES crus (o httpx recusa codificar str não-ASCII, então o teste nem
    # chegaria ao servidor): com compare_digest em str isso levantava TypeError → 500.
    {"Authorization": "Bearer tökên-não-ascii".encode("latin-1")},
])
def test_token_ausente_ou_errado_e_401(monkeypatch, cabecalho):
    _com_token(monkeypatch)
    r = _cliente().get("/api/v1/saude", headers=cabecalho)
    assert r.status_code == 401
    assert r.headers.get("www-authenticate") == "Bearer"


def test_token_certo_passa(monkeypatch):
    h = _com_token(monkeypatch)
    r = _cliente().get("/api/v1/saude", headers=h)
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["modulos_validos"] >= 4 and corpo["erros_manifests"] == {}
    assert "selos_vencidos" in corpo and "gpu" in corpo


def test_rotas_html_continuam_sem_token(monkeypatch):
    """A UI e o plugin Bioinfo dependem das rotas HTML abertas em 127.0.0.1."""
    _com_token(monkeypatch)
    assert _cliente().get("/saude").status_code == 200


# --------------------------------------------------------------- interruptor GPU
def test_gpu_ausente_nao_vira_liberada(monkeypatch):
    """Falha fechada: interruptor ausente = GPU NÃO liberada (o delegate assume 'on')."""
    h = _com_token(monkeypatch)
    gpu = _cliente().get("/api/v1/saude", headers=h).json()["gpu"]
    assert gpu["liberada"] is False and gpu["estado"] == "desconhecido"


@pytest.mark.parametrize("estado,liberada", [("on", True), ("off", False), ("ON", True), ("", False)])
def test_gpu_le_o_interruptor(monkeypatch, tmp_path, estado, liberada):
    h = _com_token(monkeypatch)
    from omica.config import get_config

    sw = get_config().gpu_switch
    sw.parent.mkdir(parents=True, exist_ok=True)
    sw.write_text(json.dumps({"estado": estado, "motivo": "MD", "em": "2026-10-18"}), encoding="utf-8")
    assert _cliente().get("/api/v1/saude", headers=h).json()["gpu"]["liberada"] is liberada


# -------------------------------------------------------------------- catálogo
def test_catalogo_traz_o_que_o_orquestrador_precisa(monkeypatch):
    h = _com_token(monkeypatch)
    cat = _cliente().get("/api/v1/catalogo", headers=h).json()
    mods = {m["id"]: m for m in cat["modulos"]}
    assert set(mods) >= {"genomica", "epigenomica", "transcriptomica", "ml"}
    deseq = next(s for s in mods["transcriptomica"]["scripts"] if s["key"] == "deseq2_demo")
    assert deseq["fila"] == "methyl" and deseq["mem_gb"] > 0
    nomes = {p["name"]: p for p in deseq["params"]}
    assert "counts" in nomes and nomes["counts"]["default"].endswith("counts_granulosa.tsv")
    assert nomes["counts"]["tipo"] == "texto"
    wf = next(w for w in mods["genomica"]["workflows"] if w["id"] == "wf_painel_demo")
    assert wf["selo_vigente"] is True  # os 5 selos estão válidos na v1.0.0


# ------------------------------------------------------------------------ jobs
def test_job_inexistente_e_404(monkeypatch, cfg):
    h = _com_token(monkeypatch)
    r = _cliente().get("/api/v1/jobs/nao-existe-20261018", headers=h)
    assert r.status_code == 404 and r.json()["detail"]["erro"] == "job_inexistente"


@pytest.mark.parametrize("jid", ["..%2F..%2Fetc", "a.b", "x" * 161])
def test_job_id_fora_do_formato_e_recusado(monkeypatch, jid):
    """read_status() monta o caminho direto com o id: validar o formato impede `../`."""
    h = _com_token(monkeypatch)
    r = _cliente().get(f"/api/v1/jobs/{jid}", headers=h)
    assert r.status_code in (404, 422)
    assert r.status_code != 200


def test_job_devolve_status_log_e_artefatos(monkeypatch, cfg):
    h = _com_token(monkeypatch)
    saida = cfg.data_dir / "results" / "genomica" / "demo" / "frequencias"
    saida.mkdir(parents=True)
    (saida / "frequencias_resumo.json").write_text('{"n": 1}', encoding="utf-8")
    queue.write_status("genomica-frequencias_demo-20261018-000000-abc123", "done",
                       modulo="genomica", script="frequencias_demo",
                       result_path="/data/omica/results/genomica/demo/frequencias/frequencias_resumo.json")
    queue.append_log("genomica-frequencias_demo-20261018-000000-abc123", "linha de log")

    r = _cliente().get("/api/v1/jobs/genomica-frequencias_demo-20261018-000000-abc123", headers=h)
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["status"] == "done" and any("linha de log" in l for l in corpo["log_tail"])
    art = {a["rel"]: a for a in corpo["artefatos"]}
    resumo = art["results/genomica/demo/frequencias/frequencias_resumo.json"]
    assert resumo["existe"] is True and resumo["bytes"] == 8 and len(resumo["sha256"]) == 64
    assert art["results/genomica/demo/frequencias/frequencias.tsv"]["existe"] is False


def test_artefato_fora_da_raiz_nao_vaza_metadados(monkeypatch, cfg, tmp_path):
    """Um `RESULTADO:` pode apontar para qualquer arquivo do container: fora da pasta de
    dados não sai nem tamanho nem hash."""
    h = _com_token(monkeypatch)
    segredo = tmp_path / "fora" / "segredo.txt"
    segredo.parent.mkdir()
    segredo.write_text("x", encoding="utf-8")
    queue.write_status("ml-x-20261018-000000-abc123", "done", modulo="ml", script="inexistente",
                       result_path=str(segredo))
    art = _cliente().get("/api/v1/jobs/ml-x-20261018-000000-abc123", headers=h).json()["artefatos"]
    assert art == [{"rel": segredo.as_posix(), "fora_da_raiz": True}]
