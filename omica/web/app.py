"""app.py — UI/API da plataforma ômica (FastAPI + Jinja2, um único entry point).

Decisões herdadas da auditoria de 2026-09-01:
  - server-rendered + JS mínimo próprio (EventSource p/ console SSE): funciona
    offline em sala de aula, sem toolchain de frontend;
  - console de job lê ARQUIVO de log (volume compartilhado com o worker), nunca
    memória de processo;
  - números na UI só via omica.results (nunca hardcoded).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import markdown as md
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from omica import __version__
from omica.config import REPO_ROOT, get_config
from omica.jobs import dispatch, guarda_ram, queue
from omica.registry import STATUS_TYPES, load_registry
from omica.results import carregar_tabela, resultado_de_job, resultado_do_modulo
from omica.web.i18n import normalizar_lang, t

WEB_DIR = Path(__file__).resolve().parent
DIDATICA_DIR = REPO_ROOT / "didatica" / "ajuda"

app = FastAPI(title="ômica", version=__version__, docs_url="/api/docs")
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
templates = Jinja2Templates(directory=WEB_DIR / "templates")
templates.env.globals.update(t=t, versao=__version__, status_types=STATUS_TYPES)


def _lang(request: Request) -> str:
    return normalizar_lang(request.query_params.get("lang") or request.cookies.get("omica_lang"))


def _render(request: Request, template: str, **ctx) -> HTMLResponse:
    lang = _lang(request)
    resposta = templates.TemplateResponse(request, template, {"lang": lang, **ctx})
    resposta.set_cookie("omica_lang", lang, max_age=60 * 60 * 24 * 365)
    return resposta


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    reg = load_registry()
    return _render(request, "home.html", registry=reg, saude=_saude_dados())


@app.get("/modulos/{mod_id}", response_class=HTMLResponse)
def modulo(request: Request, mod_id: str):
    reg = load_registry()
    m = reg.por_id(mod_id)
    if m is None:
        return RedirectResponse("/", status_code=303)
    # results_spec → conteúdo lido AGORA do disco (nunca número em template)
    resultados = []
    for rotulo, rel in m.results_spec.items():
        if rel.endswith(".json"):
            resultados.append((rotulo, "json", resultado_do_modulo(rel)))
        else:
            resultados.append((rotulo, "tabela", carregar_tabela(rel)))
    return _render(request, "modulo.html", m=m,
                   resultados=resultados, aviso=request.query_params.get("aviso"))


@app.post("/modulos/{mod_id}/scripts/{script_key}")
def rodar_script(request: Request, mod_id: str, script_key: str):
    try:
        job_id, backend = dispatch.lancar_script(mod_id, script_key)
    except (guarda_ram.RecusadoPorRAM, queue.FilaIndisponivel,
            dispatch.RuntimeIndisponivel, KeyError) as e:
        return RedirectResponse(f"/modulos/{mod_id}?aviso={e}", status_code=303)
    return RedirectResponse(f"/jobs/{job_id}?backend={backend}", status_code=303)


@app.get("/jobs", response_class=HTMLResponse)
def jobs(request: Request):
    aviso = request.query_params.get("aviso")
    return _render(request, "jobs.html", jobs=queue.listar_jobs(), aviso=aviso)


@app.post("/jobs/demo")
def lancar_demo(request: Request):
    try:
        job_id, backend = queue.enqueue("omica.jobs.demo.executar", prefixo="demo", mem_gb=0.1)
    except guarda_ram.RecusadoPorRAM as e:
        return RedirectResponse(f"/jobs?aviso={e}", status_code=303)
    return RedirectResponse(f"/jobs/{job_id}?backend={backend}", status_code=303)


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detalhe(request: Request, job_id: str):
    status = queue.read_status(job_id)
    if status is None:
        return RedirectResponse("/jobs", status_code=303)
    return _render(request, "job.html", job=status, resultado=resultado_de_job(job_id))


@app.get("/jobs/{job_id}/events")
async def job_events(job_id: str):
    """SSE: transmite o log do job linha a linha lendo o arquivo compartilhado."""

    async def stream():
        caminho = queue.log_path(job_id)
        posicao = 0
        while True:
            if caminho.exists():
                with caminho.open("r", encoding="utf-8") as f:
                    f.seek(posicao)
                    for linha in f:
                        yield f"data: {linha.rstrip()}\n\n"
                    posicao = f.tell()
            status = queue.read_status(job_id)
            if status and status.get("status") in ("done", "error"):
                yield f"event: fim\ndata: {status['status']}\n\n"
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/ajuda", response_class=HTMLResponse)
def ajuda(request: Request):
    docs = sorted(p.name for p in DIDATICA_DIR.glob("*.md")) if DIDATICA_DIR.exists() else []
    return _render(request, "ajuda.html", docs=docs)


@app.get("/ajuda/{doc}", response_class=HTMLResponse)
def ajuda_doc(request: Request, doc: str):
    caminho = (DIDATICA_DIR / doc).resolve()
    if not caminho.is_file() or caminho.parent != DIDATICA_DIR.resolve() or caminho.suffix != ".md":
        return RedirectResponse("/ajuda", status_code=303)
    corpo = md.markdown(caminho.read_text(encoding="utf-8"), extensions=["tables", "fenced_code"])
    return _render(request, "ajuda_doc.html", doc=doc, corpo=corpo)


def _saude_dados() -> dict:
    cfg = get_config()
    reg = load_registry()
    backend = "rq" if queue._rq_queue() is not None else "thread"
    return {
        "versao": __version__,
        "ram_pct": guarda_ram.uso_ram_pct(),
        "ram_limite_pct": cfg.ram_max_pct,
        "fila_backend": backend,
        "modulos_validos": len(reg.modules),
        "erros_manifests": reg.erros,
        "data_dir": str(cfg.data_dir),
    }


@app.get("/saude")
def saude():
    return JSONResponse(_saude_dados())
