"""Máquina de estados, vagas e envelope dos runs (revisão adversarial da F2, frente runs).

Cada teste é um CANÁRIO de um achado confirmado (#3, #4, #7/#27, #10, #11, #12, #13, #22,
#23, #24, #25, #26, #29, #39, e #5/#8 no ramo do relançamento) e vem com o seu CONTROLE —
o mesmo arranjo sem a mexida, que continua passando (senão o 409 viria do arranjo, não do
portão). Jobs fingidos como em tests/test_runs.py: o fixture `lancados` grava o status como
o enqueue e `_concluir` faz o papel do worker (proveniência no início, saídas, done com o
`sha_saidas` das portas). Resíduos da 1ª rodada: #7 (passo entregue conferido pelo stat),
#10 (o sha das saídas é do worker), #11 (abortar cancela o job na fila) e #13 (marcador
`lancando` antes de enfileirar).
"""
from __future__ import annotations

import errno
import json
import os
import shutil
import threading
import time
from pathlib import Path

import pytest

from omica import proveniencia, runs
from omica.config import REPO_ROOT
from omica.jobs import queue
from omica.registry import MODULES_DIR, load_registry
from test_runs import (DE_OK, DEMO, REFERENCIA, _aprovado, _com_entradas, _concluir, _novo,  # noqa: F401
                       _png_bytes, api, lancados)

RESUMO = '{"n": 1}'
DE_RUIM = DE_OK.replace("0.01", "1.7")  # padj fora de [0, 1]: o validador de tabela_de reprova


@pytest.fixture(autouse=True)
def _limites_padrao(monkeypatch):
    """Os limites vêm do ambiente; com uma variável exportada no shell os canários de vaga
    passariam a aprovar."""
    monkeypatch.delenv("OMICA_MAX_RUNS_ATIVOS", raising=False)
    monkeypatch.delenv("OMICA_MAX_RUNS_ABERTOS", raising=False)


def _executar(api, rid: str, passo: str = "de"):
    return api.post(f"/api/v1/runs/{rid}/passos/{passo}/executar")


def _envelope(api, rid: str, passo: str = "de") -> dict:
    return api.get(f"/api/v1/runs/{rid}/passos/{passo}/envelope").json()


def _erro(r) -> str:
    return r.json()["detail"]["erro"]


def _estado(api, rid: str) -> dict:
    r = api.get(f"/api/v1/runs/{rid}")
    assert r.status_code == 200, r.text
    return r.json()


def _aprovar_outro(api):
    """Tenta aprovar um run novo: 200 = havia vaga, 409 limite = não havia."""
    rid = _com_entradas(api)
    p = api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {"referencia": REFERENCIA}}).json()
    return api.post(f"/api/v1/runs/{rid}/aprovar", json={"plano_sha256": p["plano_sha256"]})


def _de_lancado(api, lancados) -> tuple[str, dict, str]:
    rid, plano = _aprovado(api)
    assert _executar(api, rid).status_code == 202
    return rid, plano, lancados[-1]["job_id"]


# ======================================================= #3 estado() sem releitura
def test_estado_nao_rele_o_coldata_a_cada_get(api, monkeypatch):
    """As opções das escolhas são calculadas quando a entrada chega e guardadas no run.json:
    o GET (alvo do polling do n8n) não reparseia o coldata — antes, 256 MB a cada GET."""
    rid = _com_entradas(api)

    def explode(*a, **k):
        raise AssertionError("o estado() releu a entrada")

    monkeypatch.setattr(runs.formatos, "valores_coluna", explode)
    corpo = _estado(api, rid)
    assert REFERENCIA in corpo["escolhas_opcoes"]["referencia"]["valores"]
    # o plano usa as MESMAS opções guardadas (as que o formulário mostrou)
    r = api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {"referencia": REFERENCIA}})
    assert r.status_code == 200, r.text


def test_perfil_longo_vai_truncado_para_o_run_json(api):
    """O veredito volta em TODO GET: a lista de amostras de um coldata enorme não vai inteira."""
    linhas = "amostra\tcondicao\n" + "".join(f"a{i}\t{'A' if i % 2 else 'B'}\n" for i in range(120))
    rid = _novo(api)
    r = api.put(f"/api/v1/runs/{rid}/entradas/coldata", content=linhas.encode(),
                headers={"Content-Type": "application/octet-stream"})
    perfil = r.json()["entradas"]["coldata"]["veredito"]["perfil"]
    assert len(perfil["amostras"]) == 50 and perfil["amostras_total"] == 120
    # controle: o coldata do demo (poucas amostras) vai inteiro, sem o campo de total
    r = api.put(f"/api/v1/runs/{rid}/entradas/coldata", json={"caminho": f"{DEMO}/coldata_granulosa.tsv"})
    perfil = r.json()["entradas"]["coldata"]["veredito"]["perfil"]
    assert "amostras_total" not in perfil and len(perfil["amostras"]) == perfil["n_amostras"]


# ============================================================== #4 disco
def test_runs_abertos_tem_teto(api, monkeypatch):
    monkeypatch.setenv("OMICA_MAX_RUNS_ABERTOS", "2")
    primeiro = _novo(api)
    _novo(api)
    r = api.post("/api/v1/runs", json={"pipeline": "transcriptomica_de"})
    assert r.status_code == 409 and _erro(r) == "limite_de_runs_abertos", r.text
    # controle: encerrado um dos abertos, cabe outro
    assert api.post(f"/api/v1/runs/{primeiro}/abortar").status_code == 200
    assert api.post("/api/v1/runs", json={"pipeline": "transcriptomica_de"}).status_code == 201


def test_reenviar_entrada_apaga_a_copia_adaptada(api, monkeypatch, tmp_path, cfg):
    from test_rotas_f3_f4 import _com_counts_baguncado, _llm

    rid = _com_counts_baguncado(api, tmp_path)
    _llm(monkeypatch)
    reg = api.post(f"/api/v1/runs/{rid}/entradas/counts/adaptar").json()["run"]["entradas"]["counts"]
    adaptado = Path(reg["caminho"])
    assert adaptado.name == "counts.adaptado.tsv" and adaptado.exists()
    r = api.put(f"/api/v1/runs/{rid}/entradas/counts", json={"caminho": f"{DEMO}/counts_granulosa.tsv"})
    assert r.status_code == 200, r.text
    assert not adaptado.exists()
    # só as cópias vigentes, sem temporário nenhum
    assert sorted(p.name for p in (cfg.raw_dir / "runs" / rid).iterdir()) == ["coldata.tsv", "counts.tsv"]


def test_copia_com_teto_em_fluxo_nao_confia_no_stat(tmp_path, monkeypatch):
    """Um arquivo que cresce entre o stat() e a cópia passava do limite."""
    monkeypatch.setattr(runs, "_MAX_ENTRADA", 100)
    grande = tmp_path / "crescendo.tsv"
    grande.write_bytes(b"x" * 1000)

    class Crescendo:  # o stat() viu o arquivo pequeno; na hora de copiar ele já cresceu
        def stat(self):
            return os.stat_result((0, 0, 0, 0, 0, 0, 10, 0, 0, 0))

        def open(self, modo):
            return grande.open(modo)

    destino = tmp_path / "run" / "counts.tsv"
    destino.parent.mkdir()
    with pytest.raises(runs.ErroRun) as e:
        runs._copiar_entrada(destino, conteudo=None, fonte=Crescendo())
    assert e.value.status == 413 and list(destino.parent.iterdir()) == []
    # controle: dentro do teto a cópia sai inteira
    pequeno = tmp_path / "pequeno.tsv"
    pequeno.write_bytes(b"y" * 50)
    runs._copiar_entrada(destino, conteudo=None, fonte=pequeno)
    assert destino.read_bytes() == b"y" * 50 and list(destino.parent.iterdir()) == [destino]


