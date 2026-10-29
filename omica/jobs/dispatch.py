"""dispatch.py — do manifest ao job: resolve o ScriptSpec e enfileira na fila certa.

Arquitetura da Fase 2 (sem docker.sock, anti-lição nº 11): cada runtime
`image:<nome>` mapeia para uma FILA RQ própria, consumida por um worker cujo
container É aquela imagem (as ferramentas estão no PATH do próprio worker).
Nada de docker-dentro-de-docker fora do futuro profile nfcore.
"""
from __future__ import annotations

import logging
import posixpath
import re
import shlex
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from omica.chaves_contexto import CHAVES_CONTEXTO, ESPECIE_LEGADA, ID_DEMO, SLUG_DEMO, eh_chave_contexto
from omica.config import REPO_ROOT, get_config
from omica.jobs import queue
from omica.registry import Manifest, ParamSpec, ScriptSpec, campos_do_cmd, load_registry

if TYPE_CHECKING:
    # só para tipos: em tempo de execução o dispatch nunca importa omica.contexto (o ciclo seria
    # contexto → especies → dispatch); quem chama passa o Contexto já resolvido
    from omica.contexto import Contexto

_log = logging.getLogger(__name__)

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


def _resolver(mod_id: str, script_key: str, api: bool = False) -> tuple[Manifest, ScriptSpec]:
    """`api=True` resolve em `scripts_api` (orquestração, saída por run); o padrão continua
    sendo `scripts` (UI, Professor, workflows selados) — as duas nunca se misturam."""
    reg = load_registry()
    m = reg.por_id(mod_id)
    if m is None:
        raise KeyError(f"módulo desconhecido: {mod_id}")
    secao = m.scripts_api if api else m.scripts
    spec = next((s for s in secao if s.key == script_key), None)
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
#
# Revisão adversarial da F1 (2026-10-19) — três ajustes:
#   - S1: a convenção `Nome=/caminho` validava só o lado direito do '=' e valia para
#     QUALQUER parâmetro: 'https://host/c=/data/x' passava, e o read.delim do R baixava a
#     URL (SSRF incluso: 'http://omica-redis:6379/k=/data/x'). Agora o lado esquerdo tem de
#     ser um nome estrito (_NOME_GRUPO), caminho/dir validam o valor INTEIRO, URL ('://') e
#     '..' são recusados em qualquer parte.
#   - S2: valor sem '/' escapava da checagem de raiz — 'Angus=.env' fazia o fst_racas.py
#     abrir o .env do token. O lado direito de um grupo é SEMPRE caminho, e os manifests
#     passaram a declarar `tipo` (teste em test_sanitizador garante que ninguém esqueça).
#   - S3: re.ASCII + fullmatch (o `\d` aceitava dígito árabe e o `$` aceitava '\n' final);
#     o decimal negativo não aceita expoente, porque o argparse lê '-1e-5' como flag.
# Tudo com fullmatch: sem âncoras, nada de `$` aceitando quebra de linha.
_TOKEN_SEGURO = re.compile(r"[A-Za-z0-9_/][A-Za-z0-9_\-./:=]*", re.ASCII)
_NOME_GRUPO = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-]*", re.ASCII)  # sem '/' e sem ':'
_INTEIRO = re.compile(r"-?[0-9]+", re.ASCII)
_DECIMAL = re.compile(r"-[0-9]*\.?[0-9]+|[0-9]*\.?[0-9]+([eE][-+]?[0-9]+)?", re.ASCII)
# Dados curados do REPOSITÓRIO: modules/<m>/demo_data|data e, na v2, a pasta de um subprojeto
# versionado (projetos/<especie>/<slug>/, ids com os padrões de omica.chaves_contexto).
_RAIZ_MODULO = re.compile(
    r"^(modules/[A-Za-z0-9_]+/(demo_data|data)|projetos/[a-z][a-z0-9_]{2,31}/[a-z][a-z0-9_]{2,47})(/|$)",
    re.ASCII)
# <módulo>/runs/<run_id>/<passo>, comum aos dois layouts de saída de run. _SAIDA_RUN (o layout
# da v1 e do demo) tem de continuar IDÊNTICA à string de antes da v2: um run aprovado antes do
# upgrade é remontado e comparado com o cmd aprovado (teste com o literal em test_m1_contexto).
_RESTO_RUN = r"/[a-z][a-z0-9_]*/runs/[A-Za-z0-9_\-]{1,80}/[a-z][a-z0-9_]*"
_SAIDA_RUN = r"/results" + _RESTO_RUN


