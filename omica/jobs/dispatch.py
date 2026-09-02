"""dispatch.py — do manifest ao job: resolve o ScriptSpec e enfileira na fila certa.

Arquitetura da Fase 2 (sem docker.sock, anti-lição nº 11): cada runtime
`image:<nome>` mapeia para uma FILA RQ própria, consumida por um worker cujo
container É aquela imagem (as ferramentas estão no PATH do próprio worker).
Nada de docker-dentro-de-docker fora do futuro profile nfcore.
"""
from __future__ import annotations

import re
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
    "omica-vep": "vep",
    "omica-methyl": "methyl",
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


_PARAM_SEGURO = re.compile(r"^[A-Za-z0-9_\-./\\:= ]+$")  # '=' p/ convenção Nome=arquivo


def montar_cmd(spec: ScriptSpec, valores: Optional[dict[str, str]] = None) -> str:
    """Substitui os placeholders do manifest. {dados} = OMICA_DATA; os demais
    vêm dos params declarados no spec (default < valor do formulário), sempre
    sanitizados — o usuário preenche VALORES, nunca shell."""
    cfg = get_config()
    contexto: dict[str, str] = {"dados": str(cfg.data_dir)}
    valores = valores or {}
    for param in spec.params:
        bruto = (valores.get(param.name) or param.default).strip()
        if not bruto:
            raise ValueError(f"parâmetro obrigatório vazio: {param.rotulo} ({param.name})")
        if ".." in bruto or not _PARAM_SEGURO.match(bruto):
            raise ValueError(f"valor inválido para {param.rotulo}: {bruto!r}")
        contexto[param.name] = bruto
    return spec.cmd.format(**contexto)


def lancar_script(mod_id: str, script_key: str,
                  valores: Optional[dict[str, str]] = None,
                  extra_meta: Optional[dict[str, Any]] = None) -> tuple[str, str]:
    """Enfileira um script de módulo. Retorna (job_id, backend).

    Levanta RecusadoPorRAM (guarda), FilaIndisponivel (fila especializada sem
    Redis), RuntimeIndisponivel (runtime de fase futura) ou ValueError (param).
    """
    m, spec = _resolver(mod_id, script_key)
    fila = fila_para_runtime(spec.runtime)
    cmd = montar_cmd(spec, valores)
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


def lancar_workflow(mod_id: str, wf_id: str) -> tuple[str, list[str], str]:
    """Enfileira um workflow inteiro (passos encadeados por depends_on).

    Retorna (wf_run_id, job_ids, backend)."""
    reg = load_registry()
    m = reg.por_id(mod_id)
    if m is None:
        raise KeyError(f"módulo desconhecido: {mod_id}")
    wf = next((w for w in m.workflows if w.id == wf_id), None)
    if wf is None:
        raise KeyError(f"workflow desconhecido: {mod_id}/{wf_id}")
    itens = []
    for passo in wf.passos:
        _, spec = _resolver(mod_id, passo)
        itens.append({
            "func_path": "omica.jobs.script_job.executar",
            "prefixo": f"{mod_id}-{passo}",
            "mem_gb": spec.mem_gb,
            "fila": fila_para_runtime(spec.runtime),
            "timeout_s": 6 * 3600,
            "meta": {"cmd": montar_cmd(spec), "modulo": mod_id, "script": passo,
                     "titulo": spec.titulo, "runtime": spec.runtime},
        })
    return queue.enqueue_cadeia(itens, wf_meta={
        "prefixo": f"wf-{mod_id}-{wf_id}", "modulo": mod_id,
        "workflow": wf_id, "titulo": wf.titulo,
    })
