"""API JSON v1 (orquestração externa, ex.: n8n) — fase F1 da integração com o n8n.

Cada portão aqui tem canário (o que ele PRECISA recusar) e controle negativo (o que ele
precisa deixar passar). Um portão que só foi visto aprovando não está testado.
"""
from __future__ import annotations

import asyncio
import json
import os
import time

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omica import config as config_mod
from omica import runs
from omica.api import v1
from omica.config import _ler_dotenv as _LER_DOTENV_REAL  # capturado antes do conftest trocar
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
    assert nomes["counts"]["tipo"] == "caminho"  # declarado no manifest (S2, revisão F1)
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


# ------------------------------------------------- revisão adversarial da F1
@pytest.mark.parametrize("estado", ["queued", "running", "error"])
def test_artefato_de_rodada_anterior_nao_vira_resultado(monkeypatch, cfg, estado):
    """EXP-2: com o job fora de `done`, o arquivo no caminho declarado é de OUTRA
    execução. Antes saía com existe=true e sha256 e um nó do n8n seguia adiante."""
    h = _com_token(monkeypatch)
    saida = cfg.data_dir / "results" / "genomica" / "demo" / "frequencias"
    saida.mkdir(parents=True)
    (saida / "frequencias.tsv").write_text("antigo", encoding="utf-8")
    jid = f"genomica-frequencias_demo-20261019-000000-{estado[:6]}"
    queue.write_status(jid, estado, modulo="genomica", script="frequencias_demo")
    art = _cliente().get(f"/api/v1/jobs/{jid}", headers=h).json()["artefatos"]
    assert art and all(a == {"rel": a["rel"], "pendente": True} for a in art)


def test_job_concluido_marca_arquivo_anterior_ao_inicio(monkeypatch, cfg):
    """O script terminou `done` sem reescrever uma saída declarada: o arquivo velho não
    pode carregar sha256 como se fosse desta execução."""
    h = _com_token(monkeypatch)
    saida = cfg.data_dir / "results" / "genomica" / "demo" / "frequencias"
    saida.mkdir(parents=True)
    velho, novo = saida / "frequencias.tsv", saida / "frequencias_resumo.json"
    velho.write_text("antigo", encoding="utf-8")
    novo.write_text('{"n": 1}', encoding="utf-8")
    inicio = time.time()
    os.utime(velho, (inicio - 3600, inicio - 3600))
    os.utime(novo, (inicio + 5, inicio + 5))
    jid = "genomica-frequencias_demo-20261019-000001-abc123"
    queue.write_status(jid, "done", modulo="genomica", script="frequencias_demo", iniciado_em=inicio)
    art = {a["rel"]: a for a in _cliente().get(f"/api/v1/jobs/{jid}", headers=h).json()["artefatos"]}
    antigo = art["results/genomica/demo/frequencias/frequencias.tsv"]
    assert antigo["anterior_ao_job"] is True and "sha256" not in antigo
    assert len(art["results/genomica/demo/frequencias/frequencias_resumo.json"]["sha256"]) == 64


def test_trocar_o_token_no_env_revoga_o_antigo_sem_reiniciar(monkeypatch, tmp_path):
    """OP-2: o token ficava no lru_cache do processo. Trocar o .env não revogava um token
    vazado até um restart que ninguém lembrava de fazer."""
    monkeypatch.setattr(config_mod, "_ler_dotenv", _LER_DOTENV_REAL)  # usa o parser real...
    monkeypatch.setattr(config_mod, "REPO_ROOT", tmp_path)            # ...num .env de teste
    reset_config_cache()
    env = tmp_path / ".env"
    env.write_text("OMICA_API_TOKEN=vazado\n", encoding="utf-8")
    c = _cliente()
    assert c.get("/api/v1/saude", headers={"Authorization": "Bearer vazado"}).status_code == 200

    env.write_text("OMICA_API_TOKEN=novo\n", encoding="utf-8")
    t = time.time() + 2
    os.utime(env, (t, t))  # garante mtime diferente mesmo em sistema de arquivos grosso
    assert c.get("/api/v1/saude", headers={"Authorization": "Bearer vazado"}).status_code == 401
    assert c.get("/api/v1/saude", headers={"Authorization": "Bearer novo"}).status_code == 200

    env.write_text("OMICA_API_TOKEN=\n", encoding="utf-8")
    os.utime(env, (t + 2, t + 2))
    assert c.get("/api/v1/saude", headers={"Authorization": "Bearer novo"}).status_code == 503


