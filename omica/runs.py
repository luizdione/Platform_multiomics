"""runs.py — runs de pipeline orquestrados (n8n): entradas → plano → aprovação → passos → envelope.

Ciclo de vida (campo `estado` do run.json):
  criado → planejado → aprovado → executando → concluido | erro | abortado

O que este arquivo existe para garantir:
- O run_id é gerado AQUI, nunca vem do cliente (vira nome de pasta).
- Entrada por caminho só sai de uma CAIXA DE ENTRADA (<dados>/entradas, modules/*/demo_data e
  modules/*/data), conferida depois de resolver symlinks. O veredito de formato ecoa cabeçalho
  e 1ª linha: com uma raiz larga (/data = o E:\\data inteiro) a rota virava leitor de arquivos.
  A outra porta é a RAIZ DE REFERÊNCIA (G1, genômica): um BCF de 1,9 GB em /data/1000bg_vcf
  entra SEM cópia, só se a entrada do pipeline aceita o modo `referencia` e o nome tem o
  sufixo do formato — antes de qualquer stat ou sha (um CSV ou log da raiz nunca tem tamanho
  ou hash revelado). O modo é decidido pelo servidor (raiz × contrato), nunca pelo cliente.
- O índice (.tbi/.csi) é IRMÃO do arquivo: o servidor o deriva do nome do principal, nunca
  recebe o caminho dele; ele entra no registro, no plano e em toda conferência.
- Nada roda sem plano aprovado. O plano congela argv, caminhos, valores e recursos (runtime,
  fila, mem_gb, cpus) de TODOS os passos e é identificado pelo sha256 do JSON canônico do
  MESMO objeto que vai para o disco; aprovar exige esse sha.
- Executar um passo não aceita parâmetro nenhum: usa o plano congelado e recusa se o
  manifest (cmd ou recursos) ou o arquivo do script mudou desde a aprovação, ou se uma
  entrada foi trocada — conferida pelo PLANO (protegido pelo sha), não pelo run.json. O
  worker atesta de novo as entradas que o passo lê, ao começar e ao terminar, e o envelope
  compara os dois atestados com o plano.
- Cada passo grava em results/<mod>/runs/<run_id>/<passo>/ — o demo selado nunca é tocado.
- O envelope (o que o passo seguinte e o relatório recebem) é montado no SERVIDOR, com
  sha256, frescor e validação de formato de cada porta, e com o que o WORKER registrou: a
  proveniência ao começar a rodar (tem de ser a do plano) e o sha256 de cada porta ao
  terminar (`sha_saidas`). Porta mais nova que o fim do job, ou com sha diferente do que o
  worker atestou, reprova; o primeiro envelope aprovado fica gravado, e um sha diferente
  depois disso também reprova — é nele que o portão de números do relatório (F4) se apoia.
- Todo run chega a um estado terminal sem intervenção: job em erro, job órfão ou portão
  reprovado levam a `erro`; o último passo entregue, a `concluido`. A vaga de run ativo é
  contada pelo estado VIVO, e job na fila ou rodando a segura em qualquer estado; abortar
  cancela o que ainda está na fila.
- Lançar é em duas gravações: o passo fica marcado `lancando` no run.json ANTES de o job
  existir, e recebe o job_id depois. Um job nunca fica na fila sem registro no run.

Armazenamento: work/runs/<run_id>/{run.json, plano_proposto.json, plano.json,
envelopes/<passo>.json (entregue)}; entradas copiadas em raw/runs/<run_id>/<nome><sufixo>
(+ os índices irmãos); a entrada por referência fica onde está e nunca é apagada.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import stat
import time
from pathlib import Path
from typing import Any, Iterator, Optional

from omica import __version__, proveniencia
from omica.config import REPO_ROOT, get_config
from omica.contrato import bgzf, formatos
from omica.jobs import dispatch, queue, script_job
from omica.pipelines import Entrada, Pipeline, carregar_pipelines, spec_api
from omica.registry import Manifest, ScriptSpec, load_registry
from omica.travas import TravaOcupada, trava

_ID_RUN = re.compile(r"[a-z][a-z0-9_]*-[0-9]{8}-[0-9]{6}-[0-9a-f]{6}", re.ASCII)
_MAX_ENTRADA = 256 * 1024 * 1024
# entrada por REFERÊNCIA não é copiada: o teto é o do sha completo (o maior BCF do Run 8, o
# Chr1, tem 1,87 GB). Acima disso, 413 — revelar o tamanho não é oráculo: o arquivo já
# passou pela raiz e pelo sufixo.
_MAX_REFERENCIA = 4 * 1024 ** 3
_MAX_SHA = 512 * 1024 * 1024
_MAX_RESUMO = 1024 * 1024
_MAX_CAMINHO = 1024
_MAX_LISTA_PERFIL = 50
ATIVOS = ("aprovado", "executando")
ABERTOS = ("criado", "planejado")
# dispatch.lancar_script dá ao RQ job_timeout de 6 h: o próprio RQ mata o job e o marca
# 'error'. Um 'running' que passa disso (com folga) é de um worker que MORREU (OOM, reinício
# do container, queda de energia) — ninguém mais vai escrever no status.json dele.
_PRAZO_JOB_S = 6 * 3600
_FOLGA_ORFAO_S = 1800
# terminado_em vem do relógio do worker (VM do WSL2) e o mtime, do sistema de arquivos do
# host: os dois derivam entre si (segundos, mais depois de o host dormir)
_FOLGA_RELOGIO_S = 300
# o mtime do status.json 'done' e o das portas vêm do MESMO sistema de arquivos: só a
# resolução do carimbo de tempo separa os dois
_FOLGA_MTIME_S = 2.0
_TENTATIVAS_TROCA = 40
_TENTATIVAS_LEITURA = 12


class ErroRun(Exception):
    """Recusa de negócio com código HTTP sugerido (a API traduz; o núcleo não conhece HTTP)."""

    # só-posicionais: um detalhe chamado `status` (do job) não pode colidir com o código HTTP
    def __init__(self, status: int, erro: str, /, **detalhe: Any) -> None:
        super().__init__(erro)
        self.status, self.erro, self.detalhe = status, erro, detalhe


# ------------------------------------------------------------------ utilitários
def sha256_arquivo(caminho: Path, limite: int = _MAX_SHA) -> Optional[str]:
    if caminho.stat().st_size > limite:
        return None
    h = hashlib.sha256()
    with caminho.open("rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def _sha_canonico(obj: Any) -> str:
    texto = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _pasta(run_id: str) -> Path:
    if not _ID_RUN.fullmatch(run_id):
        raise ErroRun(422, "run_id_invalido", run_id=run_id)
    return get_config().work_dir / "runs" / run_id


def _ler(caminho: Path) -> dict[str, Any]:
    """Leitura com releitura curta. No host Windows quem lê no instante do os.replace de um
    escritor recebe PermissionError; no bind mount do Docker Desktop o arquivo some por um
    instante (FileNotFoundError). Nenhum dos dois quer dizer "não existe"."""
    for i in range(_TENTATIVAS_LEITURA):
        try:
            return json.loads(caminho.read_text(encoding="utf-8"))
        except (PermissionError, FileNotFoundError):
            if i == _TENTATIVAS_LEITURA - 1:
                raise
            time.sleep(0.001 * (i + 1))
    raise AssertionError("inalcançável")


def _trocar(tmp: Path, destino: Path) -> None:
    """os.replace com novas tentativas: no NTFS ele falha com PermissionError enquanto outro
    processo tem o destino aberto para leitura (o open() do Python não usa FILE_SHARE_DELETE).
    Sem isso, um leitor qualquer de E:\\data no host derrubava a gravação do run.json."""
    for i in range(_TENTATIVAS_TROCA):
        try:
            os.replace(tmp, destino)
            return
        except PermissionError:
            if i == _TENTATIVAS_TROCA - 1:
                raise
            time.sleep(0.002 * (i + 1))


def _gravar(caminho: Path, dados: dict[str, Any]) -> None:
    """Escrita atômica: um leitor concorrente (o polling do n8n) nunca vê JSON pela metade.
    O temporário nunca fica para trás, nem quando a troca desiste."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    tmp = caminho.with_name(f"{caminho.name}.{secrets.token_hex(4)}.tmp")
    try:
        tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
        _trocar(tmp, caminho)
    finally:  # depois de uma troca bem-sucedida ele já não existe
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


def _gravar_run(run_id: str, run: dict[str, Any]) -> None:
    """Toda gravação do run.json passa aqui e incrementa `versao`: é o que o CAS do estado()
    compara. Comparar só o `estado` sofria ABA — um relançamento grava 'executando' de novo,
    com OUTRO job, e o estado() atropelava com um 'erro' decidido sobre o job antigo."""
    run["versao"] = int(run.get("versao") or 0) + 1
    _gravar(_pasta(run_id) / "run.json", run)


@contextlib.contextmanager
def _trava(run_id: str, espera_s: float = 10.0) -> Iterator[None]:
    """Trava por run (de kernel, `omica.travas`). Duas chamadas de executar ao mesmo tempo —
    um retry do n8n, por exemplo — não podem lançar o mesmo passo duas vezes. Run inexistente
    é recusado ANTES de criar qualquer coisa: um id bem-formado mas falso não deixa pasta órfã."""
    pasta = _pasta(run_id)
    if not (pasta / "run.json").exists():
        raise ErroRun(404, "run_inexistente", run_id=run_id)
    with contextlib.ExitStack() as pilha:
        try:  # só a AQUISIÇÃO vira run_ocupado; exceção do corpo passa intacta
            pilha.enter_context(trava(pasta / ".trava", espera_s))
        except TravaOcupada:
            raise ErroRun(409, "run_ocupado", run_id=run_id) from None
        yield


@contextlib.contextmanager
def _trava_global(nome: str, **detalhe: Any) -> Iterator[None]:
    """Trava de kernel em work/runs/<nome>, para contagens entre runs (vagas, runs abertos)."""
    with contextlib.ExitStack() as pilha:
        try:
            pilha.enter_context(trava(get_config().work_dir / "runs" / nome, 10.0))
        except TravaOcupada:
            raise ErroRun(409, "run_ocupado", **detalhe) from None
        yield


@contextlib.contextmanager
def _trava_envelopes(run_id: str) -> Iterator[None]:
    """Serializa a conferência e a gravação dos envelopes entregues de um run. Ordem fixa:
    a trava do run (quando houver) antes desta; com esta presa nada mais é adquirido."""
    with contextlib.ExitStack() as pilha:
        try:
            pilha.enter_context(trava(_pasta(run_id) / "envelopes" / ".trava", 10.0))
        except TravaOcupada:
            raise ErroRun(409, "run_ocupado", run_id=run_id) from None
        yield


@contextlib.contextmanager
def _vaga_de_run(run_id: str) -> Iterator[None]:
    """Conta os runs ativos e ocupa a vaga sob uma trava GLOBAL. Com só a trava do próprio
    run, dois runs aprovados ao mesmo tempo viam zero ativos cada um e passavam os dois do
    limite. Ordem fixa: trava do run primeiro, depois esta (nunca o contrário)."""
    with _trava_global(".trava_vagas", run_id=run_id):
        limite = int(os.environ.get("OMICA_MAX_RUNS_ATIVOS", "1"))
        ativos = _runs_ativos(exceto=run_id)
        if len(ativos) >= limite:
            raise ErroRun(409, "limite_de_runs_ativos", ativos=ativos, limite=limite)
        yield