def test_abortar_sem_passo_apaga_as_copias_com_passo_preserva(api, lancados, cfg):
    rid = _com_entradas(api)
    pasta = cfg.raw_dir / "runs" / rid
    assert any(pasta.iterdir())
    assert api.post(f"/api/v1/runs/{rid}/abortar").json()["estado"] == "abortado"
    assert not pasta.exists()
    # controle: o run que rodou passo guarda as entradas (proveniência do que rodou)
    rid2, _, _ = _de_lancado(api, lancados)
    assert api.post(f"/api/v1/runs/{rid2}/abortar").status_code == 200
    assert any((cfg.raw_dir / "runs" / rid2).iterdir())


# ===================================================== #7/#27/#39 estado terminal
def test_done_com_portao_reprovado_leva_a_erro_e_libera_a_vaga(api, lancados):
    rid, plano, job = _de_lancado(api, lancados)
    _concluir(job, plano["passos"][0]["saida"], {"de_table.tsv": DE_RUIM, "de_resumo.json": RESUMO})
    corpo = _estado(api, rid)
    assert corpo["estado"] == "erro", corpo["estado"]
    assert any("fora do formato tabela_de" in f for f in corpo["motivo_erro"]["de"])
    assert _aprovar_outro(api).status_code == 200  # o run reprovado não segura a vaga


def test_relancar_reprovado_nao_consultado_disputa_a_vaga(api, lancados):
    """O run.json ainda diz 'executando' (ninguém fez GET), mas o portão reprovou: o run já
    não ocupa vaga, outro run é aprovado — e relançar o primeiro tem de disputar a vaga."""
    rid, plano, job = _de_lancado(api, lancados)
    _concluir(job, plano["passos"][0]["saida"], {"de_table.tsv": DE_RUIM, "de_resumo.json": RESUMO})
    assert runs._carregar(rid)["estado"] == "executando"
    assert _aprovar_outro(api).status_code == 200
    r = _executar(api, rid)
    assert r.status_code == 409 and _erro(r) == "limite_de_runs_ativos", r.text
    assert len(lancados) == 1


def test_done_reprovado_e_nunca_entregue_pode_ser_relancado(api, lancados):
    rid, plano, job = _de_lancado(api, lancados)
    _concluir(job, plano["passos"][0]["saida"], {"de_table.tsv": DE_RUIM, "de_resumo.json": RESUMO})
    r = _executar(api, rid)
    assert r.status_code == 202, r.text
    assert len(lancados) == 2 and _estado(api, rid)["estado"] == "executando"
    assert "motivo_erro" not in _estado(api, rid)


def test_porta_nao_gerada_reprova_e_nao_libera_o_proximo(api, lancados):
    """#39 (C5): sem de_resumo.json, o portão não pode abrir."""
    rid, plano, job = _de_lancado(api, lancados)
    _concluir(job, plano["passos"][0]["saida"], {"de_table.tsv": DE_OK})
    env = _envelope(api, rid)
    assert not env["portao"]["ok"] and "de_resumo: arquivo não gerado" in env["portao"]["falhas"]
    assert env["portas"]["de_resumo"]["existe"] is False
    r = _executar(api, rid, "volcano")
    assert r.status_code == 409 and _erro(r) == "passo_anterior_pendente"
    assert _estado(api, rid)["estado"] == "erro"


def test_ultimo_passo_reprovado_nao_conclui(api, lancados):
    """#39 (C6): o volcano 'done' com PNG inválido não marca o run como concluído."""
    rid, plano, job = _de_lancado(api, lancados)
    de, volcano = plano["passos"]
    _concluir(job, de["saida"], {"de_table.tsv": DE_OK, "de_resumo.json": RESUMO})
    assert _executar(api, rid, "volcano").status_code == 202
    _concluir(lancados[-1]["job_id"], volcano["saida"], {"volcano.png": b"nao e png", "volcano_resumo.json": RESUMO})
    corpo = _estado(api, rid)
    assert corpo["estado"] == "erro" and "volcano" in corpo["motivo_erro"]


def test_job_orfao_leva_a_erro_e_pode_ser_relancado(api, lancados):
    """Worker morto (OOM, reinício, queda de energia): o status fica 'running' para sempre."""
    rid, _, job = _de_lancado(api, lancados)
    queue.write_status(job, "running", iniciado_em=time.time() - 7 * 3600)
    env = _envelope(api, rid)
    assert env["estado"] == "orfao" and env["portao"]["pendente"] is False
    assert _estado(api, rid)["estado"] == "erro"
    assert _executar(api, rid).status_code == 202 and len(lancados) == 2


def test_job_rodando_dentro_do_prazo_nao_e_orfao(api, lancados):
    """Controle do órfão: um job que começou há um minuto continua vivo."""
    rid, _, job = _de_lancado(api, lancados)
    queue.write_status(job, "running", iniciado_em=time.time() - 60)
    assert _estado(api, rid)["estado"] == "executando"
    r = _executar(api, rid)
    assert r.status_code == 409 and _erro(r) == "passo_ja_lancado" and r.json()["detail"]["status"] == "running"


def test_caminho_feliz_sem_get_do_run_libera_a_vaga(api, lancados):
    """A vaga é contada pelo estado VIVO: o fluxo que só consulta envelopes não deixa o run
    preso em 'executando' no run.json segurando a única vaga."""
    rid, plano, job = _de_lancado(api, lancados)
    de, volcano = plano["passos"]
    _concluir(job, de["saida"], {"de_table.tsv": DE_OK, "de_resumo.json": RESUMO})
    assert _executar(api, rid, "volcano").status_code == 202
    r = _aprovar_outro(api)  # controle: com o volcano na fila, a vaga é dele
    assert r.status_code == 409 and _erro(r) == "limite_de_runs_ativos"
    _concluir(lancados[-1]["job_id"], volcano["saida"], {"volcano.png": _png_bytes(), "volcano_resumo.json": RESUMO})
    assert runs._carregar(rid)["estado"] == "executando"  # ninguém fez GET do run
    assert _aprovar_outro(api).status_code == 200


def _contar_releituras(monkeypatch) -> list[str]:
    chamadas: list[str] = []
    validar, sha = runs.formatos.validar, runs.sha256_arquivo
    monkeypatch.setattr(runs.formatos, "validar", lambda f, c: chamadas.append(f"validar:{Path(c).name}") or validar(f, c))
    monkeypatch.setattr(runs, "sha256_arquivo", lambda c, *a: chamadas.append(f"sha:{Path(c).name}") or sha(c, *a))
    return chamadas


