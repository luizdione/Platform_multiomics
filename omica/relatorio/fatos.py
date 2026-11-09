"""fatos.py — a FICHA DE FATOS de um run: todo número (e todo identificador) que o relatório
pode citar, cada um com a fonte exata de onde saiu.

O redator (Claude, Camada 2) nunca escreve um número: escreve `{{f:<id>}}` e o renderizador
troca pelo valor daqui. Esta ficha é gerada por código a partir dos ENVELOPES aprovados
(sha256 de cada porta) — e o portão de números (portao_numeros.py) reabre cada fonte na hora
de renderizar para conferir que o valor continua lá, na célula certa.

Tipos de fonte:
  json   — campo de um resumo JSON (`campo` com pontos para aninhado; um segmento numérico é o
           índice 0-based numa lista de valores simples, como em
           `painel_resumo.nao_encontrados.0`)
  celula — tabela TSV: linha achada pela CHAVE (ex.: gene_id), coluna pelo NOME; o texto cru
           da célula fica em `bruto` (comparação exata, sem arredondamento)
  plano  — escolha do autor registrada no plano aprovado
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any, Iterator

# campos de resumo que não são resultado (carimbo de hora, caminhos)
_IGNORAR = {"gerado_em"}
# Itens de uma lista que viram fato. A genômica trouxe o primeiro resumo com resultado em lista
# (painel_resumo.nao_encontrados = ["HSPA1A"]: o gene do painel que o GTF não tem e que, por
# isso, não terá variante nenhuma). Sem isso o redator não podia nomeá-lo — o prompt proíbe
# nome de gene fora da ficha. O teto segura a ficha de um painel de 500 símbolos.
MAX_ITENS_LISTA = 50


def _escalar(v: Any) -> bool:
    return isinstance(v, (str, int, float)) and not isinstance(v, bool)


def _folhas(dados: Any, prefixo: str = "") -> Iterator[tuple[str, Any, str]]:
    """(campo, valor, rótulo) de cada folha. Lista de valores simples: um fato por item, com
    o ÍNDICE REAL na lista (o portão reabre `campo` andando pelo mesmo caminho). Lista de
    LISTAS de valores simples (X4, F6: a matriz de confusão do ML): um fato por célula,
    `{lista}.{i}.{j}`, rotulado "linha i+1, coluna j+1", até MAX_ITENS_LISTA em cada nível.
    Lista de objetos fica fora (o caminho não seria estável)."""
    if isinstance(dados, dict):
        for k, v in dados.items():
            yield from _folhas(v, f"{prefixo}{k}.")
    elif isinstance(dados, list):
        lista = prefixo[:-1]
        for i, v in enumerate(dados[:MAX_ITENS_LISTA]):
            if _escalar(v):
                yield f"{lista}.{i}", v, f"{lista}: item {i + 1} de {len(dados)}"
            elif isinstance(v, list):
                for j, w in enumerate(v[:MAX_ITENS_LISTA]):
                    if _escalar(w):
                        yield f"{lista}.{i}.{j}", w, f"{lista}: linha {i + 1}, coluna {j + 1}"
    elif _escalar(dados):
        yield prefixo[:-1], dados, prefixo[:-1]


def _parece_caminho(valor: Any) -> bool:
    return isinstance(valor, str) and ("/" in valor or "\\" in valor or valor.endswith((".tsv", ".png", ".json")))


def numero_ou_texto(bruto: str) -> Any:
    try:
        x = float(bruto)
    except ValueError:
        return bruto
    if not math.isfinite(x):
        return bruto
    return int(x) if x.is_integer() and "." not in bruto and "e" not in bruto.lower() else x


def ler_tsv(caminho: Path) -> tuple[list[str], list[list[str]]]:
    with caminho.open(encoding="utf-8", newline="") as f:
        leitor = csv.reader(f, delimiter="\t")
        cab = next(leitor, None) or []
        return cab, [l for l in leitor if l]


def _chave_ordenacao(bruto: str) -> tuple[int, float]:
    try:
        x = float(bruto)
        return (0, x) if math.isfinite(x) else (1, 0.0)
    except ValueError:
        return (1, 0.0)  # NA vai para o fim


def gerar(plano: dict[str, Any], envelopes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Ficha de fatos do run a partir do plano congelado e dos envelopes APROVADOS."""
    rel = plano.get("relatorio", {})
    fatos: list[dict[str, Any]] = []

    for nome, valor in plano.get("escolhas", {}).items():
        fatos.append({"id": f"escolha.{nome}", "valor": valor, "tipo": "texto", "passo": None,
                      "rotulo": f"escolha do autor: {nome}",
                      "fonte": {"tipo": "plano", "campo": f"escolhas.{nome}"}})

    for ref in rel.get("resumos", []):
        passo, porta = ref.split(".", 1)
        info = envelopes[passo]["portas"][porta]
        dados = json.loads(Path(info["caminho"]).read_text(encoding="utf-8"))
        for campo, valor, rotulo in _folhas(dados):
            if campo in _IGNORAR or _parece_caminho(valor):
                continue
            fatos.append({"id": f"{passo}.{porta}.{campo}", "valor": valor, "passo": passo,
                          "tipo": "texto" if isinstance(valor, str) else "numero",
                          "rotulo": rotulo,
                          "fonte": {"tipo": "json", "arquivo": info["caminho"],
                                    "sha256": info["sha256"], "campo": campo}})

    for t in rel.get("tabelas", []):
        passo, porta = t["passo"], t["porta"]
        info = envelopes[passo]["portas"][porta]
        cab, linhas = ler_tsv(Path(info["caminho"]))
        ichave = cab.index(t["chave"])
        top = t.get("top") or {}
        if top.get("ordenar"):
            iord = cab.index(top["ordenar"])
            linhas = sorted(linhas, key=lambda l: _chave_ordenacao(l[iord]))  # estável
        for i, linha in enumerate(linhas[: int(top.get("n", 10))], start=1):
            chave = linha[ichave]
            base = {"tipo": "celula", "arquivo": info["caminho"], "sha256": info["sha256"],
                    "chave_coluna": t["chave"], "chave": chave}
            for coluna in [t["chave"], *t["colunas"]]:
                bruto = linha[cab.index(coluna)]
                valor = numero_ou_texto(bruto)
                fatos.append({"id": f"{passo}.{porta}.top{i}.{coluna}", "valor": valor, "passo": passo,
                              "tipo": "texto" if isinstance(valor, str) else "numero",
                              "rotulo": f"{coluna} do {i}º por {top.get('ordenar', 'ordem do arquivo')}",
                              "fonte": {**base, "coluna": coluna, "bruto": bruto}})

    figuras = []
    for ref in rel.get("figuras", []):
        passo, porta = ref.split(".", 1)
        info = envelopes[passo]["portas"][porta]
        figuras.append({"id": ref, "passo": passo, "arquivo": info["caminho"], "sha256": info["sha256"]})

    # selo do CÓDIGO de cada passo: vigente se algum workflow selado roda o mesmo script
    selos = {p["id"]: ("vigente" if any(s["vigente"] for s in p.get("selos", [])) else
                       "vencido" if p.get("selos") else "sem_selo")
             for p in plano["passos"]}

    ids = [f["id"] for f in fatos]
    repetidos = sorted({i for i in ids if ids.count(i) > 1})
    if repetidos:
        raise ValueError(f"ids de fato repetidos: {repetidos[:5]}")
    invariantes = rel.get("invariantes", [])
    return {"formato": "omica.fatos/1", "run_id": plano["run_id"], "plano_sha256": plano["plano_sha256"],
            "fatos": fatos, "figuras": figuras, "selos": selos,
            "invariantes": invariantes,
            "tabelas_invariantes": _tabelas_invariantes(invariantes, envelopes),
            "alertas": _alertas(rel.get("alertas") or [], fatos)}