# ------------------------------------------------- revisão adversarial da F2 (frente api)
RUN_FALSO = "transcriptomica_de-20261019-000000-abc123"
URL_ENTRADA = f"/api/v1/runs/{RUN_FALSO}/entradas/counts"
URL_EXECUTAR = f"/api/v1/runs/{RUN_FALSO}/passos/de/executar"
TSV = b"gene\ta\nG1\t1\n"


def _espiao(monkeypatch, nome: str, atraso_s: float = 0.0) -> list[dict]:
    """Troca runs.<nome> por um registrador: estes testes são da CAMADA HTTP — o que chega
    ao núcleo, em que thread, e o que nem chega. O núcleo tem a própria suíte (test_runs)."""
    chamadas: list[dict] = []

    def espiao(*args, **kwargs):
        try:
            asyncio.get_running_loop()
            no_loop = True  # rodando na thread do event loop: congela o servidor inteiro
        except RuntimeError:
            no_loop = False
        if atraso_s:
            time.sleep(atraso_s)
        chamadas.append({"args": args, "kwargs": kwargs, "no_loop": no_loop})
        return {"run_id": RUN_FALSO, "job_id": "falso"}

    monkeypatch.setattr(runs, nome, espiao)
    return chamadas


# #2/#30 — PUT /entradas rodava o registro síncrono DENTRO do event loop
@pytest.mark.parametrize("tipo,corpo", [
    ("application/octet-stream", TSV),
    ("application/json", json.dumps({"caminho": "modules/transcriptomica/demo_data/counts_granulosa.tsv"}).encode()),
])
def test_registrar_entrada_roda_fora_do_event_loop(monkeypatch, tipo, corpo):
    h = _com_token(monkeypatch)
    chamadas = _espiao(monkeypatch, "registrar_entrada")
    # G1 §1.4: o upload passa ANTES pelo portão barato (lê o run.json: E/S, também fora do
    # loop); o ramo por caminho não passa por ele
    portao = _espiao(monkeypatch, "aceita_upload")
    r = _cliente().put(URL_ENTRADA, content=corpo, headers={**h, "Content-Type": tipo})
    assert r.status_code == 200, r.text
    assert len(chamadas) == 1 and chamadas[0]["no_loop"] is False
    if tipo == "application/octet-stream":
        assert len(portao) == 1 and portao[0]["no_loop"] is False
        assert portao[0]["args"] == (RUN_FALSO, "counts")
    else:
        assert portao == []


def test_put_lento_nao_congela_as_outras_rotas(monkeypatch):
    """Canário de latência, no MESMO event loop (como o uvicorn de um processo só): um
    registro que leva 1,5 s não pode segurar um GET disparado 0,2 s depois."""
    h = _com_token(monkeypatch)
    _espiao(monkeypatch, "registrar_entrada", atraso_s=1.5)
    _espiao(monkeypatch, "aceita_upload")  # run falso: o portão do upload daria 404
    from omica.web.app import app

    async def cenario() -> tuple[float, float, int, int]:
        transporte = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transporte, base_url="http://omica", headers=h,
                                     timeout=30) as c:
            t0 = time.perf_counter()
            put = asyncio.create_task(c.put(URL_ENTRADA, content=TSV,
                                            headers={"Content-Type": "application/octet-stream"}))
            await asyncio.sleep(0.2)
            r_get = await c.get("/api/v1/jobs/nao-existe-20261019")
            t_get = time.perf_counter() - t0
            r_put = await put
            return t_get, time.perf_counter() - t0, r_get.status_code, r_put.status_code

    t_get, t_put, st_get, st_put = asyncio.run(cenario())
    assert st_get == 404 and st_put == 200
    assert t_put >= 1.5            # o PUT de fato foi lento
    assert t_get < 1.0, f"GET esperou o PUT: {t_get:.2f}s"