def test_get_do_run_nao_reconfere_por_inteiro_o_passo_ja_entregue(api, lancados, monkeypatch):
    """#7 (custo): com o de entregue e o volcano rodando, todo GET do run (o polling do n8n)
    remontava o envelope do de — sha256 + validação de formato da de_table inteira, ~250 ms
    com 60 mil genes, e sob a trava global de vagas em _runs_ativos. Passo entregue e intacto
    no stat não relê nada; o envelope completo fica para o passo que acabou de terminar."""
    rid, plano, job = _de_lancado(api, lancados)
    de, _ = plano["passos"]
    _concluir(job, de["saida"], {"de_table.tsv": DE_OK, "de_resumo.json": RESUMO})
    chamadas = _contar_releituras(monkeypatch)
    assert _estado(api, rid)["estado"] == "executando"
    # controle: o passo que acabou de terminar é conferido por inteiro (e entregue aqui)
    assert {"validar:de_table.tsv", "sha:de_table.tsv"} <= set(chamadas)
    assert _executar(api, rid, "volcano").status_code == 202
    queue.write_status(lancados[-1]["job_id"], "running", iniciado_em=time.time())
    chamadas.clear()
    for _ in range(3):
        corpo = _estado(api, rid)
        assert corpo["passos_vivos"]["de"]["status"] == "done"
    assert chamadas == [], chamadas
    r = _aprovar_outro(api)  # _runs_ativos, sob a trava global: o mesmo caminho leve
    assert r.status_code == 409 and _erro(r) == "limite_de_runs_ativos"
    # (o outro run lê as próprias entradas; das portas do de, nada)
    assert not [c for c in chamadas if c.endswith(("de_table.tsv", "de_resumo.json"))], chamadas


def test_passo_entregue_mexido_volta_ao_envelope_completo(api, lancados, monkeypatch):
    """Controle do leve: a saída entregue é reescrita (o stat muda) e o GET volta a montar o
    envelope inteiro — e reprova pelo sha, levando o run a erro."""
    rid, plano, job = _de_lancado(api, lancados)
    de, _ = plano["passos"]
    _concluir(job, de["saida"], {"de_table.tsv": DE_OK, "de_resumo.json": RESUMO})
    assert _executar(api, rid, "volcano").status_code == 202  # o de é entregue aqui
    chamadas = _contar_releituras(monkeypatch)
    (Path(de["saida"]) / "de_table.tsv").write_text(DE_OK.replace("2.5", "9.9"), encoding="utf-8")
    corpo = _estado(api, rid)
    assert "sha:de_table.tsv" in chamadas
    assert corpo["estado"] == "erro" and any("difere do que o job deixou" in f for f in corpo["motivo_erro"]["de"])


def test_troca_que_engana_o_stat_ainda_reprova_quem_consome(api, lancados):
    """O leve é só do polling: a troca que preserva tamanho e mtime (e, no host Windows, o
    ctime, que lá é a criação) passa pelo stat — mas quem CONSOME a porta (o GET do envelope,
    o executar do passo seguinte) monta o envelope inteiro e reprova pelo sha."""
    rid, plano, job = _de_lancado(api, lancados)
    de, _ = plano["passos"]
    _concluir(job, de["saida"], {"de_table.tsv": DE_OK, "de_resumo.json": RESUMO})
    assert _envelope(api, rid)["portao"]["ok"]  # entregue
    tabela = Path(de["saida"]) / "de_table.tsv"
    st = tabela.stat()
    tabela.write_text(DE_OK.replace("2.5", "9.9"), encoding="utf-8")  # mesmo tamanho
    os.utime(tabela, ns=(st.st_atime_ns, st.st_mtime_ns))
    env = _envelope(api, rid)
    assert not env["portao"]["ok"] and any("artefato alterado" in f for f in env["portao"]["falhas"])
    r = _executar(api, rid, "volcano")
    assert r.status_code == 409 and _erro(r) == "passo_anterior_pendente"


# ============================================ #10 a referência é a do JOB
def test_saida_reprovada_e_reescrita_depois_do_fim_nao_e_selada(api, lancados, cfg):
    rid, plano, job = _de_lancado(api, lancados)
    saida = plano["passos"][0]["saida"]
    _concluir(job, saida, {"de_table.tsv": DE_OK.replace("0.01", "7.5"), "de_resumo.json": RESUMO})
    assert not _envelope(api, rid)["portao"]["ok"]
    (Path(saida) / "de_table.tsv").write_text(DE_OK, encoding="utf-8")  # o "conserto" à mão
    env = _envelope(api, rid)
    assert not env["portao"]["ok"]
    assert any("de_table: difere do que o job deixou" in f for f in env["portao"]["falhas"])
    assert not (cfg.work_dir / "runs" / rid / "envelopes" / "de.json").exists()
    r = _executar(api, rid, "volcano")
    assert r.status_code == 409 and _erro(r) == "passo_anterior_pendente"


def test_saida_trocada_antes_da_1a_leitura_e_pega_pelo_terminado_em(api, lancados):
    """O worker registrou o fim há 1 h; o arquivo tem mtime de agora: não é deste job."""
    rid, plano, job = _de_lancado(api, lancados)
    fim = time.time() - 3600
    _concluir(job, plano["passos"][0]["saida"], {"de_table.tsv": DE_OK, "de_resumo.json": RESUMO},
              inicio=fim - 60, terminado_em=fim)
    env = _envelope(api, rid)
    assert not env["portao"]["ok"] and env["portas"]["de_table"]["posterior_ao_job"]


def test_terminado_em_coerente_aprova(api, lancados):
    """Controle do canário acima: o fim registrado logo depois das saídas não reprova nada."""
    rid, plano, job = _de_lancado(api, lancados)
    _concluir(job, plano["passos"][0]["saida"], {"de_table.tsv": DE_OK, "de_resumo.json": RESUMO},
              terminado_em=time.time())
    env = _envelope(api, rid)
    assert env["portao"]["ok"], env["portao"]
    assert _envelope(api, rid)["portao"]["ok"]  # a 2ª leitura bate com a referência do job


def _job_terminado_ha_um_minuto(cfg, job: str, saida: str) -> Path:
    """Linha do tempo de um job que acabou há 1 min e que ninguém consultou desde então:
    começou há 2 min, gravou as saídas há 90 s e o 'done' há 60 s — sem `terminado_em` (o
    worker de hoje não o registra; o fim é o mtime do status.json)."""
    agora = time.time()
    _concluir(job, saida, {"de_table.tsv": DE_OK, "de_resumo.json": RESUMO}, inicio=agora - 120)
    for arq in Path(saida).iterdir():
        os.utime(arq, (agora - 90, agora - 90))
    status = cfg.jobs_dir / job / "status.json"
    os.utime(status, (agora - 60, agora - 60))
    return Path(saida) / "de_table.tsv"


def test_saida_trocada_antes_da_1a_leitura_sem_terminado_em_reprova(api, lancados, cfg):
    """#10 sem `terminado_em`: a troca feita entre o fim do job e a 1ª leitura do envelope
    virava a referência "do job" e era selada. O fim é o mtime do status.json 'done'."""
    rid, plano, job = _de_lancado(api, lancados)
    tabela = _job_terminado_ha_um_minuto(cfg, job, plano["passos"][0]["saida"])
    tabela.write_text(DE_OK.replace("2.5", "9.9"), encoding="utf-8")  # trocada agora
    env = _envelope(api, rid)
    assert not env["portao"]["ok"] and env["portas"]["de_table"].get("posterior_ao_job"), env["portao"]
    assert not (cfg.work_dir / "runs" / rid / "envelopes" / "de.json").exists()
    r = _executar(api, rid, "volcano")
    assert r.status_code == 409 and _erro(r) == "passo_anterior_pendente"