def _pipeline(pipeline_id: str) -> tuple[Pipeline, Manifest]:
    validos, erros = carregar_pipelines()
    if pipeline_id not in validos:
        raise ErroRun(404, "pipeline_inexistente", pipeline=pipeline_id,
                      erro_de_contrato=erros.get(f"{pipeline_id}.yaml"))
    p = validos[pipeline_id]
    m = load_registry().por_id(p.modulo)
    assert m is not None  # carregar_pipelines já conferiu
    return p, m


def _no_disco(caminho: str) -> Path:
    """Caminho de container/manifest → arquivo legível aqui. Relativo = relativo ao repo
    (modules/<m>/demo_data/...), como no cwd dos workers."""
    return Path(caminho) if caminho.startswith("/") or re.match(r"^[A-Za-z]:/", caminho) else REPO_ROOT / caminho


def _alvo_script(spec: ScriptSpec) -> Optional[str]:
    return proveniencia.alvo_do_cmd(spec.cmd)


def _sha_script(m: Manifest, spec: ScriptSpec) -> dict[str, Optional[str]]:
    """Proveniência do código que gerou cada passo (receita única em omica/proveniencia.py,
    a mesma que o worker grava ao começar a rodar)."""
    return proveniencia.sha_codigo(m.id, _alvo_script(spec))


def _selos_relacionados(m: Manifest, spec: ScriptSpec) -> list[dict[str, Any]]:
    """Workflows selados do módulo que rodam o MESMO arquivo de script: o relatório diz se
    o código deste passo tem selo vigente (R6 do portão de números, F4)."""
    from omica.selos import conferir

    alvo = _alvo_script(spec)
    usam = {s.key for s in m.scripts if _alvo_script(s) == alvo}
    vencidos = {s["workflow"]: s["vencido"] for s in conferir() if s["modulo"] == m.id}
    return [{"workflow": w.id, "vigente": not vencidos.get(w.id, True)}
            for w in m.workflows if usam & set(w.passos)]


def _runs_no_disco() -> Iterator[tuple[str, dict[str, Any]]]:
    raiz = get_config().work_dir / "runs"
    for arq in sorted(raiz.glob("*/run.json")) if raiz.exists() else []:
        try:
            r = _ler(arq)
        except (OSError, ValueError):
            continue
        if isinstance(r, dict) and isinstance(r.get("run_id"), str):
            yield r["run_id"], r


# ------------------------------------------------------------------------ ciclo
def criar(pipeline_id: str) -> dict[str, Any]:
    """Run novo. Runs ainda não aprovados (criado/planejado) têm teto,
    OMICA_MAX_RUNS_ABERTOS (padrão 20): cada um guarda cópias de entradas de até 256 MB, e
    criar runs em laço enchia o E:\\data, o disco dividido com a MD e o Boltz. A definição
    usada (passos, escolhas) fica no run.json: a visão do run não relê o catálogo."""
    p, _ = _pipeline(pipeline_id)
    limite = int(os.environ.get("OMICA_MAX_RUNS_ABERTOS", "20"))
    with _trava_global(".trava_abertos"):
        abertos = [rid for rid, r in _runs_no_disco() if r.get("estado") in ABERTOS]
        if len(abertos) >= limite:
            raise ErroRun(409, "limite_de_runs_abertos", abertos=len(abertos), limite=limite)
        run_id = f"{p.id}-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
        run = {"run_id": run_id, "pipeline": p.id, "modulo": p.modulo, "versao_omica": __version__,
               "criado_em": time.time(), "estado": "criado", "entradas": {}, "checagens": [],
               "plano_sha256": None, "passos": {}, "versao": 0}
        _derivar(p, run)
        _gravar_run(run_id, run)
    return estado(run_id)


def _carregar(run_id: str) -> dict[str, Any]:
    arq = _pasta(run_id) / "run.json"
    try:
        return _ler(arq)
    except FileNotFoundError:
        raise ErroRun(404, "run_inexistente", run_id=run_id) from None


def _raizes_de_entrada() -> list[Path]:
    """A CAIXA DE ENTRADA: <dados>/entradas (onde o operador põe, de propósito, o que um run
    pode ler) e os dados curados do repositório (modules/*/demo_data e modules/*/data). Nunca
    /data inteiro (todos os projetos do E:\\data), work/ (job.log, planos de outros runs) nem
    raw/runs de outros runs."""
    modulos = REPO_ROOT / "modules"
    candidatas = [get_config().data_dir / "entradas", *modulos.glob("*/demo_data"), *modulos.glob("*/data")]
    raizes = []
    for c in candidatas:
        with contextlib.suppress(OSError, RuntimeError):
            raizes.append(c.resolve(strict=True))
    return raizes


_DETALHE_CAMINHO = ("entrada por caminho só de <dados>/entradas/, modules/<módulo>/demo_data|data/ "
                    "ou das raízes de referência configuradas (arquivo existente)")


def _recusa_caminho() -> ErroRun:
    """O 422 de TODA recusa de caminho, com o mesmo corpo para todas as entradas: fora das
    raízes, inexistente, modo não aceito, nome sem o sufixo do formato, não regular, irmão
    fora da raiz. Corpos diferentes viravam oráculo de existência de arquivo."""
    return ErroRun(422, "caminho_recusado", detalhe=_DETALHE_CAMINHO)


def _fonte_da_entrada(caminho: str, ent: Entrada) -> tuple[Path, str, Path]:
    """Portão PRÓPRIO da entrada por caminho (o dispatch._validar_caminho é dos parâmetros de
    script e aceita /data de propósito). Devolve (arquivo resolvido, modo, raiz).

    Resolve antes de comparar — strict, seguindo symlink: um link apontando para fora é
    recusado. O MODO sai da raiz em que o caminho resolvido cai (caixa de entrada = cópia;
    raiz de referência = referência) e tem de estar na lista branca da entrada (`modos`,
    contrato). Na referência, ANTES de qualquer stat ou sha, o nome resolvido tem de ter o
    sufixo do formato e não ser arquivo em construção (crítica F5 #1 e #3: a raiz do Run 8
    tem CSV de amostras, cache de biosamples, log e script; sem isso o sha e o tamanho de
    qualquer um voltavam no GET do run). O caminho da referência vai CRU para o argv: tem de
    ser um token seguro (um espaço só estouraria depois, como parametro_recusado)."""
    recusa = _recusa_caminho()
    if not isinstance(caminho, str) or not caminho or len(caminho) > _MAX_CAMINHO:
        raise recusa
    try:
        fonte = _no_disco(caminho).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        raise recusa from None
    modo, raiz = "copia", next((r for r in _raizes_de_entrada() if fonte.is_relative_to(r)), None)
    if raiz is None:
        modo, raiz = "referencia", next((r for r in dispatch.raizes_referencia() if fonte.is_relative_to(r)), None)
    if raiz is None or modo not in ent.modos:
        raise recusa
    if modo == "referencia":
        texto = fonte.as_posix()
        if (not formatos.nome_de_referencia_ok(ent.formato, fonte.name) or ".." in texto
                or not dispatch._TOKEN_SEGURO.fullmatch(texto)):
            raise recusa
    try:
        regular = stat.S_ISREG(fonte.stat().st_mode)
    except OSError:
        raise recusa from None
    if not regular:  # FIFO, dispositivo: ler travaria ou não teria fim
        raise recusa
    return fonte, modo, raiz


def _irmaos_de(fonte: Path, formato: str, raiz: Path) -> dict[str, Path]:
    """Índices ao lado do principal, como o consumidor os procura: `<principal><sufixo>` para
    cada sufixo de `formatos.candidatos_irmaos` — o cliente nunca manda o caminho do índice.
    Irmão ausente não é recusa de caminho (quem reprova é o validador: "sem índice ao lado").
    Presente, tem de ser arquivo regular na MESMA raiz do principal, depois de resolver: um
    `x.bcf.csi` que é symlink para fora dá o mesmo 422 sem eco. E o arquivo RESOLVIDO também
    tem o sufixo do índice e não está em construção (crítica F5 #1, "nos irmãos, só os
    sufixos de IRMAOS"): um `x.vcf.gz.tbi` que é link para o CSV de amostras da mesma raiz
    teria o sha e o tamanho do CSV gravados no run.json."""
    irmaos: dict[str, Path] = {}
    for s in formatos.candidatos_irmaos(formato, fonte.name):
        p = Path(f"{fonte}{s}")
        if not os.path.lexists(p):
            continue
        try:
            resolvido = p.resolve(strict=True)
        except (OSError, RuntimeError):
            raise _recusa_caminho() from None
        if (not resolvido.name.endswith(s) or ".building." in resolvido.name
                or not resolvido.is_relative_to(raiz)):
            raise _recusa_caminho()  # antes de qualquer stat do alvo
        try:
            regular = stat.S_ISREG(resolvido.stat().st_mode)
        except OSError:
            raise _recusa_caminho() from None
        if not regular:
            raise _recusa_caminho()
        irmaos[s] = p
    return irmaos


def _copiar_entrada(destino: Path, *, conteudo: Optional[bytes], fonte: Optional[Path],
                    teto: Optional[int] = None) -> int:
    """Cópia para o run com o teto conferido em FLUXO (um stat() anterior não basta: um arquivo
    que cresce entre o stat e a cópia passava do limite), num temporário da mesma pasta que só
    vira o destino quando completo. `teto` = o que sobra do limite (principal + índices somam
    no mesmo _MAX_ENTRADA); devolve os bytes copiados."""
    limite = _MAX_ENTRADA if teto is None else teto
    grande = ErroRun(413, "entrada_grande_demais", limite_bytes=_MAX_ENTRADA)
    tmp = destino.with_name(f".{destino.name}.{secrets.token_hex(4)}.tmp")
    try:
        if conteudo is not None:
            if len(conteudo) > limite:
                raise grande
            tmp.write_bytes(conteudo)
            total = len(conteudo)
        else:
            assert fonte is not None
            if fonte.stat().st_size > limite:
                raise grande
            total = 0
            with fonte.open("rb") as r, tmp.open("wb") as w:
                while bloco := r.read(1 << 20):
                    total += len(bloco)
                    if total > limite:
                        raise grande
                    w.write(bloco)
        _trocar(tmp, destino)
        return total
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


def _copiar_mtime(origem: Path, destino: Path) -> None:
    """A cópia herda o mtime da origem (G1, decisão 14): o validador reprova índice mais velho
    que o dado, e a ORDEM da cópia (dado antes, índice depois) mascararia um índice velho na
    origem. Best-effort: a prova estrutural do índice continua sendo a barreira principal."""
    with contextlib.suppress(OSError):
        st = origem.stat()
        os.utime(destino, ns=(st.st_atime_ns, st.st_mtime_ns))


def _caminhos_do_registro(reg: Optional[dict[str, Any]]) -> list[str]:
    """Todo arquivo que um registro de entrada aponta: o lido, o original (F3) e os irmãos."""
    if not isinstance(reg, dict):
        return []
    saida = [r["caminho"] for r in (reg, reg.get("original")) if isinstance(r, dict) and r.get("caminho")]
    irmaos = reg.get("irmaos")
    if isinstance(irmaos, dict):
        saida += [i["caminho"] for i in irmaos.values() if isinstance(i, dict) and i.get("caminho")]
    return saida


def _apagar_copias_antigas(run_id: str, anterior: Optional[dict[str, Any]], manter: set[Path]) -> None:
    """Re-registrar uma entrada deixava órfãs a versão adaptada (F3), os índices e qualquer
    cópia de nome diferente — até 256 MB cada, para sempre. Só apaga DENTRO de
    raw/runs/<run_id>/ (pai exato): a origem de uma entrada por caminho e o arquivo de uma
    referência nunca são tocados."""
    if not anterior:
        return
    pasta = (get_config().raw_dir / "runs" / run_id).resolve()
    vigentes = {m.resolve() for m in manter}
    for caminho in _caminhos_do_registro(anterior):
        with contextlib.suppress(OSError):
            alvo = Path(caminho).resolve()
            if alvo.parent == pasta and alvo not in vigentes:
                alvo.unlink(missing_ok=True)


