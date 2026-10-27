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
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Optional

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


def conferir_invariantes(ficha: dict[str, Any]) -> list[str]:
    """R4 sobre a ficha inteira (independe do que o redator citou)."""
    por_id = {f["id"]: f for f in ficha["fatos"]}
    falhas = []
    for i, inv in enumerate(ficha.get("invariantes", []), start=1):
        nome = f"invariante {i} ({inv.get('tipo')})"
        try:
            if inv["tipo"] == "soma":
                soma = sum(_valor(por_id, p) for p in inv["partes"])
                total = _valor(por_id, inv["total"])
                if soma != total:
                    falhas.append(f"{nome}: {' + '.join(inv['partes'])} = {soma} ≠ {inv['total']} = {total}")
            elif inv["tipo"] == "igual":
                a, b = _valor(por_id, inv["a"]), _valor(por_id, inv["b"])
                if a != b:
                    falhas.append(f"{nome}: {inv['a']} = {a} ≠ {inv['b']} = {b}")
            elif inv["tipo"] == "recontagem":
                passo, porta = inv["tabela"].split(".", 1)
                arq = next((Path(f["fonte"]["arquivo"]) for f in ficha["fatos"]
                            if f["fonte"].get("tipo") == "celula" and f["id"].startswith(f"{passo}.{porta}.")), None)
                if arq is None:
                    falhas.append(f"{nome}: tabela {inv['tabela']} sem fatos na ficha (declare-a em relatorio.tabelas)")
                    continue
                cab, linhas = ler_tsv(arq)
                n = 0
                for linha in linhas:
                    passa = True
                    for flt in inv["filtros"]:
                        bruto = linha[cab.index(flt["coluna"])]
                        x = numero_ou_texto(bruto)
                        if not isinstance(x, (int, float)) or (isinstance(x, float) and math.isnan(x)):
                            passa = False
                            break
                        # .get e não `in`: o plano grava o filtro por model_dump(), com as
                        # chaves ausentes valendo None
                        if flt.get("menor_que") and not x < _valor(por_id, flt["menor_que"]):
                            passa = False
                        if flt.get("abs_maior_ou_igual") and not abs(x) >= _valor(por_id, flt["abs_maior_ou_igual"]):
                            passa = False
                    n += passa
                total = _valor(por_id, inv["total"])
                if n != total:
                    falhas.append(f"{nome}: recontagem em {inv['tabela']} = {n} ≠ {inv['total']} = {total}")
            else:
                falhas.append(f"{nome}: tipo desconhecido")
        except (KeyError, TypeError, ValueError) as e:
            falhas.append(f"{nome}: referência inválida ({e})")
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
