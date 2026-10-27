"""v1.py — API JSON da plataforma para orquestradores externos (ex.: n8n).

As rotas HTML ficam como estão (a UI e o plugin Bioinfo dependem delas). Esta API existe
porque elas não servem a um orquestrador: lançar job devolvia 303 com o job_id só no header
`Location`, erros de RAM/parâmetro também voltavam 303 com `?aviso=`, e um corpo JSON era
ignorado em silêncio (o script rodava com os defaults).

Contrato:
- `Authorization: Bearer <OMICA_API_TOKEN>`. Sem token configurado, TUDO responde 503 —
  falha fechada: a API nunca fica aberta por esquecimento. O token é conferido ANTES de
  ler o corpo: sem credencial a resposta é 401/503, nunca um 422 sobre o corpo — nem um
  404/405 de rota ou método inexistente sob /api/v1.
- JSON e códigos HTTP de verdade: 401, 404, 405, 413, 415, 422, 429 (+ Retry-After), 500, 503.
- Todo erro sob /api/v1 tem o mesmo formato, `{"detail": {"erro": "<código>", ...}}`:
  validação de corpo e de query (que o FastAPI devolvia como lista, sem `erro`) aqui, na
  `_RotaV1`; rota inexistente (404 `rota_inexistente`), método errado (405
  `metodo_nao_permitido`) e exceção não tratada (500 `erro_interno`, sem a mensagem) nos
  handlers de omica/web/app.py, que só valem para este prefixo — a UI HTML não muda.
- Nenhuma rota aceita caminho livre nem comando: só o que está no catálogo.
"""
from __future__ import annotations

import re
import secrets
from pathlib import Path
from typing import Any, Callable, Coroutine, Iterable, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from omica import __version__, runs
from omica.config import get_config, token_api
from omica.jobs import dispatch, queue
from omica.jobs.guarda_ram import RecusadoPorRAM
from omica.pipelines import carregar_pipelines, envio_formulario
from omica.registry import load_registry
from omica.saude import dados_saude, estado_gpu

# Formato de job_id gerado por queue.novo_job_id: sem ponto nem barra. read_status() monta o
# caminho direto com o id — validar AQUI é o que impede `../..` de atravessar diretório.
_ID_JOB = re.compile(r"^[A-Za-z0-9_\-]{1,160}$")
_MAX_SHA_BYTES = 512 * 1024 * 1024  # acima disso não calcula sha256 na hora


def exigir_token(authorization: Optional[str] = Header(default=None)) -> None:
    esperado = token_api()  # relê o .env quando ele muda: trocar o valor revoga na hora
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


def _campos(erros: Iterable[Any]) -> list[dict[str, Any]]:
    """Erros do pydantic sem o `input`: ecoar o corpo recebido não ajuda o cliente e pode
    devolver de volta o que não devia (um caminho, um pedaço de arquivo)."""
    return [{"loc": list(e.get("loc", ())), "tipo": e.get("type"), "msg": str(e.get("msg", ""))[:200]}
            for e in list(erros)[:20]]


def _resposta_validacao(exc: RequestValidationError) -> JSONResponse:
    erros = list(exc.errors())
    no_corpo = any(tuple(e.get("loc", ()))[:1] == ("body",) for e in erros)
    return JSONResponse(status_code=422, content={"detail": {
        "erro": "corpo_invalido" if no_corpo else "parametro_invalido", "campos": _campos(erros)}})


