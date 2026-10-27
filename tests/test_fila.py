"""status.json dos jobs e o início do worker (revisão adversarial da F2, frente api).

#9/#24 — o status.json virou portão de idempotência (executar_passo) e de entrega
(envelope), mas era gravado com write_text: trunca e depois escreve. Uma leitura no meio
via JSON quebrado, read_status devolvia None, e o executar entendia "passo não lançado".
#22 — o worker grava o sha do código que vai rodar DE FATO, para o envelope comparar com
o do plano aprovado, e o tira de novo no fim: código editado DURANTE a execução derruba o job.
#10 — o worker atesta o sha256 de cada porta do plano na gravação do 'done' (`sha_saidas`).
#11 — job na fila pode ser cancelado (abortar do run): o worker confere antes de começar.

Cada portão tem canário (o que precisa recusar/pegar) e controle negativo.
"""
from __future__ import annotations

import hashlib
import json
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from omica import proveniencia
from omica.jobs import queue, script_job

JOB = "transcriptomica-deseq2-20261019-000000-abc123"


def _status(cfg) -> Path:
    return cfg.jobs_dir / JOB / "status.json"


# ------------------------------------------------------------------ gravação atômica
def test_disco_cheio_no_meio_da_gravacao_nao_corrompe_o_status(monkeypatch, cfg):
    """Canário determinístico: a gravação falha no meio (disco cheio). Com write_text
    direto, o status.json ficava pela metade e o job sumia (read_status → None)."""
    queue.write_status(JOB, "queued", cmd="Rscript x.R", run_id="r1", passo="de")
    real = Path.write_text

    def metade(self, dados, *a, **k):
        with open(self, "w", encoding="utf-8") as f:
            f.write(dados[: len(dados) // 2])
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "write_text", metade)
    with pytest.raises(OSError):
        queue.write_status(JOB, "running")
    monkeypatch.setattr(Path, "write_text", real)

    st = queue.read_status(JOB)
    assert st is not None, "status.json corrompido pela gravação interrompida"
    assert (st["status"], st["cmd"], st["run_id"]) == ("queued", "Rscript x.R", "r1")
    assert not list(_status(cfg).parent.glob("*.tmp"))  # sem temporário órfão


def test_gravacao_continua_mesclando_os_campos(cfg):
    """Controle: a troca atômica não pode perder os campos gravados antes."""
    queue.write_status(JOB, "queued", cmd="Rscript x.R", run_id="r1")
    queue.write_status(JOB, "running", iniciado_em=1.0)
    st = queue.read_status(JOB)
    assert st["status"] == "running" and st["cmd"] == "Rscript x.R" and st["iniciado_em"] == 1.0
    assert not list(_status(cfg).parent.glob("*.tmp"))


# ------------------------------------------------------------------ leitura tolerante
def test_leitura_no_meio_da_gravacao_espera_em_vez_de_dizer_ausente(cfg):
    """Canário determinístico: o leitor pega o arquivo pela metade e o escritor termina
    30 ms depois. Antes: None na hora — e o executar relançava o passo."""
    caminho = _status(cfg)
    caminho.parent.mkdir(parents=True)
    caminho.write_text('{"job_id": "x", "sta', encoding="utf-8")
    completo = {"job_id": JOB, "status": "running", "run_id": "r1"}

    def terminar():
        time.sleep(0.03)
        queue._gravar_atomico(caminho, json.dumps(completo))

    t = threading.Thread(target=terminar)
    t.start()
    try:
        assert queue.read_status(JOB) == completo
    finally:
        t.join()


def test_leitor_e_escritor_concorrentes_nunca_veem_none(cfg):
    """Canário realista: o worker grava o status em laço enquanto o web lê (polling do n8n,
    retry do executar). Antes: ~3-5% das leituras davam None."""
    queue.write_status(JOB, "running", cmd="Rscript x.R", run_id="r1", passo="de")
    parar = threading.Event()
    erros: list[BaseException] = []

    def escritor():
        i = 0
        try:
            while not parar.is_set():
                queue.write_status(JOB, "running", progresso=i)
                i += 1
        except BaseException as e:  # um replace que desiste também é falha
            erros.append(e)

    t = threading.Thread(target=escritor)
    t.start()
    leituras, nenhum = 0, 0
    try:
        fim = time.perf_counter() + 0.8
        while time.perf_counter() < fim:
            st = queue.read_status(JOB)
            leituras += 1
            if st is None or st.get("run_id") != "r1":
                nenhum += 1
    finally:
        parar.set()
        t.join()
    assert erros == []
    assert leituras > 100
    assert nenhum == 0, f"{nenhum}/{leituras} leituras sem o status do job"


def test_status_ausente_e_none_na_hora(cfg):
    """Controle: job inexistente não paga as releituras (404 rápido na API)."""
    t0 = time.perf_counter()
    assert queue.read_status("nao-existe-20261019") is None
    assert time.perf_counter() - t0 < 0.05


def test_status_corrompido_de_vez_continua_none(cfg):
    """Controle: dano persistente (não uma gravação em curso) não vira exceção — None,
    como antes, depois de um instante de releitura."""
    caminho = _status(cfg)
    caminho.parent.mkdir(parents=True)
    caminho.write_text("{quebrado", encoding="utf-8")
    assert queue.read_status(JOB) is None


# ------------------------------------------------------- proveniência no worker (#22)
@pytest.fixture()
def repo_falso(monkeypatch, tmp_path):
    """REPO_ROOT de mentira para o sha: os scripts selados do repositório nunca são tocados."""
    raiz = tmp_path / "repo"
    scripts = raiz / "modules" / "transcriptomica" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "de.R").write_text("versao aprovada\n", encoding="utf-8")
    (scripts / "_comum.py").write_text("comum\n", encoding="utf-8")
    monkeypatch.setattr(proveniencia, "REPO_ROOT", raiz)
    return scripts