# #34 — o Content-Type escolhe o ramo; o que não é JSON nem octet-stream é 415
@pytest.mark.parametrize("tipo", [
    "multipart/form-data; boundary=xyz", "application/x-www-form-urlencoded", "text/json",
    "application/vnd.api+json", "text/tab-separated-values", "text/plain", "",
])
def test_put_entrada_com_tipo_nao_suportado_e_415(monkeypatch, tipo):
    """Canário: antes, tudo que não era exatamente application/json caía no ramo cru — o
    envelope multipart (com boundary) virava o próprio arquivo e voltava 200."""
    h = _com_token(monkeypatch)
    chamadas = _espiao(monkeypatch, "registrar_entrada")
    cab = {**h, "Content-Type": tipo} if tipo else h
    r = _cliente().put(URL_ENTRADA, content=b'{"caminho": "x"}', headers=cab)
    assert r.status_code == 415, r.text
    assert r.json()["detail"]["erro"] == "tipo_de_conteudo_nao_suportado"
    assert chamadas == []  # nada chegou ao núcleo: a entrada anterior fica intacta


def test_put_entrada_multipart_do_httpx_e_415(monkeypatch):
    h = _com_token(monkeypatch)
    chamadas = _espiao(monkeypatch, "registrar_entrada")
    r = _cliente().put(URL_ENTRADA, files={"arquivo": ("c.tsv", TSV, "text/plain")}, headers=h)
    assert r.status_code == 415 and chamadas == []


@pytest.mark.parametrize("tipo", ["application/json", "application/json; charset=utf-8"])
def test_put_entrada_por_caminho_continua_aceito(monkeypatch, tipo):
    """Controle negativo do 415: JSON pequeno passa, com ou sem charset."""
    h = _com_token(monkeypatch)
    chamadas = _espiao(monkeypatch, "registrar_entrada")
    r = _cliente().put(URL_ENTRADA, content=b'{"caminho": "modules/x/demo_data/a.tsv"}',
                       headers={**h, "Content-Type": tipo})
    assert r.status_code == 200, r.text
    assert chamadas[0]["kwargs"] == {"caminho": "modules/x/demo_data/a.tsv"}


def test_put_entrada_crua_continua_aceita(monkeypatch):
    h = _com_token(monkeypatch)
    chamadas = _espiao(monkeypatch, "registrar_entrada")
    _espiao(monkeypatch, "aceita_upload")  # run falso: o portão do upload daria 404
    r = _cliente().put(URL_ENTRADA, content=TSV, headers={**h, "Content-Type": "application/octet-stream"})
    assert r.status_code == 200
    assert chamadas[0]["kwargs"] == {"conteudo": TSV}


def test_put_entrada_json_tem_teto(monkeypatch):
    """Canário: o ramo JSON lia o corpo inteiro (request.json()) sem limite nenhum."""
    h = _com_token(monkeypatch)
    chamadas = _espiao(monkeypatch, "registrar_entrada")
    corpo = json.dumps({"caminho": "a" * (v1._MAX_CORPO_JSON + 10)}).encode()
    r = _cliente().put(URL_ENTRADA, content=corpo, headers={**h, "Content-Type": "application/json"})
    assert r.status_code == 413 and r.json()["detail"]["erro"] == "corpo_grande_demais"
    assert chamadas == []


def test_put_entrada_json_invalido_no_formato_do_contrato(monkeypatch):
    h = _com_token(monkeypatch)
    _espiao(monkeypatch, "registrar_entrada")
    r = _cliente().put(URL_ENTRADA, content=b'{"caminho": "x", "extra": "/etc/passwd"}',
                       headers={**h, "Content-Type": "application/json"})
    assert r.status_code == 422
    det = r.json()["detail"]
    assert det["erro"] == "corpo_invalido" and det["campos"]
    assert "/etc/passwd" not in r.text  # sem ecoar o corpo recebido


# #33 — erros de validação no formato do contrato, e o token ANTES do corpo
@pytest.mark.parametrize("corpo,campo", [
    ({}, ["body", "pipeline"]),
    ({"pipeline": "transcriptomica_de", "run_id": "inventado"}, ["body", "run_id"]),
])
def test_validacao_de_corpo_vem_com_detail_erro(monkeypatch, corpo, campo):
    """Canário: o 422 padrão do FastAPI vinha com `detail` em LISTA, sem `erro`."""
    h = _com_token(monkeypatch)
    _espiao(monkeypatch, "criar")
    r = _cliente().post("/api/v1/runs", json=corpo, headers=h)
    assert r.status_code == 422
    det = r.json()["detail"]
    assert isinstance(det, dict) and det["erro"] == "corpo_invalido"
    assert campo in [c["loc"] for c in det["campos"]]
    assert all("input" not in c for c in det["campos"])


