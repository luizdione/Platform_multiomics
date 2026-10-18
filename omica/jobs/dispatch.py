"""dispatch.py — do manifest ao job: resolve o ScriptSpec e enfileira na fila certa.

Arquitetura da Fase 2 (sem docker.sock, anti-lição nº 11): cada runtime
`image:<nome>` mapeia para uma FILA RQ própria, consumida por um worker cujo
container É aquela imagem (as ferramentas estão no PATH do próprio worker).
Nada de docker-dentro-de-docker fora do futuro profile nfcore.
"""
from __future__ import annotations

import posixpath
import re
import shlex
from typing import Any, Optional

from omica.config import get_config
from omica.jobs import queue
from omica.registry import Manifest, ParamSpec, ScriptSpec, load_registry

# imagem → fila RQ. Worker leve (omica-web) consome "omica"; o worker
# bioinformático (omica-bioinfo) consome "bioinfo".
FILA_POR_IMAGEM = {
    "omica-web": queue.FILA_DEFAULT,
    "omica-worker": queue.FILA_DEFAULT,
    "omica-bioinfo": "bioinfo",
    "omica-vep": "vep",
    "omica-methyl": "methyl",
    "omica-ml": "ml",
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


# Um valor de parâmetro vira UM token do argv: o cmd passa por shlex.split no
# script_job. O padrão antigo (`^[A-Za-z0-9_\-./\\:= ]+$`) tinha dois furos de
# injeção, achados nas auditorias de 2026-09-14 e 2026-10-18:
#   - aceitava espaço: `x --outra-flag` virava dois argumentos;
#   - não barrava '-' inicial: `--help` ou `--construir-indice`, SEM espaço nenhum,
#     já eram lidos pelo argparse do script como flag.
# Também fica de fora a barra invertida, que o shlex POSIX come em silêncio.
# A '/' inicial é permitida porque os defaults usam caminhos do container
# (/data/1000bg_vcf/...); para ONDE um caminho pode apontar quem decide é
# _validar_caminho — `/etc/passwd` passa no formato, mas não na raiz.
_TOKEN_SEGURO = re.compile(r"^[A-Za-z0-9_/][A-Za-z0-9_\-./:=]*$")
_INTEIRO = re.compile(r"^-?\d+$")
_DECIMAL = re.compile(r"^-?(\d+\.?\d*|\.\d+)([eE]-?\d+)?$")
_RAIZ_MODULO = re.compile(r"^modules/[A-Za-z0-9_]+/(demo_data|data)(/|$)")


def _validar_caminho(caminho: str, rotulo: str) -> None:
    """Checagem LÉXICA (posix): os caminhos dos manifests são do container, e no host
    (testes, CI) `/data/...` não existe — resolver no sistema de arquivos daria
    falso-negativo. Raízes aceitas: dados curados do repo (modules/<m>/demo_data|data),
    os dados pesados montados (/data) e o diretório de dados da plataforma."""
    if ".." in caminho:
        raise ValueError(f"caminho com '..' em {rotulo}: {caminho!r}")
    v = posixpath.normpath(caminho)
    raiz_dados = get_config().data_dir.as_posix().rstrip("/")
    if (_RAIZ_MODULO.match(v) or v == "/data" or v.startswith("/data/")
            or v == raiz_dados or v.startswith(raiz_dados + "/")):
        return
    raise ValueError(
        f"caminho fora das raízes permitidas em {rotulo}: {caminho!r} "
        "(use modules/<módulo>/demo_data|data/..., /data/... ou a pasta de dados da plataforma)")


def _validar_valor(param: ParamSpec, bruto: str) -> str:
    if not bruto:
        raise ValueError(f"parâmetro obrigatório vazio: {param.rotulo} ({param.name})")
    if param.tipo == "inteiro":
        if not _INTEIRO.match(bruto):
            raise ValueError(f"{param.rotulo} precisa ser inteiro: {bruto!r}")
        return bruto
    if param.tipo == "decimal":
        if not _DECIMAL.match(bruto):
            raise ValueError(f"{param.rotulo} precisa ser número: {bruto!r}")
        return bruto
    if not _TOKEN_SEGURO.match(bruto):
        dica = " — no container, E:\\data é montado em /data" if "\\" in bruto else ""
        raise ValueError(
            f"valor inválido para {param.rotulo}: {bruto!r} (um único argumento: sem espaço, "
            f"sem barra invertida, sem '-' no início){dica}")
    if param.tipo == "grupo" or ("=" in bruto and "/" in bruto):
        nome, _, caminho = bruto.partition("=")  # convenção Nome=/caminho/lista.txt
        if not nome or not caminho:
            raise ValueError(f"{param.rotulo} deve ser Nome=/caminho/arquivo: {bruto!r}")
        _validar_caminho(caminho, param.rotulo)
    elif param.tipo in ("caminho", "dir") or "/" in bruto:
        _validar_caminho(bruto, param.rotulo)
    return bruto


def montar_cmd(spec: ScriptSpec, valores: Optional[dict[str, str]] = None) -> str:
    """Substitui os placeholders do manifest. {dados} = OMICA_DATA; os demais
    vêm dos params declarados no spec (default < valor do formulário), sempre
    sanitizados — o usuário preenche VALORES, nunca shell."""
    cfg = get_config()
    contexto: dict[str, str] = {"dados": str(cfg.data_dir)}
    valores = valores or {}
    for param in spec.params:
        bruto = (valores.get(param.name) or param.default).strip()
        contexto[param.name] = _validar_valor(param, bruto)
    cmd = spec.cmd.format(**contexto)
    # Invariante estrutural, a 2ª trava: nenhum VALOR pode mudar quantos argumentos o
    # comando tem. Compara o cmd com os valores reais contra o mesmo cmd com cada
    # placeholder trocado por um token neutro.
    neutro = {k: "X" for k in contexto}
    com_valores = spec.cmd.format(**{**neutro, **{k: v for k, v in contexto.items() if k != "dados"}})
    if len(shlex.split(com_valores)) != len(shlex.split(spec.cmd.format(**neutro))):
        raise ValueError("um parâmetro alteraria a estrutura do comando (nº de argumentos)")
    return cmd


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