def _validar_caminho(caminho: str, rotulo: str) -> None:
    """Checagem LÉXICA (posix): os caminhos dos manifests são do container, e no host
    (testes, CI) `/data/...` não existe — resolver no sistema de arquivos daria
    falso-negativo. Raízes aceitas: dados curados do repo (modules/<m>/demo_data|data),
    os dados pesados montados (/data), o diretório de dados da plataforma e as raízes de
    REFERÊNCIA configuradas (G1 §1.3). No container estas já caem em /data; no host (suíte)
    o GENOME_DATA é uma pasta temporária, e sem elas o plano de um run com entrada por
    referência daria parametro_recusado. A raiz vem resolvida; o valor é comparado como
    texto, como as demais."""
    if ".." in caminho:
        raise ValueError(f"caminho com '..' em {rotulo}: {caminho!r}")
    v = posixpath.normpath(caminho)
    raiz_dados = get_config().data_dir.as_posix().rstrip("/")
    if (_RAIZ_MODULO.match(v) or v == "/data" or v.startswith("/data/")
            or v == raiz_dados or v.startswith(raiz_dados + "/")):
        return
    for r in raizes_referencia():
        rp = r.as_posix().rstrip("/")
        if v == rp or v.startswith(rp + "/"):
            return
    raise ValueError(
        f"caminho fora das raízes permitidas em {rotulo}: {caminho!r} "
        "(use modules/<módulo>/demo_data|data/..., /data/... ou a pasta de dados da plataforma)")


# ------------------------------------------------------- raízes de referência (G1 §1.3)
# Raiz de REFERÊNCIA = pasta de dados pesados que um run lê SEM copiar (um BCF de 1,9 GB não
# cabe no teto de 256 MiB da cópia). Os nomes são relativos a GENOME_DATA e vêm SÓ da config
# (`raizes_referencia`, OMICA_RAIZES_REFERENCIA; "-" desliga). Fica aqui, e não em runs.py,
# porque o _validar_caminho também precisa dela e runs importa dispatch (sem ciclo).
_NOME_RAIZ = re.compile(r"[A-Za-z0-9_.\-]+(/[A-Za-z0-9_.\-]+)*", re.ASCII)
_raizes_avisadas: set[tuple[str, str]] = set()


def _nomes_raizes_referencia() -> tuple[str, ...]:
    return tuple(get_config().raizes_referencia)


def _contem(a: Path, b: Path) -> bool:
    """Uma das duas pastas está dentro da outra (ou são a mesma)."""
    return a == b or a.is_relative_to(b) or b.is_relative_to(a)


def _avaliar_raiz(base: Path, nome: Any) -> tuple[Optional[Path], Optional[str]]:
    """(raiz resolvida, None) ou (None, motivo). Uma raiz larga demais transforma a entrada
    por referência em leitor de arquivos do servidor: sha256 e tamanho de qualquer arquivo
    dela voltam no GET do run. Por isso: estritamente DENTRO de GENOME_DATA (nunca /data
    inteiro), fora da pasta de dados da plataforma (work/ com planos e job.log de outros
    runs, raw/runs de outros runs, results/ com o demo selado) e fora do repositório (o
    .env do token), sem conter nenhuma das duas. As caixas de entrada moram nessas duas
    pastas, então uma raiz aceita nunca as cruza."""
    if not isinstance(nome, str) or not _NOME_RAIZ.fullmatch(nome) or ".." in nome.split("/"):
        return None, "nome fora do padrão (relativo a GENOME_DATA, só [A-Za-z0-9_.-] e '/', sem '..')"
    try:
        r = (base / nome).resolve(strict=True)
    except (OSError, RuntimeError):
        return None, "inexistente"
    if not r.is_dir():
        return None, "não é diretório"
    if r == base or not r.is_relative_to(base):
        return None, "não está estritamente dentro de GENOME_DATA"
    if _contem(r, get_config().data_dir.resolve()):
        return None, "cruza a pasta de dados da plataforma (OMICA_DATA)"
    if _contem(r, REPO_ROOT.resolve()):
        return None, "cruza o repositório"
    return r, None


def raizes_referencia() -> list[Path]:
    """Raízes de referência VÁLIDAS, resolvidas (lista vazia = toda referência é recusada).
    Uma raiz reprovada é descartada com um aviso no log, uma vez por processo e motivo."""
    try:
        base = get_config().genome_data.resolve(strict=True)
    except (OSError, RuntimeError):
        return []
    raizes = []
    for nome in _nomes_raizes_referencia():
        r, motivo = _avaliar_raiz(base, nome)
        if r is not None:
            raizes.append(r)
        elif (str(nome), str(motivo)) not in _raizes_avisadas:
            _raizes_avisadas.add((str(nome), str(motivo)))
            _log.warning("raiz de referência %r descartada: %s", nome, motivo)
    return raizes


