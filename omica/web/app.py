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
import json
import re
from pathlib import Path
from typing import Optional

import markdown as md
from fastapi import FastAPI, Form, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import (HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from omica import __version__
from omica import professor as prof
from omica.api import v1 as api_v1
from omica.config import REPO_ROOT, get_config
from omica.jobs import dispatch, guarda_ram, queue
from omica.registry import STATUS_TYPES, load_registry
from omica.results import carregar_tabela, resultado_de_job, resultado_do_modulo
from omica.saude import dados_saude
from omica.skills import busca
from omica.web.flux import svg_do_modulo
from omica.web.i18n import normalizar_lang, t

WEB_DIR = Path(__file__).resolve().parent
DIDATICA_DIR = REPO_ROOT / "didatica" / "ajuda"

app = FastAPI(title="ômica", version=__version__, docs_url="/api/docs")
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
# API JSON para orquestradores (n8n). Exige token; sem OMICA_API_TOKEN responde 503.
app.include_router(api_v1.router)


# Erros sob /api/v1 que não passam por rota nenhuma da API (revisão F2, resíduo do #33): a
# `_RotaV1` só envolve rota que CASOU, então 404 de caminho inexistente e 405 de método
# errado saíam como {"detail": "Not Found"} — sem `erro`, e o 405 antes do 401 —, e uma
# exceção não tratada virava 500 em texto puro. No n8n, `$json.body.detail.erro` ficava
# undefined. Fora do prefixo, tudo segue o tratamento padrão (a UI HTML não muda).
@app.exception_handler(StarletteHTTPException)
async def _erro_http(request: Request, exc: StarletteHTTPException):
    if not api_v1.na_api_v1(request.scope):
        return await http_exception_handler(request, exc)
    if not isinstance(exc.detail, dict):
        # `detail` em texto = levantado pelo framework, não por uma rota da API — o 404/405
        # do roteamento nem chegou à conferência do token. Sem credencial, a resposta é a do
        # token (401/503), como em toda a API: o 405 não diz a quem não se autenticou que a
        # rota existe com outro método.
        try:
            await api_v1.conferir_token_antes(request, request.app.dependency_overrides)
        except StarletteHTTPException as recusa:
            exc = recusa
    headers = getattr(exc, "headers", None)
    return JSONResponse(status_code=exc.status_code, headers=headers, content={
        "detail": api_v1.detalhe_do_contrato(exc.status_code, exc.detail, headers)})


@app.exception_handler(Exception)
async def _erro_interno(request: Request, exc: Exception):
    """500 sob /api/v1 no formato do contrato e SEM a mensagem da exceção (pode trazer
    caminho, trecho de arquivo ou do .env); o traceback fica no log do servidor, que o
    Starlette continua emitindo. Fora do prefixo, a mesma resposta padrão de antes."""
    if not api_v1.na_api_v1(request.scope):
        return PlainTextResponse("Internal Server Error", status_code=500)
    return JSONResponse(status_code=500, content={"detail": {
        "erro": "erro_interno", "detalhe": "exceção não tratada no servidor (detalhes no log da ômica)"}})


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
        elif rel.endswith(".html"):
            existe = (get_config().results_dir / rel).exists()
            resultados.append((rotulo, "link", f"/resultados/{rel}" if existe else None))
        else:
            resultados.append((rotulo, "tabela", carregar_tabela(rel)))
    # elo declarado no manifest → domínios da biblioteca, com a contagem lida do índice
    contagem = (busca.carregar_indice() or {}).get("dominios", {})
    skills_modulo = [(d, contagem.get(d)) for d in m.skills_dominios]
    return _render(request, "modulo.html", m=m, fluxo_svg=svg_do_modulo(m),
                   resultados=resultados, aviso=request.query_params.get("aviso"),
                   skills_modulo=skills_modulo)


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
def professor_perguntar(request: Request, frase: str = Form(""), arquivo: str = Form("")):
    # Rota SÍNCRONA de propósito (o Starlette a roda num thread do pool): como `async def`
    # (até a 0.8.0), o motor local e a busca na biblioteca rodavam dentro do event loop e
    # o servidor inteiro parava enquanto o Professor pensava.
    frase = frase.strip()
    arquivo = arquivo.strip()
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
            # read_status lê o status.json e, numa troca em andamento, relê com time.sleep:
            # fora do event loop, senão cada console aberto parava o servidor a cada polling
            status = await run_in_threadpool(queue.read_status, job_id)
            if status and status.get("status") in ("done", "error"):
                yield f"event: fim\ndata: {status['status']}\n\n"
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/ajuda", response_class=HTMLResponse)
def ajuda(request: Request):
    docs = sorted(p.name for p in DIDATICA_DIR.glob("*.md")) if DIDATICA_DIR.exists() else []
    return _render(request, "ajuda.html", docs=docs)


def _separar_frontmatter(texto: str) -> tuple[dict, str]:
    """Front-matter YAML (--- ... ---) → (metadados, corpo). Sem bloco → ({}, texto)."""
    if texto.startswith("---"):
        partes = texto.split("---", 2)
        if len(partes) == 3:
            import yaml

            try:
                meta = yaml.safe_load(partes[1]) or {}
                return (meta if isinstance(meta, dict) else {}), partes[2]
            except yaml.YAMLError:
                pass
    return {}, texto


@app.get("/ajuda/{doc}", response_class=HTMLResponse)
def ajuda_doc(request: Request, doc: str):
    caminho = (DIDATICA_DIR / doc).resolve()
    if not caminho.is_file() or caminho.parent != DIDATICA_DIR.resolve() or caminho.suffix != ".md":
        return RedirectResponse("/ajuda", status_code=303)
    meta, corpo_md = _separar_frontmatter(caminho.read_text(encoding="utf-8"))
    corpo = md.markdown(corpo_md, extensions=["tables", "fenced_code"])
    # Modo aula: front-matter com roteiro_aula: true + passos executáveis —
    # cada botão dispara as MESMAS rotas determinísticas dos módulos.
    aula = meta.get("passos") if meta.get("roteiro_aula") else None
    return _render(request, "ajuda_doc.html", doc=doc, corpo=corpo, aula=aula)


def _saude_dados() -> dict:
    return dados_saude()  # compartilhado com a API v1 (omica/saude.py)


@app.get("/skills", response_class=HTMLResponse)
def skills(request: Request):
    """Só o cabeçalho sai daqui; a lista vem de /api/skills. A página já foi o índice
    inteiro (330 KB) embutido, com busca por substring no navegador — que devolvia
    zero para "metilação", porque o índice é em inglês. `?q=` e `?dominio=` deixam a
    busca endereçável (é como a página do módulo e o Professor apontam para cá)."""
    indice = busca.carregar_indice()
    if indice is None:
        return RedirectResponse("/?lang=" + _lang(request), status_code=303)
    return _render(request, "skills.html", indice=indice,
                   q=request.query_params.get("q", ""),
                   dominio=request.query_params.get("dominio", ""),
                   tem_glossario=bool(busca.carregar_glossario()[0]))


@app.get("/api/skills")
def api_skills(q: str = "", dominio: str = "", n: int = busca.LIMITE_PADRAO):
    """Busca determinística nas skills, em português ou inglês (omica.skills.busca).
    Junto vêm os cards do domínio escolhido — quando usar, regras duras e stack."""
    resultado = busca.buscar(q, dominio=dominio, limite=max(0, n))
    cards = (busca.carregar_indice() or {}).get("cards", {})
    resultado["cards"] = cards.get(dominio, []) if dominio else []
    return JSONResponse(resultado)


@app.get("/skills/{pasta:path}", response_class=HTMLResponse)
def skill_detalhe(request: Request, pasta: str):
    """Renderiza o SKILL.md da pasta escolhida, direto da biblioteca montada
    read-only — leitura offline, zero tokens."""
    base = get_config().skills_dir.resolve()
    caminho = (base / pasta / "SKILL.md").resolve()
    if not caminho.is_file() or base not in caminho.parents:
        return RedirectResponse("/skills", status_code=303)
    # O SKILL.md abre com front-matter YAML (name/description/...): renderizado como
    # Markdown virava um parágrafo cru no topo. A descrição completa vai para o cabeçalho
    # — o índice a trunca em 140 caracteres.
    meta, corpo_md = _separar_frontmatter(caminho.read_text(encoding="utf-8", errors="replace"))
    corpo = md.markdown(corpo_md, extensions=["tables", "fenced_code"])
    # contexto da skill: de que domínio é e se a plataforma já tem análise pronta nele
    indice = busca.carregar_indice() or {}
    skill = next((s for s in indice.get("skills", []) if s["pasta"] == pasta.strip("/")), None)
    modulos = [m for m in load_registry().modules
               if skill and skill["dominio"] in m.skills_dominios]
    return _render(request, "ajuda_doc.html", doc=f"skills/{pasta}", corpo=corpo,
                   skill=skill, modulos_skill=modulos,
                   skill_descricao=str(meta.get("description") or ""))


@app.get("/glossario", response_class=HTMLResponse)
def glossario(request: Request):
    import json as _json

    caminho = REPO_ROOT / "didatica" / "glossario.json"
    dados = _json.loads(caminho.read_text(encoding="utf-8")) if caminho.exists() else {"_meta": {}, "termos": []}
    return _render(request, "glossario.html", glossario=dados)


@app.get("/diagnostico", response_class=HTMLResponse)
def diagnostico(request: Request):
    from omica.doctor import rodar as doctor_rodar

    return _render(request, "diagnostico.html", checagens=doctor_rodar(checar_docker=False))


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


_FIGURA_RELATORIO = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-]{0,120}\.png", re.ASCII)