def _compactar_veredito(v: dict[str, Any]) -> dict[str, Any]:
    """O veredito vai para o run.json e volta em TODO GET do run: as listas do perfil (as
    amostras de um coldata de um milhão de linhas) ficam nas primeiras 50, com o total ao lado."""
    perfil = dict(v.get("perfil") or {})
    for k, val in list(perfil.items()):
        if isinstance(val, list) and len(val) > _MAX_LISTA_PERFIL:
            perfil[k], perfil[f"{k}_total"] = val[:_MAX_LISTA_PERFIL], len(val)
    return {**v, "perfil": perfil}


def _derivar(p: Pipeline, run: dict[str, Any]) -> None:
    """O que depende só das entradas (imutáveis, conferidas por sha) é calculado UMA vez,
    quando elas mudam, e guardado no run.json. O estado() é o alvo do polling do n8n: antes
    ele relia o coldata inteiro (e o catálogo de pipelines) a cada GET."""
    run["checagens"] = _checagens(p, run)
    run["escolhas_opcoes"] = _opcoes_escolhas(p, run)
    run["passos_pipeline"] = [x.id for x in p.passos]


def _upload_permitido(nome: str, ent: Entrada) -> None:
    """As duas regras do upload (G1 §1.4). Upload não traz o índice: um VCF sem .tbi/.csi ao
    lado só seria reprovado DEPOIS de ler até 256 MiB de corpo."""
    if ent.formato in formatos.IRMAOS:
        raise ErroRun(422, "entrada_exige_indice", entrada=nome,
                      detalhe="este formato precisa do índice ao lado (.tbi/.csi): envie o caminho do "
                              "arquivo no servidor, {\"caminho\": ...}; o índice é achado pelo nome")
    if "copia" not in ent.modos:
        raise ErroRun(422, "entrada_so_por_referencia", entrada=nome,
                      detalhe="esta entrada só aceita o caminho de um arquivo numa raiz de referência")


def aceita_upload(run_id: str, nome: str) -> None:
    """Portão BARATO do upload, para a rota chamar ANTES de ler o corpo (G1 §1.4): lê só o
    run.json e o contrato, sem trava. 409 entradas_congeladas (run aprovado ou adiante: o
    corpo seria lido para nada); 404 entrada_inexistente; 422
    entrada_exige_indice; 422 entrada_so_por_referencia — na ordem do registrar_entrada, que
    repete as regras sob a trava."""
    run = _carregar(run_id)
    if run["estado"] not in ABERTOS:
        raise ErroRun(409, "entradas_congeladas", estado=run["estado"])
    p, _ = _pipeline(run["pipeline"])
    if nome not in p.entradas:
        raise ErroRun(404, "entrada_inexistente", entrada=nome, esperadas=sorted(p.entradas))
    _upload_permitido(nome, p.entradas[nome])


def _registro_irmao(p: Path) -> dict[str, Any]:
    """Índice: pequeno, sha completo sempre (até o teto de índice do contrato; acima = None,
    e o validador já reprova)."""
    st = p.stat()
    return {"caminho": p.as_posix(), "bytes": st.st_size,
            "sha256": sha256_arquivo(p, bgzf.MAX_INDICE), "mtime_ns": st.st_mtime_ns}


def _irmaos_conferem(irmaos: dict[str, dict[str, Any]], v: formatos.Veredito) -> bool:
    """O índice registrado é o que o VALIDADOR leu: o perfil traz o sha dos bytes que ele
    abriu (`indices_sha256`). Um índice trocado entre o sha do registro e a validação, ou que
    apareceu no meio (o validador o viu e o registro não), não confere. Sem a chave no perfil
    (o validador parou antes do índice), o veredito já reprova."""
    lidos = (v.perfil or {}).get("indices_sha256")
    if not isinstance(lidos, dict):
        return True
    if set(lidos) - set(irmaos):
        return False
    return all(s not in lidos or lidos[s] == i["sha256"] for s, i in irmaos.items())


def _registrar_referencia(nome: str, ent: Entrada, fonte: Path, caminho: str,
                          irmaos_fonte: dict[str, Path]) -> dict[str, Any]:
    """Referência: nada é gravado em raw/runs. Impressão ANTES e DEPOIS do sha completo e da
    validação; se o arquivo (ou um índice) mudou no meio, 409 referencia_instavel e o
    run.json não muda — o que se aprova tem de ser o que se validou."""
    instavel = ErroRun(409, "referencia_instavel", entrada=nome,
                       detalhe="o arquivo ou o índice mudou durante o registro: repita quando a escrita terminar")
    try:
        imp1 = script_job.impressao_arquivo(fonte)
        if imp1["bytes"] > _MAX_REFERENCIA:
            raise ErroRun(413, "entrada_grande_demais", limite_bytes=_MAX_REFERENCIA)
        sha = sha256_arquivo(fonte, _MAX_REFERENCIA)
        irmaos = {s: _registro_irmao(p) for s, p in irmaos_fonte.items()}
        # o nome final É o da origem: a regra de nome roda uma vez só
        v = formatos.validar_com_nome(ent.formato, fonte)
        imp2 = script_job.impressao_arquivo(fonte)
    except OSError:
        raise instavel from None
    if ((imp1["bytes"], imp1["amostra_sha256"]) != (imp2["bytes"], imp2["amostra_sha256"])
            or not _irmaos_conferem(irmaos, v)):
        raise instavel
    return {"modo": "referencia", "caminho": fonte.as_posix(), "origem": f"referencia:{caminho}",
            "formato": ent.formato, "bytes": imp1["bytes"], "sha256": sha, "impressao": imp1,
            "irmaos": irmaos, "candidatos_irmaos": list(formatos.candidatos_irmaos(ent.formato, fonte.name)),
            "veredito": _compactar_veredito(v.to_dict())}


def _registrar_copia(run_id: str, nome: str, ent: Entrada, p: Pipeline, run: dict[str, Any], *,
                     conteudo: Optional[bytes], fonte: Optional[Path], origem: str,
                     irmaos_fonte: dict[str, Path]) -> tuple[dict[str, Any], dict[str, Any], set[Path]]:
    """Cópia ATÔMICA (crítica F5 #8 e 2ª parte da #6): principal e índices vão para uma pasta
    temporária com os nomes finais, são validados e derivados lá, e só então os candidatos
    velhos ao lado do destino são apagados e os novos entram no lugar. Antes, o re-registro
    apagava o .tbi ANTES de copiar: um 413 na cópia nova deixava o run.json com o registro
    antigo e o índice já sumido; e uma checagem que levantasse deixava a cópia nova órfã.
    Qualquer falha antes da troca não muda nada. Devolve (registro, run derivado, arquivos)."""
    pasta = get_config().raw_dir / "runs" / run_id
    pasta.mkdir(parents=True, exist_ok=True)
    final = pasta / f"{nome}{formatos.sufixo_de_destino(ent.formato, fonte.name if fonte else None)}"
    tmpdir = pasta / f".registro.{secrets.token_hex(4)}"
    tmpdir.mkdir()
    try:
        novo = tmpdir / final.name
        usados = _copiar_entrada(novo, conteudo=conteudo, fonte=fonte)
        novos: dict[str, Path] = {}
        for s, origem_irmao in irmaos_fonte.items():
            novos[s] = tmpdir / f"{final.name}{s}"
            usados += _copiar_entrada(novos[s], conteudo=None, fonte=origem_irmao, teto=_MAX_ENTRADA - usados)
        if fonte is not None:
            _copiar_mtime(fonte, novo)
            for s, destino_i in novos.items():
                _copiar_mtime(irmaos_fonte[s], destino_i)
        # no modo caminho a regra de nome roda também sobre a ORIGEM (crítica F5 #5b): um BED
        # 1-based '.txt' viraria 'regioes.bed' e passaria a ser lido 0-based em silêncio
        v = formatos.validar_com_nome(ent.formato, novo, nome_origem=fonte.name if fonte is not None else None)
        irmaos = {s: _registro_irmao(d) for s, d in novos.items()}
        if not _irmaos_conferem(irmaos, v):
            raise ErroRun(409, "entrada_alterada", entrada=nome, arquivo="indice")
        registro: dict[str, Any] = {"modo": "copia", "caminho": novo.as_posix(), "origem": origem,
                                    "formato": ent.formato, "bytes": novo.stat().st_size,
                                    "sha256": sha256_arquivo(novo),
                                    "veredito": _compactar_veredito(v.to_dict())}
        if ent.formato in formatos.IRMAOS:
            registro["irmaos"] = irmaos
            registro["candidatos_irmaos"] = list(formatos.candidatos_irmaos(ent.formato, final.name))
        derivado = {**run, "entradas": {**run["entradas"], nome: registro}}
        _derivar(p, derivado)  # checagens sobre a pasta temporária: os nomes são os finais
        # a troca: nenhum índice velho fica ao lado do principal novo (um .csi antigo seria o
        # que o htslib carrega)
        for s in formatos.candidatos_irmaos(ent.formato, final.name):
            if s not in novos:
                Path(f"{final}{s}").unlink(missing_ok=True)
        for s, destino_i in novos.items():
            _trocar(destino_i, Path(f"{final}{s}"))
        _trocar(novo, final)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    registro["caminho"] = final.as_posix()
    for s, i in registro.get("irmaos", {}).items():
        i["caminho"] = f"{final.as_posix()}{s}"
    return registro, derivado, {final, *(Path(f"{final}{s}") for s in novos)}


def registrar_entrada(run_id: str, nome: str, *, conteudo: Optional[bytes] = None,
                      caminho: Optional[str] = None) -> dict[str, Any]:
    """Entrada por upload (bytes) ou por caminho no servidor. Caminho na caixa de entrada (e
    todo upload) é COPIADO para raw/runs/<run_id>/, com os índices irmãos: o run fica
    imutável mesmo que a origem mude depois. Caminho numa raiz de referência, numa entrada que
    aceita esse modo, é registrado SEM cópia, com sha256 completo e impressão — a imutabilidade
    é conferida no aprovar, no executar e pelo worker (G1 §1.5)."""
    with _trava(run_id):
        run = _carregar(run_id)
        if run["estado"] not in ABERTOS:
            raise ErroRun(409, "entradas_congeladas", estado=run["estado"])
        p, _ = _pipeline(run["pipeline"])
        if nome not in p.entradas:
            raise ErroRun(404, "entrada_inexistente", entrada=nome, esperadas=sorted(p.entradas))
        if (conteudo is None) == (caminho is None):
            raise ErroRun(422, "entrada_ambigua", detalhe="envie o arquivo OU um caminho, não os dois")
        ent = p.entradas[nome]
        anterior = run["entradas"].get(nome)
        if conteudo is not None:
            _upload_permitido(nome, ent)
            registro, derivado, manter = _registrar_copia(
                run_id, nome, ent, p, run, conteudo=conteudo, fonte=None, origem="upload", irmaos_fonte={})
        else:
            assert caminho is not None
            fonte, modo, raiz = _fonte_da_entrada(caminho, ent)
            irmaos_fonte = _irmaos_de(fonte, ent.formato, raiz)
            if modo == "referencia":
                registro = _registrar_referencia(nome, ent, fonte, caminho, irmaos_fonte)
                derivado = {**run, "entradas": {**run["entradas"], nome: registro}}
                _derivar(p, derivado)
                manter = set()
            else:
                registro, derivado, manter = _registrar_copia(
                    run_id, nome, ent, p, run, conteudo=None, fonte=fonte, origem=f"caminho:{caminho}",
                    irmaos_fonte=irmaos_fonte)
        run["entradas"][nome] = registro
        for chave in ("checagens", "escolhas_opcoes", "passos_pipeline"):
            run[chave] = derivado[chave]
        # trocar uma entrada invalida o plano proposto: volta para "criado"
        run["estado"], run["plano_sha256"] = "criado", None
        _gravar_run(run_id, run)
        _apagar_copias_antigas(run_id, anterior, manter)
    return estado(run_id)