def test_job_terminado_ha_um_minuto_sem_troca_aprova(api, lancados, cfg):
    """Controle: a mesma linha do tempo, sem a troca, entrega."""
    rid, plano, job = _de_lancado(api, lancados)
    _job_terminado_ha_um_minuto(cfg, job, plano["passos"][0]["saida"])
    env = _envelope(api, rid)
    assert env["portao"]["ok"], env["portao"]


def test_leitor_com_run_antigo_nao_troca_a_referencia_do_job_novo(api, lancados):
    """Uma referência por job_id: o polling com o run.json de ANTES de um relançamento não
    sobrescreve a referência do job novo pela do antigo. Antes, a leitura seguinte do job novo
    regravava a referência com o arquivo do momento — e o "conserto" à mão (com o carimbo de
    tempo devolvido) era selado como saída do script."""
    rid, plano, job1 = _de_lancado(api, lancados)
    saida = Path(plano["passos"][0]["saida"])
    _concluir(job1, str(saida), {"de_table.tsv": DE_RUIM, "de_resumo.json": RESUMO})
    antigo = runs._carregar(rid)  # o que o polling leu antes do relançamento
    assert _executar(api, rid).status_code == 202
    job2 = lancados[-1]["job_id"]
    _concluir(job2, str(saida), {"de_table.tsv": DE_RUIM, "de_resumo.json": RESUMO})
    assert not _envelope(api, rid)["portao"]["ok"]  # 1ª leitura do job2: referência gravada
    runs.envelope(rid, "de", _run=antigo)  # o leitor atrasado, ainda com o job1
    tabela = saida / "de_table.tsv"
    st = tabela.stat()
    tabela.write_text(DE_OK, encoding="utf-8")
    os.utime(tabela, ns=(st.st_atime_ns, st.st_mtime_ns))
    env = _envelope(api, rid)
    assert not env["portao"]["ok"], env["portao"]
    assert any("de_table: difere do que o job deixou" in f for f in env["portao"]["falhas"])
    r = _executar(api, rid, "volcano")
    assert r.status_code == 409 and _erro(r) == "passo_anterior_pendente"


@pytest.mark.parametrize("sha_saidas", [None, "lixo", {}], ids=["ausente", "estragado", "sem_portas"])
def test_sha_saidas_do_worker_ausente_ou_estragado_falha_fechado(api, lancados, sha_saidas):
    """Sem o atestado do worker não há com o que comparar a porta: falha, nunca "ok por
    omissão" (controle: test_execucao_conferida_aprova, o mesmo arranjo com o atestado)."""
    rid, plano, job = _de_lancado(api, lancados)
    extra = {} if sha_saidas is None else {"sha_saidas": sha_saidas}
    _concluir(job, plano["passos"][0]["saida"], {"de_table.tsv": DE_OK, "de_resumo.json": RESUMO},
              com_sha_saidas=False, **extra)
    env = _envelope(api, rid)
    assert not env["portao"]["ok"], env["portao"]
    if sha_saidas == {}:
        assert any("o worker não atestou esta porta" in f for f in env["portao"]["falhas"])
    else:
        assert any("sha_saidas ausente ou ilegível" in f for f in env["portao"]["falhas"])
    assert _executar(api, rid, "volcano").status_code == 409


def test_troca_com_mtime_preservado_antes_da_1a_leitura_reprova(api, lancados, cfg):
    """#10 (forte): a tabela é trocada entre o fim do job e a 1ª leitura do envelope com o
    carimbo de tempo DEVOLVIDO (os.utime, cp -p, rsync -t). A referência da 1ª leitura já era
    o arquivo trocado e o portão aprovava; agora a referência é o sha que o WORKER tirou ao
    terminar. Controle: test_execucao_conferida_aprova (sem a troca)."""
    rid, plano, job = _de_lancado(api, lancados)
    saida = Path(plano["passos"][0]["saida"])
    _concluir(job, str(saida), {"de_table.tsv": DE_RUIM, "de_resumo.json": RESUMO})
    tabela = saida / "de_table.tsv"
    st = tabela.stat()
    tabela.write_text(DE_OK, encoding="utf-8")  # o "conserto" à mão, antes de qualquer leitura
    os.utime(tabela, ns=(st.st_atime_ns, st.st_mtime_ns))
    env = _envelope(api, rid)
    assert not env["portao"]["ok"], env["portao"]
    assert any("de_table: difere do que o job deixou" in f for f in env["portao"]["falhas"])
    assert not env["portas"]["de_table"].get("posterior_ao_job")  # o mtime não denuncia nada
    assert not (cfg.work_dir / "runs" / rid / "envelopes" / "de.json").exists()
    r = _executar(api, rid, "volcano")
    assert r.status_code == 409 and _erro(r) == "passo_anterior_pendente"


# ===================================================== #11 abortar com job vivo
def test_abortar_com_job_vivo_segura_a_vaga(api, lancados):
    rid, _, job = _de_lancado(api, lancados)
    queue.write_status(job, "running", iniciado_em=time.time())
    assert api.post(f"/api/v1/runs/{rid}/abortar").json()["estado"] == "abortado"
    r = _aprovar_outro(api)
    assert r.status_code == 409 and _erro(r) == "limite_de_runs_ativos"
    assert r.json()["detail"]["ativos"] == [rid]
    # controle: o job termina e a vaga volta
    queue.write_status(job, "error", error="interrompido")
    assert _aprovar_outro(api).status_code == 200


def test_erro_com_job_vivo_segura_a_vaga(api, lancados):
    """A saída do de é alterada depois de entregue com o volcano rodando: o run vai a 'erro'
    (o de não é mais atestável), mas o volcano continua na máquina — a vaga é dele."""
    rid, plano, job = _de_lancado(api, lancados)
    de, _ = plano["passos"]
    _concluir(job, de["saida"], {"de_table.tsv": DE_OK, "de_resumo.json": RESUMO})
    assert _executar(api, rid, "volcano").status_code == 202  # o de é entregue aqui
    volcano = lancados[-1]["job_id"]
    queue.write_status(volcano, "running", iniciado_em=time.time())
    (Path(de["saida"]) / "de_table.tsv").write_text(DE_OK.replace("2.5", "9.9"), encoding="utf-8")
    assert _estado(api, rid)["estado"] == "erro"
    r = _aprovar_outro(api)
    assert r.status_code == 409 and _erro(r) == "limite_de_runs_ativos", r.text
    assert r.json()["detail"]["ativos"] == [rid]
    # controle: o volcano termina e a vaga volta
    queue.write_status(volcano, "error", error="interrompido")
    assert _aprovar_outro(api).status_code == 200


def test_job_perdido_na_fila_e_cancelado_ao_abortar_e_libera_a_vaga(api, lancados):
    """#11 (resíduo): o Redis perde o job (compose down/up, queda de energia) e o status.json
    fica 'queued' para sempre — 'queued' nunca vira órfão. Antes, abortar respondia 200 mas a
    vaga continuava presa (o job 'queued' contava como vivo) e abortar de novo dava
    run_encerrado: com OMICA_MAX_RUNS_ATIVOS=1, nenhum outro run era aprovado."""
    rid, _, job = _de_lancado(api, lancados)
    r = _executar(api, rid)  # relançar não resolve: o passo está 'queued'
    assert r.status_code == 409 and _erro(r) == "passo_ja_lancado"
    corpo = api.post(f"/api/v1/runs/{rid}/abortar").json()
    assert corpo["estado"] == "abortado" and corpo["jobs_cancelados"] == [job]
    st = queue.read_status(job)
    assert (st["status"], st["error"], st["cancelado"]) == ("error", "cancelado ao abortar", True)
    assert _aprovar_outro(api).status_code == 200
    env = _envelope(api, rid)  # o envelope do passo diz por que ele não entregou
    assert env["estado"] == "error" and env["portao"]["falhas"] == ["cancelado ao abortar"]


