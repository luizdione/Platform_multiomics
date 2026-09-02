"""dispatch.py — do manifest ao job: resolve o ScriptSpec e enfileira na fila certa.

Arquitetura da Fase 2 (sem docker.sock, anti-lição nº 11): cada runtime
`image:<nome>` mapeia para uma FILA RQ própria, consumida por um worker cujo
container É aquela imagem (as ferramentas estão no PATH do próprio worker).
Nada de docker-dentro-de-docker fora do futuro profile nfcore.
"""
from __future__ import annotations

from typing import Any, Optional

from omica.config import get_config
from omica.jobs import queue
from omica.registry import Manifest, ScriptSpec, load_registry

# imagem → fila RQ. Worker leve (omica-web) consome "omica"; o worker
# bioinformático (omica-bioinfo) consome "bioinfo".
FILA_POR_IMAGEM = {
    "omica-web": queue.FILA_DEFAULT,
    "omica-worker": queue.FILA_DEFAULT,
    "omica-bioinfo": "bioinfo",
}


class RuntimeIndisponivel(RuntimeError):
    """Runtime declarado no manifest ainda não tem executor nesta fase."""


def _resolver(mod_id: str, script_key: str) -> tuple[Manifest, ScriptSpec]:
    reg = load_registry()
    m = reg.por_id(mod_id)
    if m is None:
        raise KeyError(f"módulo desconhecido: {mod_id}")
    spec = next((s for s in m.scripts if s.key == script_key), None)
    if spec is None:
        raise KeyError(f"script desconhecido: {mod_id}/{script_key}")
    return m, spec


def fila_para_runtime(runtime: str) -> str:
    if runtime.startswith("image:"):
        imagem = runtime.split(":", 1)[1]
        try:
            return FILA_POR_IMAGEM[imagem]
        except KeyError:
            raise RuntimeIndisponivel(
                f"imagem {imagem!r} sem fila mapeada (FILA_POR_IMAGEM em dispatch.py)"
            ) from None
    if runtime.startswith("conda:"):
        raise RuntimeIndisponivel("runtime conda: chega na fase 5 (clawbio no host)")
    if runtime.startswith("profile:"):
        raise RuntimeIndisponivel("runtime profile: chega na fase 4 (nf-core)")
    raise RuntimeIndisponivel(f"runtime desconhecido: {runtime!r}")


def montar_cmd(spec: ScriptSpec) -> str:
    """Substitui os placeholders do manifest. {dados} = OMICA_DATA (raiz dos
    dados pesados); caminhos relativos no cmd são relativos à raiz do repo."""
    cfg = get_config()
    return spec.cmd.format(dados=str(cfg.data_dir))


def lancar_script(mod_id: str, script_key: str,
                  extra_meta: Optional[dict[str, Any]] = None) -> tuple[str, str]:
    """Enfileira um script de módulo. Retorna (job_id, backend).

    Levanta RecusadoPorRAM (guarda), FilaIndisponivel (fila especializada sem
    Redis) ou RuntimeIndisponivel (runtime de fase futura).
    """
    m, spec = _resolver(mod_id, script_key)
    fila = fila_para_runtime(spec.runtime)
    cmd = montar_cmd(spec)
    return queue.enqueue(
        "omica.jobs.script_job.executar",
        prefixo=f"{mod_id}-{script_key}",
        mem_gb=spec.mem_gb,
        timeout_s=6 * 3600,
        fila=fila,
        cmd=cmd,
        modulo=mod_id,
        script=script_key,
        titulo=spec.titulo,
        runtime=spec.runtime,
        **(extra_meta or {}),
    )