def _validar_valor(param: ParamSpec, bruto: str) -> str:
    if not bruto:
        raise ValueError(f"parâmetro obrigatório vazio: {param.rotulo} ({param.name})")
    if param.tipo == "inteiro":
        if not _INTEIRO.fullmatch(bruto):
            raise ValueError(f"{param.rotulo} precisa ser inteiro: {bruto!r}")
        return bruto
    if param.tipo == "decimal":
        if not _DECIMAL.fullmatch(bruto):
            raise ValueError(f"{param.rotulo} precisa ser número (negativo sem expoente): {bruto!r}")
        return bruto
    if ".." in bruto:
        raise ValueError(f"'..' não é permitido em {param.rotulo}: {bruto!r}")
    if "://" in bruto:
        raise ValueError(f"URL não é permitida em {param.rotulo}: parâmetros apontam para "
                         f"arquivos locais, nunca para a rede ({bruto!r})")
    if not _TOKEN_SEGURO.fullmatch(bruto):
        dica = " — no container, E:\\data é montado em /data" if "\\" in bruto else ""
        raise ValueError(
            f"valor inválido para {param.rotulo}: {bruto!r} (um único argumento: sem espaço, "
            f"sem barra invertida, sem '-' no início){dica}")
    nome, sep, resto = bruto.partition("=")
    if param.tipo in ("caminho", "dir"):
        _validar_caminho(bruto, param.rotulo)  # o valor INTEIRO: o script abre exatamente isto
    elif param.tipo == "grupo":
        if not (sep and resto and _NOME_GRUPO.fullmatch(nome)):
            raise ValueError(f"{param.rotulo} deve ser Nome=/caminho/arquivo, com Nome simples: {bruto!r}")
        _validar_caminho(resto, param.rotulo)
    elif sep and resto and _NOME_GRUPO.fullmatch(nome):
        # tipo texto com a convenção Nome=arquivo (manifests antigos sem `tipo`): o lado
        # direito é SEMPRE caminho, mesmo sem '/' — 'Angus=.env' é recusado aqui.
        _validar_caminho(resto, param.rotulo)
    elif "/" in bruto or sep:
        # tem '/' (parece caminho) ou tem '=' com lado esquerdo que não é nome simples:
        # valida o valor inteiro, que é o que o script receberá.
        _validar_caminho(bruto, param.rotulo)
    return bruto


def padrao_saida(ctx: Optional["Contexto"]) -> str:
    """Regex (sem a raiz <dados>) da pasta de saída que um run pode ter neste contexto. Sem
    contexto ou no demo: o layout da v1 (results/<mod>/runs/...), onde os runs aprovados
    antes do upgrade e os do n8n gravam. Fora do demo: results/projetos/<esp>/<slug>/<mod>/
    runs/... — um subprojeto nunca grava na árvore de outro, nem na do demo."""
    if ctx is None or ctx.eh_demo:
        return _SAIDA_RUN
    return "/results/projetos/" + re.escape(ctx.especie) + "/" + re.escape(ctx.slug) + _RESTO_RUN


def valor_efetivo(param: ParamSpec, valores: Optional[dict[str, str]], ctx: Optional["Contexto"],
                  mod_id: str, script_key: str) -> str:
    """O valor CRU (sem strip) que o parâmetro recebe. Precedência: envio > subprojeto >
    espécie > default do manifest. Sem contexto (ou com `padroes == {}` e escopo livre) é
    exatamente a regra da v1: `valores.get(nome) or default`.

    `escopo` (ParamSpec, fora do hash): o default de um parâmetro `especie` é bovino e não vale
    fora de bos_taurus; o de um `subprojeto` é do demo e não vale fora de bos_taurus/demo. Sem
    envio nem sobrescrita do contexto, recusa — rodar humano com um corte bovino seria
    resultado errado sem erro."""
    padroes = ctx.padroes(mod_id, script_key) if ctx is not None else {}
    enviado = (valores or {}).get(param.name)
    if not enviado and param.name not in padroes and ctx is not None:
        if param.escopo == "especie" and not ctx.eh_especie_legada:
            raise ValueError(f"{param.rotulo}: o padrão {param.default!r} é bovino; "
                             f"informe o valor para {ctx.especie}")
        if param.escopo == "subprojeto" and not ctx.eh_demo:
            raise ValueError(f"{param.rotulo}: o padrão é do demo; informe o valor para {ctx.id}")
    return enviado or padroes.get(param.name, param.default)