def _pasta_relatorio(run_id: str) -> Optional[Path]:
    """results/<mod>/runs/<run_id>/relatorio — o módulo vem do run.json, nunca da URL."""
    from omica import runs

    try:
        run = json.loads((runs._pasta(run_id) / "run.json").read_text(encoding="utf-8"))
    except (runs.ErroRun, OSError, ValueError):
        return None
    pasta = get_config().results_dir / run["modulo"] / "runs" / run_id / "relatorio"
    return pasta if (pasta / "relatorio.html").is_file() else None


@app.get("/relatorios/{run_id}")
def relatorio_sem_barra(run_id: str):
    # a página cita as figuras por caminho RELATIVO (figuras/x.png): precisa da barra final
    if _pasta_relatorio(run_id) is None:
        return RedirectResponse("/", status_code=303)
    return RedirectResponse(f"/relatorios/{run_id}/", status_code=307)


@app.get("/relatorios/{run_id}/", response_class=HTMLResponse)
def relatorio(run_id: str):
    """Relatório de um run, como o portão de números o aprovou (HTML já escapado no render;
    a CSP vai também no cabeçalho, além da meta da própria página)."""
    pasta = _pasta_relatorio(run_id)
    if pasta is None:
        return RedirectResponse("/", status_code=303)
    return HTMLResponse((pasta / "relatorio.html").read_text(encoding="utf-8"), headers={
        "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"})


@app.get("/relatorios/{run_id}/figuras/{nome}")
def relatorio_figura(run_id: str, nome: str):
    pasta = _pasta_relatorio(run_id)
    if pasta is None or not _FIGURA_RELATORIO.fullmatch(nome):
        return RedirectResponse("/", status_code=303)
    base = (pasta / "figuras").resolve()
    caminho = (base / nome).resolve()
    if not caminho.is_file() or caminho.parent != base:
        return RedirectResponse("/", status_code=303)
    from fastapi.responses import FileResponse

    return FileResponse(caminho, media_type="image/png")


@app.get("/saude")
def saude():
    return JSONResponse(_saude_dados())