CMD = "Rscript modules/transcriptomica/scripts/de.R --saida /data/omica/results/x"


def _rodar_ate_o_popen(monkeypatch) -> dict:
    """Roda script_job.executar e para no Popen, devolvendo o status visto NAQUELE instante."""
    vistos: list[dict] = []

    class _Parar(Exception):
        pass

    def popen(*a, **k):
        vistos.append(queue.read_status(JOB))
        raise _Parar

    monkeypatch.setattr(script_job, "subprocess",
                        types.SimpleNamespace(Popen=popen, PIPE=-1, STDOUT=-2))
    with pytest.raises(_Parar):
        script_job.executar(JOB)
    return vistos[0]


def test_worker_grava_o_sha_do_codigo_que_vai_rodar(monkeypatch, cfg, repo_falso):
    """Canário: o script é editado com o job na fila. O plano tem o sha antigo; o status,
    ANTES do Popen, tem que trazer o sha do arquivo que vai rodar de fato."""
    plano = proveniencia.sha_codigo("transcriptomica", proveniencia.alvo_do_cmd(CMD))
    queue.write_status(JOB, "running", cmd=CMD, modulo="transcriptomica")
    (repo_falso / "de.R").write_text("editado enquanto esperava na fila\n", encoding="utf-8")

    st = _rodar_ate_o_popen(monkeypatch)
    prov = st["proveniencia_execucao"]
    assert prov == proveniencia.sha_codigo("transcriptomica", "modules/transcriptomica/scripts/de.R")
    assert prov["sha_script"] != plano["sha_script"]  # o envelope tem como ver a troca
    assert prov["sha_comum"] == plano["sha_comum"]
    assert st["status"] == "running" and st["cmd"] == CMD


def test_worker_sem_edicao_bate_com_o_plano(monkeypatch, cfg, repo_falso):
    """Controle: sem edição, a proveniência da execução é IGUAL à do plano (mesma receita),
    senão o envelope reprovaria todo passo."""
    plano = proveniencia.sha_codigo("transcriptomica", proveniencia.alvo_do_cmd(CMD))
    queue.write_status(JOB, "running", cmd=CMD, modulo="transcriptomica")
    assert _rodar_ate_o_popen(monkeypatch)["proveniencia_execucao"] == plano


