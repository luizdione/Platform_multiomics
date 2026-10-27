"""render.py — rascunho APROVADO pelo portão de números → relatório (Markdown + HTML).

Só roda depois de `portao_numeros.conferir(...)["ok"]`. Troca `{{f:id}}` pelo valor da ficha
formatado em pt-BR, `{{fig:id}}` pela figura com a legenda do redator, define os termos do
glossário na 1ª ocorrência e fecha com o QUADRO DE PROVENIÊNCIA: cada número citado → arquivo,
linha/coluna (ou campo) e sha256. É esse quadro que permite à revisão humana pegar o risco
que o portão não pega (o fato certo citado no sentido errado).

Segurança: o texto do redator vem de um LLM e vira página servida pela própria ômica. Todo
texto é ESCAPADO antes do Markdown — HTML cru do modelo nunca chega ao navegador.
"""
from __future__ import annotations

import html
import json
import math
import re
from pathlib import Path
from typing import Any, Optional

from omica.config import REPO_ROOT
from omica.relatorio.portao_numeros import PLACEHOLDER

GLOSSARIO = REPO_ROOT / "didatica" / "glossario.json"
_SUPER = str.maketrans("0123456789-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁻")
MARCA_SELO = "†"


def formatar(valor: Any) -> str:
    """pt-BR: milhar com ponto, decimal com vírgula, muito pequeno em notação científica."""
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return str(valor)
    if isinstance(valor, int):
        return f"{valor:,}".replace(",", ".")
    if not math.isfinite(valor):
        return str(valor)
    if valor != 0 and abs(valor) < 1e-3:
        mantissa, expoente = f"{valor:.2e}".split("e")
        return f"{mantissa.replace('.', ',')} × 10{str(int(expoente)).translate(_SUPER)}"
    casas = 3 if abs(valor) < 1 else 2
    texto = f"{valor:,.{casas}f}".rstrip("0").rstrip(".")
    texto = texto.replace(",", "§").replace(".", ",").replace("§", ".")
    return texto.replace("-", "−")


def _glossario(modulo: str) -> list[dict[str, str]]:
    try:
        termos = json.loads(GLOSSARIO.read_text(encoding="utf-8"))["termos"]
    except (OSError, ValueError, KeyError):
        return []
    return [t for t in termos if t.get("modulo") in (modulo, "geral", None)]


def _definir_primeira_ocorrencia(texto_html: str, termos: list[dict[str, str]], usados: set[str]) -> str:
    """Envolve a 1ª ocorrência de cada termo em <abbr title=definição> — só em NÓS DE TEXTO:
    'padj' também aparece em data-fato="de.de_table.top1.padj", e um <abbr> dentro de
    atributo quebraria a página."""
    partes = re.split(r"(<[^>]+>)", texto_html)
    for i, parte in enumerate(partes):
        if parte.startswith("<") or not parte.strip():
            continue
        for t in termos:
            nome = t["termo"]
            if nome in usados:
                continue
            m = re.search(rf"(?<!\w){re.escape(html.escape(nome, quote=False))}(?!\w)", parte)
            if m:
                abbr = f'<abbr title="{html.escape(t["def_pt"], quote=True)}">{m.group()}</abbr>'
                parte = parte[:m.start()] + abbr + parte[m.end():]
                usados.add(nome)
        partes[i] = parte
    return "".join(partes)


def _paragrafo(texto_llm: str, trocar) -> str:
    """Texto do LLM → HTML seguro. Escapa TUDO primeiro; depois só **negrito** e *itálico*.
    Nada de Markdown completo: `[x](javascript:...)` e `![](http://rastreador)` virariam link
    e imagem de verdade na página da ômica."""
    seguro = html.escape(texto_llm, quote=False)
    seguro = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", seguro)
    seguro = re.sub(r"(?<![*\w])\*(?![*\s])(.+?)(?<![*\s])\*(?![*\w])", r"<em>\1</em>", seguro)
    corpo = f"<p>{PLACEHOLDER.sub(trocar, seguro)}</p>"
    return corpo.replace("<p></p>", "").replace("<p> </p>", "")


