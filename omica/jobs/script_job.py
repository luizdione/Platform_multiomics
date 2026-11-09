"""script_job.py — executor genérico de scripts de módulo (roda DENTRO do worker
da fila certa, onde as ferramentas do runtime estão no PATH).

Contrato com os scripts:
  - o comando vem do manifest (via dispatch.montar_cmd) e roda com cwd na raiz
    do repo (/app no container);
  - stdout+stderr são transmitidos linha a linha para o job.log (console SSE);
  - uma linha `RESULTADO: <caminho>` promove o caminho a result_path do job —
    é assim que a UI sabe qual arquivo de resultado exibir (omica/results.py);
  - imediatamente antes de rodar, o sha256 do código (script + `_comum.py` do módulo) vai
    para o status.json como `proveniencia_execucao` (omica/proveniencia.py); no fim ele é
    tirado de novo, e um código editado DURANTE a execução derruba o job;
  - job de run (F2) traz no status.json as `portas` do plano ({nome: caminho}): ao terminar
    bem, o worker grava o sha256 de cada uma em `sha_saidas`, na MESMA gravação do 'done'.
    É o que o envelope compara — o atestado das saídas é de quem as escreveu;
  - job de run que consome ENTRADAS do run (G1 §1.8) traz no status.json as `entradas`
    esperadas (tiradas do plano aprovado). Antes do Popen o worker as atesta
    (`entradas_execucao`, na mesma gravação da `proveniencia_execucao`) e, se diferem do
    aprovado, o job vai a erro SEM rodar o script; ao terminar bem, tira o sha256 completo de
    cada uma (`sha_entradas`). A entrada por referência mora em /data, fora do servidor:
    entre a aprovação e a execução o job pode esperar horas na fila.

A receita da atestação das entradas (impressão, sha com teto, `atestar_entradas`,
`divergencias`) mora AQUI e é a mesma que o web usa (omica/runs.py importa daqui): os dois
lados comparam números tirados pela mesma função, como na proveniência do código. Só stdlib
e Python 3.10 — o omica-vep (jammy) importa este módulo (e o omica/contrato/diretorio.py).

PASTA (F6, ESPEC §1.A.6-§1.A.7): a entrada e a porta de pasta são atestadas pelo sha da
ÁRVORE dos membros (`diretorio.sha_diretorio`, com a regra do plano), a mesma função do
envelope; a entrada ecoa `tipo` e `regra`, e uma regra diferente da do plano é "outro arquivo".
"""
from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any, Optional

from omica import proveniencia
from omica.config import REPO_ROOT
from omica.contrato import diretorio
from omica.jobs.queue import append_log, read_status, write_status

PREFIXO_RESULTADO = "RESULTADO:"
# mesmo teto do envelope (runs._MAX_SHA): acima dele a porta não é atestável de qualquer
# jeito, e o worker não gasta minutos lendo um arquivo que o portão vai recusar
_MAX_SHA_SAIDA = 512 * 1024 * 1024
# teto do sha de uma ENTRADA (= runs._MAX_REFERENCIA; o maior BCF do Run 8 tem 1,87 GB) e do
# de um índice irmão (= contrato.bgzf.MAX_INDICE; teste de sincronia em test_runs_referencia)
_MAX_SHA_ENTRADA = 4 * 1024 ** 3
_MAX_SHA_IRMAO = 64 * 1024 * 1024

# Impressão de arquivo grande (G1 §1.8): sha256 de esquema‖tamanho + primeiros e últimos
# 8 MiB + 16 blocos de 256 KiB em offsets fixos (i·n/17). Lê no máximo ~20 MiB, qualquer que
# seja o tamanho. Pega troca de tamanho, de cabeçalho, de cauda e da maioria das edições; o
# que escapa (byte trocado fora dos trechos, com o mtime devolvido) é pego pelo sha256
# completo que o worker tira no fim do job (`sha_entradas`) — resíduo R1 da especificação.
_ESQUEMA_IMPRESSAO = "omica.impressao/1"
_PONTA = 8 << 20
_BLOCO = 256 << 10