# ------------------------------------------- código editado DURANTE a execução (#22)
def _popen_falso(ao_rodar=None, saidas: dict[str, str] | None = None):
    """Popen de mentira: no meio da "execução" (ao iterar o stdout) chama `ao_rodar` e grava
    as `saidas`; termina com rc 0."""

    class _Processo:
        def __init__(self, *a, **k):
            self.stdout = self._linhas()

        def _linhas(self):
            yield "rodando\n"
            if ao_rodar is not None:
                ao_rodar()
            for caminho, conteudo in (saidas or {}).items():
                Path(caminho).parent.mkdir(parents=True, exist_ok=True)
                Path(caminho).write_text(conteudo, encoding="utf-8")
            yield "fim\n"

        def wait(self):
            return 0

    return types.SimpleNamespace(Popen=_Processo, PIPE=-1, STDOUT=-2)


@pytest.mark.parametrize("editar", [False, True], ids=["controle_sem_edicao", "editado_no_meio"])
def test_codigo_editado_durante_a_execucao_derruba_o_job(monkeypatch, cfg, repo_falso, editar):
    """Resíduo do #22: o Rscript lê o .R aos poucos. Editado no lugar no meio de um DESeq2 de
    minutos, roda o código novo da metade em diante — e a proveniência tirada no início
    continuava igual à do plano (envelope ok). O worker tira o sha de novo no fim."""
    queue.write_status(JOB, "queued", cmd=CMD, modulo="transcriptomica")

    def edita():
        (repo_falso / "de.R").write_text("editado com o job rodando\n", encoding="utf-8")

    monkeypatch.setattr(script_job, "subprocess", _popen_falso(edita if editar else None))
    if editar:
        with pytest.raises(RuntimeError, match="código mudou durante a execução"):
            queue.executar_por_caminho("omica.jobs.script_job.executar", JOB)
        st = queue.read_status(JOB)
        assert st["status"] == "error" and "mudou durante a execução" in st["error"]
    else:
        queue.executar_por_caminho("omica.jobs.script_job.executar", JOB)
        assert queue.read_status(JOB)["status"] == "done"


@pytest.mark.parametrize("editar", [False, True], ids=["controle_sem_edicao", "editado_enquanto_dorme"])
def test_processo_de_verdade_editado_enquanto_dorme_derruba_o_job(monkeypatch, cfg, repo_falso, editar):
    """O mesmo resíduo com um processo DE VERDADE (o Popen real do worker): o script avisa que
    começou e dorme até o teste liberar; no meio, o arquivo é reescrito no lugar (como o editor
    que trunca e regrava). O worker tira o sha de novo depois do fim do processo. Controle: o
    mesmo processo, sem a edição, termina 'done'."""
    raiz = repo_falso.parents[2]  # tmp_path/repo: o cwd do processo e a raiz do sha
    script, liberar = repo_falso / "dorme.py", raiz / "liberar"
    script.write_text(
        "import pathlib, time\n"
        "print('comecei', flush=True)\n"
        "limite = time.time() + 30\n"
        f"while not pathlib.Path({liberar.as_posix()!r}).exists() and time.time() < limite:\n"
        "    time.sleep(0.02)\n"
        "print('fim', flush=True)\n", encoding="utf-8")
    monkeypatch.setattr(script_job, "REPO_ROOT", raiz)
    queue.write_status(JOB, "queued", modulo="transcriptomica",
                       cmd=f"{Path(sys.executable).as_posix()} modules/transcriptomica/scripts/dorme.py")

    def vigia() -> None:  # espera o processo começar, edita (ou não) e libera
        limite = time.time() + 30
        while time.time() < limite and "comecei" not in (
                queue.log_path(JOB).read_text(encoding="utf-8") if queue.log_path(JOB).exists() else ""):
            time.sleep(0.02)
        if editar:
            script.write_text(script.read_text(encoding="utf-8") + "# editado com o job rodando\n",
                              encoding="utf-8")
        liberar.touch()

    fio = threading.Thread(target=vigia)
    fio.start()
    try:
        if editar:
            with pytest.raises(RuntimeError, match="código mudou durante a execução"):
                queue.executar_por_caminho("omica.jobs.script_job.executar", JOB)
        else:
            queue.executar_por_caminho("omica.jobs.script_job.executar", JOB)
    finally:
        fio.join(35)
    st = queue.read_status(JOB)
    log = queue.log_path(JOB).read_text(encoding="utf-8")
    assert "comecei" in log and "fim" in log  # o processo rodou inteiro nos dois casos
    if editar:
        assert st["status"] == "error" and "mudou durante a execução" in st["error"]
    else:
        assert st["status"] == "done", st