def test_abortar_de_novo_cancela_o_job_que_a_1a_vez_nao_alcancou(api, lancados, cfg):
    """#11, perna (d) do cenário: o cancelar da 1ª vez não alcança o job (trava do job ocupada
    naquele instante) e ele fica 'queued' para sempre num run já abortado. Antes, abortar de
    novo dava 409 run_encerrado e a vaga ficava presa. Agora a 2ª vez cancela (200) e a vaga
    volta. Controle: com nada na fila, abortar de novo continua 409 run_encerrado
    (test_run_encerrado_nao_aborta_nem_executa, em test_runs_canarios)."""
    from omica.travas import trava

    rid, _, job = _de_lancado(api, lancados)
    with trava(cfg.jobs_dir / job / ".trava"):  # outro dono segura a trava do job
        corpo = api.post(f"/api/v1/runs/{rid}/abortar").json()
    assert corpo["estado"] == "abortado" and "jobs_cancelados" not in corpo
    assert queue.read_status(job)["status"] == "queued"
    r = _aprovar_outro(api)
    assert r.status_code == 409 and _erro(r) == "limite_de_runs_ativos"  # a vaga continua presa
    r = api.post(f"/api/v1/runs/{rid}/abortar")
    assert r.status_code == 200 and r.json()["jobs_cancelados"] == [job], r.text
    assert r.json()["estado"] == "abortado" and queue.read_status(job)["cancelado"] is True
    assert _aprovar_outro(api).status_code == 200
    r = api.post(f"/api/v1/runs/{rid}/abortar")  # nada mais na fila: encerrado de vez
    assert r.status_code == 409 and _erro(r) == "run_encerrado"


def test_abortar_nao_cancela_job_que_ja_roda(api, lancados):
    """Controle do canário acima: o job 'running' não é tocado (termina sozinho) e segura a
    vaga até terminar — é o test_abortar_com_job_vivo_segura_a_vaga, aqui conferindo o status."""
    rid, _, job = _de_lancado(api, lancados)
    queue.write_status(job, "running", iniciado_em=time.time())
    corpo = api.post(f"/api/v1/runs/{rid}/abortar").json()
    assert corpo["estado"] == "abortado" and "jobs_cancelados" not in corpo
    st = queue.read_status(job)
    assert st["status"] == "running" and "cancelado" not in st
    r = _aprovar_outro(api)
    assert r.status_code == 409 and _erro(r) == "limite_de_runs_ativos"


# ====================================== #13 job enfileirado sem registro no run
def _segunda_gravacao_falha(monkeypatch, vezes: int = 1) -> list:
    """O run.json não grava DEPOIS de o job ir para a fila (ENOSPC no E:\\data, arquivo preso
    por antivírus): a gravação do passo COM job_id levanta."""
    original, falhas = runs._gravar_run, []

    def disco_cheio(run_id, run):
        com_job = any(isinstance(p, dict) and p.get("job_id") for p in run["passos"].values())
        if com_job and len(falhas) < vezes:
            falhas.append(1)
            raise OSError(errno.ENOSPC, "No space left on device")
        return original(run_id, run)

    monkeypatch.setattr(runs, "_gravar_run", disco_cheio)
    return falhas


def test_falha_ao_registrar_o_job_nao_lanca_um_segundo_na_mesma_saida(api, lancados, monkeypatch):
    """#13: antes o job ia para a fila ANTES do run.json; a gravação falhava, a API dava 500 e
    o retry do n8n lançava um 2º job na MESMA pasta de saída. Agora o marcador `lancando` já
    está no run.json e o retry adota o job que o status.json liga a este run/passo."""
    rid, _ = _aprovado(api)
    falhas = _segunda_gravacao_falha(monkeypatch)
    with pytest.raises(OSError):
        runs.executar_passo(rid, "de")
    assert falhas and len(lancados) == 1
    marcado = runs._carregar(rid)["passos"]["de"]
    assert marcado["lancando"] is True and marcado["job_id"] is None
    assert _envelope(api, rid)["estado"] == "lancando"
    r = _executar(api, rid)  # o retry do n8n
    assert r.status_code == 409 and _erro(r) == "passo_ja_lancado", r.text
    assert r.json()["detail"]["job_id"] == lancados[0]["job_id"] and len(lancados) == 1
    run = runs._carregar(rid)
    assert run["passos"]["de"]["job_id"] == lancados[0]["job_id"] and run["estado"] == "executando"
    # e o job adotado é cancelado ao abortar (não fica na fila sem dono)
    assert api.post(f"/api/v1/runs/{rid}/abortar").json()["jobs_cancelados"] == [lancados[0]["job_id"]]


def test_get_do_run_reconcilia_o_marcador_sem_retry(api, lancados, monkeypatch):
    """Se o n8n não repetir o executar, o polling do run adota o job: sem isso ele rodava sem
    que o run jamais o enxergasse (o run nunca concluía)."""
    rid, plano = _aprovado(api)
    _segunda_gravacao_falha(monkeypatch)
    with pytest.raises(OSError):
        runs.executar_passo(rid, "de")
    corpo = _estado(api, rid)
    assert corpo["passos"]["de"]["job_id"] == lancados[0]["job_id"] and corpo["estado"] == "executando"
    _concluir(lancados[0]["job_id"], plano["passos"][0]["saida"], {"de_table.tsv": DE_OK, "de_resumo.json": RESUMO})
    assert _envelope(api, rid)["portao"]["ok"]


def test_lancamento_que_nao_enfileirou_nada_pode_ser_repetido(api, lancados, monkeypatch):
    """Controle do #13: o enqueue falhou ANTES de existir job (nenhum status.json do run/passo)
    e nem o desfazer do marcador gravou. O retry vê que nada foi enfileirado e lança um só."""
    rid, _ = _aprovado(api)
    original_lancar, original_gravar, estado_falha = runs.dispatch.lancar_script, runs._gravar_run, {"n": 0}

    def lancar_que_falha(*a, **k):
        if estado_falha["n"] == 0:
            estado_falha["n"] = 1
            raise RuntimeError("Redis caiu antes do status")
        return original_lancar(*a, **k)

    def desfazer_falha(run_id, run):
        if estado_falha["n"] == 1 and "de" not in run["passos"]:
            estado_falha["n"] = 2
            raise OSError(errno.ENOSPC, "No space left on device")
        return original_gravar(run_id, run)

    monkeypatch.setattr(runs.dispatch, "lancar_script", lancar_que_falha)
    monkeypatch.setattr(runs, "_gravar_run", desfazer_falha)
    with pytest.raises(RuntimeError):
        runs.executar_passo(rid, "de")
    assert estado_falha["n"] == 2 and runs._eh_marcador(runs._carregar(rid)["passos"]["de"])
    assert not lancados
    assert _executar(api, rid).status_code == 202 and len(lancados) == 1
    assert runs._carregar(rid)["passos"]["de"]["job_id"] == lancados[0]["job_id"]