class _RotaV1(APIRoute):
    """Rota da API v1: confere o token ANTES de o FastAPI ler o corpo, e devolve os erros de
    validação no formato do contrato.

    Por que aqui, e não na dependência do router: o FastAPI lê e decodifica o corpo das
    rotas com modelo pydantic ANTES de resolver as dependências. Sem isto, um cliente sem
    token recebia 422 `json_invalid` em vez de 401 (ou de 503 com a API desligada), e o
    servidor bufferizava o corpo inteiro de quem nem se autenticou. E o 422 padrão vinha
    com `detail` em LISTA: `$json.detail.erro` no n8n ficava undefined. Restrito a este
    router por construção: a UI HTML segue com o tratamento padrão do FastAPI.

    A dependência `exigir_token` continua no router (documentação e defesa redundante).
    Quando um teste a substitui por `dependency_overrides`, a conferência antecipada sai do
    caminho e a substituta decide, como antes."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            sobrescritas = getattr(self.dependency_overrides_provider, "dependency_overrides", None) or {}
            await conferir_token_antes(request, sobrescritas)
            try:
                return await original(request)
            except RequestValidationError as exc:
                return _resposta_validacao(exc)

        return handler


async def conferir_token_antes(request: Request, sobrescritas: dict[Any, Any]) -> None:
    """A conferência antecipada do token (401/503 levantados como HTTPException). Usada pela
    `_RotaV1` e pelo handler de erro HTTP do app (404/405 do roteamento, que nem chegam a uma
    rota). Quando `exigir_token` foi trocada por dependency_overrides, sai do caminho."""
    if exigir_token not in sobrescritas:
        # token_api() pode reler o .env: E/S fora do event loop
        await run_in_threadpool(exigir_token, request.headers.get("authorization"))


router = APIRouter(prefix="/api/v1", dependencies=[Depends(exigir_token)], tags=["api-v1"],
                   route_class=_RotaV1)
PREFIXO = router.prefix

# Código do contrato para os erros que o FRAMEWORK levanta com `detail` em texto (rota
# inexistente, método errado, corpo que o Starlette não conseguiu ler). Os das rotas já
# nascem com `{"erro": ...}`.
_CODIGO_HTTP = {400: "requisicao_invalida", 404: "rota_inexistente", 405: "metodo_nao_permitido"}


def na_api_v1(scope: dict[str, Any]) -> bool:
    """O caminho da requisição está sob /api/v1? (sem o root_path, como o roteador o vê)"""
    caminho, raiz = scope.get("path", ""), scope.get("root_path", "")
    if raiz and caminho.startswith(raiz):
        caminho = caminho[len(raiz):]
    return caminho == PREFIXO or caminho.startswith(PREFIXO + "/")


def detalhe_do_contrato(status: int, detail: Any, headers: Optional[dict[str, str]] = None) -> dict[str, Any]:
    """`detail` no formato `{"erro": ...}`. O que já está no formato passa intacto; texto do
    framework vira código + detalhe curto (o `Allow` do 405 vira `permitidos`)."""
    if isinstance(detail, dict) and "erro" in detail:
        return detail
    saida: dict[str, Any] = {"erro": _CODIGO_HTTP.get(status, f"http_{status}")}
    if status == 404:
        saida["detalhe"] = "nenhuma rota da API v1 neste caminho (lista em /api/docs)"
    elif status == 405:
        permitidos = (headers or {}).get("Allow", "")
        saida["permitidos"] = [m.strip() for m in permitidos.split(",") if m.strip()]
    elif detail:
        saida["detalhe"] = str(detail)[:200]
    return saida


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
    return runs.sha256_arquivo(caminho, _MAX_SHA_BYTES)


def _descrever(rel: str, caminho: Path, iniciado_em: Optional[float] = None) -> dict[str, Any]:
    """Metadados de um artefato — nunca o conteúdo. Só dentro da pasta de dados da
    plataforma: um `RESULTADO:` impresso por um script poderia apontar para qualquer
    arquivo do container, e nem tamanho nem hash de fora da raiz devem vazar.

    `bytes`/`sha256` são o estado do arquivo NO MOMENTO da consulta. Arquivo mais antigo
    que o início do job é marcado `anterior_ao_job` e vai SEM sha256: o script terminou sem
    reescrevê-lo, e o hash dele não pode ser tomado como resultado desta execução."""
    raiz = get_config().data_dir.resolve()
    try:
        caminho.resolve().relative_to(raiz)
    except ValueError:
        return {"rel": rel, "fora_da_raiz": True}
    if not caminho.exists():
        return {"rel": rel, "existe": False}
    if caminho.is_dir():
        return {"rel": rel, "existe": True, "diretorio": True}
    info = caminho.stat()
    if iniciado_em is not None and info.st_mtime < iniciado_em:
        return {"rel": rel, "existe": True, "bytes": info.st_size, "anterior_ao_job": True}
    return {"rel": rel, "existe": True, "bytes": info.st_size, "sha256": _sha256(caminho)}


def _artefatos(status: dict[str, Any]) -> list[dict[str, Any]]:
    """Revisão da F1 (EXP-2): com o job em queued/running/error, os arquivos nos caminhos
    declarados são de OUTRA execução (ou estão pela metade). Antes saíam com existe=true e
    sha256, e um nó do n8n seguiria adiante com o resultado antigo. Agora, fora de `done`,
    cada saída declarada vem só como `pendente`."""
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
    if status.get("status") != "done":
        return [{"rel": rel, "pendente": True} for rel, _ in alvos]
    inicio = status.get("iniciado_em")
    inicio = float(inicio) if isinstance(inicio, (int, float)) else None
    return [_descrever(rel, caminho, inicio) for rel, caminho in alvos]


def _cauda_log(job_id: str, linhas: int) -> list[str]:
    caminho = queue.log_path(job_id)
    if not caminho.exists() or linhas == 0:
        return []
    return caminho.read_text(encoding="utf-8", errors="replace").splitlines()[-linhas:]


@router.get("/jobs/{job_id}")
def job(job_id: str, linhas_log: int = Query(default=40, ge=0, le=500)) -> dict[str, Any]:
    if not _ID_JOB.fullmatch(job_id):
        raise HTTPException(422, detail={"erro": "job_id_invalido"})
    status = queue.read_status(job_id)
    if status is None:
        raise HTTPException(404, detail={"erro": "job_inexistente", "job_id": job_id})
    return {**status, "log_tail": _cauda_log(job_id, linhas_log), "artefatos": _artefatos(status)}


# ------------------------------------------------------------------ pipelines/runs (F2)
# O orquestrador só escolhe PIPELINE, envia ENTRADAS, faz as ESCOLHAS do autor e aprova o
# plano pelo sha. Nenhuma rota aceita parâmetro de script, caminho de saída ou run_id
# inventado: tudo isso nasce no servidor (omica/runs.py).
def _http(e: runs.ErroRun) -> HTTPException:
    return HTTPException(e.status, detail={"erro": e.erro, **e.detalhe})


@router.get("/pipelines")
def pipelines() -> dict[str, Any]:
    """Catálogo para o orquestrador. Por entrada, além do contrato (`modos` incluso), `envio`:
    "arquivo" (upload cru) ou "caminho" (`{"caminho"}` de um arquivo no servidor — formato com
    índice obrigatório ou entrada que aceita referência). Vem de `pipelines.envio_formulario`,
    a MESMA função que o builder do n8n usa para o tipo do campo do formulário (G1 §1.1)."""
    validos, erros = carregar_pipelines()
    return {
        "pipelines": [{"id": p.id, "modulo": p.modulo, "titulo": p.titulo, "descricao": p.descricao,
                       "entradas": {n: {**e.model_dump(), "envio": envio_formulario(e)}
                                    for n, e in p.entradas.items()},
                       "escolhas": {n: e.model_dump() for n, e in p.escolhas_autor.items()},
                       "passos": [x.id for x in p.passos], "selo": p.selo_pipeline.model_dump()}
                      for p in validos.values()],
        "erros": erros,
    }


class _NovoRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pipeline: str


class _Plano(BaseModel):
    model_config = ConfigDict(extra="forbid")
    escolhas: dict[str, str] = Field(default_factory=dict)


class _Aprovacao(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plano_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class _EntradaPorCaminho(BaseModel):
    model_config = ConfigDict(extra="forbid")
    caminho: str


@router.post("/runs", status_code=201)
def novo_run(corpo: _NovoRun) -> dict[str, Any]:
    try:
        return runs.criar(corpo.pipeline)
    except runs.ErroRun as e:
        raise _http(e) from None


@router.get("/runs/{run_id}")
def ver_run(run_id: str) -> dict[str, Any]:
    try:
        return runs.estado(run_id)
    except runs.ErroRun as e:
        raise _http(e) from None


_TIPO_JSON = "application/json"
_TIPO_CRU = "application/octet-stream"
_MAX_CORPO_JSON = 64 * 1024  # {"caminho": ...} cabe com folga; o arquivo vai cru


async def _ler_corpo(request: Request, limite: int, erro: str) -> bytes:
    """Lê o corpo em fluxo e para no teto, sem confiar no Content-Length."""
    partes: list[bytes] = []
    total = 0
    async for bloco in request.stream():
        total += len(bloco)
        if total > limite:
            raise HTTPException(413, detail={"erro": erro, "limite_bytes": limite})
        partes.append(bloco)
    return b"".join(partes)


@router.put("/runs/{run_id}/entradas/{nome}")
async def entrada(run_id: str, nome: str, request: Request) -> dict[str, Any]:
    """Duas formas, escolhidas SÓ pelo Content-Type (o resto é 415):

    - `application/json` com `{"caminho": ...}`: arquivo que já está no servidor. Só valem
      a caixa de entrada `<dados>/entradas/` e `modules/*/demo_data|data` (cópia) e as raízes
      de referência configuradas (sem cópia, se a entrada aceita o modo); quem decide o modo
      e confere é o runs.registrar_entrada. Corpo com teto de 64 KiB.
    - `application/octet-stream` com o arquivo CRU no corpo (n8n: body "n8n Binary File" +
      cabeçalho Content-Type explícito, como no workflow gerado). Teto de `runs._MAX_ENTRADA`.
      Antes de ler o corpo, `runs.aceita_upload` recusa o que nunca seria aceito por upload.

    Multipart, form-urlencoded, `text/*` e variantes de JSON dão 415: antes caíam no ramo
    cru e o envelope multipart (com boundary) virava o próprio arquivo, com 200 e um
    veredito enigmático. O tamanho é limitado lendo em fluxo, sem confiar no Content-Length.

    A rota é `async` só para ler o corpo. Registrar (trava do run, cópia, sha256, validação
    da matriz) é síncrono e demorado: roda no threadpool. Chamado direto daqui, congelava o
    event loop — e com ele a UI, o /saude e o polling do n8n — por segundos a minutos."""
    tipo = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if tipo not in (_TIPO_JSON, _TIPO_CRU):
        raise HTTPException(415, detail={
            "erro": "tipo_de_conteudo_nao_suportado", "recebido": tipo, "aceitos": [_TIPO_JSON, _TIPO_CRU],
            "detalhe": "envie o arquivo cru com Content-Type: application/octet-stream "
                       "ou JSON {\"caminho\": ...} com Content-Type: application/json"})
    try:
        if tipo == _TIPO_JSON:
            corpo = await _ler_corpo(request, _MAX_CORPO_JSON, "corpo_grande_demais")
            try:
                dados = _EntradaPorCaminho.model_validate_json(corpo)
            except ValidationError as e:
                raise HTTPException(422, detail={"erro": "corpo_invalido", "campos": _campos(e.errors())}) from None
            return await run_in_threadpool(runs.registrar_entrada, run_id, nome, caminho=dados.caminho)
        # G1 §1.4: o portão barato ANTES do corpo — entrada inexistente, formato que exige o
        # índice ao lado (VCF/BCF) e entrada só por referência são recusados sem ler até
        # 256 MiB que seriam jogados fora. O registrar_entrada repete as regras sob a trava.
        await run_in_threadpool(runs.aceita_upload, run_id, nome)
        conteudo = await _ler_corpo(request, runs._MAX_ENTRADA, "entrada_grande_demais")
        if not conteudo:
            raise HTTPException(422, detail={"erro": "corpo_vazio"})
        return await run_in_threadpool(runs.registrar_entrada, run_id, nome, conteudo=conteudo)
    except runs.ErroRun as e:
        raise _http(e) from None


class _Adaptacao(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # ausente = o agente decide (determinístico → LLM); presente = operações do AUTOR (form)
    ops: Optional[list[dict[str, Any]]] = Field(default=None, max_length=200)


@router.post("/runs/{run_id}/entradas/{nome}/adaptar")
def adaptar(run_id: str, nome: str, corpo: Optional[_Adaptacao] = None) -> dict[str, Any]:
    """Agente adaptador (F3). `ok=false` NÃO é erro HTTP: é o caso de levar ao autor — a
    resposta traz `excecoes` e `opcoes` (cabeçalhos REAIS para os dropdowns do formulário).
    503 `motor_indisponivel` = GPU reservada pelo dono ou LLM ocupado: esperar e repetir.
    Pode levar minutos (inferência local): o nó HTTP do n8n precisa de timeout ≥ 10 min."""
    try:
        return runs.adaptar_entrada(run_id, nome, ops_autor=corpo.ops if corpo else None)
    except runs.ErroRun as e:
        raise _http(e) from None


@router.post("/runs/{run_id}/plano")
def plano(run_id: str, corpo: _Plano) -> dict[str, Any]:
    try:
        return runs.propor_plano(run_id, corpo.escolhas)
    except runs.ErroRun as e:
        raise _http(e) from None


@router.post("/runs/{run_id}/aprovar")
def aprovar(run_id: str, corpo: _Aprovacao) -> dict[str, Any]:
    try:
        return runs.aprovar(run_id, corpo.plano_sha256)
    except runs.ErroRun as e:
        raise _http(e) from None


@router.post("/runs/{run_id}/abortar")
def abortar(run_id: str) -> dict[str, Any]:
    try:
        return runs.abortar(run_id)
    except runs.ErroRun as e:
        raise _http(e) from None


async def _sem_query(request: Request) -> None:
    """O mesmo invariante do `_sem_corpo`, na query string (revisão F2, resíduo do #36):
    `executar?padj=0.9&saida=/etc` dava 202 com a query ignorada em silêncio — e um Query
    param acrescentado um dia à rota passaria a mesclar valor fora do plano sem nenhum teste
    reprovar. Qualquer query não vazia é recusada, antes de ler o corpo."""
    if request.scope.get("query_string"):
        raise HTTPException(422, detail={
            "erro": "parametro_nao_aceito",
            "detalhe": "executar não aceita parâmetro na URL: o passo roda exatamente o plano aprovado"})


async def _sem_corpo(request: Request) -> None:
    """Executar roda SÓ o plano congelado. Um corpo era ignorado em silêncio — e nada
    impedia uma mudança futura de passar a lê-lo (parâmetro fora do plano aprovado). Agora
    qualquer byte é recusado, e o cliente que achava estar mudando algo fica sabendo.
    Dependência da ROTA: roda depois da do router, logo depois do token."""
    async for bloco in request.stream():
        if bloco:
            raise HTTPException(422, detail={
                "erro": "corpo_nao_aceito",
                "detalhe": "executar não aceita parâmetro: o passo roda exatamente o plano aprovado"})


@router.post("/runs/{run_id}/passos/{passo}/executar", status_code=202,
             dependencies=[Depends(_sem_query), Depends(_sem_corpo)])
def executar(run_id: str, passo: str) -> Any:
    try:
        return runs.executar_passo(run_id, passo)
    except runs.ErroRun as e:
        raise _http(e) from None
    except RecusadoPorRAM as e:
        # o n8n espera Retry-After e tenta de novo; nunca empurrar a máquina além de 80%
        return JSONResponse(status_code=429, headers={"Retry-After": "120"},
                            content={"detail": {"erro": "ram_insuficiente", "detalhe": str(e)}})
    except (queue.FilaIndisponivel, dispatch.RuntimeIndisponivel) as e:
        raise HTTPException(503, detail={"erro": "fila_indisponivel", "detalhe": str(e)}) from None


@router.get("/runs/{run_id}/passos/{passo}/envelope")
def ver_envelope(run_id: str, passo: str) -> dict[str, Any]:
    try:
        return runs.envelope(run_id, passo)
    except runs.ErroRun as e:
        raise _http(e) from None


@router.get("/runs/{run_id}/passos/{passo}/aguardar")
def aguardar(run_id: str, passo: str,
             segundos: float = Query(default=60, ge=0, le=runs.ESPERA_MAX_S)) -> dict[str, Any]:
    """Segura a resposta até o job do passo terminar (ou o prazo vencer): o laço do n8n fica
    só com HTTP. Timeout do nó HTTP > `segundos` + folga."""
    try:
        return runs.aguardar(run_id, passo, segundos)
    except runs.ErroRun as e:
        raise _http(e) from None


# ------------------------------------------------------------------- relatório (F4)
# O n8n pede o pacote (prompt versionado + ficha de fatos + esquema), chama o Claude com a
# credencial DELE e devolve o JSON do bloco de texto. A chave da Anthropic nunca passa aqui.
class _Rascunho(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rascunho: Any = None
    modelo: str = Field(default="", max_length=120)
    stop_reason: str = Field(default="", max_length=40)
    autor: bool = False  # edição do autor no formulário: mesmo portão, fora da cota automática


@router.post("/runs/{run_id}/relatorio/pedido")
def relatorio_pedido(run_id: str) -> dict[str, Any]:
    from omica.relatorio import servico

    try:
        return servico.pedido(run_id)
    except servico.ErroRelatorio as e:
        raise HTTPException(e.status, detail={"erro": e.erro, **e.detalhe}) from None


@router.post("/runs/{run_id}/relatorio/rascunho")
def relatorio_rascunho(run_id: str, corpo: _Rascunho) -> dict[str, Any]:
    """`ok=false` com `falhas` = o portão de números reprovou: reenviar ao redator com as
    falhas (até a cota automática) e depois ao autor. 409 `tentativas_esgotadas` = cota usada."""
    from omica.relatorio import servico

    try:
        return servico.receber(run_id, corpo.rascunho, modelo=corpo.modelo,
                               stop_reason=corpo.stop_reason, autor=corpo.autor)
    except servico.ErroRelatorio as e:
        raise HTTPException(e.status, detail={"erro": e.erro, **e.detalhe}) from None