# ------------------------------------------------ o worker atesta as saídas (#10)
def test_worker_atesta_o_sha_de_cada_porta_ao_terminar(monkeypatch, cfg, repo_falso, tmp_path):
    """O sha das portas é do WORKER, na mesma gravação do 'done': nenhum leitor posterior (nem
    uma troca que preserve o mtime) define o que o job deixou. Porta não escrita = None."""
    tabela, resumo = tmp_path / "saida" / "de_table.tsv", tmp_path / "saida" / "de_resumo.json"
    queue.write_status(JOB, "queued", cmd=CMD, modulo="transcriptomica",
                       portas={"de_table": tabela.as_posix(), "de_resumo": resumo.as_posix()})
    monkeypatch.setattr(script_job, "subprocess", _popen_falso(saidas={str(tabela): "gene_id\nG1\n"}))
    queue.executar_por_caminho("omica.jobs.script_job.executar", JOB)
    st = queue.read_status(JOB)
    assert st["status"] == "done"
    esperado = hashlib.sha256(tabela.read_bytes()).hexdigest()
    assert st["sha_saidas"] == {"de_table": esperado, "de_resumo": None}


def test_job_sem_portas_nao_ganha_sha_saidas(monkeypatch, cfg, repo_falso):
    """Controle: job da UI (fora de run) não tem portas — o 'done' é o de sempre."""
    queue.write_status(JOB, "queued", cmd=CMD, modulo="transcriptomica")
    monkeypatch.setattr(script_job, "subprocess", _popen_falso())
    queue.executar_por_caminho("omica.jobs.script_job.executar", JOB)
    st = queue.read_status(JOB)
    assert st["status"] == "done" and "sha_saidas" not in st


# ------------------------------------------------- cancelar job na fila (#11)
def test_job_cancelado_na_fila_nao_roda(monkeypatch, cfg):
    """O Redis perdeu o job, ou o run foi abortado com ele na fila: cancelado, o worker que
    porventura o pegar não chama a função nem sobrescreve o motivo. Controle no MESMO teste:
    um job não cancelado roda."""
    chamadas: list[str] = []
    monkeypatch.setattr(script_job, "executar", lambda job_id: chamadas.append(job_id))
    queue.write_status(JOB, "queued", cmd="x", run_id="r1", passo="de")
    assert queue.cancelar(JOB, "cancelado ao abortar") is True
    with pytest.raises(queue.JobCancelado):
        queue.executar_por_caminho("omica.jobs.script_job.executar", JOB)
    st = queue.read_status(JOB)
    assert chamadas == [] and "iniciado_em" not in st
    assert (st["status"], st["error"], st["cancelado"]) == ("error", "cancelado ao abortar", True)
    outro = JOB.replace("abc123", "def456")
    queue.write_status(outro, "queued", cmd="x")
    queue.executar_por_caminho("omica.jobs.script_job.executar", outro)
    assert chamadas == [outro] and queue.read_status(outro)["status"] == "done"


@pytest.mark.parametrize("status", ["running", "done", "error"])
def test_cancelar_so_toca_job_na_fila(cfg, status):
    """Controle: o que já começou (ou terminou) não é cancelado — roda até o fim e segura a
    vaga até lá."""
    queue.write_status(JOB, status, iniciado_em=1.0)
    assert queue.cancelar(JOB, "cancelado ao abortar") is False
    st = queue.read_status(JOB)
    assert st["status"] == status and "cancelado" not in st


def test_cancelar_job_inexistente_nao_cria_pasta(cfg):
    assert queue.cancelar("nao-existe-20261020", "x") is False
    assert not (cfg.jobs_dir / "nao-existe-20261020").exists()