def adaptar_entrada(run_id: str, nome: str, *, ops_autor: Optional[list[dict[str, Any]]] = None,
                    chamar=None) -> dict[str, Any]:
    """Põe uma entrada JÁ registrada no formato do pipeline (agente adaptador, F3).

    O LLM roda FORA da trava do run: uma inferência leva minutos, e segurar a trava travaria
    o polling do n8n. Fotografa a entrada sob a trava, adapta sem ela e só grava se, ao
    retomar a trava, nada mudou no meio. Parte SEMPRE do arquivo original (adaptar de novo
    não empilha operações) e a adaptação entra no registro da entrada — logo no plano e no
    sha que o autor aprova: modo, operações, modelo, auditoria e os sha256 dos dois arquivos."""
    from omica.agentes import adaptador
    from omica.agentes.motor_local import MotorIndisponivel

    with _trava(run_id):
        run = _carregar(run_id)
        if run["estado"] not in ABERTOS:
            raise ErroRun(409, "entradas_congeladas", estado=run["estado"])
        p, _ = _pipeline(run["pipeline"])
        if nome in p.entradas and not p.entradas[nome].adaptavel:
            # o pipeline declara onde o LLM pode entrar; fora disso a entrada vem pronta
            raise ErroRun(409, "entrada_nao_adaptavel", entrada=nome)
        reg = run["entradas"].get(nome)
        if reg is None:
            raise ErroRun(404, "entrada_nao_registrada", entrada=nome)
        if reg.get("modo") == "referencia":
            # crítica F5 #13: a adaptação grava '.adaptando.*' e '.adaptado.tsv' AO LADO do
            # original — numa referência, dentro de /data/1000bg_vcf. O contrato já recusa
            # adaptavel + referencia; esta é a guarda em tempo de execução.
            raise ErroRun(409, "entrada_nao_adaptavel", entrada=nome,
                          detalhe="entrada por referência não é adaptada (a raiz de referência não é do servidor)")
        original = reg.get("original") or {k: reg[k] for k in ("caminho", "origem", "bytes", "sha256")}
        if original.get("sha256") is None:
            # L1: sem sha não há como provar que o original é o registrado (None == None não prova nada)
            raise ErroRun(409, "entrada_nao_atestavel", entrada=nome)
        foto, formato = reg["sha256"], reg["formato"]
    fonte = Path(original["caminho"])
    if not fonte.is_file() or sha256_arquivo(fonte) != original["sha256"]:
        raise ErroRun(409, "entrada_alterada", entrada=nome)
    tmp = fonte.with_name(f"{nome}.adaptando.{secrets.token_hex(4)}.tsv")
    try:
        try:
            res = adaptador.adaptar(fonte, formato, tmp, ops_autor=ops_autor, chamar=chamar)
        except MotorIndisponivel as e:
            # GPU reservada pelo dono, Ollama fora ou outra inferência na vez: nada foi decidido
            raise ErroRun(503, "motor_indisponivel", codigo=e.codigo, detalhe=e.detalhe) from None
        with _trava(run_id):
            run = _carregar(run_id)
            if run["estado"] not in ABERTOS:
                raise ErroRun(409, "entradas_congeladas", estado=run["estado"])
            atual = run["entradas"].get(nome)
            if atual is None or atual["sha256"] != foto:
                raise ErroRun(409, "entrada_mudou_durante_adaptacao", entrada=nome)
            if res.ok and res.modo != "nenhuma":
                destino = fonte.with_name(f"{nome}.adaptado.tsv")
                _trocar(tmp, destino)
                p, _ = _pipeline(run["pipeline"])
                run["entradas"][nome] = {
                    "modo": "copia",
                    "caminho": destino.as_posix(), "origem": original["origem"], "formato": formato,
                    "bytes": destino.stat().st_size, "sha256": sha256_arquivo(destino),
                    "veredito": _compactar_veredito(formatos.validar(formato, destino).to_dict()),
                    "original": original,
                    "adaptacao": {"modo": res.modo, "ops": res.ops, "modelo": res.modelo,
                                  "auditoria": res.auditoria, "relatorio": res.relatorio}}
                _derivar(p, run)
                run["estado"], run["plano_sha256"] = "criado", None
            run.setdefault("adaptacoes", {})[nome] = {"em": time.time(), "ok": res.ok, "modo": res.modo,
                                                      "excecoes": res.excecoes}
            _gravar_run(run_id, run)
    finally:
        tmp.unlink(missing_ok=True)
    return {"entrada": nome, **res.model_dump(), "run": estado(run_id)}


def _checagens(p: Pipeline, run: dict[str, Any]) -> list[dict[str, Any]]:
    saida = []
    for c in p.checagens_entrada:
        func, campos = formatos.CHECAGENS[c.tipo]
        alvos = [getattr(c, campo) for campo in campos]
        if all(a in run["entradas"] for a in alvos):
            saida.append(func(*[Path(run["entradas"][a]["caminho"]) for a in alvos]).to_dict())
    return saida


def _opcoes_escolhas(p: Pipeline, run: dict[str, Any]) -> dict[str, Any]:
    opcoes = {}
    for nome, esc in p.escolhas_autor.items():
        entrada, _, coluna = esc.valores_de.partition(".")
        reg = run["entradas"].get(entrada)
        valores = (formatos.valores_coluna(Path(reg["caminho"]), coluna)
                   if reg and reg["veredito"]["ok"] else None)
        opcoes[nome] = {"rotulo": esc.rotulo, "valores": valores}
    return opcoes


def _entradas_prontas(p: Pipeline, run: dict[str, Any]) -> None:
    faltam = [n for n in p.entradas if n not in run["entradas"]]
    if faltam:
        raise ErroRun(409, "entradas_faltando", faltam=faltam)
    ruins = {n: r["veredito"]["falhas"] for n, r in run["entradas"].items() if not r["veredito"]["ok"]}
    ruins.update({c["formato"]: c["falhas"] for c in run["checagens"] if not c["ok"]})
    if ruins:
        raise ErroRun(409, "entradas_reprovadas", falhas=ruins)


def _exigir_atestavel(nome: str, reg: dict[str, Any]) -> None:
    """L1: um sha esperado None nunca "confere" — `sha256_arquivo` também devolve None acima do
    teto, e None != None é falso: uma referência de 2 GB passaria sem conferência nenhuma.
    Vale para o arquivo lido, o original (F3), cada índice irmão (crítica F5 #14) e a
    impressão de uma referência."""
    esperados = [r.get("sha256") for r in (reg, reg.get("original")) if isinstance(r, dict)]
    irmaos = reg.get("irmaos")
    if isinstance(irmaos, dict):
        esperados += [i.get("sha256") if isinstance(i, dict) else None for i in irmaos.values()]
    if reg.get("modo") == "referencia":
        imp = reg.get("impressao")
        esperados += [imp.get("amostra_sha256") if isinstance(imp, dict) else None]
    if any(e is None for e in esperados):
        raise ErroRun(409, "entrada_nao_atestavel", entrada=nome)


def _candidatos_irmaos(reg: dict[str, Any]) -> list[str]:
    """Os sufixos que o consumidor procura ao lado do arquivo, congelados no registro (e no
    plano); registro sem a chave (antigo, ou formato sem irmão) = os do contrato pelo nome."""
    cands = reg.get("candidatos_irmaos")
    if isinstance(cands, list):
        return [str(s) for s in cands]
    return list(formatos.candidatos_irmaos(str(reg.get("formato") or ""), Path(str(reg.get("caminho") or "")).name))


def _irmaos_intactos(nome: str, reg: dict[str, Any]) -> None:
    """O CONJUNTO de índices presentes ao lado tem de ser o registrado (um .csi que aparece ao
    lado de um VCF aprovado só com .tbi muda o que o htslib lê; um índice removido derruba a
    extração), e cada um com o sha registrado."""
    alterado = ErroRun(409, "entrada_alterada", entrada=nome, arquivo="indice")
    registrados = reg.get("irmaos") if isinstance(reg.get("irmaos"), dict) else {}
    presentes = {s for s in _candidatos_irmaos(reg) if os.path.lexists(f"{reg['caminho']}{s}")}
    if presentes != set(registrados):
        raise alterado
    for s, i in registrados.items():
        try:
            agora = sha256_arquivo(Path(f"{reg['caminho']}{s}"), bgzf.MAX_INDICE)
        except OSError:
            raise alterado from None
        if agora is None or agora != i.get("sha256"):
            raise alterado


def _entradas_intactas(entradas: dict[str, Any]) -> None:
    """Confere as entradas pelo PLANO (L3): `proposto["entradas"]` no aprovar e
    `plano["entradas"]` no executar — os dois protegidos pelo sha que o autor aprovou. Antes
    lia o run.json, que não é: trocar arquivo e registro juntos passava.
    Cópia: sha completo (≤ 256 MiB). Referência: só a impressão (≤ ~20 MiB lidos) — o sha
    completo foi tirado no registro e é tirado de novo pelo worker no fim do job."""
    for nome, reg in entradas.items():
        _exigir_atestavel(nome, reg)
        if reg.get("modo") == "referencia":
            try:
                imp = script_job.impressao_arquivo(Path(reg["caminho"]))
            except OSError:
                raise ErroRun(409, "entrada_alterada", entrada=nome) from None
            esperado = reg["impressao"]
            if (imp["bytes"], imp["amostra_sha256"]) != (esperado.get("bytes"), esperado.get("amostra_sha256")):
                raise ErroRun(409, "entrada_alterada", entrada=nome)
        else:
            # adaptada: o arquivo que o script lê E o original de onde ele saiu (proveniência)
            for r in (reg, reg.get("original")) if reg.get("original") else (reg,):
                arq = Path(r["caminho"])
                if not arq.is_file() or sha256_arquivo(arq, _MAX_SHA) != r["sha256"]:
                    raise ErroRun(409, "entrada_alterada", entrada=nome)
        _irmaos_intactos(nome, reg)


# Tudo do registro de uma entrada vai ao plano, menos o veredito (que fica no run.json)
_CAMPOS_DA_ENTRADA_NO_PLANO = ("caminho", "formato", "bytes", "sha256", "origem", "original", "adaptacao",
                               "modo", "impressao", "irmaos", "candidatos_irmaos")