def renderizar(rascunho: dict[str, Any], ficha: dict[str, Any], resultado_portao: dict[str, Any],
               modulo: str, titulo: str, base_figuras: str = "figuras",
               arquivos_rel: Optional[callable] = None) -> dict[str, Any]:
    """Devolve {"markdown", "html", "proveniencia"}. `arquivos_rel(caminho)` encurta o caminho
    de origem para o quadro (ex.: relativo à pasta de dados)."""
    if not resultado_portao.get("ok"):
        raise ValueError("rascunho não passou no portão de números — nada é renderizado")
    por_id = {f["id"]: f for f in ficha["fatos"]}
    figs = {f["id"]: f for f in ficha["figuras"]}
    legendas = {l["fig"]: l["texto"] for l in rascunho.get("legendas", [])}
    marcados = set(resultado_portao.get("marcados", []))
    curto = arquivos_rel or (lambda c: c)
    termos = _glossario(modulo)
    usados: set[str] = set()

    def trocar(m: re.Match) -> str:
        tipo, ident = m.group(1), m.group(2)
        if tipo == "f":
            f = por_id[ident]
            marca = f"<sup>{MARCA_SELO}</sup>" if ident in marcados else ""
            return f'<span class="fato" data-fato="{html.escape(ident)}">{html.escape(formatar(f["valor"]))}</span>{marca}'
        fig = figs[ident]
        nome = Path(fig["arquivo"]).name
        legenda = html.escape(legendas.get(ident, ""))
        legenda = PLACEHOLDER.sub(lambda mm: trocar(mm) if mm.group(1) == "f" else "", legenda)
        # figura é bloco: fecha o parágrafo, põe a figura, reabre (o vazio é limpo depois)
        return (f'</p><figure><img src="{html.escape(base_figuras)}/{html.escape(nome)}" alt="{html.escape(ident)}">'
                f"<figcaption>{legenda}</figcaption></figure><p>")

    partes_md = [f"# {html.escape(titulo)}"]
    corpo_html = [f"<h1>{html.escape(titulo)}</h1>"]
    for s in rascunho.get("secoes", []):
        partes_md.append(f"## {s['titulo']}")
        corpo_html.append(f"<h2>{html.escape(s['titulo'])}</h2>")
        for p in s.get("paragrafos", []):
            partes_md.append(PLACEHOLDER.sub(lambda m: formatar(por_id[m.group(2)]["valor"])
                                             if m.group(1) == "f" else f"[figura {m.group(2)}]", p))
            corpo_html.append(_definir_primeira_ocorrencia(_paragrafo(p, trocar), termos, usados))

    citados = resultado_portao.get("citados", [])
    proveniencia = []
    for ident in citados:
        f = por_id[ident]
        fonte = f["fonte"]
        onde = (f"linha {fonte['chave_coluna']}={fonte['chave']}, coluna {fonte['coluna']}"
                if fonte["tipo"] == "celula" else f"campo {fonte['campo']}")
        proveniencia.append({"fato": ident, "valor": formatar(f["valor"]),
                             "arquivo": curto(fonte.get("arquivo", "plano.json")), "onde": onde,
                             "sha256": (fonte.get("sha256") or "")[:12], "selo": ident not in marcados})
    linhas = "".join(
        f"<tr><td><code>{html.escape(p['fato'])}</code></td><td>{html.escape(p['valor'])}</td>"
        f"<td><code>{html.escape(p['arquivo'])}</code></td><td>{html.escape(p['onde'])}</td>"
        f"<td><code>{p['sha256']}</code></td></tr>" for p in proveniencia)
    corpo_html.append("<h2>Proveniência dos números</h2><table><thead><tr><th>Fato</th><th>Valor</th>"
                      f"<th>Arquivo</th><th>Onde</th><th>sha256</th></tr></thead><tbody>{linhas}</tbody></table>")
    if marcados:
        corpo_html.append(f"<p><sup>{MARCA_SELO}</sup> Número produzido por código sem selo vigente: "
                          "o script mudou desde a última re-execução validada.</p>")
    partes_md.append("## Proveniência dos números")
    partes_md += [f"- `{p['fato']}` = {p['valor']} — `{p['arquivo']}`, {p['onde']}, sha256 {p['sha256']}"
                  for p in proveniencia]
    return {"markdown": "\n\n".join(partes_md) + "\n", "html": "\n".join(corpo_html), "proveniencia": proveniencia}