def impressao_arquivo(p: Path) -> dict[str, Any]:
    """Impressão de `p` (segue symlink). O tamanho é o do descritor ABERTO, não de um stat
    anterior; um arquivo que encolhe no meio da leitura dá OSError (quem chama decide)."""
    with p.open("rb") as f:
        st = os.fstat(f.fileno())
        n = st.st_size
        trechos = [(0, min(_PONTA, n)), (max(0, n - _PONTA), min(_PONTA, n))]
        trechos += [(i * n // 17, min(_BLOCO, n - i * n // 17)) for i in range(1, 17)]
        h = hashlib.sha256(_ESQUEMA_IMPRESSAO.encode() + b"\0" + n.to_bytes(8, "big"))
        for off, tam in trechos:
            f.seek(off)
            b = f.read(tam)
            if len(b) != tam:
                raise OSError("arquivo encolheu durante a leitura da impressão")
            h.update(b)
    return {"esquema": _ESQUEMA_IMPRESSAO, "bytes": n, "mtime_ns": st.st_mtime_ns,
            "amostra_sha256": h.hexdigest()}


def sha256_ate(p: Path, limite: int) -> Optional[str]:
    """sha256 completo, ou None acima do teto — também quando o arquivo CRESCE durante a
    leitura e passa dele (um stat anterior não basta). Nunca None por arquivo ausente: isso
    é OSError, e quem chama decide."""
    h = hashlib.sha256()
    with p.open("rb") as f:
        if os.fstat(f.fileno()).st_size > limite:
            return None
        lidos = 0
        for bloco in iter(lambda: f.read(1 << 20), b""):
            lidos += len(bloco)
            if lidos > limite:
                return None
            h.update(bloco)
    return h.hexdigest()


def _sha_ou_none(p: Path, limite: int) -> Optional[str]:
    try:
        return sha256_ate(p, limite)
    except OSError:
        return None


def _arvore_ou_none(p: Path, regra: Any, limite: int) -> tuple[Optional[str], Optional[int]]:
    """(sha da árvore, bytes somados) de uma pasta, ou (None, None): regra que não é dict,
    membro recusado (diretorio.ErroMembros) ou pasta ilegível. Acima do teto, o sha é None."""
    if not isinstance(regra, dict):
        return None, None
    try:
        arv = diretorio.sha_diretorio(p, regra, limite)
    except (diretorio.ErroMembros, OSError):
        return None, None
    return arv["sha256"], arv["bytes"]


def atestar_entradas(espec: dict[str, Any], *, completo: bool) -> dict[str, Any]:
    """O que o job vai ler (`completo=False`, antes do Popen) ou leu (`completo=True`, depois
    do rc 0), por entrada. `espec[n]` = {"caminho", "modo", "bytes", "sha256", "impressao"?,
    "irmaos": {sufixo: sha}, "candidatos_irmaos": [sufixos]}, tirado do plano.

    O registro repete `caminho`, `modo` e `candidatos_irmaos` (crítica F5 #9): o envelope
    exige que sejam os do PLANO, então um status.json com o caminho trocado não atesta outro
    arquivo. `irmaos` lista os candidatos que EXISTEM ao lado ({sufixo: sha}; um .csi que
    aparece ao lado de um VCF aprovado só com .tbi muda o que o htslib lê). Início: a cópia
    pelo sha completo (é pequena), a referência pela impressão. Fim: sha completo das duas.
    Ilegível ou acima do teto vira None — e None nunca confere com nada (`divergencias`)."""
    saida: dict[str, Any] = {}
    for nome, e in espec.items():
        if not isinstance(e, dict):
            saida[str(nome)] = None
            continue
        caminho = str(e.get("caminho") or "")
        modo = str(e.get("modo") or "copia")
        candidatos = [str(s) for s in (e.get("candidatos_irmaos") or [])]
        p = _no_repo(caminho)
        r: dict[str, Any] = {"caminho": caminho, "modo": modo, "candidatos_irmaos": candidatos}
        if e.get("tipo") == "diretorio":
            # F6: pasta — ecoa tipo e regra; no início e no fim, o sha da árvore e os bytes
            # (a cópia tem no máximo 256 MiB: o sha completo é barato); sem índice irmão
            r["tipo"], r["regra"] = "diretorio", e.get("regra")
            r["sha256"], r["bytes"] = _arvore_ou_none(p, e.get("regra"), _MAX_SHA_ENTRADA)
            r["irmaos"] = {}
            saida[str(nome)] = r
            continue
        if completo:
            r["sha256"] = _sha_ou_none(p, _MAX_SHA_ENTRADA)
        elif modo == "referencia":
            try:
                imp = impressao_arquivo(p)
                r["bytes"], r["amostra_sha256"] = imp["bytes"], imp["amostra_sha256"]
            except OSError:
                r["bytes"] = r["amostra_sha256"] = None
        else:
            try:
                r["bytes"] = p.stat().st_size
            except OSError:
                r["bytes"] = None
            r["sha256"] = _sha_ou_none(p, _MAX_SHA_ENTRADA)
        r["irmaos"] = {s: _sha_ou_none(Path(f"{p}{s}"), _MAX_SHA_IRMAO)
                       for s in candidatos if os.path.lexists(f"{p}{s}")}
        saida[str(nome)] = r
    return saida


def divergencias(esperado: dict[str, Any], lido: Any, *, completo: bool) -> set[str]:
    """Em que um registro de `atestar_entradas` difere do esperado (tirado do plano):
    'caminho' (outro arquivo, outro modo ou outros candidatos), 'conteudo' (sha ou impressão)
    e 'indice' (conjunto de irmãos presentes ou sha de algum). Vazio = confere. None nunca
    casa com None (achado L1): um esperado sem sha não é "igual por omissão"."""
    if not isinstance(lido, dict):
        return {"caminho", "conteudo", "indice"}
    tags: set[str] = set()
    modo = esperado.get("modo") or "copia"
    if (lido.get("caminho") != esperado.get("caminho") or (lido.get("modo") or "copia") != modo
            or list(lido.get("candidatos_irmaos") or []) != list(esperado.get("candidatos_irmaos") or [])
            # F6: pasta lida com outra regra (ou como arquivo) é outra entrada
            or lido.get("tipo") != esperado.get("tipo") or lido.get("regra") != esperado.get("regra")):
        tags.add("caminho")
    if completo or modo != "referencia":
        sha = lido.get("sha256")
        if sha is None or sha != esperado.get("sha256"):
            tags.add("conteudo")
    else:
        imp = esperado.get("impressao") if isinstance(esperado.get("impressao"), dict) else {}
        n, amostra = lido.get("bytes"), lido.get("amostra_sha256")
        if (n is None or amostra is None or n != esperado.get("bytes") or n != imp.get("bytes")
                or amostra != imp.get("amostra_sha256")):
            tags.add("conteudo")
    irm_esp = esperado.get("irmaos") if isinstance(esperado.get("irmaos"), dict) else {}
    irm_lido = lido.get("irmaos")
    if (not isinstance(irm_lido, dict) or irm_lido != irm_esp
            or any(v is None for v in irm_esp.values())):
        tags.add("indice")
    return tags


def _no_repo(caminho: str) -> Path:
    alvo = Path(caminho)
    return alvo if alvo.is_absolute() else REPO_ROOT / alvo


def _sha256(caminho: Path) -> Optional[str]:
    if not caminho.is_file() or caminho.stat().st_size > _MAX_SHA_SAIDA:
        return None
    h = hashlib.sha256()
    with caminho.open("rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def _sha_porta(valor: Any) -> Optional[str]:
    if isinstance(valor, dict):
        # F6: porta de PASTA, {"caminho", "regra"} — o sha da árvore, a receita do envelope
        return _arvore_ou_none(_no_repo(str(valor.get("caminho") or "")), valor.get("regra"), _MAX_SHA_SAIDA)[0]
    return _sha256(_no_repo(str(valor)))


def atestar_saidas(portas: dict[str, Any]) -> dict[str, Optional[str]]:
    """sha256 de cada porta declarada no plano, no instante em que o job termina (None =
    arquivo ausente ou acima do teto). Revisão F2 (#10): a referência tirada na 1ª LEITURA do
    envelope dependia do carimbo de tempo — uma troca entre o fim do job e essa leitura que
    preservasse o mtime (os.utime, cp -p, rsync -t) era selada como saída do script.
    Porta de pasta (F6): o valor é {"caminho", "regra"} e o sha é o da árvore dos membros."""
    return {str(nome): _sha_porta(valor) for nome, valor in portas.items()}


def executar(job_id: str) -> None:
    status = read_status(job_id) or {}
    cmd = status.get("cmd")
    if not cmd:
        raise RuntimeError(f"job {job_id} sem 'cmd' no status.json")
    modulo, alvo = str(status.get("modulo") or ""), proveniencia.alvo_do_cmd(cmd)
    # Revisão F2 (#22): o plano confere o sha do script ao ENFILEIRAR, mas o job pode
    # esperar minutos na fila (worker único por fila, repositório em bind mount) enquanto
    # o arquivo é editado ou o branch trocado. O sha do que vai rodar DE FATO é tirado aqui,
    # com a mesma receita do plano, e o envelope compara os dois. Uma falha ao ler o código
    # derruba o job: sem proveniência não há o que atestar.
    antes = proveniencia.sha_codigo(modulo, alvo)
    # G1 §1.8 (L2): as entradas do run foram conferidas no executar, e o job pode ter
    # esperado horas na fila depois disso — uma referência em /data muda sem o servidor
    # saber. Atesta o que vai ler AGORA, grava na mesma gravação da proveniência e, se
    # diferir do aprovado, não roda (só os nomes vão na mensagem: nada de caminho no log).
    esperadas = status.get("entradas")
    extra_inicio: dict[str, Any] = {}
    if isinstance(esperadas, dict):
        extra_inicio["entradas_execucao"] = atestar_entradas(esperadas, completo=False)
    write_status(job_id, str(status.get("status") or "running"), proveniencia_execucao=antes,
                 **extra_inicio)
    if isinstance(esperadas, dict):
        alteradas = sorted(n for n, e in esperadas.items()
                           if not isinstance(e, dict)
                           or divergencias(e, extra_inicio["entradas_execucao"].get(n), completo=False))
        if alteradas:
            raise RuntimeError("entrada alterada entre a aprovação e a execução: " + ", ".join(alteradas))
    append_log(job_id, f"$ {cmd}")
    processo = subprocess.Popen(
        shlex.split(cmd),
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    result_path = None
    assert processo.stdout is not None
    for linha in processo.stdout:
        linha = linha.rstrip()
        if not linha:
            continue
        append_log(job_id, linha)
        if linha.startswith(PREFIXO_RESULTADO):
            result_path = linha[len(PREFIXO_RESULTADO):].strip()
    rc = processo.wait()
    if rc != 0:
        raise RuntimeError(f"script terminou com código {rc}")
    # Resíduo do #22: o Rscript lê o .R aos poucos enquanto executa. Um arquivo editado no
    # lugar no meio de um DESeq2 de minutos (editor que trunca e regrava) roda o código novo
    # da metade em diante — e a proveniência tirada no início continuava igual à do plano.
    # O sha é tirado de novo DEPOIS do fim: diferente, o resultado não é de versão nenhuma.
    if proveniencia.sha_codigo(modulo, alvo) != antes:
        raise RuntimeError("o código mudou durante a execução (script ou _comum.py editado "
                           "com o job rodando): o resultado não corresponde a uma versão inteira")
    extra: dict[str, Any] = {}
    portas = status.get("portas")
    if isinstance(portas, dict):
        extra["sha_saidas"] = atestar_saidas(portas)
    if isinstance(esperadas, dict):
        # o sha COMPLETO do que o job leu: fecha o resíduo da impressão por amostragem (byte
        # trocado fora dos trechos, com o mtime devolvido, durante a espera na fila)
        extra["sha_entradas"] = atestar_entradas(esperadas, completo=True)
    if result_path:
        # Falso-done: returncode 0 NÃO prova que o artefato existe (ferramenta que
        # sai 0 sem escrever, disco cheio, caminho de dentro do container que não
        # existe no volume). Sem esta conferência, a UI mostraria "concluído" e
        # depois "sem dados" — e o workflow encadeado seguiria para o passo 2.
        alvo_resultado = _no_repo(result_path)
        if not alvo_resultado.exists():
            raise RuntimeError(
                f"script anunciou 'RESULTADO: {result_path}' mas o arquivo não existe")
        if alvo_resultado.is_file() and alvo_resultado.stat().st_size == 0:
            raise RuntimeError(f"resultado vazio (0 bytes): {result_path}")
        write_status(job_id, "done", result_path=result_path, **extra)
    else:
        append_log(job_id, "AVISO: script não imprimiu 'RESULTADO: <caminho>' "
                           "— job sem artefato declarado (nada a exibir na UI)")
        write_status(job_id, "done", **extra)
