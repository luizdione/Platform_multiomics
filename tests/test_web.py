"""Aceite da Fase 1 em forma de teste: home PT-BR responde, job demo atravessa a
fila (fallback de thread) e o resultado exibido vem de ARQUIVO."""
import time

from fastapi.testclient import TestClient


def _client():
    from omica.web.app import app

    return TestClient(app)


def test_home_em_ptbr():
    # v2: a 1ª página escolhe a ESPÉCIE (as 3, com o estado da referência), não o módulo
    r = _client().get("/")
    assert r.status_code == 200
    assert "plataforma de ômicas" in r.text
    for especie in ("homo_sapiens", "mus_musculus", "bos_taurus"):
        assert f'href="/e/{especie}?lang=pt"' in r.text
    assert "Humano (Homo sapiens)" in r.text and "Bovinos (Bos taurus / Bos indicus)" in r.text
    assert r.text.count("badge estado-") == 3 and "sem referência" in r.text
    assert 'href="/modulos/' not in r.text


def test_home_em_en():
    r = _client().get("/?lang=en")
    assert "omics platform" in r.text


def test_saude_reporta_backend_e_modulos():
    r = _client().get("/saude")
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["fila_backend"] == "thread"  # sem redis nos testes
    assert corpo["modulos_validos"] >= 3
    assert corpo["erros_manifests"] == {}


def test_pagina_de_modulo():
    # a URL v1 só redireciona (307, sem seguir aqui); a página vive no subprojeto demo
    antiga = _client().get("/modulos/genomica", follow_redirects=False)
    assert antiga.status_code == 307 and antiga.headers["location"] == "/e/bos_taurus/demo/modulos/genomica"
    r = _client().get("/e/bos_taurus/demo/modulos/genomica", follow_redirects=False)
    assert r.status_code == 200
    assert "SnpEff" in r.text
    assert 'action="/e/bos_taurus/demo/modulos/genomica/workflows/wf_painel_demo"' in r.text


def test_job_demo_ponta_a_ponta(cfg):
    client = _client()
    r = client.post("/jobs/demo", follow_redirects=False)
    assert r.status_code == 303
    job_id = r.headers["location"].split("/jobs/")[1].split("?")[0]

    from omica.jobs.queue import read_status

    fim = time.time() + 20
    status = None
    while time.time() < fim:
        status = read_status(job_id)
        if status and status["status"] in ("done", "error"):
            break
        time.sleep(0.3)
    assert status is not None and status["status"] == "done", f"status final: {status}"

    # O resultado exibido vem de arquivo (anti-lição nº 4)
    from omica.results import resultado_de_job

    resultado = resultado_de_job(job_id)
    assert resultado is not None
    assert resultado["soma_verificacao"] == 15  # 1+2+3+4+5 passos

    pagina = client.get(f"/jobs/{job_id}")
    assert "soma_verificacao" in pagina.text


def test_pagina_modulo_lista_scripts_e_resultados_vazios(cfg):
    r = _client().get("/e/bos_taurus/demo/modulos/genomica", follow_redirects=False)
    assert r.status_code == 200
    assert "Extração por painel (demo BTA14)" in r.text
    assert "▶ Rodar" in r.text
    assert 'action="/e/bos_taurus/demo/modulos/genomica/scripts/extracao_demo"' in r.text
    assert 'action="/modulos/' not in r.text
    # sem arquivos de resultado ainda → a UI diz "sem dados", nunca inventa
    assert "nunca inventa número" in r.text


def test_script_bioinfo_sem_redis_mostra_aviso(cfg):
    r = _client().post("/modulos/genomica/scripts/extracao_demo", follow_redirects=False)
    assert r.status_code == 303
    assert "aviso=" in r.headers["location"]


def test_ajuda_renderiza_markdown():
    r = _client().get("/ajuda/01_visao_geral.md")
    assert r.status_code == 200
    assert "Visão geral da plataforma" in r.text


def test_ajuda_bloqueia_path_traversal():
    r = _client().get("/ajuda/..%2F..%2FREADME.md", follow_redirects=False)
    assert r.status_code in (303, 404)