def propor_plano(run_id: str, escolhas: dict[str, str]) -> dict[str, Any]:
    """Monta o plano COMPLETO: todo valor que cada script vai receber (defaults explícitos),
    a pasta de saída, o argv final e as portas. Todos os valores passam pelo sanitizador
    aqui — um plano que existe é um plano que roda sem surpresa de parâmetro.

    Resíduo declarado (crítica F5 #10): as REFERÊNCIAS FIXAS dos scripts (o GTF e o banco do
    SnpEff, literais no cmd do manifest; o cache do VEP, default do vep_anotacao.py) ficam
    FORA do plano — sem impressão nem sha. O argv congelado prova QUAL caminho foi usado, não
    o conteúdo dele: trocar o banco muda o resultado sem o plano acusar. Fechar isso pede o
    manifest declarar as referências de cada script (o contrato hoje não tem esse campo)."""
    with _trava(run_id):
        run = _carregar(run_id)
        if run["estado"] not in ABERTOS:
            raise ErroRun(409, "plano_congelado", estado=run["estado"])
        p, m = _pipeline(run["pipeline"])
        _entradas_prontas(p, run)
        # as opções guardadas quando as entradas chegaram (as mesmas que o formulário mostrou);
        # recalcula só se o catálogo mudou as escolhas desde então
        opcoes = run.get("escolhas_opcoes")
        if not isinstance(opcoes, dict) or set(opcoes) != set(p.escolhas_autor):
            opcoes = _opcoes_escolhas(p, run)
        extras = sorted(set(escolhas) - set(p.escolhas_autor))
        if extras:
            raise ErroRun(422, "escolha_desconhecida", escolhas=extras)
        for nome, op in opcoes.items():
            if escolhas.get(nome) not in (op["valores"] or []):
                raise ErroRun(422, "escolha_fora_do_dado", escolha=nome,
                              recebido=escolhas.get(nome), validos=op["valores"])

        cfg = get_config()
        base = cfg.results_dir / p.modulo / "runs" / run_id
        resolvidos: dict[str, dict[str, Any]] = {}
        passos = []
        for passo in p.passos:
            spec = spec_api(m, passo.script)
            assert spec is not None  # validar_contrato já garantiu
            valores: dict[str, str] = {}
            for param, ref in passo.liga.items():
                origem, _, campo = ref.partition(".")
                if origem == "entrada":
                    valores[param] = run["entradas"][campo]["caminho"]
                elif origem == "escolha":
                    valores[param] = escolhas[campo]
                else:
                    valores[param] = resolvidos[origem]["portas"][campo]["caminho"]
            for param, v in passo.params.items():
                if v.startswith("="):
                    origem, _, outro = v[1:].partition(".")
                    valores[param] = resolvidos[origem]["valores"][outro]
                else:
                    valores[param] = v
            for par in spec.params:
                valores.setdefault(par.name, par.default)
            saida = (base / passo.id).as_posix()
            try:
                cmd = dispatch.montar_cmd(spec, valores, saida=saida)
            except ValueError as e:
                raise ErroRun(422, "parametro_recusado", passo=passo.id, detalhe=str(e)) from None
            portas = {nome: {"arquivo": porta.arquivo, "formato": porta.formato,
                             "caminho": f"{saida}/{porta.arquivo}"}
                      for nome, porta in spec.produz.items()}
            resolvidos[passo.id] = {"valores": valores, "portas": portas}
            passos.append({
                "id": passo.id, "script": spec.key, "titulo": spec.titulo,
                "runtime": spec.runtime, "fila": dispatch.fila_para_runtime(spec.runtime),
                "mem_gb": spec.mem_gb, "cpus": spec.cpus, "tempo_estimado": spec.tempo_estimado,
                "liga": passo.liga, "valores": valores, "saida": saida, "cmd": cmd,
                "argv": shlex.split(cmd), "portas": portas,
                "proveniencia": _sha_script(m, spec), "selos": _selos_relacionados(m, spec),
            })
        plano = {
            "formato": "omica.plano/1", "run_id": run_id, "pipeline": p.id, "modulo": p.modulo,
            "versao_omica": __version__, "proposto_em": time.time(),
            # a adaptação (modo, operações, modelo, sha do original) entra no sha aprovado; o
            # modo, a impressão e os índices também (um campo fora desta lista ficaria fora
            # do sha sem aviso: o canário estrutural em test_runs_referencia confere)
            "entradas": {n: {k: r[k] for k in _CAMPOS_DA_ENTRADA_NO_PLANO if k in r}
                         for n, r in run["entradas"].items()},
            "escolhas": dict(escolhas),
            "passos": passos,
            "recursos": {"pico_mem_gb": max((x["mem_gb"] for x in passos), default=0),
                         "tempos": [x["tempo_estimado"] for x in passos]},
            "relatorio": p.relatorio.model_dump(),
        }
        # O sha é do objeto que VOLTA do disco, não do que está em memória: uma chave não-texto
        # vinda do YAML (top: {10: ..., 9: ...}) muda de ordem ao virar texto, e o aprovar
        # (que relê o arquivo) recusava um plano legítimo como "adulterado".
        plano = json.loads(json.dumps(plano, ensure_ascii=False))
        plano["plano_sha256"] = _sha_canonico(plano)
        _gravar(_pasta(run_id) / "plano_proposto.json", plano)
        run["estado"], run["plano_sha256"] = "planejado", None
        run["escolhas_opcoes"], run["passos_pipeline"] = opcoes, [x.id for x in p.passos]
        _gravar_run(run_id, run)
    return plano


def _sem_sha(plano: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in plano.items() if k != "plano_sha256"}


def aprovar(run_id: str, plano_sha256: str) -> dict[str, Any]:
    with _trava(run_id):
        run = _carregar(run_id)
        if run["estado"] != "planejado":
            raise ErroRun(409, "nada_a_aprovar", estado=run["estado"])
        proposto = _ler(_pasta(run_id) / "plano_proposto.json")
        if _sha_canonico(_sem_sha(proposto)) != proposto.get("plano_sha256"):
            raise ErroRun(409, "plano_adulterado")
        if not secrets.compare_digest(str(plano_sha256), proposto["plano_sha256"]):
            # o autor aprovou OUTRO plano (ex.: entradas trocadas depois do formulário)
            raise ErroRun(409, "plano_divergente", esperado=proposto["plano_sha256"])
        _entradas_intactas(proposto["entradas"])
        with _vaga_de_run(run_id):
            _gravar(_pasta(run_id) / "plano.json", proposto)
            run["estado"], run["plano_sha256"], run["aprovado_em"] = "aprovado", proposto["plano_sha256"], time.time()
            _gravar_run(run_id, run)
    return estado(run_id)


def _plano_congelado(run_id: str, run: dict[str, Any]) -> dict[str, Any]:
    arq = _pasta(run_id) / "plano.json"
    if not run.get("plano_sha256") or not arq.exists():
        raise ErroRun(409, "plano_nao_aprovado", estado=run["estado"])
    plano = _ler(arq)
    if (_sha_canonico(_sem_sha(plano)) != run["plano_sha256"]
            or plano.get("plano_sha256") != run["plano_sha256"]):
        raise ErroRun(409, "plano_adulterado")
    return plano


# ------------------------------------------------------------------ jobs vivos
def _epoch(valor: Any) -> Optional[float]:
    return float(valor) if isinstance(valor, (int, float)) and not isinstance(valor, bool) else None


def _situacao(status: Optional[dict[str, Any]]) -> str:
    """Status do job como o run o enxerga. 'ilegivel' = status.json ausente ou estragado (o
    queue já releu): NÃO é "não lançado". 'orfao' = 'running' além do prazo do RQ (o RQ mata
    e marca 'error' quem passa do job_timeout; quem continua 'running' depois disso não tem
    mais worker), e o run vai a `erro`, com o passo relançável. 'queued' NUNCA vira órfão: a
    fila de um runtime tem um worker só e pode esperar horas atrás de um job longo da
    epigenômica; um job perdido na fila (Redis apagado) se resolve abortando o run, que
    cancela o que está na fila (`queue.cancelar`) e libera a vaga."""
    if status is None:
        return "ilegivel"
    st = str(status.get("status") or "desconhecido")
    inicio = _epoch(status.get("iniciado_em"))
    prazo = _epoch(status.get("timeout_s")) or _PRAZO_JOB_S
    if st == "running" and inicio is not None and time.time() - inicio > prazo + _FOLGA_ORFAO_S:
        return "orfao"
    return st


def _eh_marcador(reg: Any) -> bool:
    """Passo marcado `lancando` sem job_id: o executar gravou o marcador e não chegou a gravar
    o job (falhou ou o processo morreu no meio)."""
    return isinstance(reg, dict) and bool(reg.get("lancando")) and not reg.get("job_id")


def _ids_anteriores(reg: Optional[dict[str, Any]]) -> list[str]:
    if not reg:
        return []
    return [*reg.get("anteriores", []), *([reg["job_id"]] if reg.get("job_id") else [])]


def _jobs_do_marcador(run_id: str, passo_id: str, marcador: dict[str, Any]) -> tuple[list[str], bool]:
    """Jobs que um lançamento interrompido pode ter posto na fila. O status.json de todo job de
    run traz run_id e passo (extra_meta), e é gravado pelo enqueue ANTES de o job ir ao RQ: sem
    status.json com este run/passo (fora os jobs anteriores do passo), nada foi enfileirado.
    Devolve (candidatos, incerto); incerto = um status.json recente do mesmo script ilegível,
    que pode ser o dele. Só olha pastas com o prefixo do job_id (o de dispatch.lancar_script)
    e mexidas depois do marcador (com a folga do relógio)."""
    raiz = get_config().jobs_dir
    if not raiz.exists():
        return [], False
    fora = set(_ids_anteriores(marcador.get("anterior")))
    desde = (_epoch(marcador.get("lancando_em")) or 0.0) - _FOLGA_RELOGIO_S
    prefixo = marcador.get("prefixo")
    candidatos: list[str] = []
    incerto = False
    for pasta in sorted(raiz.glob(f"{prefixo}-*" if isinstance(prefixo, str) and prefixo else "*")):
        if pasta.name in fora:
            continue
        try:
            # pasta sem status.json: a 1ª gravação do enqueue falhou e nada foi à fila
            if pasta.stat().st_mtime < desde or not (pasta / "status.json").exists():
                continue
        except OSError:
            continue
        st = queue.read_status(pasta.name)
        if st is None:
            incerto = True
        elif st.get("cancelado"):
            # o próprio enqueue o cancelou (falha no RQ): o worker o recusa, nunca roda —
            # contá-lo tornaria incerto um lançamento que tem UM job vivo de verdade
            continue
        elif st.get("run_id") == run_id and st.get("passo") == passo_id:
            candidatos.append(pasta.name)
    return candidatos, incerto


def _reconciliar_marcadores(run_id: str, run: dict[str, Any]) -> bool:
    """Resolve, no `run` em memória, os marcadores `lancando` que sobraram (revisão F2, #13).
    Chamar só com a _trava do run presa: o executar a segura do marcador até o registro do job,
    então um marcador visto sob ela é de um lançamento que JÁ acabou. Um job do run/passo na
    fila → adotado (era a 2ª gravação que tinha falhado); nenhum → o passo volta ao que era
    (nada foi enfileirado); incerto → fica, e o executar recusa com 409 lancamento_incerto.
    Devolve True se mudou o run (quem chama grava)."""
    mudou = False
    for pid, reg in list(run["passos"].items()):
        if not _eh_marcador(reg):
            continue
        candidatos, incerto = _jobs_do_marcador(run_id, pid, reg)
        if incerto or len(candidatos) > 1:
            continue
        anterior = reg.get("anterior")
        if candidatos:
            novo = {"job_id": candidatos[0], "lancado_em": reg.get("lancando_em"),
                    "backend": "desconhecido", "reconciliado_em": time.time()}
            if anterior:
                novo["anteriores"] = _ids_anteriores(anterior)
            run["passos"][pid] = novo
            if run.get("estado") in ("aprovado", "erro"):
                run["estado"] = "executando"
                run.pop("motivo_erro", None)
        elif anterior:
            run["passos"][pid] = anterior
        else:
            del run["passos"][pid]
        mudou = True
    return mudou


