"""flux.py — fluxograma SVG do pipeline de um módulo, gerado do MANIFEST.

Herda a ideia do pipeline_svg do epigenoma (status por cor), mas sem arquivo
intermediário: o SVG nasce do registry a cada render — impossível divergir do
manifest (anti-lição nº 4 aplicada a diagramas)."""
from __future__ import annotations

from html import escape

from omica.registry import STATUS_TYPES, Manifest

_LARG_CAIXA, _ALT_CAIXA, _ESP_X, _ESP_Y, _TOPO = 250, 34, 40, 10, 46


def svg_do_modulo(m: Manifest) -> str:
    colunas = []
    for fase in m.fases:
        colunas.append((fase, fase.etapas))
    if not colunas:
        return ""
    n_col = len(colunas)
    altura = _TOPO + max(len(e) for _, e in colunas) * (_ALT_CAIXA + _ESP_Y) + 16
    largura = n_col * (_LARG_CAIXA + _ESP_X)
    partes = [
        f'<svg viewBox="0 0 {largura} {altura}" xmlns="http://www.w3.org/2000/svg" '
        f'role="img" style="max-width:100%;height:auto;font-family:Segoe UI,system-ui,sans-serif">'
    ]
    for i, (fase, etapas) in enumerate(colunas):
        x = i * (_LARG_CAIXA + _ESP_X)
        partes.append(
            f'<text x="{x + _LARG_CAIXA / 2}" y="18" text-anchor="middle" '
            f'font-size="13" font-weight="700" fill="#1f2d3d">{escape(fase.nome)}</text>'
        )
        if i > 0:
            y_seta = _TOPO + _ALT_CAIXA / 2
            partes.append(
                f'<path d="M {x - _ESP_X + 6} {y_seta} L {x - 8} {y_seta}" '
                f'stroke="#5c6b7a" stroke-width="2" fill="none"/>'
                f'<path d="M {x - 8} {y_seta - 5} L {x} {y_seta} L {x - 8} {y_seta + 5} Z" fill="#5c6b7a"/>'
            )
        for j, etapa in enumerate(etapas):
            y = _TOPO + j * (_ALT_CAIXA + _ESP_Y)
            cor = STATUS_TYPES[etapa.status_type]["cor"]
            rotulo = etapa.desc if len(etapa.desc) <= 38 else etapa.desc[:35] + "…"
            partes.append(
                f'<rect x="{x}" y="{y}" width="{_LARG_CAIXA}" height="{_ALT_CAIXA}" rx="8" '
                f'fill="{cor}" opacity="0.92"/>'
                f'<text x="{x + _LARG_CAIXA / 2}" y="{y + 21}" text-anchor="middle" '
                f'font-size="11" fill="#ffffff">{escape(rotulo)}</text>'
            )
    partes.append("</svg>")
    return "".join(partes)
