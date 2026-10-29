"""app.py — UI/API da plataforma ômica (FastAPI + Jinja2, um único entry point).

Decisões herdadas da auditoria de 2026-09-01:
  - server-rendered + JS mínimo próprio (EventSource p/ console SSE): funciona
    offline em sala de aula, sem toolchain de frontend;
  - console de job lê ARQUIVO de log (volume compartilhado com o worker), nunca
    memória de processo;
  - números na UI só via omica.results (nunca hardcoded).

v2 multiespécie (M3, ESPEC §7): a 1ª página escolhe a ESPÉCIE; o contexto vive na URL
(/e/<espécie>/<subprojeto>/modulos/<módulo>), resolvido no servidor por omica.contexto — nada
de cookie de contexto. Fora de bos_taurus/demo só PIPELINES executam: a página não desenha
botão de script/workflow e o POST canônico recusa (409 + X-Omica-Recusa) ANTES de ler o
formulário. As rotas v1 (/modulos/...) continuam: o GET vira 307 para o demo e os POST são
aliases do demo, intactos (o plugin Bioinfo exige 303 e lê `aviso` na query como recusa).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

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
from omica import contexto
from omica import especies as especies_mod
from omica import professor as prof
from omica import subprojetos as subprojetos_mod
from omica.api import v1 as api_v1
from omica.chaves_contexto import ESPECIE_LEGADA, ID_DEMO, ID_ESPECIE, SLUG, SLUG_DEMO, SLUG_NU
from omica.config import REPO_ROOT, get_config, token_api
from omica.jobs import dispatch, guarda_ram, queue
from omica.registry import STATUS_TYPES, load_registry
from omica.results import carregar_tabela, resultado_de_job, resultado_do_modulo
from omica.saude import dados_saude, redigir_erros_subprojetos
from omica.skills import busca
from omica.web.flux import svg_do_modulo
from omica.web.i18n import normalizar_lang, t

WEB_DIR = Path(__file__).resolve().parent
DIDATICA_DIR = REPO_ROOT / "didatica" / "ajuda"
# o subprojeto dos selos: único lugar em que `scripts:` e os workflows selados executam
DEMO_URL = f"/e/{ID_DEMO}"

_log = logging.getLogger(__name__)

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


def _data_hora(epoch: Any) -> str:
    """`criado_em` dos runs (epoch) → texto local; valor estranho vira travessão, nunca 500."""
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(epoch)))
    except (TypeError, ValueError, OverflowError, OSError):
        return "—"


templates = Jinja2Templates(directory=WEB_DIR / "templates")
templates.env.globals.update(t=t, versao=__version__, status_types=STATUS_TYPES, demo_url=DEMO_URL)
templates.env.filters["data_hora"] = _data_hora


def _lang(request: Request) -> str:
    return normalizar_lang(request.query_params.get("lang") or request.cookies.get("omica_lang"))


def _render(request: Request, template: str, status_code: int = 200, **ctx) -> HTMLResponse:
    lang = _lang(request)
    resposta = templates.TemplateResponse(request, template, {"lang": lang, **ctx},
                                          status_code=status_code)
    resposta.set_cookie("omica_lang", lang, max_age=60 * 60 * 24 * 365)
    return resposta


# ------------------------------------------------- autorização do autor (privados)
# Crítica v2, médio 16: a página de um subprojeto mostra pergunta, amostras e dados legados, e a
# UI não tem login — com OMICA_BIND=0.0.0.0 (modo aula) os alunos veriam os subprojetos
# `privado: true` (rascunhos criados aqui e os legados fora do git). Falha fechada: privado só
# aparece com a credencial do autor, que é o token da API (o mesmo segredo do n8n, só no .env).
# O navegador guarda um HMAC do token (cookie HttpOnly), não o token: trocar o token no .env
# revoga os cookies na hora. Sem token configurado, ninguém vê privado.
_COOKIE_AUTOR = "omica_autor"
_SAL_AUTOR = b"omica-web/privados/v1"


def _selo_autor(token: str) -> str:
    return hmac.new(token.encode("utf-8"), _SAL_AUTOR, hashlib.sha256).hexdigest()


def _iguais(a: str, b: str) -> bool:
    # bytes: compare_digest em str com caractere não-ASCII levanta TypeError (500)
    return hmac.compare_digest(a.encode("utf-8", "replace"), b.encode("utf-8", "replace"))


def _autorizado(request: Request) -> bool:
    token = token_api()
    if not token:
        return False
    cabecalho = request.headers.get("authorization", "")
    if cabecalho[:7].lower() == "bearer " and _iguais(cabecalho[7:].strip(), token):
        return True
    cookie = request.cookies.get(_COOKIE_AUTOR)
    return bool(cookie) and _iguais(cookie, _selo_autor(token))


def _visivel(reg: subprojetos_mod.Registro, autorizado: bool) -> bool:
    return autorizado or not reg.sub.privado


def _oculto(request: Request, especie: Any, slug: Any) -> bool:
    """(espécie, slug) é de um subprojeto privado e o pedido não tem a autorização do autor. Vale
    para o que sai do subprojeto por OUTRA porta que não a página dele: o job (detalhe e console,
    que mostram o cmd com a pasta do subprojeto), o relatório do run e o arquivo de resultado."""
    if not (isinstance(especie, str) and isinstance(slug, str)) or _autorizado(request):
        return False
    return _privado_no_catalogo(contexto.catalogo(), especie, slug)


def _privado_no_catalogo(cat: contexto.Catalogo, especie: str, slug: str) -> bool:
    """Falha FECHADA para o que mora fora do git (verificação v2, médio 16): um subprojeto de
    <dados> que deixou de carregar (typo, `forbid`, parâmetro que sumiu do manifest, assembly
    renomeada) não sabe mais dizer se é privado, então conta como privado. Carregado: vale o
    `privado` dele. Id malformado: privado. Inexistente (nem pasta nem erro): público."""
    reg = cat.subprojetos.get(f"{especie}/{slug}")
    if reg is not None:
        return reg.sub.privado
    if not (ID_ESPECIE.fullmatch(especie) and SLUG.fullmatch(slug)):
        return True
    return (f"dados:{especie}/{slug}" in cat.erros_subprojetos
            or (get_config().projetos_dir / especie / slug).exists())


def _mesma_origem(request: Request) -> bool:
    """POST com `Origin` de outro site é recusado (403). Sem `Origin` passa: o plugin Bioinfo
    e o curl não mandam. Vale só nas rotas POST novas; as v1 ficam para a T-0028."""
    origem = request.headers.get("origin")
    if origem is None:
        return True
    try:
        return urlsplit(origem).netloc == request.url.netloc
    except ValueError:
        return False


# ---------------------------------------------------------------- contexto na URL
def _slug_valido(sub: str) -> bool:
    return bool(SLUG.fullmatch(sub)) and not SLUG_NU.fullmatch(sub)


def _volta(especie: str = "", sub: str = "") -> str:
    """Para onde o "voltar" de uma recusa aponta: só ids no padrão viram link."""
    if especie and ID_ESPECIE.fullmatch(especie):
        if sub and _slug_valido(sub):
            return f"/e/{especie}/{sub}"
        return f"/e/{especie}"
    return "/"


def _recusa(request: Request, motivo: str, status_code: int, *, especie: str = "", sub: str = "",
            **detalhe: Any) -> HTMLResponse:
    """recusa.html com o motivo em `data-motivo` e em `X-Omica-Recusa` (o mesmo código do núcleo)."""
    resposta = _render(request, "recusa.html", status_code=status_code, motivo=motivo,
                       detalhe=detalhe, volta=_volta(especie, sub))
    resposta.headers["X-Omica-Recusa"] = motivo
    return resposta


def _abrir(request: Request, especie: str, sub: Optional[str] = None):
    """(catálogo, espécie, registro) do contexto da URL, ou o redirect do GET: espécie inválida
    ou desconhecida → 303 "/"; subprojeto inválido, desconhecido ou privado sem autorização →
    303 /e/<espécie> (privado e inexistente são indistinguíveis: nada de oráculo de existência).
    Os ids são conferidos ANTES de qualquer acesso a disco."""
    if not ID_ESPECIE.fullmatch(especie):
        return RedirectResponse("/", status_code=303)
    cat = contexto.catalogo()
    esp = cat.especies.get(especie)
    if esp is None:
        return RedirectResponse("/", status_code=303)
    if sub is None:
        return cat, esp, None
    reg = cat.subprojetos.get(f"{especie}/{sub}") if _slug_valido(sub) else None
    if reg is None or not _visivel(reg, _autorizado(request)):
        return RedirectResponse(f"/e/{especie}", status_code=303)
    return cat, esp, reg


def _estado(e: especies_mod.Especie) -> especies_mod.EstadoEspecie:
    try:
        return especies_mod.estado(e)   # só stat
    except (ValueError, OSError):
        return especies_mod.EstadoEspecie("sem_referencia", 0, 0, ())


def _manifests() -> dict:
    return {m.id: m for m in load_registry().modules}


def _esp_do_rotulo(rotulo: str) -> str:
    """'<origem>:<especie>/<slug>' (ou '<origem>:<especie>') → '<especie>'."""
    return rotulo.partition(":")[2].partition("/")[0]


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    cat = contexto.catalogo()
    autorizado = _autorizado(request)
    manifests = _manifests()
    cards = []
    for e in contexto.listar_especies(cat):
        regs = [r for r in contexto.listar_subprojetos(e.id, cat=cat) if _visivel(r, autorizado)]
        cards.append({"esp": e, "estado": _estado(e), "n_sub": len(regs),
                      "n_exec": sum(1 for r in regs if r.sub.executavel),
                      "modulos": [manifests[m] for m in e.modulos if m in manifests]})
    return _render(request, "home.html", cards=cards, saude=_saude_dados(),
                   erros_especies=cat.erros_especies,
                   erros_subprojetos=redigir_erros_subprojetos(cat.erros_subprojetos, autorizado))


@app.get("/e/{especie}", response_class=HTMLResponse)
def pagina_especie(request: Request, especie: str):
    aberto = _abrir(request, especie)
    if isinstance(aberto, RedirectResponse):
        return aberto
    cat, esp, _ = aberto
    autorizado = _autorizado(request)
    arquivados = request.query_params.get("arquivados") == "1"
    manifests = _manifests()
    linhas = []
    for r in contexto.listar_subprojetos(esp.id, incluir_arquivados=arquivados, cat=cat):
        if not _visivel(r, autorizado):
            continue
        linhas.append({"reg": r, "n_amostras": subprojetos_mod.n_amostras(r),
                       "modulos": [manifests[m] for m in contexto.modulos_habilitados(r, cat)
                                   if m in manifests]})
    erros = {k: v for k, v in cat.erros_subprojetos.items() if _esp_do_rotulo(k) == esp.id}
    return _render(request, "especie.html", esp=esp, estado=_estado(esp), linhas=linhas,
                   arquivados=arquivados, permitir_rascunho=get_config().permitir_rascunho,
                   erros_subprojetos=redigir_erros_subprojetos(erros, autorizado),
                   autorizado=autorizado, token_configurado=bool(token_api()))


# ------------------------------------------------------------- rascunho pela UI
# Declaradas ANTES de /e/{especie}/{sub}: senão "novo-subprojeto" casaria como slug (e o slug
# nem é válido — o hífen não está no padrão —, então nunca colide com um subprojeto real).
def _form_rascunho(request: Request, esp, *, status_code: int = 200, erro: str = "",
                   erro_detalhe: str = "", valores: Optional[dict] = None) -> HTMLResponse:
    base = {"slug": "", "titulo": "", "pergunta": "", "assembly": esp.assembly_padrao}
    return _render(request, "subprojeto_novo.html", status_code=status_code, esp=esp,
                   permitir=get_config().permitir_rascunho, autorizado=_autorizado(request),
                   erro=erro, erro_detalhe=erro_detalhe,
                   valores={**base, **(valores or {})})


@app.get("/e/{especie}/novo-subprojeto", response_class=HTMLResponse)
def novo_subprojeto(request: Request, especie: str):
    aberto = _abrir(request, especie)
    if isinstance(aberto, RedirectResponse):
        return aberto
    return _form_rascunho(request, aberto[1])


@app.post("/e/{especie}/novo-subprojeto")
async def criar_subprojeto(request: Request, especie: str):
    """Rascunho em <OMICA_DATA>/projetos (decisão D-11). Flag desligada → 403 antes de ler o
    formulário (nada é lido nem gravado); recusa do núcleo → 422 com o formulário de volta."""
    if not _mesma_origem(request):
        return _recusa(request, "origem_recusada", 403, especie=especie)
    if not get_config().permitir_rascunho:
        return _recusa(request, "rascunho_desligado", 403, especie=especie)
    # Sem a autorização do autor, nada é gravado em <dados> e a colisão de slug/alias com um
    # privado não vira oráculo de existência (422 × 303): 403 antes de ler o formulário.
    if not _autorizado(request):
        return _recusa(request, "rascunho_sem_autorizacao", 403, especie=especie)
    cat = await run_in_threadpool(contexto.catalogo) if ID_ESPECIE.fullmatch(especie) else None
    esp = cat.especies.get(especie) if cat is not None else None
    if esp is None:
        return _recusa(request, "especie_inexistente", 404)
    form = await request.form(max_files=1, max_fields=8)
    valores = {k: str(form.get(k) or "") for k in ("slug", "titulo", "pergunta", "assembly")}
    valores["slug"] = valores["slug"].strip()
    conteudo: Optional[bytes] = None
    arquivo = form.get("amostras")
    if arquivo is not None and not isinstance(arquivo, str):
        # lê no máximo 1 MiB + 1 byte: o núcleo recusa o que passar do teto (amostras_invalidas)
        dados = await arquivo.read(subprojetos_mod.AMOSTRAS_MAX_BYTES + 1)
        # navegador sem arquivo escolhido manda a parte vazia e sem nome: não é amostras
        conteudo = dados if (dados or getattr(arquivo, "filename", "")) else None
    try:
        reg = await run_in_threadpool(
            lambda: subprojetos_mod.criar_rascunho(
                especie, valores["slug"], titulo=valores["titulo"], pergunta=valores["pergunta"],
                assembly=valores["assembly"], amostras=conteudo))
    except subprojetos_mod.RascunhoRecusado as e:
        return _form_rascunho(request, esp, status_code=422, erro=e.erro, erro_detalhe=e.detalhe,
                              valores=valores)
    _log.info("rascunho criado: %s (%s)", reg.sub.id, reg.pasta)
    return RedirectResponse(f"/e/{especie}/{reg.sub.slug}", status_code=303)


# ------------------------------------------------------------------ subprojeto
_MAX_LINHAS_AMOSTRAS = 15
_MAX_ARQUIVOS = 50


def _previa_amostras(reg: subprojetos_mod.Registro) -> Optional[dict]:
    """As primeiras linhas do amostras.tsv, lidas em fluxo (nunca o arquivo inteiro)."""
    arq = reg.pasta / reg.sub.amostras
    if not arq.is_file():
        return None
    linhas: list[list[str]] = []
    with arq.open("r", encoding="utf-8", errors="replace", newline="") as f:
        for linha in f:
            linha = linha.rstrip("\r\n")
            if not linha.strip():
                continue
            linhas.append((linha.lstrip("﻿") if not linhas else linha).split("\t"))
            if len(linhas) > _MAX_LINHAS_AMOSTRAS:
                break
    if not linhas:
        return None
    return {"colunas": linhas[0], "linhas": linhas[1:1 + _MAX_LINHAS_AMOSTRAS]}


def _arquivos_declarados(sub: subprojetos_mod.Subprojeto) -> tuple[list[dict], bool]:
    """datasets e dados_legados com ✅/❌ só por exists() (no máximo 50; nunca lista pasta nem
    lê conteúdo). Os caminhos já passaram pelo padrão lexical do loader (crítica v2, médio 10c)."""
    todos = [("datasets", nome, valor) for nome, valor in sub.datasets.items()]
    todos += [("dados_legados", "", valor) for valor in sub.dados_legados]
    itens = []
    for tipo, nome, valor in todos[:_MAX_ARQUIVOS]:
        try:
            presente = especies_mod.no_disco(valor).exists()
        except (ValueError, OSError):
            presente = False
        itens.append({"tipo": tipo, "nome": nome, "valor": valor, "presente": presente})
    return itens, len(todos) > _MAX_ARQUIVOS


@app.get("/e/{especie}/{sub}", response_class=HTMLResponse)
def pagina_subprojeto(request: Request, especie: str, sub: str):
    from omica import runs

    aberto = _abrir(request, especie, sub)
    if isinstance(aberto, RedirectResponse):
        return aberto
    cat, esp, reg = aberto
    manifests = _manifests()
    pipelines = contexto.pipelines_do_subprojeto(reg, cat=cat)
    arquivos, truncados = _arquivos_declarados(reg.sub)
    exemplo = json.dumps({"pipeline": pipelines[0].id if pipelines else "<pipeline>",
                          "especie": esp.id, "subprojeto": reg.sub.slug}, ensure_ascii=False)
    return _render(request, "subprojeto.html", esp=esp, sub=reg.sub, em_demo=reg.sub.id == ID_DEMO,
                   modulos=[manifests[m] for m in contexto.modulos_habilitados(reg, cat) if m in manifests],
                   pipelines=pipelines, exemplo_run=exemplo, amostras=_previa_amostras(reg),
                   n_amostras=subprojetos_mod.n_amostras(reg), arquivos=arquivos,
                   arquivos_truncados=truncados, runs_sub=runs.listar(esp.id, reg.sub.slug))


def _resultados_demo(m) -> list:
    """results_spec → conteúdo lido AGORA do disco (nunca número em template). SÓ no demo: fora
    dele, results/<mod>/demo é o resultado de OUTRO subprojeto."""
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
    return resultados


def _aviso_do_modulo(aviso: str, m) -> bool:
    """Aviso do subprojeto que começa por "<módulo>:" (id ou nome, ex.: "epigenômica: …")."""
    prefixo, sep, _ = aviso.partition(":")
    return bool(sep) and prefixo.strip().casefold() in {m.id.casefold(), m.nome_pt.casefold(),
                                                       m.nome_en.casefold()}


@app.get("/e/{especie}/{sub}/modulos/{mod_id}", response_class=HTMLResponse)
def modulo(request: Request, especie: str, sub: str, mod_id: str):
    from omica import runs

    aberto = _abrir(request, especie, sub)
    if isinstance(aberto, RedirectResponse):
        return aberto
    cat, esp, reg = aberto
    base = f"/e/{especie}/{sub}"
    m = load_registry().por_id(mod_id) if mod_id in contexto.modulos_habilitados(reg, cat) else None
    if m is None:
        return RedirectResponse(base, status_code=303)
    em_demo = reg.sub.id == ID_DEMO
    pode_script, motivo_script = contexto.execucao_permitida(especie, sub, "script", cat=cat)
    wf_pode = {wf.id: contexto.execucao_permitida(especie, sub, "workflow", workflow=wf, cat=cat)[0]
               for wf in m.workflows}
    # elo declarado no manifest → domínios da biblioteca, com a contagem lida do índice
    contagem = (busca.carregar_indice() or {}).get("dominios", {})
    skills_modulo = [(d, contagem.get(d)) for d in m.skills_dominios]
    return _render(request, "modulo.html", m=m, esp=esp, sub=reg.sub, base=base, em_demo=em_demo,
                   pode_script=pode_script, motivo_script=motivo_script, wf_pode=wf_pode,
                   pipelines=contexto.pipelines_do_subprojeto(reg, mod_id, cat=cat),
                   fluxo_svg=svg_do_modulo(m),
                   resultados=_resultados_demo(m) if em_demo else [],
                   runs_modulo=[] if em_demo else [r for r in runs.listar(especie, sub)
                                                   if r["modulo"] == mod_id],
                   avisos_modulo=[a for a in reg.sub.avisos if _aviso_do_modulo(a, m)],
                   aviso=request.query_params.get("aviso"), skills_modulo=skills_modulo)


@app.get("/modulos/{mod_id}")
def modulo_antigo(request: Request, mod_id: str):
    """v1 → v2: 307 para o módulo no demo, com a query CRUA (sem decodificar nem recodificar:
    ?lang= e o ?aviso= de uma recusa da rota antiga chegam iguais). Inexistente → 303 "/"."""
    if load_registry().por_id(mod_id) is None:
        return RedirectResponse("/", status_code=303)
    q = request.url.query
    return RedirectResponse(f"{DEMO_URL}/modulos/{mod_id}" + (f"?{q}" if q else ""), status_code=307)


# --------------------------------------------------------------- execução (POST)
def _permissao(request: Request, especie: str, sub: str, mod_id: str, tipo: str,
               wf_id: str = "") -> Optional[tuple[str, int]]:
    """None = pode executar; senão (motivo, status HTTP). A precedência é a de
    contexto.execucao_permitida (o mesmo motivo sai na UI, no dispatch e na API); privado sem
    autorização conta como subprojeto inexistente. Roda ANTES de ler o formulário e da guarda
    de RAM."""
    if not ID_ESPECIE.fullmatch(especie):
        return "especie_inexistente", contexto.STATUS_DA_RECUSA["especie_inexistente"]
    cat = contexto.catalogo()
    if especie not in cat.especies:
        return "especie_inexistente", contexto.STATUS_DA_RECUSA["especie_inexistente"]
    reg = cat.subprojetos.get(f"{especie}/{sub}") if _slug_valido(sub) else None
    if reg is None or not _visivel(reg, _autorizado(request)):
        return "subprojeto_inexistente", contexto.STATUS_DA_RECUSA["subprojeto_inexistente"]
    wf = None
    if tipo == "workflow":
        m = load_registry().por_id(mod_id)
        wf = next((w for w in m.workflows if w.id == wf_id), None) if m is not None else None
    ok, motivo = contexto.execucao_permitida(especie, sub, tipo, workflow=wf, cat=cat)  # type: ignore[arg-type]
    if not ok:
        return motivo, contexto.STATUS_DA_RECUSA[motivo]
    if mod_id not in contexto.modulos_habilitados(reg, cat):
        return "modulo_fora_do_subprojeto", contexto.STATUS_DA_RECUSA["modulo_fora_do_subprojeto"]
    return None


@app.post("/e/{especie}/{sub}/modulos/{mod_id}/scripts/{script_key}")
async def rodar_script_ctx(request: Request, especie: str, sub: str, mod_id: str, script_key: str):
    if not _mesma_origem(request):
        return _recusa(request, "origem_recusada", 403, especie=especie, sub=sub)
    negado = await run_in_threadpool(_permissao, request, especie, sub, mod_id, "script")
    if negado is not None:
        return _recusa(request, negado[0], negado[1], especie=especie, sub=sub)
    # permitido = bos_taurus/demo: o código da v1, com ctx=None (o cmd sai o da v1, byte a byte)
    valores = {k: str(v) for k, v in (await request.form()).items()}
    try:
        job_id, backend = dispatch.lancar_script(mod_id, script_key, valores=valores)
    except (guarda_ram.RecusadoPorRAM, queue.FilaIndisponivel,
            dispatch.RuntimeIndisponivel, KeyError, ValueError) as e:
        return RedirectResponse(f"{DEMO_URL}/modulos/{mod_id}?aviso={e}", status_code=303)
    return RedirectResponse(f"/jobs/{job_id}?backend={backend}", status_code=303)


@app.post("/e/{especie}/{sub}/modulos/{mod_id}/workflows/{wf_id}")
def rodar_workflow_ctx(request: Request, especie: str, sub: str, mod_id: str, wf_id: str):
    # síncrona (threadpool) como a v1: lancar_workflow grava status de cada passo
    if not _mesma_origem(request):
        return _recusa(request, "origem_recusada", 403, especie=especie, sub=sub)
    negado = _permissao(request, especie, sub, mod_id, "workflow", wf_id)
    if negado is not None:
        return _recusa(request, negado[0], negado[1], especie=especie, sub=sub)
    try:
        wf_run_id, _, backend = dispatch.lancar_workflow(mod_id, wf_id)
    except (guarda_ram.RecusadoPorRAM, queue.FilaIndisponivel,
            dispatch.RuntimeIndisponivel, KeyError, ValueError) as e:
        return RedirectResponse(f"{DEMO_URL}/modulos/{mod_id}?aviso={e}", status_code=303)
    return RedirectResponse(f"/workflows/{wf_run_id}?backend={backend}", status_code=303)


# Aliases v1 de bos_taurus/demo, com o código da v1 INTACTO. Sempre 303 (o plugin Bioinfo exige
# 303 + Location em /jobs/ ou /workflows/ e lê QUALQUER `aviso` na query como recusa): nada de
# 307/308 aqui, e o aviso de rota antiga vai só para o log. A recusa volta para
# /modulos/{mod}?aviso=, cujo GET faz o 307 para o demo.
@app.post("/modulos/{mod_id}/scripts/{script_key}")
async def rodar_script(request: Request, mod_id: str, script_key: str):
    _log.info("rota antiga POST /modulos/%s/scripts/%s (alias de %s)", mod_id, script_key, ID_DEMO)
    valores = {k: str(v) for k, v in (await request.form()).items()}
    try:
        job_id, backend = dispatch.lancar_script(mod_id, script_key, valores=valores)
    except (guarda_ram.RecusadoPorRAM, queue.FilaIndisponivel,
            dispatch.RuntimeIndisponivel, KeyError, ValueError) as e:
        return RedirectResponse(f"/modulos/{mod_id}?aviso={e}", status_code=303)
    return RedirectResponse(f"/jobs/{job_id}?backend={backend}", status_code=303)


@app.post("/modulos/{mod_id}/workflows/{wf_id}")
def rodar_workflow(request: Request, mod_id: str, wf_id: str):
    _log.info("rota antiga POST /modulos/%s/workflows/%s (alias de %s)", mod_id, wf_id, ID_DEMO)
    try:
        wf_run_id, _, backend = dispatch.lancar_workflow(mod_id, wf_id)
    except (guarda_ram.RecusadoPorRAM, queue.FilaIndisponivel,
            dispatch.RuntimeIndisponivel, KeyError, ValueError) as e:
        return RedirectResponse(f"/modulos/{mod_id}?aviso={e}", status_code=303)
    return RedirectResponse(f"/workflows/{wf_run_id}?backend={backend}", status_code=303)


# "voltar" de /autorizacao: só a home, uma espécie ou um subprojeto (nunca URL de fora)
_VOLTA_AUTORIZACAO = re.compile(r"/(e/[a-z][a-z0-9_]{2,31}(/[a-z][a-z0-9_]{2,47})?)?", re.ASCII)


@app.post("/autorizacao")
async def autorizacao(request: Request):
    """Token da API certo → cookie com o HMAC dele (privados visíveis neste navegador); token
    vazio → apaga o cookie (sair); errado → 403. Crítica v2, médio 16."""
    if not _mesma_origem(request):
        return _recusa(request, "origem_recusada", 403)
    form = await request.form(max_files=0, max_fields=4)
    dado = str(form.get("token") or "").strip()
    volta = str(form.get("volta") or "/")
    resposta = RedirectResponse(volta if _VOLTA_AUTORIZACAO.fullmatch(volta) else "/", status_code=303)
    if not dado:
        resposta.delete_cookie(_COOKIE_AUTOR, path="/")
        return resposta
    token = token_api()
    if not token or not _iguais(dado, token):
        _log.warning("autorização do autor recusada (token errado ou API sem token)")
        return _recusa(request, "autorizacao_recusada", 403)
    resposta.set_cookie(_COOKIE_AUTOR, _selo_autor(token), max_age=60 * 60 * 24 * 30,
                        httponly=True, samesite="strict", path="/")
    return resposta


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


def _contexto_do_job(j: dict) -> tuple[str, str]:
    """(espécie, slug) gravados no status.json; job de antes da v2 é do demo."""
    esp, sub = j.get("especie"), j.get("subprojeto")
    return (esp if isinstance(esp, str) else ESPECIE_LEGADA,
            sub if isinstance(sub, str) else SLUG_DEMO)


@app.get("/jobs", response_class=HTMLResponse)
def jobs(request: Request):
    aviso = request.query_params.get("aviso")
    f_esp = (request.query_params.get("especie") or "").strip()
    f_sub = (request.query_params.get("sub") or "").strip()
    filtrando = bool(f_esp or f_sub)
    # o filtro vem DEPOIS do corte de listar_jobs: com filtro, corta mais largo e só então em 50
    lista = queue.listar_jobs(limite=500) if filtrando else queue.listar_jobs()
    cat = contexto.catalogo()
    autorizado = _autorizado(request)
    itens = []
    for j in lista:
        esp, sub = _contexto_do_job(j)
        oculto = not autorizado and _privado_no_catalogo(cat, esp, sub)
        if filtrando and ((f_esp and esp != f_esp) or (f_sub and sub != f_sub) or oculto):
            continue
        itens.append({**j, "ctx_especie": esp, "ctx_sub": sub, "ctx_oculto": oculto,
                      "ctx_link": bool(ID_ESPECIE.fullmatch(esp) and _slug_valido(sub))})
    return _render(request, "jobs.html", jobs=itens[:50], aviso=aviso,
                   especies=contexto.listar_especies(cat), filtro_especie=f_esp, filtro_sub=f_sub)


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
    # job de subprojeto privado: o console traz o cmd com a pasta do subprojeto (médio 16)
    if status is None or _oculto(request, *_contexto_do_job(status)):
        return RedirectResponse("/jobs", status_code=303)
    return _render(request, "job.html", job=status, resultado=resultado_de_job(job_id))


@app.get("/jobs/{job_id}/events")
async def job_events(request: Request, job_id: str):
    """SSE: transmite o log do job linha a linha lendo o arquivo compartilhado."""
    # read_status lê o status.json e, numa troca em andamento, relê com time.sleep: fora do
    # event loop, senão cada console aberto parava o servidor a cada polling. O 1º status vem
    # ANTES do log: decide se o job é de subprojeto privado (o log traz o cmd com a pasta dele,
    # médio 16) e, lido antes, garante que um "done" já tem o log inteiro gravado.
    status = await run_in_threadpool(queue.read_status, job_id)
    if status is not None and await run_in_threadpool(_oculto, request, *_contexto_do_job(status)):
        return PlainTextResponse("", status_code=404)

    async def stream(status):
        caminho = queue.log_path(job_id)
        posicao = 0
        while True:
            if caminho.exists():
                with caminho.open("r", encoding="utf-8") as f:
                    f.seek(posicao)
                    for linha in f:
                        yield f"data: {linha.rstrip()}\n\n"
                    posicao = f.tell()
            if status and status.get("status") in ("done", "error"):
                yield f"event: fim\ndata: {status['status']}\n\n"
                break
            await asyncio.sleep(0.5)
            status = await run_in_threadpool(queue.read_status, job_id)

    return StreamingResponse(stream(status), media_type="text/event-stream")


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
    # cada botão dispara as MESMAS rotas determinísticas dos módulos (no demo, v2).
    aula = meta.get("passos") if meta.get("roteiro_aula") else None
    return _render(request, "ajuda_doc.html", doc=doc, corpo=corpo, aula=aula)


def _saude_dados() -> dict:
    return dados_saude()  # compartilhado com a API v1 (omica/saude.py)


@app.get("/skills", response_class=HTMLResponse)
def skills(request: Request):
    """Só o cabeçalho sai daqui; a lista vem de /api/skills. A página já foi o índice
    inteiro (330 KB) embutido, com busca por substring no navegador — que devolvia
    zero para "metilação", porque o índice é em inglês. `?q=` e `?dominio=` deixam a
    busca endereçável (é como a página do módulo e o Professor apontam para cá).
    Sem índice: 503 com o aviso (a "/" da v2 é a escolha da espécie, não explicava nada)."""
    indice = busca.carregar_indice()
    if indice is None:
        return _render(request, "skills.html", status_code=503, indice=None, q="", dominio="",
                       tem_glossario=False)
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

    # erros de subprojeto fora do repositório (privados) só detalhados para o autor (médio 16)
    return _render(request, "diagnostico.html",
                   checagens=doctor_rodar(checar_docker=False, detalhar_privados=_autorizado(request)))


@app.get("/resultados/{rel:path}")
def servir_resultado(request: Request, rel: str):
    """Serve UM arquivo de resultado (figura/tabela) de dentro de results_dir —
    caminho resolvido e validado para nunca sair do diretório de resultados."""
    base = get_config().results_dir.resolve()
    caminho = (base / rel).resolve()
    if not caminho.is_file() or base not in caminho.parents:
        return RedirectResponse("/", status_code=303)
    # results/projetos/<espécie>/<slug>/... de um subprojeto privado: só para o autor (médio 16)
    partes = caminho.relative_to(base).parts
    if len(partes) > 3 and partes[0] == "projetos" and _oculto(request, partes[1], partes[2]):
        return RedirectResponse("/", status_code=303)
    from fastapi.responses import FileResponse

    return FileResponse(caminho)


_FIGURA_RELATORIO = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-]{0,120}\.png", re.ASCII)


def _pasta_relatorio(run_id: str, request: Request) -> Optional[Path]:
    """<results>/<rel do run>/relatorio — o lugar sai de runs.pasta_resultados (espécie,
    subprojeto, módulo e run_id do run.json, conferidos), nunca da URL. Run corrompido
    (`run_corrompido`) ou ilegível → None (303), nunca 500 (crítica v2, médio 5). Run de
    subprojeto privado sem a autorização do autor → None (médio 16)."""
    from omica import runs

    try:
        run = json.loads((runs._pasta(run_id) / "run.json").read_text(encoding="utf-8"))
        if not isinstance(run, dict):
            return None
        pasta = runs.pasta_resultados(run) / "relatorio"
    except (runs.ErroRun, OSError, ValueError):
        return None
    if _oculto(request, run.get("especie"), run.get("subprojeto")):
        return None
    return pasta if (pasta / "relatorio.html").is_file() else None


@app.get("/relatorios/{run_id}")
def relatorio_sem_barra(request: Request, run_id: str):
    # a página cita as figuras por caminho RELATIVO (figuras/x.png): precisa da barra final
    if _pasta_relatorio(run_id, request) is None:
        return RedirectResponse("/", status_code=303)
    return RedirectResponse(f"/relatorios/{run_id}/", status_code=307)


@app.get("/relatorios/{run_id}/", response_class=HTMLResponse)
def relatorio(request: Request, run_id: str):
    """Relatório de um run, como o portão de números o aprovou (HTML já escapado no render;
    a CSP vai também no cabeçalho, além da meta da própria página)."""
    pasta = _pasta_relatorio(run_id, request)
    if pasta is None:
        return RedirectResponse("/", status_code=303)
    return HTMLResponse((pasta / "relatorio.html").read_text(encoding="utf-8"), headers={
        "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"})


@app.get("/relatorios/{run_id}/figuras/{nome}")
def relatorio_figura(request: Request, run_id: str, nome: str):
    pasta = _pasta_relatorio(run_id, request)
    if pasta is None or not _FIGURA_RELATORIO.fullmatch(nome):
        return RedirectResponse("/", status_code=303)
    base = (pasta / "figuras").resolve()
    caminho = (base / nome).resolve()
    if not caminho.is_file() or caminho.parent != base:
        return RedirectResponse("/", status_code=303)
    from fastapi.responses import FileResponse

    return FileResponse(caminho, media_type="image/png")


@app.get("/saude")
def saude(request: Request):
    """Lida pelo plugin Bioinfo (bioinfo_omica.py): chaves da v1 intactas. Os erros de carga de
    subprojeto fora do repositório (privados) saem resumidos sem a autorização do autor."""
    dados = _saude_dados()
    if "erros_subprojetos" in dados:
        dados["erros_subprojetos"] = redigir_erros_subprojetos(dados["erros_subprojetos"],
                                                               _autorizado(request))
    return JSONResponse(dados)