def test_lancamento_falho_desfaz_o_marcador(api, monkeypatch, lancados):
    """Controle: RAM recusada (429) não deixa marcador — o passo volta ao que era e o próximo
    executar lança normalmente."""
    from omica.jobs.guarda_ram import RecusadoPorRAM

    rid, _ = _aprovado(api)
    original = runs.dispatch.lancar_script

    def recusa(*a, **k):
        raise RecusadoPorRAM("uso projetado 91% > 80%")

    monkeypatch.setattr(runs.dispatch, "lancar_script", recusa)
    assert _executar(api, rid).status_code == 429
    assert "de" not in runs._carregar(rid)["passos"]
    monkeypatch.setattr(runs.dispatch, "lancar_script", original)
    assert _executar(api, rid).status_code == 202 and len(lancados) == 1


def test_status_ilegivel_de_job_do_script_deixa_o_lancamento_incerto(api, lancados, monkeypatch, cfg):
    """Sem como saber se o lançamento interrompido deixou job (um status.json recente do mesmo
    script ilegível pode ser o dele): 409 lancamento_incerto, nada lançado. Controle: removido
    o status ilegível, o mesmo retry reconcilia."""
    rid, _ = _aprovado(api)
    _segunda_gravacao_falha(monkeypatch)
    with pytest.raises(OSError):
        runs.executar_passo(rid, "de")
    estragado = cfg.jobs_dir / queue.novo_job_id("transcriptomica-deseq2") / "status.json"
    estragado.parent.mkdir(parents=True)
    estragado.write_text('{"status": "que', encoding="utf-8")
    r = _executar(api, rid)
    assert r.status_code == 409 and _erro(r) == "lancamento_incerto", r.text
    assert r.json()["detail"]["candidatos"] == [lancados[0]["job_id"]] and len(lancados) == 1
    estragado.unlink()
    r = _executar(api, rid)
    assert r.status_code == 409 and _erro(r) == "passo_ja_lancado" and len(lancados) == 1


# ============================================================ #12 CAS do estado()
def _no_meio_do_estado(monkeypatch, rid: str, acao) -> None:
    """Roda `acao` depois de o estado() decidir e antes de ele gravar (a janela do ABA)."""
    original, feito = runs._decidir, []

    def intercalado(run_id, run, plano):
        r = original(run_id, run, plano)
        if run_id == rid and not feito:
            feito.append(1)
            acao()
        return r

    monkeypatch.setattr(runs, "_decidir", intercalado)


def test_relancamento_no_meio_do_estado_nao_e_atropelado(api, lancados, monkeypatch):
    rid, _, job = _de_lancado(api, lancados)
    queue.write_status(job, "error", error="R morreu")  # o GET vai decidir 'erro'...
    _no_meio_do_estado(monkeypatch, rid, lambda: runs.executar_passo(rid, "de"))  # ...e o retry entra
    corpo = _estado(api, rid)
    assert len(lancados) == 2
    assert corpo["estado"] == runs._carregar(rid)["estado"] == "executando"
    r = _aprovar_outro(api)  # o run continua com job na fila: segura a vaga
    assert r.status_code == 409 and _erro(r) == "limite_de_runs_ativos"


def test_abortar_no_meio_do_estado_responde_o_persistido(api, lancados, monkeypatch):
    rid, _, job = _de_lancado(api, lancados)
    queue.write_status(job, "error", error="R morreu")
    _no_meio_do_estado(monkeypatch, rid, lambda: runs.abortar(rid))
    assert _estado(api, rid)["estado"] == runs._carregar(rid)["estado"] == "abortado"


def test_estado_sem_concorrencia_promove_e_grava(api, lancados):
    """Controle: sem nada no meio, a decisão é gravada (e só uma vez)."""
    rid, _, job = _de_lancado(api, lancados)
    queue.write_status(job, "error", error="R morreu")
    v0 = runs._carregar(rid)["versao"]
    assert _estado(api, rid)["estado"] == "erro"
    assert _estado(api, rid)["estado"] == "erro"
    assert runs._carregar(rid)["versao"] == v0 + 1


# ========================================================= #13 os.replace no NTFS
def _replace_ocupado(monkeypatch, vezes: int) -> dict:
    real, conta = os.replace, {"n": 0}

    def ocupado(src, dst):
        if str(dst).endswith("alvo.json") and conta["n"] < vezes:
            conta["n"] += 1
            raise PermissionError(13, "aberto por outro processo")
        return real(src, dst)

    monkeypatch.setattr(runs.os, "replace", ocupado)
    return conta


def test_gravar_tenta_de_novo_com_o_destino_aberto(tmp_path, monkeypatch):
    conta = _replace_ocupado(monkeypatch, 3)
    runs._gravar(tmp_path / "alvo.json", {"a": 1})
    assert conta["n"] == 3 and json.loads((tmp_path / "alvo.json").read_text(encoding="utf-8")) == {"a": 1}
    assert list(tmp_path.glob("*.tmp")) == []


def test_gravar_que_desiste_nao_deixa_temporario(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "_TENTATIVAS_TROCA", 3)
    _replace_ocupado(monkeypatch, 10**6)
    with pytest.raises(PermissionError):
        runs._gravar(tmp_path / "alvo.json", {"a": 1})
    assert list(tmp_path.iterdir()) == []