def _entrega_intacta(run_id: str, passo_id: str, job_id: str) -> bool:
    """Checagem LEVE de um passo já entregue (revisão F2, #7): o estado() é o alvo do polling
    do n8n e roda sob a trava global de vagas em _runs_ativos; refazer sha256 + validação de
    formato de TODAS as portas entregues a cada GET custava ~250 ms com uma de_table de 60 mil
    genes (e cresce até o teto de 512 MB). Aqui só o stat: tamanho, mtime, ctime e inode
    iguais aos da entrega. No container (Linux) o ctime muda com QUALQUER escrita ou os.utime
    — uma troca de conteúdo não passa; o que não bate cai no envelope completo. Quem CONSOME
    a porta (o executar do passo seguinte, o GET do envelope) monta sempre o envelope inteiro."""
    arq = _pasta(run_id) / "envelopes" / f"{passo_id}.json"
    if not arq.exists():
        # passo ainda não entregue: o _ler releria ~12 vezes à espera de um bind mount que
        # "sumiu" — em todo GET e com a trava global de vagas presa
        return False
    try:
        entregue = _ler(arq)
    except (OSError, ValueError):
        return False
    assinatura, portas = entregue.get("assinatura_fs"), entregue.get("portas")
    if (entregue.get("job_id") != job_id or not isinstance(assinatura, dict)
            or not isinstance(portas, dict) or set(assinatura) != set(portas)):
        return False
    if _situacao(queue.read_status(job_id)) != "done":
        return False
    for nome, esperado in assinatura.items():
        try:
            if _assinatura(Path(portas[nome]["caminho"]).stat()) != esperado:
                return False
        except (OSError, KeyError, TypeError):
            return False
    return True


def _assinatura(st: os.stat_result) -> list[int]:
    return [st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino]


def _decidir(run_id: str, run: dict[str, Any], plano: dict[str, Any]) -> tuple[str, dict[str, Any], Optional[dict[str, Any]]]:
    """Estado VIVO do run pelo status de cada job e pelo portão de cada passo lançado. Não
    grava o run.json (quem grava é o estado(), com CAS) — e o _runs_ativos usa a MESMA
    decisão, então a vaga é liberada no fim mesmo que ninguém faça GET do run.

    Terminal: job em 'error', job órfão, ou 'done' com o portão reprovado (porta fora do
    formato, ausente, alterada, proveniência divergente). Antes só 'error' levava a erro, e
    um rc=0 com a tabela vazia prendia o run em 'executando' — e a única vaga — para sempre.
    Status ilegível não decide nada: sem saber se o job vive, o run fica como está. Passo já
    entregue e intacto no stat não remonta o envelope (`_entrega_intacta`)."""
    vivos: dict[str, Any] = {}
    reprovados: dict[str, list[str]] = {}
    entregue: dict[str, bool] = {}
    for x in plano["passos"]:
        pid = x["id"]
        reg = run["passos"].get(pid)
        if not reg:
            vivos[pid] = {"job_id": None, "status": "nao_lancado"}
            continue
        if _eh_marcador(reg):
            vivos[pid] = {"job_id": None, "status": "lancando"}
            continue
        if _entrega_intacta(run_id, pid, reg["job_id"]):
            vivos[pid] = {"job_id": reg["job_id"], "status": "done"}
            entregue[pid] = True
            continue
        env = envelope(run_id, pid, _run=run, _plano=plano)
        vivos[pid] = {"job_id": reg["job_id"], "status": env["estado"]}
        entregue[pid] = bool(env["portao"]["ok"])
        if not env["portao"]["ok"] and not env["portao"]["pendente"]:
            reprovados[pid] = env["portao"]["falhas"][:5]
    ultimo = plano["passos"][-1]["id"]
    if reprovados:
        return "erro", vivos, reprovados
    if entregue.get(ultimo):
        return "concluido", vivos, None
    if run["estado"] == "erro" and any(v["status"] in ("queued", "running") for v in vivos.values()):
        return "executando", vivos, None
    return run["estado"], vivos, None


def _jobs_do_passo(run_id: str, passo_id: str, reg: dict[str, Any]) -> list[str]:
    """O job registrado do passo ou, num marcador `lancando`, os que o status.json liga a ele."""
    if _eh_marcador(reg):
        return _jobs_do_marcador(run_id, passo_id, reg)[0]
    return [reg["job_id"]] if reg.get("job_id") else []


def _job_vivo(run_id: str, run: dict[str, Any]) -> bool:
    """Algum passo com job na fila ou rodando. Ilegível não conta: a vaga não pode ficar
    presa para sempre a um status.json apagado."""
    return any(_situacao(queue.read_status(j)) in ("queued", "running")
               for pid, p in (run.get("passos") or {}).items()
               for j in _jobs_do_passo(run_id, pid, p))


def _ocupa_vaga(run_id: str, run: dict[str, Any]) -> bool:
    """Job vivo SEMPRE ocupa a vaga, qualquer que seja o estado do run: abortar cancela o job
    que ainda está na fila, mas o que já roda termina sozinho ("abortar → aprovar outro"
    rodava dois pipelines com OMICA_MAX_RUNS_ATIVOS=1), e um run vai a `erro` com o passo
    seguinte ainda rodando quando a saída de um passo anterior é alterada depois de entregue.
    Abortado e erro olham SÓ o job vivo, sem montar envelope: runs em erro se acumulam com o
    tempo, e esta contagem roda sob a trava global de vagas a cada aprovar/executar.
    Aprovado/executando seguem o estado VIVO (`_decidir`); lançamento sem job registrado
    (`lancando`) segura a vaga — pode haver um job na fila."""
    est = run.get("estado")
    if est in ("abortado", "erro"):
        return _job_vivo(run_id, run)
    if est not in ATIVOS:
        return False
    try:
        novo, vivos, _ = _decidir(run_id, run, _plano_congelado(run_id, run))
    except (ErroRun, OSError, ValueError, KeyError, TypeError):
        return True  # sem como decidir, vale o persistido (ativo)
    return novo in ATIVOS or any(v["status"] in ("queued", "running", "lancando") for v in vivos.values())


def _runs_ativos(exceto: str = "") -> list[str]:
    """Runs que ocupam vaga pelo estado VIVO, não pelo run.json persistido: um run cujo
    último passo já entregou, mas que ninguém consultou, não segura a vaga; um run abortado
    ou em erro com job vivo segura."""
    return [rid for rid, r in _runs_no_disco() if rid != exceto and _ocupa_vaga(rid, r)]


def _recursos_divergentes(spec: ScriptSpec, item: dict[str, Any]) -> dict[str, Any]:
    """runtime (a imagem: versão do R, do DESeq2), fila e recursos que o autor aprovou. O
    lancar_script enfileira com os do manifest ATUAL: trocá-los depois da aprovação rodava o
    passo noutra imagem, ou com reserva de RAM menor que a aprovada, sem o plano acusar."""
    try:
        fila = dispatch.fila_para_runtime(spec.runtime)
    except dispatch.RuntimeIndisponivel as e:
        fila = f"indisponível: {e}"
    atual = {"runtime": spec.runtime, "fila": fila, "mem_gb": spec.mem_gb, "cpus": spec.cpus}
    return {k: {"aprovado": item.get(k), "atual": v} for k, v in atual.items() if item.get(k) != v}


def _conferir_relancamento(run_id: str, passo_id: str, atual: dict[str, Any],
                           run: dict[str, Any], plano: dict[str, Any]) -> None:
    """Relançar só quando o job anterior acabou SEM entregar: 'error', órfão (worker morto) ou
    'done' com o portão reprovado e nada entregue. 'done' entregue e depois alterado é
    adulteração, não falha de execução: não se relança por cima. Status ilegível NÃO é "não
    lançado" — sem saber se o job vive, relançar punha dois jobs na mesma pasta."""
    status = queue.read_status(atual["job_id"])
    if status is None:
        raise ErroRun(409, "status_ilegivel", job_id=atual["job_id"])
    sit = _situacao(status)
    if sit in ("error", "orfao"):
        return
    if sit == "done" and not (_pasta(run_id) / "envelopes" / f"{passo_id}.json").exists():
        portao = envelope(run_id, passo_id, _run=run, _plano=plano)["portao"]
        if not portao["ok"] and not portao["pendente"]:
            return
    raise ErroRun(409, "passo_ja_lancado", job_id=atual["job_id"], status=sit)


def executar_passo(run_id: str, passo_id: str) -> dict[str, Any]:
    """Lança UM passo do plano congelado. Sem parâmetros: tudo vem do plano aprovado.
    Todo lançamento disputa a vaga (trava global): um run que saiu da conta de ativos — em
    erro, ou com o portão reprovado ainda não consultado — não volta a rodar furando o limite.

    Duas gravações (revisão F2, #13): o marcador `lancando` vai ao run.json ANTES de
    enfileirar e o job_id, depois. Antes o job ia para a fila primeiro: se a gravação do
    run.json falhasse (ENOSPC no E:\\data, arquivo preso por antivírus), a API respondia 500,
    o retry do n8n lançava um 2º job na MESMA pasta de saída. Agora o retry encontra o
    marcador, adota o job que o status.json liga a este run/passo e responde
    passo_ja_lancado; sem como saber se há job, 409 lancamento_incerto."""
    with _trava(run_id):
        run = _carregar(run_id)
        if run["estado"] not in ("aprovado", "executando", "erro"):
            raise ErroRun(409, "run_nao_executavel", estado=run["estado"])
        if _reconciliar_marcadores(run_id, run):
            _gravar_run(run_id, run)
        plano = _plano_congelado(run_id, run)
        ids = [x["id"] for x in plano["passos"]]
        if passo_id not in ids:
            raise ErroRun(404, "passo_inexistente", passo=passo_id, passos=ids)
        idx = ids.index(passo_id)
        for anterior in ids[:idx]:
            env = envelope(run_id, anterior, _run=run, _plano=plano)
            if not env["portao"]["ok"]:
                raise ErroRun(409, "passo_anterior_pendente", passo=anterior,
                              estado=env["estado"], falhas=env["portao"]["falhas"])
        atual = run["passos"].get(passo_id)
        if _eh_marcador(atual):
            candidatos, _ = _jobs_do_marcador(run_id, passo_id, atual)
            raise ErroRun(409, "lancamento_incerto", passo=passo_id, candidatos=candidatos,
                          detalhe="um lançamento anterior foi interrompido e não dá para saber "
                                  "se deixou job na fila; abortar o run cancela o que estiver lá")
        if atual:
            _conferir_relancamento(run_id, passo_id, atual, run, plano)
        item = plano["passos"][idx]
        m = load_registry().por_id(plano["modulo"])
        spec = spec_api(m, item["script"]) if m else None
        if spec is None:
            raise ErroRun(409, "manifest_mudou", detalhe=f"{item['script']} saiu de scripts_api")
        try:
            cmd = dispatch.montar_cmd(spec, item["valores"], saida=item["saida"])
        except (ValueError, KeyError) as e:  # KeyError: placeholder sem param (manifest mexido)
            raise ErroRun(409, "manifest_mudou", detalhe=str(e)) from None
        if cmd != item["cmd"]:
            raise ErroRun(409, "manifest_mudou", detalhe="o cmd remontado difere do aprovado")
        diferencas = _recursos_divergentes(spec, item)
        if diferencas:
            raise ErroRun(409, "manifest_mudou", detalhe="runtime/recursos diferem do aprovado",
                          diferencas=diferencas)
        if _sha_script(m, spec) != item["proveniencia"]:
            raise ErroRun(409, "script_mudou", detalhe="o arquivo do script mudou desde a aprovação")
        _entradas_intactas(plano["entradas"])
        extra_meta: dict[str, Any] = {
            "run_id": run_id, "passo": passo_id,
            # o worker atesta o sha de cada porta ao terminar (envelope, #10)
            "portas": {n: p["caminho"] for n, p in item["portas"].items()}}
        consumidas = _entradas_consumidas(item)
        if consumidas:
            # e as entradas que o passo lê, ao começar e ao terminar (G1 §1.8): o esperado sai
            # do plano aprovado, nunca do run.json
            extra_meta["entradas"] = {n: _espec_da_entrada(plano["entradas"][n]) for n in consumidas}

        with _vaga_de_run(run_id):
            # 1ª gravação: o lançamento fica registrado ANTES de existir job. O prefixo é o
            # do job_id que dispatch.lancar_script gera (é por ele que o retry acha o job).
            marcador: dict[str, Any] = {"job_id": None, "lancando": True, "lancando_em": time.time(),
                                        "prefixo": f"{plano['modulo']}-{item['script']}"}
            if atual:
                marcador["anterior"] = atual
            run["passos"][passo_id] = marcador
            _gravar_run(run_id, run)
            try:
                job_id, backend = dispatch.lancar_script(
                    plano["modulo"], item["script"], valores=item["valores"],
                    extra_meta=extra_meta, api=True, saida=item["saida"])
            except BaseException:
                # nada ficou na fila (o enqueue marca 'error' o status que chegou a gravar): o
                # passo volta ao que era; se nem isso gravar, o retry reconcilia o marcador
                if atual:
                    run["passos"][passo_id] = atual
                else:
                    run["passos"].pop(passo_id, None)
                with contextlib.suppress(OSError):
                    _gravar_run(run_id, run)
                raise
            novo = {"job_id": job_id, "lancado_em": time.time(), "backend": backend}
            if atual:  # o job anterior fica no histórico do passo (auditoria do relançamento)
                novo["anteriores"] = _ids_anteriores(atual)
            run["passos"][passo_id] = novo
            run["estado"] = "executando"
            run.pop("motivo_erro", None)
            _gravar_run(run_id, run)  # 2ª gravação: o job_id
    return {"run_id": run_id, "passo": passo_id, "job_id": job_id, "backend": backend}


