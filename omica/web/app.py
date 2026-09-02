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
from omica import professor as prof
from omica.config import REPO_ROOT, get_config
from omica.jobs import dispatch, guarda_ram, queue
from omica.registry import STATUS_TYPES, load_registry
from omica.results import carregar_tabela, resultado_de_job, resultado_do_modulo
from omica.web.flux import svg_do_modulo
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
        elif rel.endswith((".png", ".svg")):
            existe = (get_config().results_dir / rel).exists()
            resultados.append((rotulo, "img", f"/resultados/{rel}" if existe else None))
        else:
            resultados.append((rotulo, "tabela", carregar_tabela(rel)))
    return _render(request, "modulo.html", m=m, fluxo_svg=svg_do_modulo(m),
                   resultados=resultados, aviso=request.query_params.get("aviso"))


@app.post("/modulos/{mod_id}/scripts/{script_key}")
async def rodar_script(request: Request, mod_id: str, script_key: str):
    valores = {k: str(v) for k, v in (await request.form()).items()}
    try:
        job_id, backend = dispatch.lancar_script(mod_id, script_key, valores=valores)
    except (guarda_ram.RecusadoPorRAM, queue.FilaIndisponivel,
            dispatch.RuntimeIndisponivel, KeyError, ValueError) as e:
        return RedirectResponse(f"/modulos/{mod_id}?aviso={e}", status_code=303)
    return RedirectResponse(f"/jobs/{job_id}?backend={backend}", status_code=303)


@app.post("/modulos/{mod_id}/workflows/{wf_id}")
def rodar_workflow(request: Request, mod_id: str, wf_id: str):
    try:
        wf_run_id, _, backend = dispatch.lancar_workflow(mod_id, wf_id)
    except (guarda_ram.RecusadoPorRAM, queue.FilaIndisponivel,
            dispatch.RuntimeIndisponivel, KeyError, ValueError) as e:
        return RedirectResponse(f"/modulos/{mod_id}?aviso={e}", status_code=303)
    return RedirectResponse(f"/workflows/{wf_run_id}?backend={backend}", status_code=303)


@app.get("/workflows/{wf_run_id}", response_class=HTMLResponse)
def workflow_run(request: Request, wf_run_id: str):
    run = queue.ler_workflow_run(wf_run_id)
    if run is None:
        return RedirectResponse("/jobs", status_code=303)
    return _render(request, "workflow.html", run=run,
                   ativo=run["status"] in ("queued", "running"))


@app.get("/professor", response_class=HTMLResponse)
def professor_home(request: Request):
    ok, modelo = prof.motor_disponivel()
    return _render(request, "professor.html", motor_ok=ok, modelo=modelo,
                   vitrine=prof.workflows_testados(), resposta=None,
                   frase="", arquivo="")


@app.post("/professor/perguntar", response_class=HTMLResponse)
async def professor_perguntar(request: Request):
    form = await request.form()
    frase = str(form.get("frase", "")).strip()
    arquivo = str(form.get("arquivo", "")).strip()
    resposta = prof.perguntar(frase, arquivo) if frase else None
    ok, modelo = prof.motor_disponivel()
    return _render(request, "professor.html", motor_ok=ok, modelo=modelo,
                   vitrine=prof.workflows_testados(), resposta=resposta,
                   frase=frase, arquivo=arquivo)


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


@app.get("/resultados/{rel:path}")
def servir_resultado(rel: str):
    """Serve UM arquivo de resultado (figura/tabela) de dentro de results_dir —
    caminho resolvido e validado para nunca sair do diretório de resultados."""
    base = get_config().results_dir.resolve()
    caminho = (base / rel).resolve()
    if not caminho.is_file() or base not in caminho.parents:
        return RedirectResponse("/", status_code=303)
    from fastapi.responses import FileResponse

    return FileResponse(caminho)


@app.get("/saude")
def saude():
    return JSONResponse(_saude_dados())