def test_validacao_de_query_vem_com_detail_erro(monkeypatch):
    h = _com_token(monkeypatch)
    r = _cliente().get("/api/v1/jobs/nao-existe-20261019", params={"linhas_log": -1}, headers=h)
    assert r.status_code == 422
    assert r.json()["detail"]["erro"] == "parametro_invalido"


def test_corpo_valido_continua_passando(monkeypatch):
    """Controle negativo do handler de validação."""
    h = _com_token(monkeypatch)
    chamadas = _espiao(monkeypatch, "criar")
    r = _cliente().post("/api/v1/runs", json={"pipeline": "transcriptomica_de"}, headers=h)
    assert r.status_code == 201 and chamadas[0]["args"] == ("transcriptomica_de",)


@pytest.mark.parametrize("cabecalho", [{}, {"Authorization": "Bearer errado"}])
def test_sem_token_e_401_mesmo_com_corpo_quebrado(monkeypatch, cabecalho):
    """Canário: o FastAPI parseava o corpo antes das dependências — sem token, `{` dava
    422 json_invalid em vez de 401."""
    _com_token(monkeypatch)
    r = _cliente().post("/api/v1/runs", content=b"{",
                        headers={**cabecalho, "Content-Type": "application/json"})
    assert r.status_code == 401 and r.json()["detail"]["erro"] == "nao_autorizado"


def test_api_desligada_e_503_mesmo_com_corpo_quebrado():
    r = _cliente().post("/api/v1/runs", content=b"{",
                        headers={"Content-Type": "application/json", "Authorization": "Bearer qualquer"})
    assert r.status_code == 503 and r.json()["detail"]["erro"] == "api_desativada"


def test_corpo_nao_e_lido_antes_do_token(monkeypatch):
    """Canário no nível ASGI: sem credencial, NENHUM bloco do corpo é consumido."""
    _com_token(monkeypatch)
    from omica.web.app import app

    blocos = [b'{"pipeline": "'] + [b"x" * 1024] * 20 + [b'"}']
    lidos = 0
    enviados: list[dict] = []

    async def receive():
        nonlocal lidos
        if lidos < len(blocos):
            lidos += 1
            return {"type": "http.request", "body": blocos[lidos - 1], "more_body": lidos < len(blocos)}
        return {"type": "http.disconnect"}

    async def send(msg):
        enviados.append(msg)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
             "scheme": "http", "path": "/api/v1/runs", "raw_path": b"/api/v1/runs", "root_path": "",
             "query_string": b"", "headers": [(b"host", b"omica"), (b"content-type", b"application/json")],
             "client": ("127.0.0.1", 50000), "server": ("omica", 8710)}
    asyncio.run(app(scope, receive, send))
    inicio = next(m for m in enviados if m["type"] == "http.response.start")
    assert inicio["status"] == 401
    assert lidos == 0, f"{lidos} blocos lidos antes de recusar a credencial"


def test_formato_de_validacao_so_vale_dentro_da_api_v1():
    """Controle: fora do router v1 o tratamento padrão do FastAPI continua (a UI HTML não
    muda). App mínimo com o router real e uma rota de fora."""
    app = FastAPI()
    app.include_router(v1.router)

    @app.get("/fora")
    def fora(n: int) -> dict:
        return {"n": n}

    r = TestClient(app).get("/fora", params={"n": "x"})
    assert r.status_code == 422 and isinstance(r.json()["detail"], list)


def test_dependency_override_do_token_continua_valendo(monkeypatch):
    """Controle: experimentos e testes que trocam `exigir_token` por dependency_overrides
    (sem tocar no token real) seguem funcionando — a conferência antecipada sai do caminho."""
    chamadas = _espiao(monkeypatch, "criar")
    app = FastAPI()
    app.include_router(v1.router)
    app.dependency_overrides[v1.exigir_token] = lambda: None
    c = TestClient(app)
    assert c.post("/api/v1/runs", json={"pipeline": "p"}).status_code == 201 and len(chamadas) == 1
    assert c.post("/api/v1/runs", json={}).json()["detail"]["erro"] == "corpo_invalido"


