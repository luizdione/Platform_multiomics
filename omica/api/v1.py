"""v1.py — API JSON da plataforma para orquestradores externos (ex.: n8n).

As rotas HTML ficam como estão (a UI e o plugin Bioinfo dependem delas). Esta API existe
porque elas não servem a um orquestrador: lançar job devolvia 303 com o job_id só no header
`Location`, erros de RAM/parâmetro também voltavam 303 com `?aviso=`, e um corpo JSON era
ignorado em silêncio (o script rodava com os defaults).

Contrato:
- `Authorization: Bearer <OMICA_API_TOKEN>`. Sem token configurado, TUDO responde 503 —
  falha fechada: a API nunca fica aberta por esquecimento.
- JSON e códigos HTTP de verdade: 401, 404, 422, 429 (+ Retry-After), 503.
- Nenhuma rota aceita caminho livre nem comando: só o que está no catálogo.
"""
from __future__ import annotations

import hashlib
import re
import secrets
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from omica import __version__
from omica.config import get_config
from omica.jobs import dispatch, queue
from omica.registry import load_registry
from omica.saude import dados_saude, estado_gpu

# Formato de job_id gerado por queue.novo_job_id: sem ponto nem barra. read_status() monta o
# caminho direto com o id — validar AQUI é o que impede `../..` de atravessar diretório.
_ID_JOB = re.compile(r"^[A-Za-z0-9_\-]{1,160}$")
_MAX_SHA_BYTES = 512 * 1024 * 1024  # acima disso não calcula sha256 na hora


def exigir_token(authorization: Optional[str] = Header(default=None)) -> None:
    esperado = get_config().api_token
    if not esperado:
        raise HTTPException(503, detail={
            "erro": "api_desativada",
            "detalhe": "OMICA_API_TOKEN não configurado no .env (falha fechada)"})
    esquema, _, recebido = (authorization or "").partition(" ")
    # compare_digest em BYTES: com str, um caractere não-ASCII levanta TypeError (500).
    if (esquema.lower() != "bearer" or not recebido.strip()
            or not secrets.compare_digest(recebido.strip().encode(), esperado.encode())):
        raise HTTPException(401, detail={"erro": "nao_autorizado"},
                            headers={"WWW-Authenticate": "Bearer"})


router = APIRouter(prefix="/api/v1", dependencies=[Depends(exigir_token)], tags=["api-v1"])


@router.get("/saude")
def saude() -> dict[str, Any]:
    from omica.selos import conferir

    return {
        **dados_saude(),
        "selos_vencidos": [f"{s['modulo']}/{s['workflow']}" for s in conferir() if s["vencido"]],
        "gpu": estado_gpu(),
    }


@router.get("/catalogo")
def catalogo() -> dict[str, Any]:
    """Tudo o que um orquestrador precisa para montar um passo, sem ler YAML: params com
    default e tipo, fila, RAM, entradas/saídas declaradas e o estado do selo. (O
    `professor.catalogo()` omite params, defaults e mem_gb — serve ao LLM, não a isto.)"""
    from omica.selos import conferir

    selos = {(s["modulo"], s["workflow"]): s for s in conferir()}
    modulos = []
    for m in load_registry().modules:
        scripts = []
        for s in m.scripts:
            try:
                fila = dispatch.fila_para_runtime(s.runtime)
            except dispatch.RuntimeIndisponivel:
                fila = None
            scripts.append({
                "key": s.key, "titulo": s.titulo, "descricao": s.descricao,
                "runtime": s.runtime, "fila": fila, "mem_gb": s.mem_gb, "cpus": s.cpus,
                "tempo_estimado": s.tempo_estimado,
                "params": [p.model_dump() for p in s.params],
                "inputs": s.inputs, "outputs": s.outputs,
            })
        workflows = []
        for w in m.workflows:
            selo = selos.get((m.id, w.id))
            workflows.append({
                "id": w.id, "titulo": w.titulo, "passos": w.passos, "testado_em": w.testado_em,
                "selo_vigente": None if selo is None else not selo["vencido"],
            })
        modulos.append({"id": m.id, "nome": m.nome_pt, "scripts": scripts, "workflows": workflows})
    return {"versao": __version__, "modulos": modulos}


def _sha256(caminho: Path) -> Optional[str]:
    if caminho.stat().st_size > _MAX_SHA_BYTES:
        return None
    h = hashlib.sha256()
    with caminho.open("rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def _descrever(rel: str, caminho: Path) -> dict[str, Any]:
    """Metadados de um artefato — nunca o conteúdo. Só dentro da pasta de dados da
    plataforma: um `RESULTADO:` impresso por um script poderia apontar para qualquer
    arquivo do container, e nem tamanho nem hash de fora da raiz devem vazar."""
    raiz = get_config().data_dir.resolve()
    try:
        caminho.resolve().relative_to(raiz)
    except ValueError:
        return {"rel": rel, "fora_da_raiz": True}
    if not caminho.exists():
        return {"rel": rel, "existe": False}
    if caminho.is_dir():
        return {"rel": rel, "existe": True, "diretorio": True}
    return {"rel": rel, "existe": True, "bytes": caminho.stat().st_size, "sha256": _sha256(caminho)}


def _artefatos(status: dict[str, Any]) -> list[dict[str, Any]]:
    cfg = get_config()
    alvos: list[tuple[str, Path]] = []
    m = load_registry().por_id(str(status.get("modulo", "")))
    spec = next((s for s in m.scripts if s.key == status.get("script")), None) if m else None
    for rel in (spec.outputs if spec else []):
        alvos.append((rel, cfg.data_dir / rel))
    if status.get("result_path"):
        rp = Path(str(status["result_path"]))
        # result_path é caminho de container (/data/omica/...): traduz para o data_dir local
        texto = rp.as_posix()
        container = "/data/omica/"
        if texto.startswith(container):
            rp = cfg.data_dir / texto[len(container):]
        try:
            rel = rp.resolve().relative_to(cfg.data_dir.resolve()).as_posix()
        except ValueError:
            rel = texto
        if all(a[1].resolve() != rp.resolve() for a in alvos):
            alvos.append((rel, rp))
    return [_descrever(rel, caminho) for rel, caminho in alvos]


def _cauda_log(job_id: str, linhas: int) -> list[str]:
    caminho = queue.log_path(job_id)
    if not caminho.exists() or linhas == 0:
        return []
    return caminho.read_text(encoding="utf-8", errors="replace").splitlines()[-linhas:]


@router.get("/jobs/{job_id}")
def job(job_id: str, linhas_log: int = Query(default=40, ge=0, le=500)) -> dict[str, Any]:
    if not _ID_JOB.match(job_id):
        raise HTTPException(422, detail={"erro": "job_id_invalido"})
    status = queue.read_status(job_id)
    if status is None:
        raise HTTPException(404, detail={"erro": "job_inexistente", "job_id": job_id})
    return {**status, "log_tail": _cauda_log(job_id, linhas_log), "artefatos": _artefatos(status)}
