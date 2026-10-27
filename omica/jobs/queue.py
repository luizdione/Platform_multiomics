"""queue.py — execução assíncrona de jobs, herdada do padrão do bonder_platform.

Dois backends, escolhidos automaticamente:
  - RQ + Redis (OMICA_REDIS_URL definido e alcançável): fila real, consumida
    pelo container omica-worker (caminho normal no docker compose);
  - Thread local (fallback): roda no próprio processo web — útil em testes e
    em uso mono-usuário sem Redis.

O estado de cada job é persistido em <jobs_dir>/<job_id>/status.json e o log em
job.log — arquivos no volume compartilhado, para que web e worker se enxerguem
e o console SSE só precise ler arquivo (nada em memória de processo).
"""
from __future__ import annotations

import contextlib
import importlib
import json
import os
import re
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Optional

from omica.config import get_config
from omica.jobs import guarda_ram
from omica.travas import TravaOcupada, trava

FILA_DEFAULT = "omica"
# O RQ só aceita id de job com letras, dígitos, '_' e '-' (rq.job.JOB_ID_PATTERN). Com o
# MESMO id no RQ e no status.json, cancelar um job na fila é um Job.fetch(job_id).
_ID_RQ = re.compile(r"[A-Za-z0-9_-]+")

# Releitura de status.json (revisão F2, #9/#24): no host Windows o leitor pode pegar o
# arquivo no instante do os.replace (PermissionError) — no Linux o rename é atômico e a
# primeira leitura já basta. 12 tentativas de 1 ms × i somam ~80 ms no pior caso: curto
# o bastante para o console SSE, que chama read_status de dentro do event loop.
_TENTATIVAS_LEITURA = 12
# os.replace no Windows falha com PermissionError enquanto um leitor está com o destino
# aberto; 40 tentativas de 2 ms × i somam ~1,6 s antes de desistir.
_TENTATIVAS_TROCA = 40


class FilaIndisponivel(RuntimeError):
    """Fila especializada pedida sem Redis alcançável — não há fallback seguro:
    a thread local roda no container web, que NÃO tem as ferramentas bioinfo."""


class JobCancelado(RuntimeError):
    """O job foi cancelado (ex.: run abortado) enquanto esperava na fila: nada rodou."""


def _job_dir(job_id: str) -> Path:
    d = get_config().jobs_dir / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ler_status(caminho: Path) -> Optional[dict[str, Any]]:
    """None = arquivo AUSENTE (resposta imediata) ou ilegível mesmo depois de reler.

    Releitura com espera curta: um leitor que cai no meio de uma troca (PermissionError no
    Windows) ou num arquivo pela metade não pode concluir "não existe". Esse None era o que
    fazia o executar relançar um passo em andamento e o envelope dar falha terminal falsa."""
    if not caminho.exists():
        return None
    for i in range(_TENTATIVAS_LEITURA):
        try:
            dados = json.loads(caminho.read_text(encoding="utf-8"))
            if isinstance(dados, dict):
                return dados
        except (OSError, ValueError):  # ValueError cobre JSONDecodeError e UnicodeDecodeError
            pass
        time.sleep(0.001 * (i + 1))
    return None


