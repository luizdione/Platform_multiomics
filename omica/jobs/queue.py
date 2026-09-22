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

import importlib
import json
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Optional

from omica.config import get_config
from omica.jobs import guarda_ram

FILA_DEFAULT = "omica"


class FilaIndisponivel(RuntimeError):
    """Fila especializada pedida sem Redis alcançável — não há fallback seguro:
    a thread local roda no container web, que NÃO tem as ferramentas bioinfo."""


def _job_dir(job_id: str) -> Path:
    d = get_config().jobs_dir / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_status(job_id: str, status: str, **extra: Any) -> None:
    payload = {
        "job_id": job_id,
        "status": status,
        "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        **extra,
    }
    caminho = _job_dir(job_id) / "status.json"
    anterior: dict[str, Any] = {}
    if caminho.exists():
        try:
            anterior = json.loads(caminho.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            anterior = {}
    anterior.update(payload)
    caminho.write_text(json.dumps(anterior, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def read_status(job_id: str) -> Optional[dict[str, Any]]:
    caminho = get_config().jobs_dir / job_id / "status.json"
    if not caminho.exists():
        return None
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def append_log(job_id: str, linha: str) -> None:
    with (_job_dir(job_id) / "job.log").open("a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%H:%M:%S')}] {linha}\n")


def log_path(job_id: str) -> Path:
    return get_config().jobs_dir / job_id / "job.log"


def novo_job_id(prefixo: str) -> str:
    return f"{prefixo}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def executar_por_caminho(func_path: str, job_id: str) -> None:
    """Resolve 'pacote.modulo.funcao' e executa. Usado pelo RQ e pelo fallback."""
    mod_name, _, func_name = func_path.rpartition(".")
    func = getattr(importlib.import_module(mod_name), func_name)
    write_status(job_id, "running")
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
        q.enqueue(executar_por_caminho, func_path, job_id, job_timeout=timeout_s)
        return job_id, "rq"
    t = threading.Thread(target=_thread_segura, args=(func_path, job_id), daemon=True)
    t.start()
    return job_id, "thread"


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