def abortar(run_id: str) -> dict[str, Any]:
    """Marca o run como abortado: nenhum passo novo é lançado, e o job que ainda está NA FILA
    é cancelado (`queue.cancelar`: status 'error' com "cancelado ao abortar", e fora do RQ).
    O job que já está rodando termina sozinho (o envelope dele continua consultável) e, até
    terminar, o run continua ocupando a vaga (`_ocupa_vaga`). Revisão F2 (#11): sem cancelar,
    um job perdido na fila (Redis reiniciado sem persistência) prendia a vaga para sempre —
    'queued' nunca vira órfão, e abortar de novo dava run_encerrado.
    Cancela ANTES de gravar 'abortado': se a gravação falhar, o run vai a `erro` pelo job
    cancelado e pode ser abortado de novo. Abortar de novo um run JÁ abortado só é aceito se
    ainda houver job dele na fila (o cancelar da 1ª vez não o alcançou: status ilegível
    naquele instante, trava do job ocupada) — cancela e responde 200; sem nada a cancelar,
    409 run_encerrado, como antes. Run que nunca lançou passo tem as cópias das
    entradas apagadas (raw/runs/<run_id>): nada mais as lê, e criar → enviar → abortar em
    laço não pode encher o E:\\data. Só essa pasta: uma entrada por REFERÊNCIA (/data/...)
    nunca é apagada."""
    with _trava(run_id):
        run = _carregar(run_id)
        if run["estado"] == "concluido":
            raise ErroRun(409, "run_encerrado", estado=run["estado"])
        ja_abortado = run["estado"] == "abortado"
        _reconciliar_marcadores(run_id, run)
        cancelados = [j for pid, p in run["passos"].items() for j in _jobs_do_passo(run_id, pid, p)
                      if queue.cancelar(j, "cancelado ao abortar")]
        if ja_abortado and not cancelados:
            raise ErroRun(409, "run_encerrado", estado="abortado")
        if not ja_abortado:
            run["estado"], run["abortado_em"] = "abortado", time.time()
        if cancelados:
            run["jobs_cancelados"] = [*run.get("jobs_cancelados", []), *cancelados]
        apagar = not run["passos"]  # (já abortado só chega aqui com job cancelado: tem passo)
        if apagar:
            run["entradas_apagadas"] = True
        _gravar_run(run_id, run)
        if apagar:
            shutil.rmtree(get_config().raw_dir / "runs" / run_id, ignore_errors=True)
    return estado(run_id)


# --------------------------------------------------------------------- envelope
def _ler_resumo(caminho: Path) -> Any:
    """O resumo vai ao envelope como veio; ilegível vira None (o veredito do formato já
    reprova a porta). Roda no GET do run (polling do n8n): JSON aninhado fundo demais
    (RecursionError do json) e número com dígitos demais (ValueError do limite do int) não
    podem virar 500 — mesmas exceções de formatos.resumo_json. ValueError cobre também
    JSONDecodeError e UnicodeDecodeError."""
    try:
        if caminho.stat().st_size > _MAX_RESUMO:
            return {"_omitido": "resumo maior que 1 MB"}
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None


def _conferir_execucao(status: dict[str, Any], item: dict[str, Any]) -> list[str]:
    """O que o WORKER registrou ao rodar tem de ser o que o autor aprovou. O executar confere
    o sha do script ao enfileirar, mas o job pode esperar minutos na fila (worker único por
    fila, repositório em bind mount) enquanto o arquivo é editado ou o branch trocado: vale a
    proveniência tirada no instante de rodar (`proveniencia_execucao`, mesma receita de
    omica/proveniencia.py). Ausente = não atestável: falha, nunca "ok por omissão"."""
    falhas = []
    prov = status.get("proveniencia_execucao")
    if prov is None:
        falhas.append("o worker não registrou a proveniência da execução (proveniencia_execucao ausente)")
    elif prov != item["proveniencia"]:
        falhas.append("o código que rodou difere do aprovado (script ou _comum.py trocado "
                      "entre a fila e a execução)")
    for campo in ("cmd", "runtime", "fila", "mem_gb"):
        if status.get(campo) != item.get(campo):
            falhas.append(f"{campo} do job difere do plano aprovado")
    return falhas


def _fim_do_job(job_id: str, status: dict[str, Any]) -> tuple[Optional[float], float]:
    """Instante em que o job terminou, e a folga para compará-lo com o mtime das portas: o
    mtime do próprio status.json do job 'done' — o 'done' é a ÚLTIMA gravação do worker e
    passa pelo MESMO sistema de arquivos das portas (mesmo relógio, folga curta). É a 2ª
    barreira contra a porta trocada depois do fim do job; a 1ª é o sha256 que o worker atesta
    (`sha_saidas`), que não depende do carimbo de tempo.
    L4 (levantamento F5): HOJE NENHUM worker grava `terminado_em` (o queue só grava
    `iniciado_em`), então o ramo que vale é o do mtime. O ramo do `terminado_em` (relógio da
    VM do WSL2, que deriva do mtime do host: folga larga) fica para um worker que o grave —
    e gravá-lo AFROUXARIA a folga de 2 s para 300 s; não é ganho."""
    fim = _epoch(status.get("terminado_em"))
    if fim is not None:
        return fim, _FOLGA_RELOGIO_S
    try:
        return (get_config().jobs_dir / job_id / "status.json").stat().st_mtime, _FOLGA_MTIME_S
    except OSError:
        return None, 0.0


def _sha_do_worker(status: dict[str, Any]) -> tuple[Optional[dict[str, Any]], list[str]]:
    """O sha256 de cada porta que o WORKER gravou ao terminar (`sha_saidas`, na mesma gravação
    do 'done'). A referência é o que o JOB deixou, não o que algum leitor viu depois: antes,
    a 1ª leitura do envelope fotografava as portas, e uma troca entre o fim do job e essa
    leitura que preservasse o mtime (os.utime, cp -p, rsync -t) era selada como saída do
    script (revisão F2, #10). Por job, no status.json dele: um leitor com o run.json de antes
    de um relançamento não troca a referência do job novo. Ausente ou estragado = não
    atestável: falha, nunca "ok por omissão"."""
    sha = status.get("sha_saidas")
    if not isinstance(sha, dict):
        return None, ["o worker não atestou as saídas (sha_saidas ausente ou ilegível no status do job)"]
    return sha, []


def _conferir_sha_do_worker(nome: str, sha_worker: dict[str, Any], agora: Optional[str]) -> list[str]:
    if nome not in sha_worker:
        return [f"{nome}: o worker não atestou esta porta ao terminar"]
    if sha_worker[nome] is None:
        return [f"{nome}: o worker não tirou sha256 ao terminar (arquivo ausente naquele "
                "instante — escrito depois do fim do job — ou acima do teto)"]
    if agora is not None and agora != sha_worker[nome]:
        return [f"{nome}: difere do que o job deixou ao terminar (alterada depois do fim do job)"]
    return []


def _entradas_consumidas(item: dict[str, Any]) -> list[str]:
    """Entradas do run que o passo lê (as `entrada.X` do liga), em ordem estável."""
    return sorted({ref.split(".", 1)[1] for ref in (item.get("liga") or {}).values()
                   if isinstance(ref, str) and ref.startswith("entrada.")})


def _espec_da_entrada(reg: dict[str, Any]) -> dict[str, Any]:
    """O que o worker tem de encontrar ao ler uma entrada, tirado do registro CONGELADO no
    plano. É o que vai no status do job (`entradas`) e o que o envelope remonta do plano para
    comparar com o atestado do worker — nunca o que o status.json diz que era."""
    irmaos = reg.get("irmaos") if isinstance(reg.get("irmaos"), dict) else {}
    esp: dict[str, Any] = {
        "caminho": reg.get("caminho"), "modo": reg.get("modo") or "copia", "formato": reg.get("formato"),
        "bytes": reg.get("bytes"), "sha256": reg.get("sha256"),
        "irmaos": {s: (i.get("sha256") if isinstance(i, dict) else None) for s, i in irmaos.items()},
        "candidatos_irmaos": _candidatos_irmaos(reg)}
    if esp["modo"] == "referencia":
        esp["impressao"] = reg.get("impressao")
    return esp


def _conferir_entradas_do_worker(status: dict[str, Any], item: dict[str, Any],
                                 plano: dict[str, Any]) -> list[str]:
    """O que o WORKER atestou das entradas que o passo lê (G1 §1.8/§1.9), contra o PLANO:
    ao começar (`entradas_execucao`: sha da cópia ou impressão da referência, e os índices) e
    ao terminar (`sha_entradas`: sha completo). Fecha a janela da fila (L2) — o executar
    confere as entradas, e o job pode esperar horas antes de rodar — e o resíduo da impressão
    por amostragem. O caminho e os candidatos a índice atestados têm de ser os do plano
    (crítica F5 #9: o status.json não é protegido por sha). Ausente = não atestável: falha
    fechada, como em `_sha_do_worker`. Nenhuma E/S: só compara valores gravados."""
    nomes = _entradas_consumidas(item)
    if not nomes:
        return []
    inicio, fim = status.get("entradas_execucao"), status.get("sha_entradas")
    if not isinstance(inicio, dict) or not isinstance(fim, dict):
        return ["o worker não atestou as entradas (entradas_execucao/sha_entradas ausente)"]
    falhas: list[str] = []
    for n in nomes:
        reg = (plano.get("entradas") or {}).get(n)
        if not isinstance(reg, dict):
            falhas.append(f"{n}: entrada consumida ausente do plano")
            continue
        if not isinstance(inicio.get(n), dict) or not isinstance(fim.get(n), dict):
            falhas.append(f"{n}: o worker não atestou esta entrada")
            continue
        esperado = _espec_da_entrada(reg)
        no_inicio = script_job.divergencias(esperado, inicio[n], completo=False)
        no_fim = script_job.divergencias(esperado, fim[n], completo=True)
        if "caminho" in no_inicio | no_fim:
            falhas.append(f"{n}: o worker atestou outro arquivo (caminho, modo ou candidatos a índice "
                          "diferentes do plano)")
        if "conteudo" in no_inicio:
            falhas.append(f"{n}: a entrada lida pelo job difere da aprovada (início)")
        if "conteudo" in no_fim:
            falhas.append(f"{n}: a entrada lida pelo job difere da aprovada (sha256 ao terminar)")
        if "indice" in no_inicio | no_fim:
            falhas.append(f"{n}: índice da entrada difere do aprovado")
    return falhas