# =============================================== #22 o que rodou é o aprovado
def test_script_trocado_entre_a_fila_e_a_execucao_reprova(api, lancados, monkeypatch, tmp_path):
    """O executar confere o sha ao ENFILEIRAR; o arquivo é editado com o job na fila e o
    worker roda o novo. O envelope compara a proveniência que o worker tirou ao rodar."""
    rid, plano, job = _de_lancado(api, lancados)
    copia = tmp_path / "repo_editado"
    shutil.copytree(REPO_ROOT / "modules" / "transcriptomica" / "scripts",
                    copia / "modules" / "transcriptomica" / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    with (copia / "modules" / "transcriptomica" / "scripts" / "expressao_deseq2.R").open("ab") as f:
        f.write(b"\n# editado com o job na fila\n")
    monkeypatch.setattr(proveniencia, "REPO_ROOT", copia)  # o que o worker enxerga ao rodar
    _concluir(job, plano["passos"][0]["saida"], {"de_table.tsv": DE_OK, "de_resumo.json": RESUMO})
    env = _envelope(api, rid)
    assert not env["portao"]["ok"]
    assert any("difere do aprovado" in f for f in env["portao"]["falhas"])
    assert _executar(api, rid, "volcano").status_code == 409
    assert _estado(api, rid)["estado"] == "erro"


def test_worker_sem_proveniencia_nao_aprova(api, lancados):
    rid, plano, job = _de_lancado(api, lancados)
    _concluir(job, plano["passos"][0]["saida"], {"de_table.tsv": DE_OK, "de_resumo.json": RESUMO},
              com_proveniencia=False)
    env = _envelope(api, rid)
    assert not env["portao"]["ok"] and any("proveniencia_execucao ausente" in f for f in env["portao"]["falhas"])


def test_cmd_do_job_diferente_do_plano_reprova(api, lancados):
    rid, plano, job = _de_lancado(api, lancados)
    queue.write_status(job, "queued", cmd=plano["passos"][0]["cmd"].replace("--lfc 1", "--lfc 0"))
    _concluir(job, plano["passos"][0]["saida"], {"de_table.tsv": DE_OK, "de_resumo.json": RESUMO})
    env = _envelope(api, rid)
    assert not env["portao"]["ok"] and "cmd do job difere do plano aprovado" in env["portao"]["falhas"]


def test_execucao_conferida_aprova(api, lancados):
    """Controle dos três acima: worker com a proveniência e o cmd do plano."""
    rid, plano, job = _de_lancado(api, lancados)
    _concluir(job, plano["passos"][0]["saida"], {"de_table.tsv": DE_OK, "de_resumo.json": RESUMO})
    env = _envelope(api, rid)
    assert env["portao"]["ok"], env["portao"]
    assert env["runtime"] == plano["passos"][0]["runtime"] and env["fila"] == plano["passos"][0]["fila"]


# ======================================================= #23 recursos do plano
def _registry_mexido(tmp_path: Path, velho: str | None, novo: str | None):
    destino = tmp_path / "copia" / "modules"
    shutil.copytree(MODULES_DIR / "transcriptomica", destino / "transcriptomica",
                    ignore=shutil.ignore_patterns("demo_data", "__pycache__"))
    if velho is not None:
        arq = destino / "transcriptomica" / "manifest.yaml"
        cab, sep, secao = arq.read_text(encoding="utf-8").partition("\nscripts_api:\n")
        assert sep and velho in secao, f"o manifest mudou: {velho!r} não está em scripts_api"
        arq.write_text(cab + sep + secao.replace(velho, novo, 1), encoding="utf-8")
    reg = load_registry(destino)
    assert "transcriptomica" not in reg.erros, reg.erros
    return reg


@pytest.mark.parametrize("velho,novo,campo", [
    (None, None, None),  # controle: cópia intacta
    ("    mem_gb: 3\n", "    mem_gb: 0.1\n", "mem_gb"),
    ("    cpus: 1\n", "    cpus: 8\n", "cpus"),
    ('runtime: "image:omica-methyl"', 'runtime: "image:omica-bioinfo"', "runtime"),
])
def test_recursos_trocados_depois_da_aprovacao_bloqueiam(api, monkeypatch, lancados, tmp_path, velho, novo, campo):
    rid, _ = _aprovado(api)
    reg = _registry_mexido(tmp_path, velho, novo)
    monkeypatch.setattr(runs, "load_registry", lambda *a, **k: reg)
    r = _executar(api, rid)
    if campo is None:
        assert r.status_code == 202, r.text
        return
    assert r.status_code == 409 and _erro(r) == "manifest_mudou", r.text
    assert campo in r.json()["detail"]["diferencas"] and not lancados


def test_placeholder_sem_param_e_409_nao_500(api, monkeypatch, lancados):
    """Manifest mexido: o param saiu, o {placeholder} ficou no cmd. KeyError virava 500."""
    rid, _ = _aprovado(api)

    def sem_param(*a, **k):
        raise KeyError("lfc")

    monkeypatch.setattr(runs.dispatch, "montar_cmd", sem_param)
    r = _executar(api, rid)
    assert r.status_code == 409 and _erro(r) == "manifest_mudou" and not lancados


# =================================================== #24 status ilegível
@pytest.mark.parametrize("dano", ["vazio", "truncado", "apagado"])
def test_status_ilegivel_nao_relanca(api, lancados, cfg, dano):
    rid, _, job = _de_lancado(api, lancados)
    arq = cfg.jobs_dir / job / "status.json"
    if dano == "vazio":
        arq.write_text("", encoding="utf-8")
    elif dano == "truncado":
        arq.write_text('{"status": "runn', encoding="utf-8")
    else:
        arq.unlink()
    r = _executar(api, rid)
    assert r.status_code == 409 and _erro(r) == "status_ilegivel", r.text
    assert len(lancados) == 1
    assert _estado(api, rid)["estado"] == "executando"  # sem saber se o job vive, não decide


# ================================================= #25 porta grande demais
def test_porta_sem_sha_nao_passa(api, lancados, monkeypatch, cfg):
    rid, plano, job = _de_lancado(api, lancados)
    _concluir(job, plano["passos"][0]["saida"], {"de_table.tsv": DE_OK, "de_resumo.json": RESUMO})
    monkeypatch.setattr(runs, "_MAX_SHA", 50)  # de_table passa do teto; de_resumo não
    env = _envelope(api, rid)
    assert not env["portao"]["ok"] and env["portas"]["de_table"]["sha256"] is None
    assert any("de_table" in f and "não é atestável" in f for f in env["portao"]["falhas"])
    assert not (cfg.work_dir / "runs" / rid / "envelopes" / "de.json").exists()
    # controle: o mesmo arranjo com o teto real é o test_execucao_conferida_aprova


# ====================================== resumo hostil no envelope (frente contrato)
_RESUMOS_HOSTIS = {
    "controle": lambda: RESUMO,
    "lista_aninhada": lambda: '{"a": ' + "[" * 200_000 + "]" * 200_000 + "}",
    "numero_de_5000_digitos": lambda: '{"n": ' + "9" * 5000 + "}",
}


@pytest.mark.parametrize("caso", sorted(_RESUMOS_HOSTIS))  # id curto: o conteúdo estoura o env do Windows
def test_resumo_hostil_reprova_o_passo_sem_derrubar_o_get(api, lancados, caso):
    """O validador já reprovava o resumo aninhado fundo demais (RecursionError do json) e o
    número de 5000 dígitos (ValueError do limite do int), mas o envelope ainda o relia em
    `_ler_resumo`, que só pegava JSONDecodeError: o GET do run — o polling do n8n — virava 500
    a cada consulta. Agora o passo reprova pelo formato e o run vai a 'erro'."""
    rid, plano, job = _de_lancado(api, lancados)
    _concluir(job, plano["passos"][0]["saida"], {"de_table.tsv": DE_OK, "de_resumo.json": _RESUMOS_HOSTIS[caso]()})
    r = api.get(f"/api/v1/runs/{rid}/passos/de/envelope")
    assert r.status_code == 200
    env = r.json()
    if caso == "controle":
        assert env["portao"]["ok"] and env["resumos"]["de_resumo"] == json.loads(RESUMO), env["portao"]
        return
    assert not env["portao"]["ok"] and env["resumos"]["de_resumo"] is None
    assert any(f.startswith("de_resumo: fora do formato resumo_json") for f in env["portao"]["falhas"])
    assert _estado(api, rid)["estado"] == "erro"


# ================================================= #26 sha do que vai ao disco
@pytest.mark.parametrize("top", [
    {"n": 10, "ordenar": "padj"},  # controle: o YAML atual
    {10: "log2FoldChange", 9: "padj"},  # 9 < 10 em memória; '10' < '9' depois de reler
    {"n": 10, "ordenar": "padj", True: "x"},  # 'on:' do YAML 1.1 vira True: sort_keys estourava
], ids=["controle", "chaves_int", "chave_bool"])
def test_plano_com_chave_nao_texto_aprova(api, monkeypatch, top):
    original = runs._pipeline

    class _Relatorio:
        def __init__(self, dump):
            self._dump = dump

        def model_dump(self):
            return self._dump

    def com_top(pipeline_id):
        p, m = original(pipeline_id)
        dump = p.relatorio.model_dump()
        dump["tabelas"][0]["top"] = dict(top)
        return p.model_copy(update={"relatorio": _Relatorio(dump)}), m

    monkeypatch.setattr(runs, "_pipeline", com_top)
    rid = _com_entradas(api)
    r = api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {"referencia": REFERENCIA}})
    assert r.status_code == 200, r.text
    a = api.post(f"/api/v1/runs/{rid}/aprovar", json={"plano_sha256": r.json()["plano_sha256"]})
    assert a.status_code == 200 and a.json()["estado"] == "aprovado", a.text