def _gravar_atomico(caminho: Path, texto: str) -> None:
    """Grava num temporário da MESMA pasta e troca com os.replace (mesmo padrão de
    runs._gravar). O write_text direto truncava o arquivo antes de escrever: quem lesse no
    meio via JSON quebrado, e um disco cheio no meio deixava o status corrompido de vez."""
    tmp = caminho.with_name(f"{caminho.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(texto, encoding="utf-8")
        for i in range(_TENTATIVAS_TROCA):
            try:
                os.replace(tmp, caminho)
                return
            except PermissionError:  # Windows: destino aberto por um leitor
                if i == _TENTATIVAS_TROCA - 1:
                    raise
                time.sleep(0.002 * (i + 1))
    finally:  # depois do replace bem-sucedido ele já não existe; falha aqui não mascara a original
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


def write_status(job_id: str, status: str, **extra: Any) -> None:
    payload = {
        "job_id": job_id,
        "status": status,
        "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        **extra,
    }
    caminho = _job_dir(job_id) / "status.json"
    # Ilegível mesmo depois de reler (só com dano externo, já que a gravação é atômica)
    # recomeça do zero, como antes: sem run_id/passo o envelope falha FECHADO.
    anterior: dict[str, Any] = _ler_status(caminho) or {}
    anterior.update(payload)
    _gravar_atomico(caminho, json.dumps(anterior, indent=2, ensure_ascii=False, default=str))


def read_status(job_id: str) -> Optional[dict[str, Any]]:
    return _ler_status(get_config().jobs_dir / job_id / "status.json")


def append_log(job_id: str, linha: str) -> None:
    with (_job_dir(job_id) / "job.log").open("a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%H:%M:%S')}] {linha}\n")


def log_path(job_id: str) -> Path:
    return get_config().jobs_dir / job_id / "job.log"


def novo_job_id(prefixo: str) -> str:
    return f"{prefixo}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def _trava_do_job(job_id: str) -> Path:
    return _job_dir(job_id) / ".trava"


def _comecar(job_id: str) -> None:
    """queued → running. É a MESMA decisão do `cancelar` (queued → error), e as duas correm
    sob a trava do job: sem ela, o worker podia ler 'queued', o abortar cancelar e o worker
    gravar 'running' por cima — o job rodava com o run já fora da conta de vagas. Trava
    ocupada além da espera (só com dano externo: o cancelar a segura por milissegundos) não
    prende o job: começa assim mesmo, e o `cancelado` ainda é conferido."""
    with contextlib.ExitStack() as pilha:
        with contextlib.suppress(TravaOcupada):
            pilha.enter_context(trava(_trava_do_job(job_id), 10.0))
        atual = read_status(job_id) or {}
        if atual.get("cancelado"):
            append_log(job_id, f"job cancelado antes de começar ({atual.get('error')}): nada foi executado")
            raise JobCancelado(job_id)
        # `iniciado_em` (epoch) é o que permite à API v1 distinguir um artefato escrito POR
        # ESTE job de um que sobrou de uma rodada anterior no mesmo caminho (EXP-2, revisão F1).
        write_status(job_id, "running", iniciado_em=time.time())


def executar_por_caminho(func_path: str, job_id: str) -> None:
    """Resolve 'pacote.modulo.funcao' e executa. Usado pelo RQ e pelo fallback. Job
    cancelado na fila levanta JobCancelado SEM tocar no status (que já diz 'error' com o
    motivo) e sem rodar nada: o RQ o dá por falho e a cadeia (enqueue_cadeia) não segue."""
    mod_name, _, func_name = func_path.rpartition(".")
    func = getattr(importlib.import_module(mod_name), func_name)
    _comecar(job_id)
    try:
        func(job_id)
        # a própria função marca "done" com os campos dela; garante o mínimo:
        atual = read_status(job_id) or {}
        if atual.get("status") == "running":
            write_status(job_id, "done")
    except Exception as e:  # nunca deixar job em estado ambíguo (padrão bonder)
        append_log(job_id, f"ERRO: {e}")
        write_status(job_id, "error", error=str(e), traceback=traceback.format_exc())
        raise


def _rq_queue(fila: str = FILA_DEFAULT):
    url = get_config().redis_url
    if not url:
        return None
    try:
        from redis import Redis
        from rq import Queue

        conexao = Redis.from_url(url, socket_connect_timeout=2)
        conexao.ping()
        return Queue(fila, connection=conexao)
    except Exception:
        return None


def enqueue(func_path: str, prefixo: str, mem_gb: float = 0.5,
            timeout_s: int = 3600, fila: str = FILA_DEFAULT, **meta: Any) -> tuple[str, str]:
    """Enfileira (RQ) ou dispara (thread) um job. Retorna (job_id, backend).

    Levanta guarda_ram.RecusadoPorRAM se não couber no orçamento de RAM, e
    FilaIndisponivel se uma fila especializada (ex.: bioinfo) for pedida sem
    Redis — o fallback de thread só vale para a fila default.
    """
    guarda_ram.autorizar(mem_gb)
    q = _rq_queue(fila)
    if q is None and fila != FILA_DEFAULT:
        raise FilaIndisponivel(
            f"fila {fila!r} exige Redis + worker dedicado (docker compose up); "
            "sem Redis só a fila default roda em thread local."
        )
    job_id = novo_job_id(prefixo)
    write_status(job_id, "queued", mem_gb=mem_gb, func=func_path, fila=fila, **meta)
    if q is not None:
        extra = {"job_id": job_id} if _ID_RQ.fullmatch(job_id) else {}
        try:
            q.enqueue(executar_por_caminho, func_path, job_id, job_timeout=timeout_s, **extra)
        except Exception as e:
            # O status já diz 'queued' e nenhum worker vai pegar o job: sem isto ele ficava
            # 'queued' para sempre, segurando o orçamento de RAM e a vaga de run. Marcado
            # `cancelado` também: se o comando chegou ao Redis e só a resposta se perdeu, o
            # worker que pegar o job não o roda (_comecar) — quem chamou já deu o lançamento
            # por falho e pode lançar outro na MESMA pasta de saída (revisão F2, #13).
            write_status(job_id, "error", error=f"falha ao enfileirar: {e}", cancelado=True)
            raise
        return job_id, "rq"
    t = threading.Thread(target=_thread_segura, args=(func_path, job_id), daemon=True)
    t.start()
    return job_id, "thread"


def _cancelar_no_rq(job_id: str) -> None:
    """Tira o job da fila do RQ, quando dá. Job enfileirado com id próprio do RQ (antes de o
    id do RQ ser o nosso), Redis fora ou job já perdido: nada a fazer aqui — o `cancelado` do
    status.json é o que impede o worker de rodá-lo (`_comecar`)."""
    q = _rq_queue()
    if q is None:
        return
    with contextlib.suppress(Exception):
        from rq.job import Job

        Job.fetch(job_id, connection=q.connection).cancel()


def cancelar(job_id: str, motivo: str) -> bool:
    """Cancela um job que AINDA ESTÁ NA FILA: status 'error' com `cancelado` e o motivo, e o
    job sai do RQ. Devolve True se cancelou. Job que já começou (running), terminou, tem status
    ilegível ou cuja trava está ocupada não é tocado (False): quem roda termina sozinho.

    Também é a saída do job PERDIDO na fila — o Redis do compose não tem volume, e um
    `compose down/up` ou a queda de energia apagam a fila com o status.json ainda em 'queued',
    que nunca vira órfão (uma fila de worker único pode esperar horas atrás de um job longo).
    Sem Redis o cancelamento vale igual: o worker confere `cancelado` antes de começar."""
    if not (get_config().jobs_dir / job_id / "status.json").exists():
        return False
    try:
        with trava(_trava_do_job(job_id), 2.0):
            atual = read_status(job_id)
            if not atual or atual.get("status") != "queued":
                return False
            write_status(job_id, "error", error=motivo, cancelado=True, cancelado_em=time.time())
    except TravaOcupada:
        return False
    _cancelar_no_rq(job_id)
    append_log(job_id, f"cancelado na fila: {motivo}")
    return True


def _thread_segura(func_path: str, job_id: str) -> None:
    try:
        executar_por_caminho(func_path, job_id)
    except Exception:
        pass  # status/log já registrados por executar_por_caminho


def enqueue_cadeia(itens: list[dict[str, Any]], wf_meta: dict[str, Any]) -> tuple[str, list[str], str]:
    """Enfileira uma CADEIA de jobs em que cada um depende do anterior.

    itens: [{func_path, prefixo, mem_gb, fila, timeout_s, meta:{...}}, ...]
    Backend RQ: usa depends_on (o passo N só roda se o N-1 terminar OK).
    Fallback thread (só fila default): executa sequencial numa única thread e
    interrompe na primeira falha, marcando os passos restantes como error.
    Guarda de RAM: autoriza pelo PICO (max de mem_gb) — os passos são
    sequenciais, nunca simultâneos (a soma no orçamento fica conservadora).

    Retorna (wf_run_id, [job_ids], backend) e grava o run em
    <work_dir>/workflows/<wf_run_id>.json.
    """
    cfg = get_config()
    guarda_ram.autorizar(max(i.get("mem_gb", 0.5) for i in itens))
    filas = {i.get("fila", FILA_DEFAULT) for i in itens}
    precisa_rq = filas != {FILA_DEFAULT}
    q_teste = _rq_queue(next(iter(filas)))
    if q_teste is None and precisa_rq:
        raise FilaIndisponivel(
            "workflow usa filas especializadas e exige Redis + workers dedicados "
            "(docker compose up)."
        )
    wf_run_id = novo_job_id(wf_meta.get("prefixo", "wf"))
    job_ids: list[str] = []
    for item in itens:
        jid = novo_job_id(item["prefixo"])
        job_ids.append(jid)
        write_status(jid, "queued", mem_gb=item.get("mem_gb", 0.5),
                     func=item["func_path"], fila=item.get("fila", FILA_DEFAULT),
                     wf_run_id=wf_run_id, **item.get("meta", {}))

    backend = "thread"
    if q_teste is not None:
        backend = "rq"
        anterior = None
        for item, jid in zip(itens, job_ids):
            q = _rq_queue(item.get("fila", FILA_DEFAULT))
            anterior = q.enqueue(
                executar_por_caminho, item["func_path"], jid,
                job_timeout=item.get("timeout_s", 3600), depends_on=anterior,
            )
    else:
        def _sequencial() -> None:
            falhou = False
            for item, jid in zip(itens, job_ids):
                if falhou:
                    write_status(jid, "error", error="passo anterior da cadeia falhou")
                    continue
                try:
                    executar_por_caminho(item["func_path"], jid)
                except Exception:
                    falhou = True

        threading.Thread(target=_sequencial, daemon=True).start()

    registro = cfg.work_dir / "workflows"
    registro.mkdir(parents=True, exist_ok=True)
    (registro / f"{wf_run_id}.json").write_text(
        json.dumps({"wf_run_id": wf_run_id, "backend": backend,
                    "criado": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "jobs": job_ids, **wf_meta}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return wf_run_id, job_ids, backend


def ler_workflow_run(wf_run_id: str) -> Optional[dict[str, Any]]:
    caminho = get_config().work_dir / "workflows" / f"{wf_run_id}.json"
    if not caminho.exists():
        return None
    try:
        run = json.loads(caminho.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    passos = [read_status(j) or {"job_id": j, "status": "queued"} for j in run["jobs"]]
    # No RQ, o passo cuja dependência falhou fica DEFERIDO para sempre: o
    # status.json congela em "queued", a UI mostra a cadeia "em andamento" sem
    # nada rodando e — pior — a guarda de RAM continua somando o mem_gb desse
    # fantasma, apertando o orçamento de todos os jobs seguintes. O fallback de
    # thread já marcava esses passos como erro; aqui o RQ passa a fazer o mesmo.
    ja_falhou = False
    for passo in passos:
        if ja_falhou and passo.get("status") == "queued":
            motivo = "passo anterior da cadeia falhou (dependência cancelada)"
            write_status(passo["job_id"], "error", error=motivo)
            passo["status"], passo["error"] = "error", motivo
        elif passo.get("status") == "error":
            ja_falhou = True
    estados = [p["status"] for p in passos]
    if "error" in estados:
        agregado = "error"
    elif all(e == "done" for e in estados):
        agregado = "done"
    elif "running" in estados or "done" in estados:
        agregado = "running"
    else:
        agregado = "queued"
    run["passos"] = passos
    run["status"] = agregado
    return run


def listar_jobs(limite: int = 50) -> list[dict[str, Any]]:
    cfg = get_config()
    if not cfg.jobs_dir.exists():
        return []
    pastas = sorted(cfg.jobs_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    jobs = []
    for pasta in pastas[:limite]:
        status = read_status(pasta.name)
        if status:
            jobs.append(status)
    return jobs