# #36 — executar roda SÓ o plano: corpo nenhum é aceito
@pytest.mark.parametrize("kwargs", [
    {"json": {"valores": {"padj": "0.9", "lfc": "0"}, "saida": "/etc", "cmd": "rm -rf /"}},
    {"json": {}},
    {"content": b"padj=0.9", "headers": {"Content-Type": "application/x-www-form-urlencoded"}},
    {"content": b" "},
])
def test_executar_recusa_qualquer_corpo(monkeypatch, kwargs):
    """Canário: o corpo era ignorado em silêncio — nada garantia que um dia não seria lido."""
    h = _com_token(monkeypatch)
    chamadas = _espiao(monkeypatch, "executar_passo")
    kwargs = {**kwargs, "headers": {**h, **kwargs.get("headers", {})}}
    r = _cliente().post(URL_EXECUTAR, **kwargs)
    assert r.status_code == 422 and r.json()["detail"]["erro"] == "corpo_nao_aceito"
    assert chamadas == []


def test_executar_sem_corpo_continua_lancando(monkeypatch):
    """Controle negativo: o n8n chama executar SEM corpo (workflow gerado)."""
    h = _com_token(monkeypatch)
    chamadas = _espiao(monkeypatch, "executar_passo")
    r = _cliente().post(URL_EXECUTAR, headers=h)
    assert r.status_code == 202
    assert len(chamadas) == 1 and chamadas[0]["args"] == (RUN_FALSO, "de")


def test_executar_com_corpo_e_sem_token_e_401(monkeypatch):
    _com_token(monkeypatch)
    chamadas = _espiao(monkeypatch, "executar_passo")
    r = _cliente().post(URL_EXECUTAR, json={"valores": {"padj": "0.9"}})
    assert r.status_code == 401 and chamadas == []


# #36 (resíduo) — executar também não aceita query string
@pytest.mark.parametrize("query", ["?padj=0.9&saida=/etc", "?padj"])
def test_executar_recusa_query_string(monkeypatch, query):
    h = _com_token(monkeypatch)
    chamadas = _espiao(monkeypatch, "executar_passo")
    r = _cliente().post(URL_EXECUTAR + query, headers=h)
    assert r.status_code == 422 and r.json()["detail"]["erro"] == "parametro_nao_aceito", r.text
    assert chamadas == []


def test_executar_com_query_e_sem_token_e_401(monkeypatch):
    """A query é recusada DEPOIS do token: sem credencial, nada sobre parâmetro."""
    _com_token(monkeypatch)
    chamadas = _espiao(monkeypatch, "executar_passo")
    r = _cliente().post(URL_EXECUTAR + "?padj=0.9")
    assert r.status_code == 401 and r.json()["detail"]["erro"] == "nao_autorizado" and chamadas == []


# #33 (resíduo) — 404/405/500 sob /api/v1 no formato do contrato, e o token antes deles
@pytest.mark.parametrize("metodo,url,codigo,erro", [
    ("get", "/api/v1/naoexiste", 404, "rota_inexistente"),
    ("get", "/api/v1", 404, "rota_inexistente"),
    ("get", "/api/v1/runs", 405, "metodo_nao_permitido"),               # só existe POST
    ("delete", f"/api/v1/runs/{RUN_FALSO}", 405, "metodo_nao_permitido"),
])
def test_rota_ou_metodo_inexistente_no_formato_do_contrato(monkeypatch, metodo, url, codigo, erro):
    """Canário: a _RotaV1 só envolve rota que CASOU — 404 e 405 saíam {"detail": "Not Found"},
    e `$json.body.detail.erro` ficava undefined no n8n."""
    h = _com_token(monkeypatch)
    r = getattr(_cliente(), metodo)(url, headers=h)
    assert r.status_code == codigo, r.text
    det = r.json()["detail"]
    assert isinstance(det, dict) and det["erro"] == erro
    if codigo == 405:
        assert det["permitidos"] and r.headers.get("allow")