# ========================================== #29 run existente sem o catálogo
def _catalogo_quebrado(monkeypatch) -> None:
    monkeypatch.setattr(runs, "carregar_pipelines", lambda *a, **k: (
        {}, {"transcriptomica_de.yaml": "contrato quebrado: passo de: params.alpha não é parâmetro de deseq2"}))


def test_run_existente_vive_com_o_catalogo_quebrado(api, lancados, monkeypatch):
    rid = _com_entradas(api)
    plano = api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {"referencia": REFERENCIA}}).json()
    assert "aviso" not in _estado(api, rid)  # controle: catálogo intacto, sem aviso
    _catalogo_quebrado(monkeypatch)
    assert _estado(api, rid)["estado"] == "planejado"
    r = api.post(f"/api/v1/runs/{rid}/aprovar", json={"plano_sha256": plano["plano_sha256"]})
    assert r.status_code == 200 and r.json()["estado"] == "aprovado", r.text
    de, volcano = plano["passos"]
    assert _executar(api, rid).status_code == 202
    _concluir(lancados[-1]["job_id"], de["saida"], {"de_table.tsv": DE_OK, "de_resumo.json": RESUMO})
    assert _executar(api, rid, "volcano").status_code == 202
    _concluir(lancados[-1]["job_id"], volcano["saida"], {"volcano.png": _png_bytes(), "volcano_resumo.json": RESUMO})
    corpo = _estado(api, rid)
    assert corpo["estado"] == "concluido" and corpo["passos_pipeline"] == ["de", "volcano"]
    r = api.post(f"/api/v1/runs/{rid}/abortar")
    assert r.status_code == 409 and _erro(r) == "run_encerrado"


def test_abortar_com_catalogo_quebrado_responde_o_que_gravou(api, monkeypatch):
    rid = _com_entradas(api)
    _catalogo_quebrado(monkeypatch)
    r = api.post(f"/api/v1/runs/{rid}/abortar")
    assert r.status_code == 200 and r.json()["estado"] == "abortado", r.text


# ================================== #5/#8 a vaga também no relançamento de 'erro'
def test_relancar_de_erro_e_aprovar_outro_ao_mesmo_tempo_nao_furam_o_limite(api, lancados, monkeypatch):
    rid_a, _, job = _de_lancado(api, lancados)
    queue.write_status(job, "error", error="R morreu")
    assert _estado(api, rid_a)["estado"] == "erro"
    rid_b = _com_entradas(api)
    sha_b = api.post(f"/api/v1/runs/{rid_b}/plano", json={"escolhas": {"referencia": REFERENCIA}}).json()["plano_sha256"]
    original = runs._runs_ativos

    def lento(exceto=""):
        r = original(exceto)
        time.sleep(0.3)
        return r

    monkeypatch.setattr(runs, "_runs_ativos", lento)
    largada = threading.Barrier(2)
    resultados: list[str] = []

    def tenta(acao):
        largada.wait()
        try:
            acao()
            resultados.append("ok")
        except runs.ErroRun as e:
            resultados.append(e.erro)

    fios = [threading.Thread(target=tenta, args=(lambda: runs.executar_passo(rid_a, "de"),)),
            threading.Thread(target=tenta, args=(lambda: runs.aprovar(rid_b, sha_b),))]
    for f in fios:
        f.start()
    for f in fios:
        f.join(30)
    assert sorted(resultados) == ["limite_de_runs_ativos", "ok"]


# ---------------------------------------------------------- resíduos da 2ª rodada (#13, #7)
def _falha_no_rq_uma_vez(monkeypatch):
    """Na 1ª chamada faz o que o queue.enqueue real faz quando o RQ levanta: o status vira
    'error' com cancelado=True e a exceção sobe. Nas seguintes, lança normalmente."""
    falso = runs.dispatch.lancar_script
    vezes = {"n": 0}

    def lancar(*a, **k):
        vezes["n"] += 1
        job_id, backend = falso(*a, **k)
        if vezes["n"] == 1:
            queue.write_status(job_id, "error", error="falha ao enfileirar: redis caiu", cancelado=True)
            raise ConnectionError("redis caiu")
        return job_id, backend

    monkeypatch.setattr(runs.dispatch, "lancar_script", lancar)


def test_job_cancelado_pelo_enqueue_nao_trava_a_reconciliacao(api, lancados, monkeypatch):
    """Tentativa 1: o RQ falha (job A = error/cancelado, nunca roda). Tentativa 2: o job B vai
    à fila e a 2ª gravação do run.json falha. O retry tem de ADOTAR B — contar A deixava o
    lançamento 'incerto' para sempre, com a vaga presa e o resultado de B invisível."""
    rid, _ = _aprovado(api)
    _falha_no_rq_uma_vez(monkeypatch)
    with pytest.raises(ConnectionError):
        runs.executar_passo(rid, "de")
    assert "de" not in runs._carregar(rid)["passos"]
    _segunda_gravacao_falha(monkeypatch)
    with pytest.raises(OSError):
        runs.executar_passo(rid, "de")
    a, b = lancados[0]["job_id"], lancados[1]["job_id"]
    assert queue.read_status(a)["cancelado"] is True
    r = _executar(api, rid)
    assert r.status_code == 409 and _erro(r) == "passo_ja_lancado" and r.json()["detail"]["job_id"] == b


def test_relancamento_com_marcador_adota_o_novo_e_guarda_o_antigo(api, lancados, monkeypatch):
    """O passo errou (job A) e é relançado; B vai à fila e a 2ª gravação falha. O retry adota B
    e mantém A no histórico (sem a exclusão dos anteriores, A virava candidato e o lançamento
    ficava incerto)."""
    rid, _ = _aprovado(api)
    assert _executar(api, rid).status_code == 202
    a = lancados[0]["job_id"]
    queue.write_status(a, "error", error="R morreu")
    assert _estado(api, rid)["estado"] == "erro"
    _segunda_gravacao_falha(monkeypatch)
    with pytest.raises(OSError):
        runs.executar_passo(rid, "de")
    b = lancados[1]["job_id"]
    r = _executar(api, rid)
    assert r.status_code == 409 and _erro(r) == "passo_ja_lancado" and r.json()["detail"]["job_id"] == b
    reg = runs._carregar(rid)["passos"]["de"]
    assert reg["job_id"] == b and reg["anteriores"] == [a]


def test_get_do_run_nao_espera_envelope_que_ainda_nao_existe(api, lancados, monkeypatch):
    """Passo rodando não tem envelope: o GET não pode reler o arquivo inexistente com sleep
    (o _ler espera um bind mount que 'sumiu') — custo multiplicado por passo e por polling."""
    rid, plano = _aprovado(api)
    assert _executar(api, rid).status_code == 202
    _concluir(lancados[0]["job_id"], plano["passos"][0]["saida"], {"de_table.tsv": DE_OK, "de_resumo.json": RESUMO})
    assert _executar(api, rid, "volcano").status_code == 202
    queue.write_status(lancados[-1]["job_id"], "running", iniciado_em=time.time())
    _estado(api, rid)
    dormidas: list[float] = []
    monkeypatch.setattr(runs.time, "sleep", lambda s: dormidas.append(s))
    for _ in range(3):
        runs.estado(rid)
    assert dormidas == []
