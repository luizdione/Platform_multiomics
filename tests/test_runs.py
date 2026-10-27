"""Runs de pipeline pela API v1 (F2): entradas → plano → aprovação → passos → envelope.

Os jobs são FINGIDOS (dispatch.lancar_script trocado por um registrador) — o que se testa
aqui é o contrato do orquestrador: o que a API aceita, o que recusa e o que o envelope
atesta. A execução real dos scripts é o E2E sobre o demo, fora da suíte.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from omica import proveniencia, runs
from omica.config import REPO_ROOT, reset_config_cache
from omica.jobs import dispatch, queue, script_job
from omica.jobs.guarda_ram import RecusadoPorRAM
from omica.pipelines import spec_api
from omica.registry import load_registry

TOKEN = "token-de-teste-nao-e-segredo"
DEMO = "modules/transcriptomica/demo_data"
REFERENCIA = "winter_control"


@pytest.fixture()
def api(monkeypatch, cfg):
    monkeypatch.setenv("OMICA_API_TOKEN", TOKEN)
    reset_config_cache()
    from omica.web.app import app

    c = TestClient(app)
    c.headers.update({"Authorization": f"Bearer {TOKEN}"})
    return c


@pytest.fixture()
def lancados(monkeypatch):
    """Troca o lançamento real por um registrador que grava o status como o enqueue faria:
    cmd, runtime, fila e mem_gb tirados do manifest como dispatch.lancar_script tira (o
    envelope confere que o job que rodou é o do plano)."""
    chamadas: list[dict] = []

    def falso(mod_id, script_key, valores=None, extra_meta=None, api=False, saida=None):
        spec = spec_api(load_registry().por_id(mod_id), script_key)
        job_id = queue.novo_job_id(f"{mod_id}-{script_key}")
        queue.write_status(job_id, "queued", modulo=mod_id, script=script_key,
                           cmd=dispatch.montar_cmd(spec, valores, saida=saida), runtime=spec.runtime,
                           fila=dispatch.fila_para_runtime(spec.runtime), mem_gb=spec.mem_gb,
                           **(extra_meta or {}))
        chamadas.append({"mod": mod_id, "script": script_key, "valores": dict(valores or {}),
                         "api": api, "saida": saida, "job_id": job_id, **(extra_meta or {})})
        return job_id, "thread"

    monkeypatch.setattr(runs.dispatch, "lancar_script", falso)
    return chamadas


def _novo(api) -> str:
    r = api.post("/api/v1/runs", json={"pipeline": "transcriptomica_de"})
    assert r.status_code == 201, r.text
    return r.json()["run_id"]


def _com_entradas(api) -> str:
    rid = _novo(api)
    for nome, arq in (("counts", "counts_granulosa.tsv"), ("coldata", "coldata_granulosa.tsv")):
        r = api.put(f"/api/v1/runs/{rid}/entradas/{nome}", json={"caminho": f"{DEMO}/{arq}"})
        assert r.status_code == 200, r.text
    return rid


def _aprovado(api) -> tuple[str, dict]:
    rid = _com_entradas(api)
    plano = api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {"referencia": REFERENCIA}}).json()
    r = api.post(f"/api/v1/runs/{rid}/aprovar", json={"plano_sha256": plano["plano_sha256"]})
    assert r.status_code == 200 and r.json()["estado"] == "aprovado", r.text
    return rid, plano


def _concluir(job_id: str, saida: str, arquivos: dict[str, str | bytes], inicio: float | None = None,
              *, com_proveniencia: bool = True, com_sha_saidas: bool = True, com_entradas: bool = True,
              **extra_done) -> None:
    """Simula o worker: ao começar a rodar grava `iniciado_em` e a `proveniencia_execucao`
    (a MESMA receita do worker — omica.proveniencia sobre o cmd do status.json, no instante
    de rodar), grava as saídas DEPOIS do início e marca done com o `sha_saidas` das portas
    que o executar passou no status (a MESMA função do worker, script_job.atestar_saidas).
    Com `entradas` no status (passo que lê entrada do run), atesta-as como o worker: na
    largada (`entradas_execucao`) e no done (`sha_entradas`), com script_job.atestar_entradas.
    `com_proveniencia=False` / `com_sha_saidas=False` / `com_entradas=False` são o worker
    antigo, que não registrava o código que rodou / o que deixou / o que leu."""
    inicio = time.time() - 5 if inicio is None else inicio
    status = queue.read_status(job_id) or {}
    rodando: dict = {"iniciado_em": inicio}
    if com_proveniencia and status.get("cmd"):
        rodando["proveniencia_execucao"] = proveniencia.sha_codigo(
            status["modulo"], proveniencia.alvo_do_cmd(status["cmd"]))
    entradas = status.get("entradas") if com_entradas else None
    if isinstance(entradas, dict):
        rodando["entradas_execucao"] = script_job.atestar_entradas(entradas, completo=False)
    queue.write_status(job_id, "running", **rodando)
    pasta = Path(saida)
    pasta.mkdir(parents=True, exist_ok=True)
    for nome, conteudo in arquivos.items():
        if isinstance(conteudo, bytes):
            (pasta / nome).write_bytes(conteudo)
        else:
            (pasta / nome).write_text(conteudo, encoding="utf-8")
    if com_sha_saidas and isinstance(status.get("portas"), dict):
        extra_done.setdefault("sha_saidas", script_job.atestar_saidas(status["portas"]))
    if isinstance(entradas, dict):  # no fim, como o worker: o sha do que o job leu
        extra_done.setdefault("sha_entradas", script_job.atestar_entradas(entradas, completo=True))
    queue.write_status(job_id, "done", **extra_done)


def _png_bytes(lado: int = 64) -> bytes:
    import struct
    import zlib

    def bloco(tipo: bytes, dados: bytes) -> bytes:
        return struct.pack(">I", len(dados)) + tipo + dados + struct.pack(">I", zlib.crc32(tipo + dados))

    cru = b"".join(b"\x00" + bytes((x * 7 + y * 13) % 256 for x in range(lado * 3)) for y in range(lado))
    return (b"\x89PNG\r\n\x1a\n" + bloco(b"IHDR", struct.pack(">IIBBBBB", lado, lado, 8, 2, 0, 0, 0))
            + bloco(b"IDAT", zlib.compress(cru, 0)) + bloco(b"IEND", b""))


# com gene_name: a tabela_de passou a exigi-lo (o volcano e o relatório leem essa coluna)
DE_OK = ("gene_id\tgene_name\tbaseMean\tlog2FoldChange\tpvalue\tpadj\n"
         "G1\tGENE1\t10\t2.5\t0.001\t0.01\nG2\tGENE2\t5\t-1.2\t0.2\tNA\n")


# --------------------------------------------------------------------- criação
def test_pipelines_lista_o_contrato_sem_erros(api):
    corpo = api.get("/api/v1/pipelines").json()
    assert corpo["erros"] == {}
    p = next(x for x in corpo["pipelines"] if x["id"] == "transcriptomica_de")
    assert p["passos"] == ["de", "volcano"] and set(p["entradas"]) == {"counts", "coldata"}


def test_run_sem_token_e_401(api):
    r = api.post("/api/v1/runs", json={"pipeline": "transcriptomica_de"}, headers={"Authorization": "Bearer x"})
    assert r.status_code == 401


@pytest.mark.parametrize("corpo,codigo", [
    ({"pipeline": "nao_existe"}, 404),
    ({"pipeline": "transcriptomica_de", "run_id": "meu-id"}, 422),  # run_id nunca vem do cliente
    ({}, 422),
])
def test_criar_run_recusa(api, corpo, codigo):
    assert api.post("/api/v1/runs", json=corpo).status_code == codigo


@pytest.mark.parametrize("rid", [
    "%2e%2e",  # chega ao handler como '..': work/runs/.. seria a pasta de controle
    "transcriptomica_de-x", "a" * 200,
    "TRANSCRIPTOMICA_DE-20990101-000000-abcdef",  # a regex é ASCII minúsculo
])
def test_run_id_fora_do_formato_nao_vira_caminho(api, rid):
    """Exatamente 422 run_id_invalido: aceitar 404 também passava com a regex desligada (o id
    virava pasta e respondia run_inexistente). A barra codificada nem casa a rota — é o
    test_barra_codificada_no_run_id_nem_casa_a_rota, em test_runs_canarios."""
    r = api.get(f"/api/v1/runs/{rid}")
    assert r.status_code == 422 and r.json()["detail"]["erro"] == "run_id_invalido", r.text


def test_run_id_bem_formado_passa_pela_regex(api):
    """Controle do canário acima: o MESMO formato, válido, chega ao run_inexistente."""
    r = api.get("/api/v1/runs/transcriptomica_de-20990101-000000-abcdef")
    assert r.status_code == 404 and r.json()["detail"]["erro"] == "run_inexistente", r.text


# ---------------------------------------------------------------------- entradas
def test_entradas_por_caminho_e_upload_viram_copia_imutavel(api, cfg):
    rid = _novo(api)
    r = api.put(f"/api/v1/runs/{rid}/entradas/counts", json={"caminho": f"{DEMO}/counts_granulosa.tsv"})
    ent = r.json()["entradas"]["counts"]
    assert ent["veredito"]["ok"] and ent["caminho"].startswith(cfg.raw_dir.as_posix() + "/runs/" + rid)
    cru = (REPO_ROOT / DEMO / "coldata_granulosa.tsv").read_bytes()
    r = api.put(f"/api/v1/runs/{rid}/entradas/coldata", content=cru,
                headers={"Content-Type": "application/octet-stream"})
    corpo = r.json()
    assert r.status_code == 200 and corpo["entradas"]["coldata"]["origem"] == "upload"
    assert corpo["checagens"][0]["ok"] is True
    assert REFERENCIA in corpo["escolhas_opcoes"]["referencia"]["valores"]


@pytest.mark.parametrize("caminho", [
    "/etc/passwd",
    f"{DEMO}/../../../.env",
    ".env",
    "https://exemplo.org/counts.tsv",
    f"{DEMO}/nao_existe.tsv",  # inexistente DENTRO da raiz: o mesmo 422 (sem oráculo)
])
def test_entrada_por_caminho_canarios(api, caminho):
    rid = _novo(api)
    r = api.put(f"/api/v1/runs/{rid}/entradas/counts", json={"caminho": caminho})
    assert r.status_code == 422 and r.json()["detail"]["erro"] == "caminho_recusado"
    assert caminho not in r.text  # o caminho recusado não volta ecoado


SEGREDO = "sk-NAO-E-REAL-canario-123"


@pytest.mark.parametrize("onde", ["work", "raw_outro_run", "results", "irmao_da_pasta_de_dados"])
def test_entrada_por_caminho_nao_le_arquivo_fora_da_caixa_de_entrada(api, cfg, onde):
    """Achados #1/#31: a raiz da entrada era a do dispatch (/data inteiro = E:\\data com todos
    os projetos, e toda a pasta de dados), e o veredito ecoava a 1ª linha do arquivo. Agora só
    a caixa de entrada; o resto é o mesmo 422, sem conteúdo nenhum na resposta."""
    arq = {"work": cfg.work_dir / "qualquer" / "config.json",
           "raw_outro_run": cfg.raw_dir / "runs" / "transcriptomica_de-20990101-000000-abcdef" / "counts.tsv",
           "results": cfg.results_dir / "transcriptomica" / "demo" / "de_table.tsv",
           # o GENOME_DATA dos testes: no compose, /data = E:\data, irmão de /data/omica
           "irmao_da_pasta_de_dados": cfg.data_dir.parent / "outro_projeto" / "credenciais.tsv"}[onde]
    arq.parent.mkdir(parents=True, exist_ok=True)
    arq.write_text(f"gene_id\t{SEGREDO}\nG1\t1\n", encoding="utf-8")
    rid = _novo(api)
    r = api.put(f"/api/v1/runs/{rid}/entradas/counts", json={"caminho": arq.as_posix()})
    assert r.status_code == 422 and r.json()["detail"]["erro"] == "caminho_recusado", r.text
    assert SEGREDO not in r.text
    assert "counts" not in api.get(f"/api/v1/runs/{rid}").json()["entradas"]


def test_entrada_inexistente_e_fora_da_raiz_respondem_igual(api, cfg):
    """Sem oráculo de existência: um arquivo que EXISTE fora da raiz e um inexistente dentro
    dela são indistinguíveis (se os dois não existissem, o teste não separava nada)."""
    (cfg.data_dir / "entradas").mkdir(parents=True, exist_ok=True)
    existe_fora = cfg.work_dir / "x.tsv"
    existe_fora.parent.mkdir(parents=True, exist_ok=True)
    existe_fora.write_text("gene_id\ta\nG1\t1\n", encoding="utf-8")
    rid = _novo(api)
    fora = api.put(f"/api/v1/runs/{rid}/entradas/counts", json={"caminho": existe_fora.as_posix()})
    falta = api.put(f"/api/v1/runs/{rid}/entradas/counts",
                    json={"caminho": (cfg.data_dir / "entradas" / "nao_existe.tsv").as_posix()})
    assert fora.status_code == falta.status_code == 422 and fora.json() == falta.json()


def test_symlink_na_caixa_de_entrada_nao_escapa(api, cfg):
    alvo = cfg.work_dir / "segredo.tsv"
    alvo.parent.mkdir(parents=True, exist_ok=True)
    alvo.write_text(f"gene_id\t{SEGREDO}\n", encoding="utf-8")
    link = cfg.data_dir / "entradas" / "atalho.tsv"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(alvo)
    except OSError:
        pytest.skip("sem permissão para criar symlink neste sistema")
    rid = _novo(api)
    r = api.put(f"/api/v1/runs/{rid}/entradas/counts", json={"caminho": link.as_posix()})
    assert r.status_code == 422 and r.json()["detail"]["erro"] == "caminho_recusado"
    assert SEGREDO not in r.text


def test_caixa_de_entrada_aceita_o_que_o_operador_poe_la(api, cfg):
    """Controle dos canários acima: <dados>/entradas é o lugar deliberado das entradas."""
    caixa = cfg.data_dir / "entradas" / "lote1"
    caixa.mkdir(parents=True, exist_ok=True)
    (caixa / "coldata.tsv").write_bytes((REPO_ROOT / DEMO / "coldata_granulosa.tsv").read_bytes())
    rid = _novo(api)
    r = api.put(f"/api/v1/runs/{rid}/entradas/coldata", json={"caminho": (caixa / "coldata.tsv").as_posix()})
    assert r.status_code == 200, r.text
    assert r.json()["entradas"]["coldata"]["veredito"]["ok"]


def test_entrada_desconhecida_e_404_e_corpo_vazio_e_422(api):
    rid = _novo(api)
    assert api.put(f"/api/v1/runs/{rid}/entradas/segredo", json={"caminho": f"{DEMO}/counts_granulosa.tsv"}).status_code == 404
    assert api.put(f"/api/v1/runs/{rid}/entradas/counts", content=b"",
                   headers={"Content-Type": "application/octet-stream"}).status_code == 422


def test_entrada_fora_do_formato_fica_registrada_mas_bloqueia_o_plano(api):
    rid = _novo(api)
    r = api.put(f"/api/v1/runs/{rid}/entradas/counts", content=b"gene\ta\nG1\t1\n",
                headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 200 and not r.json()["entradas"]["counts"]["veredito"]["ok"], r.text
    api.put(f"/api/v1/runs/{rid}/entradas/coldata", json={"caminho": f"{DEMO}/coldata_granulosa.tsv"})
    r = api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {"referencia": REFERENCIA}})
    assert r.status_code == 409 and r.json()["detail"]["erro"] == "entradas_reprovadas"


# ------------------------------------------------------------------------- plano
def test_plano_congela_tudo_e_nunca_aponta_para_o_demo(api, cfg):
    rid = _com_entradas(api)
    r = api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {"referencia": REFERENCIA}})
    assert r.status_code == 200, r.text
    plano = r.json()
    assert len(plano["plano_sha256"]) == 64
    de, volcano = plano["passos"]
    base = f"{cfg.data_dir.as_posix()}/results/transcriptomica/runs/{rid}"
    assert de["saida"] == f"{base}/de" and volcano["saida"] == f"{base}/volcano"
    # a ponte tipada: o volcano lê exatamente a porta de_table do DE
    assert volcano["valores"]["de"] == f"{base}/de/de_table.tsv"
    # "=de.padj": cortes coerentes entre passos
    assert volcano["valores"]["padj"] == de["valores"]["padj"] == "0.05"
    assert de["valores"]["referencia"] == REFERENCIA
    assert not any("/demo/" in a for p in plano["passos"] for a in p["argv"])
    assert de["proveniencia"]["sha_script"] and de["fila"] == "methyl"
    assert plano["recursos"]["pico_mem_gb"] == 3


@pytest.mark.parametrize("escolhas,erro", [
    ({"referencia": "condicao_inventada"}, "escolha_fora_do_dado"),
    ({}, "escolha_fora_do_dado"),
    ({"referencia": REFERENCIA, "padj": "0.5"}, "escolha_desconhecida"),  # param de script não é escolha
])
def test_plano_so_aceita_escolha_com_valor_real(api, escolhas, erro):
    rid = _com_entradas(api)
    r = api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": escolhas})
    assert r.status_code == 422 and r.json()["detail"]["erro"] == erro


def test_plano_sem_entradas_e_409(api):
    rid = _novo(api)
    r = api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {"referencia": REFERENCIA}})
    assert r.status_code == 409 and r.json()["detail"]["erro"] == "entradas_faltando"


# --------------------------------------------------------------------- aprovação
def test_aprovar_exige_o_sha_do_plano_proposto(api):
    rid = _com_entradas(api)
    api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {"referencia": REFERENCIA}})
    r = api.post(f"/api/v1/runs/{rid}/aprovar", json={"plano_sha256": "0" * 64})
    assert r.status_code == 409 and r.json()["detail"]["erro"] == "plano_divergente"
    assert api.post(f"/api/v1/runs/{rid}/aprovar", json={"plano_sha256": "curto"}).status_code == 422


def test_trocar_entrada_depois_do_plano_invalida_a_aprovacao(api):
    rid = _com_entradas(api)
    plano = api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {"referencia": REFERENCIA}}).json()
    api.put(f"/api/v1/runs/{rid}/entradas/coldata", json={"caminho": f"{DEMO}/coldata_granulosa.tsv"})
    r = api.post(f"/api/v1/runs/{rid}/aprovar", json={"plano_sha256": plano["plano_sha256"]})
    assert r.status_code == 409 and r.json()["detail"]["erro"] == "nada_a_aprovar"


def test_plano_adulterado_em_disco_nao_e_aprovado(api, cfg):
    rid = _com_entradas(api)
    plano = api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {"referencia": REFERENCIA}}).json()
    arq = cfg.work_dir / "runs" / rid / "plano_proposto.json"
    dados = json.loads(arq.read_text(encoding="utf-8"))
    dados["passos"][0]["valores"]["padj"] = "0.9"
    arq.write_text(json.dumps(dados), encoding="utf-8")
    r = api.post(f"/api/v1/runs/{rid}/aprovar", json={"plano_sha256": plano["plano_sha256"]})
    assert r.status_code == 409 and r.json()["detail"]["erro"] == "plano_adulterado"


def test_um_run_ativo_por_vez(api):
    _aprovado(api)
    rid2 = _com_entradas(api)
    plano = api.post(f"/api/v1/runs/{rid2}/plano", json={"escolhas": {"referencia": REFERENCIA}}).json()
    r = api.post(f"/api/v1/runs/{rid2}/aprovar", json={"plano_sha256": plano["plano_sha256"]})
    assert r.status_code == 409 and r.json()["detail"]["erro"] == "limite_de_runs_ativos"


def test_dois_runs_aprovados_ao_mesmo_tempo_nao_furam_o_limite(api, monkeypatch):
    """Canário da corrida: com só a trava de cada run, os dois contavam zero ativos e passavam.
    O `_runs_ativos` lento alarga a janela; a trava global tem de serializar a contagem."""
    import threading

    planos = []
    for _ in range(2):
        rid = _com_entradas(api)
        p = api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {"referencia": REFERENCIA}}).json()
        planos.append((rid, p["plano_sha256"]))
    original = runs._runs_ativos

    def lento(exceto=""):
        r = original(exceto)
        time.sleep(0.3)
        return r

    monkeypatch.setattr(runs, "_runs_ativos", lento)
    largada = threading.Barrier(2)
    resultados: list[str] = []

    def aprova(rid, sha):
        largada.wait()
        try:
            runs.aprovar(rid, sha)
            resultados.append("aprovado")
        except runs.ErroRun as e:
            resultados.append(e.erro)

    fios = [threading.Thread(target=aprova, args=x) for x in planos]
    for f in fios:
        f.start()
    for f in fios:
        f.join(15)
    assert sorted(resultados) == ["aprovado", "limite_de_runs_ativos"]


def test_run_inexistente_nao_deixa_pasta_orfa(api, cfg):
    falso = "transcriptomica_de-20990101-000000-abcdef"
    r = api.put(f"/api/v1/runs/{falso}/entradas/counts", json={"caminho": f"{DEMO}/counts_granulosa.tsv"})
    assert r.status_code == 404 and r.json()["detail"]["erro"] == "run_inexistente"
    assert not (cfg.work_dir / "runs" / falso).exists()


def test_trava_ocupada_vira_409_e_erro_do_corpo_passa_intacto(api, cfg):
    from omica.travas import trava

    rid = _novo(api)
    with trava(cfg.work_dir / "runs" / rid / ".trava"):  # outro dono segurando
        with pytest.raises(runs.ErroRun) as e:
            with runs._trava(rid, espera_s=0.2):
                pass
    assert e.value.erro == "run_ocupado"
    with pytest.raises(KeyError):  # exceção de dentro do bloco não é confundida com trava
        with runs._trava(rid):
            raise KeyError("corpo")


def test_entradas_congelam_depois_da_aprovacao(api):
    rid, _ = _aprovado(api)
    r = api.put(f"/api/v1/runs/{rid}/entradas/counts", json={"caminho": f"{DEMO}/counts_granulosa.tsv"})
    assert r.status_code == 409 and r.json()["detail"]["erro"] == "entradas_congeladas"


# ---------------------------------------------------------------------- execução
def test_executar_usa_so_o_plano_congelado(api, lancados):
    rid, plano = _aprovado(api)
    r = api.post(f"/api/v1/runs/{rid}/passos/de/executar")
    assert r.status_code == 202, r.text
    (ch,) = lancados
    de = plano["passos"][0]
    assert ch["api"] is True and ch["saida"] == de["saida"] and ch["valores"] == de["valores"]
    assert ch["run_id"] == rid and ch["passo"] == "de"
    # as portas do plano vão ao status do job: é o que o worker atesta ao terminar (#10)
    assert ch["portas"] == {n: p["caminho"] for n, p in de["portas"].items()}
    # o endpoint não recebe parâmetro: corpo não vazio é recusado com 422 corpo_nao_aceito
    # (test_executar_recusa_qualquer_corpo, em test_runs_canarios)
    assert api.get(f"/api/v1/runs/{rid}").json()["estado"] == "executando"


def test_executar_antes_de_aprovar_e_409(api, lancados):
    rid = _com_entradas(api)
    r = api.post(f"/api/v1/runs/{rid}/passos/de/executar")
    assert r.status_code == 409 and not lancados


def test_passo_seguinte_espera_o_portao_do_anterior(api, lancados):
    rid, _ = _aprovado(api)
    r = api.post(f"/api/v1/runs/{rid}/passos/volcano/executar")
    assert r.status_code == 409 and r.json()["detail"]["erro"] == "passo_anterior_pendente"
    assert not lancados


def test_nao_relanca_passo_em_andamento(api, lancados):
    rid, _ = _aprovado(api)
    api.post(f"/api/v1/runs/{rid}/passos/de/executar")
    r = api.post(f"/api/v1/runs/{rid}/passos/de/executar")
    assert r.status_code == 409 and r.json()["detail"]["erro"] == "passo_ja_lancado"
    assert len(lancados) == 1


def test_passo_com_erro_pode_ser_relancado(api, lancados):
    rid, _ = _aprovado(api)
    api.post(f"/api/v1/runs/{rid}/passos/de/executar")
    queue.write_status(lancados[0]["job_id"], "error", error="R morreu")
    assert api.get(f"/api/v1/runs/{rid}").json()["estado"] == "erro"
    assert api.post(f"/api/v1/runs/{rid}/passos/de/executar").status_code == 202
    assert len(lancados) == 2


def test_plano_adulterado_depois_da_aprovacao_bloqueia_execucao(api, cfg, lancados):
    rid, _ = _aprovado(api)
    arq = cfg.work_dir / "runs" / rid / "plano.json"
    dados = json.loads(arq.read_text(encoding="utf-8"))
    dados["passos"][0]["valores"]["lfc"] = "0"
    arq.write_text(json.dumps(dados), encoding="utf-8")
    r = api.post(f"/api/v1/runs/{rid}/passos/de/executar")
    assert r.status_code == 409 and r.json()["detail"]["erro"] == "plano_adulterado" and not lancados


def test_entrada_trocada_em_disco_bloqueia_execucao(api, cfg, lancados):
    rid, plano = _aprovado(api)
    Path(plano["entradas"]["coldata"]["caminho"]).write_text("amostra\tcondicao\n", encoding="utf-8")
    r = api.post(f"/api/v1/runs/{rid}/passos/de/executar")
    assert r.status_code == 409 and r.json()["detail"]["erro"] == "entrada_alterada" and not lancados


@pytest.mark.parametrize("editar", [False, True], ids=["controle_copia_intacta", "script_editado"])
def test_script_alterado_depois_da_aprovacao_bloqueia(api, monkeypatch, lancados, tmp_path, editar):
    """O ARQUIVO muda (numa cópia do repositório — o script selado nunca é tocado), e o sha sai
    da receita real (omica.proveniencia). Monkeypatchar runs._sha_script testava o próprio
    monkeypatch: um executar que não recalculasse o sha passava."""
    import shutil

    rid, _ = _aprovado(api)
    copia = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / "modules" / "transcriptomica" / "scripts",
                    copia / "modules" / "transcriptomica" / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    if editar:
        with (copia / "modules" / "transcriptomica" / "scripts" / "expressao_deseq2.R").open("ab") as f:
            f.write(b"\n# editado depois da aprovacao\n")
    monkeypatch.setattr(proveniencia, "REPO_ROOT", copia)
    r = api.post(f"/api/v1/runs/{rid}/passos/de/executar")
    if not editar:
        assert r.status_code == 202, r.text
        return
    assert r.status_code == 409 and r.json()["detail"]["erro"] == "script_mudou" and not lancados


def test_ram_insuficiente_vira_429_com_retry_after(api, monkeypatch):
    rid, _ = _aprovado(api)

    def recusa(*a, **k):
        raise RecusadoPorRAM("uso projetado 91% > 80%")

    monkeypatch.setattr(runs.dispatch, "lancar_script", recusa)
    r = api.post(f"/api/v1/runs/{rid}/passos/de/executar")
    assert r.status_code == 429 and r.headers["retry-after"] == "120"


def test_abortar_impede_novos_passos(api, lancados):
    rid, _ = _aprovado(api)
    assert api.post(f"/api/v1/runs/{rid}/abortar").json()["estado"] == "abortado"
    assert api.post(f"/api/v1/runs/{rid}/passos/de/executar").status_code == 409
    assert not lancados


# ---------------------------------------------------------------------- envelope
def test_envelope_atesta_portas_e_aponta_o_proximo_passo(api, lancados):
    rid, plano = _aprovado(api)
    api.post(f"/api/v1/runs/{rid}/passos/de/executar")
    de = plano["passos"][0]
    pendente = api.get(f"/api/v1/runs/{rid}/passos/de/envelope").json()
    assert pendente["portao"] == {"ok": False, "pendente": True, "falhas": ["job em queued"]}

    _concluir(lancados[0]["job_id"], de["saida"], {"de_table.tsv": DE_OK, "de_resumo.json": '{"n_deg": 1}'})
    env = api.get(f"/api/v1/runs/{rid}/passos/de/envelope").json()
    assert env["formato"] == "omica.envelope/1" and env["portao"]["ok"], env["portao"]
    assert env["portas"]["de_table"]["validado"] and len(env["portas"]["de_table"]["sha256"]) == 64
    assert env["resumos"]["de_resumo"] == {"n_deg": 1}
    # o volcano lê a de_table (e o de_resumo, para rotular o contraste): o envelope aponta as duas
    assert {"passo": "volcano", "param": "de", "caminho": f"{de['saida']}/de_table.tsv"} in env["proximo"]
    assert all(x["caminho"].startswith(f"{de['saida']}/") for x in env["proximo"])
    # o portão aberto libera o passo seguinte
    assert api.post(f"/api/v1/runs/{rid}/passos/volcano/executar").status_code == 202


def test_envelope_reprova_saida_fora_do_formato(api, lancados):
    rid, plano = _aprovado(api)
    api.post(f"/api/v1/runs/{rid}/passos/de/executar")
    ruim = DE_OK.replace("0.01", "1.7")
    _concluir(lancados[0]["job_id"], plano["passos"][0]["saida"],
              {"de_table.tsv": ruim, "de_resumo.json": '{"n_deg": 1}'})
    env = api.get(f"/api/v1/runs/{rid}/passos/de/envelope").json()
    assert not env["portao"]["ok"] and any("fora do formato tabela_de" in f for f in env["portao"]["falhas"])
    assert api.post(f"/api/v1/runs/{rid}/passos/volcano/executar").status_code == 409


def test_envelope_reprova_arquivo_de_rodada_anterior(api, lancados):
    rid, plano = _aprovado(api)
    api.post(f"/api/v1/runs/{rid}/passos/de/executar")
    saida = plano["passos"][0]["saida"]
    inicio = time.time()
    _concluir(lancados[0]["job_id"], saida, {"de_table.tsv": DE_OK, "de_resumo.json": '{"n": 1}'}, inicio=inicio)
    velho = Path(saida) / "de_table.tsv"
    os.utime(velho, (inicio - 3600, inicio - 3600))
    env = api.get(f"/api/v1/runs/{rid}/passos/de/envelope").json()
    assert not env["portao"]["ok"] and env["portas"]["de_table"]["anterior_ao_job"]


def test_envelope_reprova_artefato_alterado_depois_de_entregue(api, lancados):
    rid, plano = _aprovado(api)
    api.post(f"/api/v1/runs/{rid}/passos/de/executar")
    saida = plano["passos"][0]["saida"]
    _concluir(lancados[0]["job_id"], saida, {"de_table.tsv": DE_OK, "de_resumo.json": '{"n": 1}'})
    assert api.get(f"/api/v1/runs/{rid}/passos/de/envelope").json()["portao"]["ok"]
    (Path(saida) / "de_table.tsv").write_text(DE_OK.replace("2.5", "9.9"), encoding="utf-8")
    env = api.get(f"/api/v1/runs/{rid}/passos/de/envelope").json()
    assert not env["portao"]["ok"] and any("alterado" in f for f in env["portao"]["falhas"])


def test_run_conclui_quando_o_ultimo_passo_entrega(api, lancados):
    rid, plano = _aprovado(api)
    de, volcano = plano["passos"]
    api.post(f"/api/v1/runs/{rid}/passos/de/executar")
    _concluir(lancados[0]["job_id"], de["saida"], {"de_table.tsv": DE_OK, "de_resumo.json": '{"n": 1}'})
    assert api.post(f"/api/v1/runs/{rid}/passos/volcano/executar").status_code == 202
    _concluir(lancados[1]["job_id"], volcano["saida"],
              {"volcano.png": _png_bytes(), "volcano_resumo.json": '{"n": 2}'})
    assert api.get(f"/api/v1/runs/{rid}").json()["estado"] == "concluido"
    # concluído não conta como ativo: outro run pode ser aprovado
    _aprovado(api)


def test_envelope_ignora_status_de_job_alheio(api, lancados):
    """O run.json aponta para um job, mas o status.json desse job é de OUTRO run: o
    envelope não pode atestar arquivo nenhum com base nele."""
    rid, plano = _aprovado(api)
    api.post(f"/api/v1/runs/{rid}/passos/de/executar")
    queue.write_status(lancados[0]["job_id"], "done", run_id="outro-run", passo="de")
    env = api.get(f"/api/v1/runs/{rid}/passos/de/envelope").json()
    assert not env["portao"]["ok"] and "não pertence" in env["portao"]["falhas"][0]