@pytest.mark.parametrize("metodo,url", [
    ("get", "/api/v1/runs"),                 # 405 com token
    ("put", "/api/v1/saude"),                # 405 com token
    ("get", "/api/v1/naoexiste"),            # 404 com token
])
@pytest.mark.parametrize("cabecalho", [{}, {"Authorization": "Bearer errado"}], ids=["sem", "errado"])
def test_rota_ou_metodo_inexistente_sem_token_e_401(monkeypatch, metodo, url, cabecalho):
    """Canário: o 405 saía ANTES do token — dizia a quem não se autenticou que a rota existe
    com outro método. Sem credencial, toda a /api/v1 responde 401."""
    _com_token(monkeypatch)
    r = getattr(_cliente(), metodo)(url, headers=cabecalho)
    assert r.status_code == 401, r.text
    assert r.json()["detail"]["erro"] == "nao_autorizado"
    assert r.headers.get("www-authenticate") == "Bearer" and "allow" not in r.headers


def test_rota_inexistente_com_api_desligada_e_503():
    r = _cliente().get("/api/v1/runs", headers={"Authorization": "Bearer qualquer"})
    assert r.status_code == 503 and r.json()["detail"]["erro"] == "api_desativada"


def test_rota_inexistente_respeita_override_do_token(monkeypatch):
    """Controle: com `exigir_token` trocado por dependency_overrides (experimentos), o 404 de
    rota sai direto, sem conferir token — como nas rotas que casam."""
    from omica.web.app import app

    monkeypatch.setitem(app.dependency_overrides, v1.exigir_token, lambda: None)
    r = _cliente().get("/api/v1/naoexiste")
    assert r.status_code == 404 and r.json()["detail"]["erro"] == "rota_inexistente"


def test_excecao_nao_tratada_vira_500_do_contrato_sem_a_mensagem(monkeypatch):
    """Canário: a exceção virava 500 em texto puro. E a mensagem não sai na resposta (pode
    trazer caminho ou trecho de arquivo); fica no log do servidor."""
    h = _com_token(monkeypatch)
    from omica.web.app import app

    def explode(*_a, **_k):
        raise RuntimeError(r"C:\segredo\caminho_interno OMICA_API_TOKEN=vazou")

    monkeypatch.setattr(runs, "estado", explode)
    r = TestClient(app, raise_server_exceptions=False).get(f"/api/v1/runs/{RUN_FALSO}", headers=h)
    assert r.status_code == 500 and r.json()["detail"]["erro"] == "erro_interno", r.text
    assert "segredo" not in r.text and "vazou" not in r.text


def test_erros_fora_da_api_v1_seguem_o_padrao(monkeypatch):
    """Controle: os handlers só valem para /api/v1 — a UI HTML e caminhos parecidos com o
    prefixo (/api/v1x) mantêm o tratamento padrão, inclusive o 500 em texto."""
    from omica.web import app as app_mod

    c = TestClient(app_mod.app, raise_server_exceptions=False)
    for url in ("/naoexiste", "/api/v1x", "/api/v1x/runs"):
        r = c.get(url)
        assert r.status_code == 404 and r.json() == {"detail": "Not Found"}, (url, r.text)

    def explode():
        raise RuntimeError("falha na UI")

    monkeypatch.setattr(app_mod, "dados_saude", explode)
    r = c.get("/saude")
    assert r.status_code == 500 and r.text == "Internal Server Error"


def test_erro_de_rota_da_api_passa_intacto_pelo_handler(monkeypatch, cfg):
    """Controle: o `detail` que a rota já monta (erro + campos próprios) não é reescrito."""
    h = _com_token(monkeypatch)
    r = _cliente().get("/api/v1/jobs/nao-existe-20261020", headers=h)
    assert r.status_code == 404
    assert r.json() == {"detail": {"erro": "job_inexistente", "job_id": "nao-existe-20261020"}}


# console SSE (/jobs/{id}/events): read_status fora do event loop
def test_console_sse_le_o_status_fora_do_event_loop(monkeypatch, cfg):
    """Canário: read_status (arquivo + releitura com time.sleep) rodava DENTRO do event loop
    a cada meio segundo de cada console aberto."""
    chamadas: list[bool] = []

    def espiao(job_id):
        try:
            asyncio.get_running_loop()
            chamadas.append(True)
        except RuntimeError:
            chamadas.append(False)
        return {"status": "done"}

    monkeypatch.setattr(queue, "read_status", espiao)
    r = _cliente().get("/jobs/demo-20261020-000000-abc123/events")
    assert r.status_code == 200 and "event: fim" in r.text
    assert chamadas == [False]