def _tabelas_invariantes(invariantes: list[dict[str, Any]],
                         envelopes: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """X3 (F6): as portas que as invariantes leem INTEIRAS (`porta` e `desempate`), com o
    arquivo e o sha256 do envelope ENTREGUE — o portão reabre e confere antes de contar.
    Plano de antes da F6 (sem as chaves): nenhuma."""
    saida: dict[str, dict[str, Any]] = {}
    for inv in invariantes:
        for ref in (inv.get("porta"), inv.get("desempate")):
            if not ref or ref in saida:
                continue
            passo, porta = ref.split(".", 1)
            info = envelopes[passo]["portas"][porta]
            saida[ref] = {"arquivo": info["caminho"], "sha256": info["sha256"], "formato": info.get("formato")}
    return saida


def _alertas(alertas: list[dict[str, Any]], fatos: list[dict[str, Any]]) -> list[str]:
    """X5 (F6): os textos dos alertas que VALEM — incondicional, ou com o fato igual a `igual`.
    Fato ausente da ficha não é "não vale": a geração falha (409 ficha_impossivel), porque um
    alerta que some em silêncio é exatamente o que o alerta existe para impedir."""
    por_id = {f["id"]: f for f in fatos}
    textos: list[str] = []
    for a in alertas:
        fato = a.get("fato") or ""
        if fato:
            if fato not in por_id:
                raise ValueError(f"alerta: fato {fato!r} ausente da ficha")
            valor = por_id[fato]["valor"]
            if isinstance(valor, bool) or not isinstance(valor, (int, float)) or valor != a.get("igual"):
                continue
        textos.append(str(a["texto"]))
    return textos