def test_cancelar_com_o_worker_no_meio_da_partida_nao_cancela(cfg):
    """O worker segura a trava do job enquanto passa de queued a running: o cancelar não pode
    gravar 'error' no meio (o job rodaria com o run fora da conta de vagas)."""
    from omica.travas import trava

    queue.write_status(JOB, "queued", cmd="x")
    with trava(cfg.jobs_dir / JOB / ".trava"):
        assert queue.cancelar(JOB, "cancelado ao abortar") is False
    assert queue.read_status(JOB)["status"] == "queued"
    assert queue.cancelar(JOB, "cancelado ao abortar") is True  # controle: trava livre


class _FilaRQ:
    def __init__(self, falha: Exception | None = None):
        self.falha, self.enfileirados, self.connection = falha, [], object()

    def enqueue(self, *a, **k):
        if self.falha:
            raise self.falha
        self.enfileirados.append((a, k))


def test_enqueue_que_falha_no_rq_nao_deixa_queued_eterno(monkeypatch, cfg):
    """O status 'queued' é gravado antes de o job ir ao RQ: se o enqueue do RQ falha, ninguém
    pega o job, e o 'queued' prendia RAM e vaga para sempre."""
    monkeypatch.setattr(queue, "_rq_queue", lambda fila=queue.FILA_DEFAULT: _FilaRQ(ConnectionError("redis caiu")))
    with pytest.raises(ConnectionError):
        queue.enqueue("omica.jobs.script_job.executar", "transcriptomica-deseq2", run_id="r1", passo="de")
    (pasta,) = cfg.jobs_dir.iterdir()
    st = queue.read_status(pasta.name)
    assert st["status"] == "error" and "falha ao enfileirar" in st["error"] and st["run_id"] == "r1"


def test_enqueue_que_falha_no_rq_nao_roda_se_o_job_chegou_ao_redis(monkeypatch, cfg):
    """O comando chega ao Redis e só a RESPOSTA se perde: o enqueue levanta, quem chamou dá o
    lançamento por falho (o executar do run desfaz o marcador e o retry lança outro na MESMA
    pasta de saída), mas o job está na fila. O worker que o pegar não pode rodá-lo. Controle:
    o job enfileirado sem falha roda (test_job_cancelado_na_fila_nao_roda, 2ª metade)."""
    rodou: list[str] = []
    monkeypatch.setattr(script_job, "executar", lambda job_id: rodou.append(job_id))

    class _RespostaPerdida(_FilaRQ):
        def enqueue(self, *a, **k):
            self.enfileirados.append((a, k))  # chegou ao Redis...
            raise ConnectionError("conexão caiu antes da resposta")  # ...e a resposta não

    fila = _RespostaPerdida()
    monkeypatch.setattr(queue, "_rq_queue", lambda fila_=queue.FILA_DEFAULT: fila)
    with pytest.raises(ConnectionError):
        queue.enqueue("omica.jobs.script_job.executar", "transcriptomica-deseq2", run_id="r1", passo="de")
    (args, _), = fila.enfileirados
    funcao, *resto = args
    with pytest.raises(queue.JobCancelado):
        funcao(*resto)  # o worker do RQ pega o job: executar_por_caminho(func_path, job_id)
    assert rodou == [] and queue.read_status(resto[1])["status"] == "error"


def test_enqueue_usa_o_mesmo_id_no_rq_e_cancelar_tira_da_fila(monkeypatch, cfg):
    """Controle do acima, e o caminho do cancelar com Redis: o id do RQ é o nosso, e o
    cancelar faz Job.fetch(job_id).cancel()."""
    import rq.job

    fila = _FilaRQ()
    monkeypatch.setattr(queue, "_rq_queue", lambda fila_=queue.FILA_DEFAULT: fila)
    job_id, backend = queue.enqueue("omica.jobs.script_job.executar", "transcriptomica-deseq2")
    assert backend == "rq" and fila.enfileirados[0][1]["job_id"] == job_id
    assert queue.read_status(job_id)["status"] == "queued"
    cancelados: list[str] = []

    class _Job:
        def __init__(self, jid):
            self.jid = jid

        def cancel(self):
            cancelados.append(self.jid)

    monkeypatch.setattr(rq.job.Job, "fetch", classmethod(lambda cls, jid, connection=None: _Job(jid)))
    assert queue.cancelar(job_id, "cancelado ao abortar") is True
    assert cancelados == [job_id] and queue.read_status(job_id)["cancelado"] is True