def montar_cmd(spec: ScriptSpec, valores: Optional[dict[str, str]] = None,
               saida: Optional[str] = None, *, ctx: Optional["Contexto"] = None,
               mod_id: str = "") -> str:
    """Substitui os placeholders do manifest. {dados} = OMICA_DATA; os demais
    vêm dos params declarados no spec (default < valor do formulário), sempre
    sanitizados — o usuário preenche VALORES, nunca shell.

    v2 multiespécie: `ctx` (omica.contexto.Contexto, resolvido no SERVIDOR) dá os
    placeholders de contexto (`{ref_gtf}`...) e as sobrescritas de default. Sem `ctx`, um cmd
    que precisa de referência é recusado; com `ctx` fora de bos_taurus/demo, só cmd que grava
    em {saida} (scripts_api, isto é, pipeline) monta."""
    cfg = get_config()
    if ctx is not None and not mod_id:
        raise ValueError(f"{spec.key}: montar_cmd com contexto exige mod_id (as sobrescritas são "
                         "por <modulo>.<script>.<param>)")
    # Os campos como o str.format os lê, não o texto do cmd (resíduo do achado #37): com a
    # busca literal por "{dados}", `{dados!s}` e `{dados:}` passavam pelas duas travas
    # abaixo e gravavam no demo selado. Conversão/especificador nem chegam a formatar —
    # mesma regra do registry, aqui para o ScriptSpec montado fora dele.
    campos, problemas = campos_do_cmd(spec.cmd)
    if problemas:
        raise ValueError(f"{spec.key}: cmd com campo fora do formato: {'; '.join(problemas)}")
    if ctx is not None and ctx.especie not in spec.especies:
        raise ValueError(f"{spec.key} não declara a espécie {ctx.especie} (declara {spec.especies})")
    if ctx is not None and not ctx.eh_demo and "saida" not in campos:
        # `scripts:` e os passos de workflow selado gravam em caminho fixo do demo: fora dele
        # sobrescreveriam o que sustenta os selos (ESPEC §10, a trava do núcleo)
        raise ValueError(f"fora de bos_taurus/demo só pipelines executam: {spec.key} grava em caminho fixo")
    contexto: dict[str, str] = {"dados": str(cfg.data_dir)}
    for param in spec.params:
        bruto = valor_efetivo(param, valores, ctx, mod_id, spec.key).strip()
        contexto[param.name] = _validar_valor(param, bruto)
    # Placeholders de contexto: lista FECHADA, valores só do contexto do servidor e pelo MESMO
    # sanitizador dos parâmetros. `requer_refs` conta mesmo sem aparecer no cmd (o script pode
    # ler a referência por outro caminho e ainda assim exigir que ela exista no contexto).
    for k in sorted({c for c in campos if eh_chave_contexto(c)} | set(spec.requer_refs)):
        if k not in CHAVES_CONTEXTO:
            raise ValueError(f"{spec.key}: placeholder de contexto fora da lista fechada: {k}")
        if ctx is None:
            raise ValueError(f"{spec.key} requer {k} e nenhum contexto foi dado")
        if k not in ctx.valores:
            raise ValueError(f"referência ausente: {k} (espécie {ctx.especie}, assembly {ctx.assembly})")
        v = _validar_valor(ParamSpec(name=k, rotulo=k, tipo=CHAVES_CONTEXTO[k]), ctx.valores[k])
        if k in campos:
            contexto[k] = v
    if "saida" in campos:
        # {saida} é a pasta do run, injetada pelo SERVIDOR (omica/runs.py) — nunca vem do
        # cliente. Ainda assim passa pela mesma checagem de raiz: fora da pasta de dados,
        # um run poderia gravar por cima do demo selado ou fora do volume.
        if not saida:
            raise ValueError(f"{spec.key} grava em {{saida}} e nenhuma pasta de run foi dada")
        _validar_caminho(saida, "saida")
        raiz = re.escape(cfg.data_dir.as_posix().rstrip("/"))
        # só results/<mod>/runs/<run_id>/<passo> (ou, fora do demo, a mesma forma sob
        # results/projetos/<esp>/<slug>/): nem results/<mod>/demo (selado), nem raw/
        if not re.fullmatch(raiz + padrao_saida(ctx), saida):
            if ctx is None or ctx.eh_demo:
                layout = "<dados>/results/<módulo>/runs/<run>/<passo>"
            else:
                layout = f"<dados>/results/projetos/{ctx.especie}/{ctx.slug}/<módulo>/runs/<run>/<passo>"
            raise ValueError(f"saida precisa ser {layout}: {saida!r}")
        contexto["saida"] = saida
    elif saida is not None:
        # Template SEM {saida} recebendo uma pasta de run (revisão F2, achado #37): antes a
        # pasta era descartada em silêncio, e um cmd copiado da seção `scripts:` gravaria onde
        # o texto fixo manda — no transcriptomica, `{dados}/results/<mod>/demo`, o demo SELADO.
        # O envelope só acusaria "arquivo não gerado" DEPOIS de o demo já ter sido sobrescrito.
        raise ValueError(f"{spec.key} não grava em {{saida}}: a pasta do run {saida!r} seria ignorada")
    if saida is not None and "dados" in campos:
        # 2ª trava do mesmo invariante (a 1ª é o registry): no caminho da API toda entrada
        # vem por parâmetro (congelada no plano) e toda saída vai para {saida}. Um caminho
        # fixo sob {dados} fica fora do plano — e pode ser results/<mod>/demo, work/ ou raw/.
        raise ValueError(f"{spec.key}: cmd de run cita {{dados}} (caminho fixo fora do plano congelado)")
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
                  extra_meta: Optional[dict[str, Any]] = None,
                  api: bool = False, saida: Optional[str] = None, *,
                  ctx: Optional["Contexto"] = None) -> tuple[str, str]:
    """Enfileira um script de módulo. Retorna (job_id, backend).

    `api=True` + `saida`: script de `scripts_api` gravando na pasta do run (F2, n8n).
    `ctx`: o contexto (espécie, subprojeto) resolvido no servidor; sem ele, o do demo. A
    espécie e o subprojeto vão ao meta do job (status.json, filtro de /jobs), nunca aos
    argumentos do worker — o cmd é o único que roda.

    Levanta RecusadoPorRAM (guarda), FilaIndisponivel (fila especializada sem
    Redis), RuntimeIndisponivel (runtime de fase futura) ou ValueError (param).
    """
    m, spec = _resolver(mod_id, script_key, api=api)
    fila = fila_para_runtime(spec.runtime)
    cmd = montar_cmd(spec, valores, saida=saida, ctx=ctx, mod_id=mod_id)
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
        # runs.py nunca põe estas duas em extra_meta (seria kwarg duplicado)
        especie=ctx.especie if ctx is not None else ESPECIE_LEGADA,
        subprojeto=ctx.slug if ctx is not None else SLUG_DEMO,
        **(extra_meta or {}),
    )


