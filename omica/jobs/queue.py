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

FILA = "omica"


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


def _rq_queue():
    url = get_config().redis_url
    if not url:
        return None
    try:
        from redis import Redis
        from rq import Queue

        conexao = Redis.from_url(url, socket_connect_timeout=2)
        conexao.ping()
        return Queue(FILA, connection=conexao)
    except Exception:
        return None


def enqueue(func_path: str, prefixo: str, mem_gb: float = 0.5,
            timeout_s: int = 3600, **meta: Any) -> tuple[str, str]:
    """Enfileira (RQ) ou dispara (thread) um job. Retorna (job_id, backend).

    Levanta guarda_ram.RecusadoPorRAM se não couber no orçamento de RAM.
    """
    guarda_ram.autorizar(mem_gb)
    job_id = novo_job_id(prefixo)
    write_status(job_id, "queued", mem_gb=mem_gb, func=func_path, **meta)
    q = _rq_queue()
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