def envelope(run_id: str, passo_id: str, *, _run: Optional[dict[str, Any]] = None,
             _plano: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """`omica.envelope/1`: o que um passo entregou, conferido. O portão só passa com o job
    `done`, rodado com o código e o comando do plano (o que o worker registrou), cada porta
    existente, escrita POR ESTE job (iniciado_em ≤ mtime ≤ fim do job — o `terminado_em` do
    worker ou, sem ele, o mtime do status.json 'done'), atestável por sha256, no formato
    declarado, igual ao que o worker atestou ao terminar (`sha_saidas`) e ao primeiro
    envelope entregue — e com as ENTRADAS que o passo leu atestadas pelo worker, no início e
    no fim, iguais às do plano (`_conferir_entradas_do_worker`)."""
    run = _run or _carregar(run_id)
    plano = _plano or _plano_congelado(run_id, run)
    item = next((x for x in plano["passos"] if x["id"] == passo_id), None)
    if item is None:
        raise ErroRun(404, "passo_inexistente", passo=passo_id)
    env: dict[str, Any] = {
        "formato": "omica.envelope/1", "run_id": run_id, "passo": passo_id,
        "script": item["script"], "argv": item["argv"], "valores": item["valores"],
        "runtime": item.get("runtime"), "fila": item.get("fila"),
        "proveniencia": item["proveniencia"], "selos": item["selos"],
        "plano_sha256": plano["plano_sha256"],
    }
    reg = run["passos"].get(passo_id)
    if not reg:
        return {**env, "estado": "nao_lancado", "job_id": None,
                "portao": {"ok": False, "pendente": True, "falhas": ["passo ainda não lançado"]}}
    if _eh_marcador(reg):
        # o executar foi interrompido entre marcar o passo e registrar o job: o próximo
        # executar (ou GET do run) reconcilia pelo status.json dos jobs
        return {**env, "estado": "lancando", "job_id": None,
                "portao": {"ok": False, "pendente": True,
                           "falhas": ["lançamento sem job registrado (interrompido): aguardando reconciliação"]}}
    status = queue.read_status(reg["job_id"])
    estado_job = _situacao(status)
    env.update({"job_id": reg["job_id"], "estado": estado_job})
    if status is None:
        # não dá para afirmar nada: nem terminal, nem aprovado (o run fica como está)
        return {**env, "portao": {"ok": False, "pendente": True,
                                  "falhas": ["status.json do job ausente ou ilegível"]}}
    if status.get("run_id") != run_id or status.get("passo") != passo_id:
        return {**env, "portao": {"ok": False, "pendente": False,
                                  "falhas": ["status do job não pertence a este run/passo"]}}
    if estado_job != "done":
        if estado_job == "error":
            falhas = [str(status.get("error"))]
        elif estado_job == "orfao":
            falhas = ["job órfão: em 'running' além do prazo do job (o worker morreu?)"]
        else:
            falhas = [f"job em {estado_job}"]
        return {**env, "portao": {"ok": False, "pendente": estado_job in ("queued", "running"),
                                  "falhas": falhas}}

    inicio = _epoch(status.get("iniciado_em"))
    fim, folga = _fim_do_job(reg["job_id"], status)
    falhas: list[str] = _conferir_execucao(status, item)
    sha_worker, falhas_sha = _sha_do_worker(status)
    falhas += falhas_sha
    falhas += _conferir_entradas_do_worker(status, item, plano)
    portas: dict[str, Any] = {}
    resumos: dict[str, Any] = {}
    assinatura: dict[str, list[int]] = {}
    for nome, porta in item["portas"].items():
        arq = Path(porta["caminho"])
        info: dict[str, Any] = {"formato": porta["formato"], "caminho": porta["caminho"],
                                "existe": arq.is_file()}
        portas[nome] = info
        if not info["existe"]:
            falhas.append(f"{nome}: arquivo não gerado")
            continue
        st = arq.stat()  # ANTES do sha: uma escrita no meio desfaz a assinatura (leve, #7)
        assinatura[nome] = _assinatura(st)
        info["bytes"] = st.st_size
        if inicio is not None and st.st_mtime < inicio:
            info["anterior_ao_job"] = True
            falhas.append(f"{nome}: arquivo anterior ao início do job (não foi reescrito)")
        if fim is not None and st.st_mtime > fim + folga:
            info["posterior_ao_job"] = True
            falhas.append(f"{nome}: arquivo escrito depois do fim do job")
        info["sha256"] = sha256_arquivo(arq, _MAX_SHA)
        if info["sha256"] is None:
            # sem sha não há "artefato alterado depois de entregue": não é atestável
            falhas.append(f"{nome}: {st.st_size} bytes > {_MAX_SHA}: sem sha256, a porta não é atestável")
        if sha_worker is not None:
            falhas += _conferir_sha_do_worker(nome, sha_worker, info["sha256"])
        v = formatos.validar(porta["formato"], arq)
        info["validado"], info["falhas_formato"], info["perfil"] = v.ok, v.falhas, v.perfil
        if not v.ok:
            falhas.append(f"{nome}: fora do formato {porta['formato']}: {'; '.join(v.falhas[:3])}")
        if porta["formato"] == "resumo_json":
            resumos[nome] = _ler_resumo(arq)

    proximo = [{"passo": x["id"], "param": param, "caminho": item["portas"][ref.split(".", 1)[1]]["caminho"]}
               for x in plano["passos"] for param, ref in x["liga"].items()
               if ref.split(".", 1)[0] == passo_id and ref.split(".", 1)[1] in item["portas"]]
    gravado = _pasta(run_id) / "envelopes" / f"{passo_id}.json"
    with _trava_envelopes(run_id):
        if gravado.exists():
            anterior = _ler(gravado)
            for nome, info in portas.items():
                antes = (anterior.get("portas", {}).get(nome) or {}).get("sha256")
                if antes is None or info.get("sha256") != antes:
                    falhas.append(f"{nome}: sha256 diferente do envelope entregue (artefato alterado)")
        ok = not falhas
        env.update({"iniciado_em": inicio, "terminado_em": fim, "portas": portas, "resumos": resumos,
                    "proximo": proximo, "portao": {"ok": ok, "pendente": False, "falhas": falhas}})
        if ok and not gravado.exists():
            # a assinatura do sistema de arquivos é o que o estado() confere sem reler (#7)
            _gravar(gravado, {**env, "entregue_em": time.time(), "assinatura_fs": assinatura})
    return env


ESPERA_MAX_S = 120.0


def aguardar(run_id: str, passo_id: str, segundos: float = 60.0, intervalo: float = 2.0) -> dict[str, Any]:
    """Espera longa para o orquestrador: devolve o envelope assim que o job do passo sai de
    queued/running, ou o envelope ainda pendente quando o prazo vence. Substitui o laço
    Wait→GET no n8n (nó Wait dentro de sub-workflow é frágil) por um HTTP que segura até
    `ESPERA_MAX_S`. Passo não lançado volta na hora. No fim promove o estado do run
    (concluido/erro) e o devolve junto, para o n8n decidir com uma resposta só."""
    limite = time.monotonic() + max(0.0, min(float(segundos), ESPERA_MAX_S))
    while True:
        env = envelope(run_id, passo_id)
        if env["estado"] not in ("queued", "running") or time.monotonic() >= limite:
            break
        time.sleep(intervalo)
    return {**env, "run_estado": estado(run_id)["estado"]}


# ----------------------------------------------------------------------- estado
def _visao(run_id: str, run: dict[str, Any]) -> dict[str, Any]:
    """O run como está no disco. Opções das escolhas e passos vêm do run.json (gravados
    quando as entradas mudaram), nunca do catálogo atual: um YAML editado ou removido não
    faz um run existente — até concluído — responder 404. Run de antes desse registro usa o
    catálogo só para o que faltar, e a falta dele vira aviso, não erro."""
    resposta = {**run}
    if "escolhas_opcoes" not in run or "passos_pipeline" not in run:
        try:
            p, _ = _pipeline(run["pipeline"])
            resposta.setdefault("escolhas_opcoes", _opcoes_escolhas(p, run))
            resposta.setdefault("passos_pipeline", [x.id for x in p.passos])
        except (ErroRun, OSError, ValueError, KeyError) as e:
            resposta.setdefault("escolhas_opcoes", {})
            resposta.setdefault("passos_pipeline", None)
            resposta["aviso"] = {"erro": "pipeline_alterado", "causa": getattr(e, "erro", type(e).__name__)}
    return resposta


def estado(run_id: str) -> dict[str, Any]:
    """Visão do run para o orquestrador: estado, entradas com veredito, opções das escolhas
    (valores reais) e, por passo, o status VIVO do job. Promove o estado do run (concluido,
    erro com `motivo_erro`, ou de volta a executando) decidindo FORA da trava — calcular os
    envelopes leva tempo, e o polling não pode segurar o executar — e gravando com CAS pela
    `versao` do run.json: se alguém gravou no meio (relançamento, abortar), decide de novo
    sobre o run fresco. A resposta é sempre o estado PERSISTIDO, nunca um decidido e não gravado.
    Um marcador `lancando` que sobrou de um executar interrompido é reconciliado aqui também
    (sob a trava, com espera curta: com um executar em curso, fica para o próximo GET) — sem
    isso, se o n8n não repetisse o executar, o job rodava sem que o run jamais o enxergasse."""
    for _ in range(3):
        run = _carregar(run_id)
        if any(_eh_marcador(p) for p in run.get("passos", {}).values()):
            with contextlib.suppress(ErroRun):  # run_ocupado: um executar está no meio
                with _trava(run_id, espera_s=0.5):
                    run = _carregar(run_id)
                    if _reconciliar_marcadores(run_id, run):
                        _gravar_run(run_id, run)
        resposta = _visao(run_id, run)
        if run["estado"] not in ("executando", "erro", "aprovado"):
            return resposta
        plano = _plano_congelado(run_id, run)
        novo, vivos, motivo = _decidir(run_id, run, plano)
        extra = {"passos_vivos": vivos, "passos_pipeline": [x["id"] for x in plano["passos"]]}
        if novo == run["estado"] and (novo != "erro" or motivo == run.get("motivo_erro")):
            return {**resposta, **extra}
        with _trava(run_id):
            atual = _carregar(run_id)
            if atual.get("versao") == run.get("versao"):
                atual["estado"] = novo
                if novo == "erro":
                    atual["motivo_erro"] = motivo
                else:
                    atual.pop("motivo_erro", None)
                _gravar_run(run_id, atual)
                return {**_visao(run_id, atual), **extra}
    return _visao(run_id, _carregar(run_id))
