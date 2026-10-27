"""Ficha de fatos e portão de números do relatório (F4). Cada regra com canário que morde e
controle negativo que passa. Run sintético em tmp: nada depende de E:\\data nem de LLM.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from omica.relatorio import fatos as fatos_mod
from omica.relatorio.portao_numeros import NUMERO_SOLTO, conferir, conferir_invariantes

DE = ("gene_id\tgene_name\tbaseMean\tlog2FoldChange\tpvalue\tpadj\n"
      "ENSBTAG00000020550\tENSBTAG00000020550\t294.27\t-3.68\t2.39e-44\t3.75e-40\n"
      "ENSBTAG00000052337\tRPS29\t3540.8\t-2.14\t6.95e-33\t5.44e-29\n"
      "ENSBTAG00000030164\tRPL38\t2252.3\t1.64\t1.38e-32\t7.21e-29\n"
      "ENSBTAG00000000001\tGENE1\t10\t0.5\t0.01\t0.03\n"
      "ENSBTAG00000000002\tGENE2\t10\t2.5\t0.2\tNA\n")
RESUMO_DE = {"gerado_em": "x", "n_genes_testados": 5, "criterios": {"padj": 0.05, "lfc": 1},
             "n_deg_padj": 4, "n_deg_padj_e_lfc": 3, "n_up": 1, "n_down": 2,
             "top_gene": "ENSBTAG00000020550", "tabela_tsv": "/data/omica/x/de_table.tsv"}
RESUMO_VOLCANO = {"n_pontos": 4, "n_significativos": 3, "criterios": {"padj": 0.05, "lfc": 1.0},
                  "figura_png": "/data/omica/x/volcano.png"}
INVARIANTES = [
    {"tipo": "soma", "partes": ["de.de_resumo.n_up", "de.de_resumo.n_down"], "total": "de.de_resumo.n_deg_padj_e_lfc"},
    {"tipo": "igual", "a": "volcano.volcano_resumo.n_significativos", "b": "de.de_resumo.n_deg_padj_e_lfc"},
    {"tipo": "recontagem", "tabela": "de.de_table",
     "filtros": [{"coluna": "padj", "menor_que": "de.de_resumo.criterios.padj"},
                 {"coluna": "log2FoldChange", "abs_maior_ou_igual": "de.de_resumo.criterios.lfc"}],
     "total": "de.de_resumo.n_deg_padj_e_lfc"},
    {"tipo": "recontagem", "tabela": "de.de_table",
     "filtros": [{"coluna": "padj", "menor_que": "de.de_resumo.criterios.padj"}], "total": "de.de_resumo.n_deg_padj"},
]


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _run(tmp_path: Path, resumo_de: dict = RESUMO_DE, selos_volcano=None) -> tuple[dict, dict]:
    arqs = {"de_table": tmp_path / "de_table.tsv", "de_resumo": tmp_path / "de_resumo.json",
            "volcano_resumo": tmp_path / "volcano_resumo.json", "volcano_png": tmp_path / "volcano.png"}
    arqs["de_table"].write_text(DE, encoding="utf-8")
    arqs["de_resumo"].write_text(json.dumps(resumo_de), encoding="utf-8")
    arqs["volcano_resumo"].write_text(json.dumps(RESUMO_VOLCANO), encoding="utf-8")
    arqs["volcano_png"].write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 2000)
    porta = lambda n: {"caminho": arqs[n].as_posix(), "sha256": _sha(arqs[n])}  # noqa: E731
    envelopes = {"de": {"portas": {"de_table": porta("de_table"), "de_resumo": porta("de_resumo")}},
                 "volcano": {"portas": {"volcano_resumo": porta("volcano_resumo"),
                                        "volcano_png": porta("volcano_png")}}}
    plano = {"run_id": "r1", "plano_sha256": "a" * 64, "escolhas": {"referencia": "winter_control"},
             "passos": [{"id": "de", "selos": [{"workflow": "wf", "vigente": True}]},
                        {"id": "volcano", "selos": selos_volcano if selos_volcano is not None
                         else [{"workflow": "wf", "vigente": True}]}],
             "relatorio": {"figuras": ["volcano.volcano_png"], "resumos": ["de.de_resumo", "volcano.volcano_resumo"],
                           "tabelas": [{"passo": "de", "porta": "de_table", "chave": "gene_id",
                                        "colunas": ["gene_name", "log2FoldChange", "padj"],
                                        "top": {"n": 3, "ordenar": "padj"}}],
                           "invariantes": INVARIANTES}}
    return plano, fatos_mod.gerar(plano, envelopes)


RASCUNHO_BOM = {
    "secoes": [{"id": "resultados", "titulo": "Expressão diferencial com DESeq2",
                "paragrafos": ["Com referência {{f:escolha.referencia}}, {{f:de.de_resumo.n_deg_padj_e_lfc}} genes "
                               "passaram nos dois cortes ({{f:de.de_resumo.n_up}} induzidos, "
                               "{{f:de.de_resumo.n_down}} reprimidos). O mais significativo foi "
                               "{{f:de.de_table.top1.gene_id}} (padj {{f:de.de_table.top1.padj}}, "
                               "log2FC {{f:de.de_table.top1.log2FoldChange}}). {{fig:volcano.volcano_png}}"]}],
    "legendas": [{"fig": "volcano.volcano_png", "texto": "Volcano: −log10(padj) contra log2FC."}],
}


# ------------------------------------------------------------------ ficha
def test_ficha_tem_fonte_exata_e_ignora_caminhos(tmp_path):
    _, ficha = _run(tmp_path)
    por_id = {f["id"]: f for f in ficha["fatos"]}
    assert por_id["de.de_resumo.n_up"]["valor"] == 1
    assert por_id["de.de_resumo.criterios.padj"]["valor"] == 0.05
    assert "de.de_resumo.tabela_tsv" not in por_id and "de.de_resumo.gerado_em" not in por_id
    top1 = por_id["de.de_table.top1.padj"]
    assert top1["fonte"]["chave"] == "ENSBTAG00000020550" and top1["fonte"]["bruto"] == "3.75e-40"
    # NA vai para o fim da ordenação, não para o topo
    assert por_id["de.de_table.top3.gene_id"]["valor"] == "ENSBTAG00000030164"


def test_controle_rascunho_honesto_passa(tmp_path):
    plano, ficha = _run(tmp_path)
    r = conferir(RASCUNHO_BOM, ficha, plano)
    assert r["ok"], r["falhas"]
    assert "de.de_table.top1.padj" in r["citados"] and r["figuras"] == ["volcano.volcano_png"]


# ------------------------------------------------------------------ R1
@pytest.mark.parametrize("texto,trecho", [
    ("Foram 2393 genes.", "2393"),
    ("Com padj < 0,05 e 1.059 induzidos.", "0,05"),
    ("Cerca de 15% dos genes.", "15"),
    ("O gene ENSBTAG00000020550 lidera.", "ENSBTAG00000020550"),
    ("Dígito arábico ٣ solto.", "٣"),
])
def test_r1_numero_ou_gene_fora_de_placeholder_reprova(tmp_path, texto, trecho):
    plano, ficha = _run(tmp_path)
    r = conferir({"secoes": [{"id": "s", "titulo": "t", "paragrafos": [texto]}]}, ficha, plano)
    assert not r["ok"] and any(f["regra"] == "R1" and trecho in f["detalhe"] for f in r["falhas"])


@pytest.mark.parametrize("texto", ["DESeq2 e log2FC", "cromossomo BTA14", "modelo qwen3", "−log10(padj)"])
def test_r1_nomes_com_digito_nao_sao_numeros(texto):
    assert not NUMERO_SOLTO.findall(texto)


# ------------------------------------------------------------------ R2
def test_r2_fato_ou_figura_inexistente_reprova(tmp_path):
    plano, ficha = _run(tmp_path)
    r = conferir({"secoes": [{"id": "s", "titulo": "t", "paragrafos": ["{{f:de.de_resumo.n_inventado}} {{fig:nada}}"]}]},
                 ficha, plano)
    assert sorted(f["regra"] for f in r["falhas"]) == ["R2", "R2"]


# ------------------------------------------------------------------ R3
def test_r3_valor_de_pvalue_no_fato_padj_da_mesma_linha_reprova(tmp_path):
    """O canário do plano: a célula certa é (linha da chave, coluna padj). O pvalue da MESMA
    linha é número plausível — e tem de ser reprovado."""
    plano, ficha = _run(tmp_path)
    adulterada = copy.deepcopy(ficha)
    f = next(x for x in adulterada["fatos"] if x["id"] == "de.de_table.top1.padj")
    f["valor"], f["fonte"]["bruto"] = 2.39e-44, "2.39e-44"
    r = conferir(RASCUNHO_BOM, adulterada, plano)
    assert not r["ok"] and [x["regra"] for x in r["falhas"]] == ["R3"]
    assert conferir(RASCUNHO_BOM, ficha, plano)["ok"]  # controle: a original passa


def test_r3_linha_errada_reprova(tmp_path):
    plano, ficha = _run(tmp_path)
    f = next(x for x in ficha["fatos"] if x["id"] == "de.de_table.top1.padj")
    f["fonte"]["chave"] = "ENSBTAG00000052337"  # aponta para a linha do 2º gene
    assert not conferir(RASCUNHO_BOM, ficha, plano)["ok"]


def test_r3_arquivo_alterado_depois_do_envelope_reprova(tmp_path):
    plano, ficha = _run(tmp_path)
    (tmp_path / "de_resumo.json").write_text(json.dumps({**RESUMO_DE, "n_up": 1}) + " ", encoding="utf-8")
    r = conferir(RASCUNHO_BOM, ficha, plano)
    assert not r["ok"] and any("sha256" in f["detalhe"] for f in r["falhas"] if f["regra"] == "R3")


def test_r3_escolha_diferente_do_plano_reprova(tmp_path):
    plano, ficha = _run(tmp_path)
    plano["escolhas"]["referencia"] = "summer_heat"
    assert not conferir(RASCUNHO_BOM, ficha, plano)["ok"]


# ------------------------------------------------------------------ R4
@pytest.mark.parametrize("mexida,trecho", [
    ({"n_up": 2}, "invariante 1"),             # 2 + 2 ≠ 3
    ({"n_deg_padj_e_lfc": 4, "n_up": 2}, "invariante 2"),  # volcano diz 3
    ({"n_deg_padj": 5}, "invariante 4"),       # a tabela só tem 4 com padj < 0,05
])
def test_r4_invariante_quebrado_reprova(tmp_path, mexida, trecho):
    plano, ficha = _run(tmp_path, resumo_de={**RESUMO_DE, **mexida})
    falhas = conferir_invariantes(ficha)
    assert any(trecho in f for f in falhas), falhas


def test_r4_controle_resumo_coerente_passa(tmp_path):
    _, ficha = _run(tmp_path)
    assert conferir_invariantes(ficha) == []


def test_r4_invariantes_como_o_plano_grava(tmp_path):
    """No run real os invariantes chegam por Pipeline.model_dump(): filtro sem um dos limites
    vem com a chave valendo None. Isso não pode virar 'referência inválida'."""
    from omica.pipelines import Invariante

    _, ficha = _run(tmp_path)
    ficha["invariantes"] = [Invariante.model_validate(i).model_dump() for i in INVARIANTES]
    assert conferir_invariantes(ficha) == []


def test_r4_recontagem_morde_quando_a_tabela_diverge(tmp_path):
    plano, ficha = _run(tmp_path)
    tabela = tmp_path / "de_table.tsv"
    tabela.write_text(DE.replace("1.64", "0.64"), encoding="utf-8")  # um DEG a menos na tabela
    assert any("recontagem" in f for f in conferir_invariantes(ficha))


# ------------------------------------------------------------------ R5 / R6
def test_r5_figura_alterada_reprova(tmp_path):
    plano, ficha = _run(tmp_path)
    (tmp_path / "volcano.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"y" * 2000)
    r = conferir(RASCUNHO_BOM, ficha, plano)
    assert [f["regra"] for f in r["falhas"]] == ["R5"]


def test_r6_fato_de_passo_sem_selo_vigente_sai_marcado(tmp_path):
    plano, ficha = _run(tmp_path, selos_volcano=[{"workflow": "wf", "vigente": False}])
    rasc = {"secoes": [{"id": "s", "titulo": "t",
                        "paragrafos": ["{{f:volcano.volcano_resumo.n_significativos}} e {{f:de.de_resumo.n_up}}"]}]}
    r = conferir(rasc, ficha, plano)
    assert r["ok"] and r["marcados"] == ["volcano.volcano_resumo.n_significativos"]


def test_pipeline_real_declara_invariantes_validos():
    from omica.pipelines import carregar_pipelines

    validos, erros = carregar_pipelines()
    assert erros == {}
    assert len(validos["transcriptomica_de"].relatorio.invariantes) == 4


# ------------------------------------------------------------------ renderização
@pytest.mark.parametrize("valor,esperado", [
    (2393, "2.393"), (0.05, "0,05"), (-3.6831333144704, "−3,68"), (294.272290545492, "294,27"),
    (3.75066409540525e-40, "3,75 × 10⁻⁴⁰"), (0.0, "0"), (1234.5, "1.234,5"), ("RPS29", "RPS29"),
])
def test_formatacao_pt_br(valor, esperado):
    from omica.relatorio.render import formatar

    assert formatar(valor) == esperado


def test_render_recusa_rascunho_reprovado(tmp_path):
    from omica.relatorio.render import renderizar

    plano, ficha = _run(tmp_path)
    with pytest.raises(ValueError):
        renderizar(RASCUNHO_BOM, ficha, {"ok": False}, "transcriptomica", "t")


def test_render_escapa_html_e_links_do_modelo(tmp_path):
    from omica.relatorio.render import renderizar

    plano, ficha = _run(tmp_path)
    # sem dígitos de propósito: um "1" solto já seria reprovado pelo R1 antes de chegar aqui
    malicioso = {"secoes": [{"id": "s", "titulo": "<img src=x onerror=alert(document.cookie)>",
                             "paragrafos": ["<script>alert(x)</script> [clique](javascript:alert(x)) "
                                            "![p](http://rastreador.example/p.png) **ok** {{f:de.de_resumo.n_up}}"]}]}
    portao = conferir(malicioso, ficha, plano)
    assert portao["ok"], portao["falhas"]
    saida = renderizar(malicioso, ficha, portao, "transcriptomica", "Relatório")["html"]
    assert "<script" not in saida and "<img src=x" not in saida and "href=" not in saida
    assert "rastreador.example/p.png\"" not in saida and "<strong>ok</strong>" in saida


def test_render_troca_valores_figura_e_proveniencia(tmp_path, monkeypatch):
    from omica.relatorio import render

    monkeypatch.setattr(render, "_glossario", lambda m: [{"termo": "padj", "def_pt": "p ajustado"}])
    plano, ficha = _run(tmp_path)
    portao = conferir(RASCUNHO_BOM, ficha, plano)
    r = render.renderizar(RASCUNHO_BOM, ficha, portao, "transcriptomica", "Relatório")
    assert "3,75 × 10⁻⁴⁰" in r["html"] and '<figure><img src="figuras/volcano.png"' in r["html"]
    assert "<p></p>" not in r["html"]
    # o termo é definido no TEXTO, nunca dentro do atributo data-fato="...padj"
    assert '<abbr title="p ajustado">padj</abbr>' in r["html"]
    assert 'data-fato="de.de_table.top1.padj"' in r["html"]
    prov = {p["fato"]: p for p in r["proveniencia"]}
    assert prov["de.de_table.top1.padj"]["onde"] == "linha gene_id=ENSBTAG00000020550, coluna padj"
    assert len(prov["de.de_resumo.n_up"]["sha256"]) == 12
    assert "{{" not in r["markdown"]