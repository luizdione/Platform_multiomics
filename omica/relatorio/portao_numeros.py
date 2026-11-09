"""portao_numeros.py — portão de números do relatório (Camada 0). Nenhum número chega ao
leitor sem ter sido conferido NA FONTE, na hora de renderizar.

Regras (cada uma tem canário em tests/test_relatorio_portao.py):
  R1  nenhum número solto no texto do redator — só `{{f:<id>}}`; idem para identificadores
      Ensembl (um gene inventado é tão grave quanto um número inventado);
  R2  todo `{{f:id}}` e `{{fig:id}}` existe na ficha de fatos;
  R3  cada fato citado é reaberto: sha256 do arquivo igual ao do envelope, linha achada
      pela CHAVE e coluna pelo NOME, texto cru da célula idêntico (o valor de `pvalue`
      posto no fato `padj` da mesma linha é reprovado);
  R4  invariantes do pipeline (soma de partes, igualdade entre passos, recontagem na tabela);
  R5  cada figura citada tem o mesmo sha256 do envelope;
  R6  fato de passo cujo código não tem selo vigente sai marcado (não reprova: declara).

Risco residual declarado: o portão confere NÚMEROS, não DIREÇÃO — "sobe" no lugar de
"desce" passa. O quadro de proveniência existe para a revisão humana pegar isso.

F6 (X3/X3′): invariantes por PORTA (`contagem_linhas`, `soma_coluna` e `recontagem` sobre a
tabela inteira de uma porta, reaberta e conferida pelo sha256 do envelope, lida em FLUXO —
`todas_cpgs` pode ter milhões de linhas), o filtro `abs_maior_que` e o `desempate` da linha de
borda. Campos novos do plano são lidos com `.get`: um plano aprovado antes da F6 continua
com relatório.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from omica.relatorio.fatos import ler_tsv, numero_ou_texto

PLACEHOLDER = re.compile(r"\{\{(f|fig):([A-Za-z0-9_.\-]+)\}\}")
# número que não está colado a letra, dígito ou '_' (DESeq2, log2 e BTA14 são nomes, não
# quantidades — e o '4' de BTA14 não pode casar sozinho só porque vem depois de um '1').
# Sem re.ASCII de propósito: dígito árabe ou devanágari solto também reprova.
NUMERO_SOLTO = re.compile(r"(?<!\w)\d+(?:[.,]\d+)*(?!\w)")
ID_ENSEMBL = re.compile(r"ENS[A-Z]{0,8}[GTPE]\d{5,}")
CHAVES_PROIBIDAS = re.compile(r"\{\{|\}\}")


def _sha(caminho: Path) -> Optional[str]:
    if not caminho.is_file():
        return None
    h = hashlib.sha256()
    with caminho.open("rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def textos(rascunho: dict[str, Any]) -> list[tuple[str, str]]:
    """(onde, texto) de tudo que o redator escreveu."""
    saida = []
    for s in rascunho.get("secoes", []):
        saida.append((f"secao {s.get('id')}: título", str(s.get("titulo", ""))))
        for i, p in enumerate(s.get("paragrafos", []), start=1):
            saida.append((f"secao {s.get('id')} §{i}", str(p)))
    for leg in rascunho.get("legendas", []):
        saida.append((f"legenda {leg.get('fig')}", str(leg.get("texto", ""))))
    return saida


def _campo(dados: Any, caminho: str) -> Any:
    """Anda pelo caminho com pontos. Numa LISTA, o segmento é o índice 0-based em ASCII (o
    mesmo que fatos._folhas grava); índice fora da lista ou não numérico é campo ausente
    (KeyError), nunca IndexError/ValueError — que escapariam do R3 como erro 500."""
    for parte in caminho.split("."):
        if isinstance(dados, dict) and parte in dados:
            dados = dados[parte]
        elif isinstance(dados, list) and parte.isascii() and parte.isdigit() and int(parte) < len(dados):
            dados = dados[int(parte)]
        else:
            raise KeyError(caminho)
    return dados


def conferir_fato(fato: dict[str, Any], plano: Optional[dict[str, Any]] = None,
                  _cache: Optional[dict] = None) -> Optional[str]:
    """R3. None = confere; senão, o motivo. Reabre a fonte de verdade."""
    fonte = fato["fonte"]
    cache = _cache if _cache is not None else {}
    if fonte["tipo"] == "plano":
        if plano is None:
            return "fato do plano sem plano para conferir"
        try:
            return None if _campo(plano, fonte["campo"]) == fato["valor"] else "difere do plano aprovado"
        except KeyError:
            return "campo ausente no plano"
    arq = Path(fonte["arquivo"])
    if arq not in cache:
        cache[arq] = _sha(arq)
    if cache[arq] is None:
        return "arquivo de origem sumiu"
    if cache[arq] != fonte["sha256"]:
        return "arquivo de origem mudou depois do envelope (sha256 diferente)"
    if fonte["tipo"] == "json":
        chave_json = ("json", arq)
        if chave_json not in cache:
            cache[chave_json] = json.loads(arq.read_text(encoding="utf-8"))
        try:
            valor = _campo(cache[chave_json], fonte["campo"])
        except KeyError:
            return f"campo {fonte['campo']!r} ausente"
        return None if valor == fato["valor"] and type(valor) is type(fato["valor"]) else \
            f"valor na fonte ({valor!r}) difere do fato ({fato['valor']!r})"
    if fonte["tipo"] == "celula":
        chave_tsv = ("tsv", arq)
        if chave_tsv not in cache:
            cache[chave_tsv] = ler_tsv(arq)
        cab, linhas = cache[chave_tsv]
        if fonte["chave_coluna"] not in cab or fonte["coluna"] not in cab:
            return "coluna ausente na tabela"
        ic, iv = cab.index(fonte["chave_coluna"]), cab.index(fonte["coluna"])
        achadas = [l for l in linhas if len(l) > max(ic, iv) and l[ic] == fonte["chave"]]
        if len(achadas) != 1:
            return f"chave {fonte['chave']!r} achada {len(achadas)} vezes (precisa ser 1)"
        bruto = achadas[0][iv]
        if bruto != fonte["bruto"] or numero_ou_texto(bruto) != fato["valor"]:
            return f"célula ({fonte['chave']}, {fonte['coluna']}) = {bruto!r}, fato diz {fato['valor']!r}"
        return None
    return f"tipo de fonte desconhecido: {fonte['tipo']!r}"


def _valor(por_id: dict[str, dict], ref: str) -> float:
    if ref not in por_id:
        raise KeyError(ref)
    v = por_id[ref]["valor"]
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise TypeError(f"{ref} não é número")
    return v


class _Falha(Exception):
    """Falha de uma invariante, com a mensagem pronta (sem o prefixo "invariante i")."""


_LIMITES = ("menor_que", "abs_maior_ou_igual", "abs_maior_que")
_TOLERANCIA_BORDA = 1e-9  # relativa: tol = 1e-9 · max(1, |L|) (adendo §A2), só com desempate


@contextmanager
def _porta_em_fluxo(ficha: dict[str, Any], ref: str) -> Iterator[tuple[list[str], Iterator[list[str]]]]:
    """(cabeçalho, linhas de dado não vazias) da tabela INTEIRA de uma porta, em fluxo, depois
    de conferir o sha256 do envelope entregue (`ficha["tabelas_invariantes"]`). Nada de
    ler_tsv: ele carrega tudo, e `todas_cpgs` pode ter milhões de linhas."""
    info = (ficha.get("tabelas_invariantes") or {}).get(ref)
    if not isinstance(info, dict) or not info.get("arquivo"):
        raise _Falha(f"porta {ref} sem tabela na ficha (o envelope não a entregou)")
    arq = Path(info["arquivo"])
    sha = _sha(arq)
    if sha is None:
        raise _Falha(f"tabela de {ref} sumiu depois do envelope")
    if sha != info.get("sha256"):
        raise _Falha(f"tabela de {ref} mudou depois do envelope (sha256 diferente)")
    with arq.open(encoding="utf-8", newline="") as f:
        leitor = csv.reader(f, delimiter="\t")
        cab = next(leitor, None) or []
        yield cab, (linha for linha in leitor if linha)


def _celula(linha: list[str], i: int) -> str:
    return linha[i] if i < len(linha) else ""


def _estado(x: Any, tipo: str, limite: float, tolerante: bool) -> str:
    """"certo", "fora" ou "borda" de UM filtro. Exato (sem desempate): a regra de sempre —
    `x < L`, `|x| ≥ L`, `|x| > L`. Tolerante (com desempate, X3′): a menos de
    1e-9·max(1, |L|) do limite é "borda" — o texto de 15 algarismos não diz de que lado do
    limite o double da ferramenta estava."""
    if isinstance(x, bool) or not isinstance(x, (int, float)) or (isinstance(x, float) and math.isnan(x)):
        return "fora"
    v = x if tipo == "menor_que" else abs(x)
    if not tolerante:
        ok = v < limite if tipo == "menor_que" else v > limite if tipo == "abs_maior_que" else v >= limite
        return "certo" if ok else "fora"
    tol = _TOLERANCIA_BORDA * max(1.0, abs(limite))
    if tipo == "menor_que":
        return "certo" if v < limite - tol else "fora" if v > limite + tol else "borda"
    return "certo" if v > limite + tol else "fora" if v < limite - tol else "borda"


def _filtros(inv: dict[str, Any], cab: list[str], por_id: dict[str, dict]) -> list[tuple[int, str, float]]:
    """(índice da coluna, tipo do limite, valor do limite) de cada filtro. `.get` e não `in`: o
    plano grava o filtro por model_dump(), com as chaves ausentes valendo None (e um plano de
    antes da F6 nem tem `abs_maior_que`)."""
    saida = []
    for flt in inv["filtros"]:
        i = cab.index(flt["coluna"])  # coluna ausente: ValueError → "referência inválida"
        saida += [(i, tipo, _valor(por_id, flt[tipo])) for tipo in _LIMITES if flt.get(tipo)]
    return saida


def _estado_linha(linha: list[str], filtros: list[tuple[int, str, float]], tolerante: bool) -> str:
    estados = {_estado(numero_ou_texto(_celula(linha, i)), tipo, lim, tolerante) for i, tipo, lim in filtros}
    return "fora" if "fora" in estados else "certo" if estados <= {"certo"} else "borda"


def _chaves(cab: list[str], colunas: tuple[str, ...]) -> list[int]:
    return [cab.index(c) for c in colunas]


def _formato(ficha: dict[str, Any], ref: str) -> Optional[str]:
    info = (ficha.get("tabelas_invariantes") or {}).get(ref)
    return info.get("formato") if isinstance(info, dict) else None


def _recontar_porta(ficha: dict[str, Any], inv: dict[str, Any], por_id: dict[str, dict]) -> Optional[str]:
    """Recontagem sobre a tabela inteira de uma porta. Sem `desempate`, EXATA (como a de
    tabela). Com desempate (X3′): n = certas + bordas cuja chave está na porta de desempate;
    toda chave do desempate tem de existir na principal."""
    total = _valor(por_id, inv["total"])
    desempate = inv.get("desempate")
    if not desempate:
        with _porta_em_fluxo(ficha, inv["porta"]) as (cab, linhas):
            filtros = _filtros(inv, cab, por_id)
            n = sum(_estado_linha(linha, filtros, False) == "certo" for linha in linhas)
        if n != total:
            return f"recontagem em {inv['porta']} = {n} ≠ {inv['total']} = {total}"
        return None
    from omica.contrato.formatos import CHAVE_LINHA  # leve; só quando há desempate

    colunas_p, colunas_d = CHAVE_LINHA.get(_formato(ficha, inv["porta"]) or ""), CHAVE_LINHA.get(
        _formato(ficha, desempate) or "")
    if colunas_p is None or colunas_d is None or colunas_p != colunas_d:
        raise _Falha(f"desempate {desempate} sem a mesma chave de linha de {inv['porta']}")
    with _porta_em_fluxo(ficha, desempate) as (cab, linhas):
        idx = _chaves(cab, colunas_d)
        no_desempate = {":".join(_celula(linha, i) for i in idx) for linha in linhas}
    n_certo = n_borda = n_borda_dentro = 0
    achadas: set[str] = set()
    with _porta_em_fluxo(ficha, inv["porta"]) as (cab, linhas):
        filtros = _filtros(inv, cab, por_id)
        idx = _chaves(cab, colunas_p)
        for linha in linhas:
            chave = ":".join(_celula(linha, i) for i in idx)
            dentro = chave in no_desempate
            if dentro:
                achadas.add(chave)
            estado = _estado_linha(linha, filtros, True)
            if estado == "certo":
                n_certo += 1
            elif estado == "borda":
                n_borda += 1
                n_borda_dentro += dentro
    ausentes = len(no_desempate) - len(achadas)
    if ausentes:
        return f"{ausentes} linha(s) de {desempate} ausente(s) em {inv['porta']}"
    n = n_certo + n_borda_dentro
    if n != total:
        return (f"recontagem em {inv['porta']} = {n} (certas {n_certo}, de borda {n_borda}, das quais "
                f"{n_borda_dentro} em {desempate}) ≠ {inv['total']} = {total}")
    return None


def _tabela_dos_fatos(ficha: dict[str, Any], tabela: str) -> Optional[Path]:
    passo, porta = tabela.split(".", 1)
    return next((Path(f["fonte"]["arquivo"]) for f in ficha["fatos"]
                 if f["fonte"].get("tipo") == "celula" and f["id"].startswith(f"{passo}.{porta}.")), None)


def _soma_coluna(cab: list[str], linhas: Iterator[list[str]], coluna: str) -> float:
    i = cab.index(coluna)
    soma: float = 0
    for n, linha in enumerate(linhas, start=2):
        x = numero_ou_texto(_celula(linha, i))
        if isinstance(x, bool) or not isinstance(x, (int, float)):
            raise _Falha(f"coluna {coluna}: valor não numérico na linha {n}")
        soma += x
    return soma


def _conferir_uma(ficha: dict[str, Any], inv: dict[str, Any], por_id: dict[str, dict]) -> Optional[str]:
    """A falha de UMA invariante (sem o prefixo), ou None."""
    tipo = inv["tipo"]
    if tipo == "soma":
        soma = sum(_valor(por_id, p) for p in inv["partes"])
        total = _valor(por_id, inv["total"])
        return None if soma == total else f"{' + '.join(inv['partes'])} = {soma} ≠ {inv['total']} = {total}"
    if tipo == "igual":
        a, b = _valor(por_id, inv["a"]), _valor(por_id, inv["b"])
        return None if a == b else f"{inv['a']} = {a} ≠ {inv['b']} = {b}"
    if tipo == "contagem_linhas":
        total = _valor(por_id, inv["total"])
        with _porta_em_fluxo(ficha, inv["porta"]) as (_cab, linhas):
            n = sum(1 for _ in linhas)
        return None if n == total else f"linhas de dado em {inv['porta']} = {n} ≠ {inv['total']} = {total}"
    if tipo == "soma_coluna":
        total = _valor(por_id, inv["total"])
        if inv.get("porta"):
            with _porta_em_fluxo(ficha, inv["porta"]) as (cab, linhas):
                soma = _soma_coluna(cab, linhas, inv["coluna"])
            onde = inv["porta"]
        else:
            arq = _tabela_dos_fatos(ficha, inv["tabela"])
            if arq is None:
                return f"tabela {inv['tabela']} sem fatos na ficha (declare-a em relatorio.tabelas)"
            cab, linhas = ler_tsv(arq)
            soma, onde = _soma_coluna(cab, iter(linhas), inv["coluna"]), inv["tabela"]
        return None if soma == total else f"Σ {inv['coluna']} em {onde} = {soma} ≠ {inv['total']} = {total}"
    if tipo == "recontagem":
        if inv.get("porta"):
            return _recontar_porta(ficha, inv, por_id)
        arq = _tabela_dos_fatos(ficha, inv["tabela"])
        if arq is None:
            return f"tabela {inv['tabela']} sem fatos na ficha (declare-a em relatorio.tabelas)"
        cab, linhas = ler_tsv(arq)
        filtros = _filtros(inv, cab, por_id)
        n = sum(_estado_linha(linha, filtros, False) == "certo" for linha in linhas)
        total = _valor(por_id, inv["total"])
        return None if n == total else f"recontagem em {inv['tabela']} = {n} ≠ {inv['total']} = {total}"
    return "tipo desconhecido"


def conferir_invariantes(ficha: dict[str, Any]) -> list[str]:
    """R4 sobre a ficha inteira (independe do que o redator citou)."""
    por_id = {f["id"]: f for f in ficha["fatos"]}
    falhas = []
    for i, inv in enumerate(ficha.get("invariantes", []), start=1):
        nome = f"invariante {i} ({inv.get('tipo')})"
        try:
            motivo = _conferir_uma(ficha, inv, por_id)
        except _Falha as e:
            motivo = str(e)
        except (KeyError, TypeError, ValueError, OSError, csv.Error) as e:
            motivo = f"referência inválida ({e})"
        if motivo:
            falhas.append(f"{nome}: {motivo}")
    return falhas


def conferir(rascunho: dict[str, Any], ficha: dict[str, Any],
             plano: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Portão completo sobre o rascunho do redator. {"ok", "falhas": [{regra, onde, detalhe}],
    "citados", "marcados"}."""
    falhas: list[dict[str, str]] = []
    por_id = {f["id"]: f for f in ficha["fatos"]}
    figs = {f["id"]: f for f in ficha["figuras"]}
    citados: list[str] = []
    figuras_citadas: list[str] = []

    for onde, texto in textos(rascunho):
        sem = PLACEHOLDER.sub(" ", texto)
        if CHAVES_PROIBIDAS.search(sem):
            falhas.append({"regra": "R1", "onde": onde, "detalhe": "placeholder malformado"})
        for m in NUMERO_SOLTO.finditer(sem):
            falhas.append({"regra": "R1", "onde": onde, "detalhe": f"número fora de placeholder: {m.group()!r}"})
        for m in ID_ENSEMBL.finditer(sem):
            falhas.append({"regra": "R1", "onde": onde, "detalhe": f"identificador fora de placeholder: {m.group()!r}"})
        for tipo, ident in PLACEHOLDER.findall(texto):
            alvo = por_id if tipo == "f" else figs
            if ident not in alvo:
                falhas.append({"regra": "R2", "onde": onde, "detalhe": f"{{{{{tipo}:{ident}}}}} não existe na ficha"})
            elif tipo == "f":
                citados.append(ident)
            else:
                figuras_citadas.append(ident)

    cache: dict = {}
    for ident in dict.fromkeys(citados):
        motivo = conferir_fato(por_id[ident], plano, cache)
        if motivo:
            falhas.append({"regra": "R3", "onde": ident, "detalhe": motivo})

    for detalhe in conferir_invariantes(ficha):
        falhas.append({"regra": "R4", "onde": "ficha", "detalhe": detalhe})

    for ident in dict.fromkeys(figuras_citadas):
        f = figs[ident]
        if _sha(Path(f["arquivo"])) != f["sha256"]:
            falhas.append({"regra": "R5", "onde": ident, "detalhe": "figura mudou depois do envelope"})

    selos = ficha.get("selos", {})
    marcados = sorted({i for i in citados if selos.get(por_id[i].get("passo") or "", "vigente") != "vigente"})
    return {"ok": not falhas, "falhas": falhas, "citados": sorted(set(citados)),
            "figuras": sorted(set(figuras_citadas)), "marcados": marcados}