def lancar_workflow(mod_id: str, wf_id: str, *,
                    ctx: Optional["Contexto"] = None) -> tuple[str, list[str], str]:
    """Enfileira um workflow inteiro (passos encadeados por depends_on).

    Um workflow selado só roda nos subprojetos de `validado_em` (padrão: o demo): os passos
    são de `scripts:` e gravam em caminho fixo. Sem `ctx`, é o demo.

    Retorna (wf_run_id, job_ids, backend)."""
    reg = load_registry()
    m = reg.por_id(mod_id)
    if m is None:
        raise KeyError(f"módulo desconhecido: {mod_id}")
    wf = next((w for w in m.workflows if w.id == wf_id), None)
    if wf is None:
        raise KeyError(f"workflow desconhecido: {mod_id}/{wf_id}")
    alvo = ctx.id if ctx is not None else ID_DEMO
    if alvo not in wf.validado_em:
        raise ValueError(f"{mod_id}/{wf_id} não é validado em {alvo} (validado em {wf.validado_em}); "
                         "fora de bos_taurus/demo só pipelines executam")
    especie = ctx.especie if ctx is not None else ESPECIE_LEGADA
    subprojeto = ctx.slug if ctx is not None else SLUG_DEMO
    itens = []
    for passo in wf.passos:
        _, spec = _resolver(mod_id, passo)
        itens.append({
            "func_path": "omica.jobs.script_job.executar",
            "prefixo": f"{mod_id}-{passo}",
            "mem_gb": spec.mem_gb,
            "fila": fila_para_runtime(spec.runtime),
            "timeout_s": 6 * 3600,
            "meta": {"cmd": montar_cmd(spec, ctx=ctx, mod_id=mod_id), "modulo": mod_id, "script": passo,
                     "titulo": spec.titulo, "runtime": spec.runtime,
                     "especie": especie, "subprojeto": subprojeto},
        })
    return queue.enqueue_cadeia(itens, wf_meta={
        "prefixo": f"wf-{mod_id}-{wf_id}", "modulo": mod_id,
        "workflow": wf_id, "titulo": wf.titulo,
        "especie": especie, "subprojeto": subprojeto,
    })
