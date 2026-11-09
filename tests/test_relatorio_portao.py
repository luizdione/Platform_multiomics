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

# ======================================================= F6: invariantes por porta (X3/X3′)
# A recontagem do DMC: o methylKit decidiu |meth.diff| > 15 sobre o double; o texto de 15
# algarismos tem uma CpG com |diff| = 15 exatos. Com '>=' reconta 276, com '>' 275 (o selado).
# O desempate (X3′) decide a linha de borda pela presença dela no dmc.tsv.
SELADO = Path("E:/data/omica/results/epigenomica/demo")
_COM_SELADO = pytest.mark.skipif(not (SELADO / "dmc" / "todas_cpgs.tsv").is_file(),
                                 reason="sem os selados da epigenômica em E:\\data")
CAB_MK = "chr\tstart\tend\tstrand\tpvalue\tqvalue\tmeth.diff\n"
RESUMO_DMC = {"criterios": {"qvalue": 0.05, "diff_pct": 15}, "n_dmc": 275, "n_genes_painel_com_dmc": 267}
RECONT = {"tipo": "recontagem", "porta": "dmc.todas_cpgs",
          "filtros": [{"coluna": "qvalue", "menor_que": "dmc.dmc_resumo.criterios.qvalue"},
                      {"coluna": "meth.diff", "abs_maior_que": "dmc.dmc_resumo.criterios.diff_pct"}],
          "total": "dmc.dmc_resumo.n_dmc", "desempate": "dmc.dmc"}


def _ficha_porta(tmp_path: Path, invariantes: list, resumo: dict, **tabelas: Path) -> dict:
    """Ficha de um run fictício cujo passo `dmc` entregou as tabelas dadas (porta → arquivo) e
    o resumo; os envelopes levam o formato, como o runs.envelope grava."""
    formatos_porta = {"todas_cpgs": "cpgs_methylkit", "dmc": "dmc_methylkit", "intersecao": "tabela_intersecao_painel"}
    arq_resumo = tmp_path / "dmc_resumo.json"
    arq_resumo.write_text(json.dumps(resumo), encoding="utf-8")
    portas = {"dmc_resumo": {"caminho": arq_resumo.as_posix(), "sha256": _sha(arq_resumo), "formato": "resumo_json"}}
    for porta, arq in tabelas.items():
        portas[porta] = {"caminho": arq.as_posix(), "sha256": _sha(arq), "formato": formatos_porta[porta]}
    plano = {"run_id": "r", "plano_sha256": "a" * 64, "escolhas": {}, "passos": [{"id": "dmc", "selos": []}],
             "relatorio": {"figuras": [], "resumos": ["dmc.dmc_resumo"], "tabelas": [], "invariantes": invariantes}}
    return fatos_mod.gerar(plano, {"dmc": {"portas": portas}})


def _tsv_mk(caminho: Path, linhas: list[str]) -> Path:
    caminho.write_text(CAB_MK + "".join(f"{l}\n" for l in linhas), encoding="utf-8")
    return caminho


@_COM_SELADO
@pytest.mark.parametrize("filtro,desempate,total,ok", [
    ("abs_maior_que", "dmc.dmc", 275, True),        # X3′ 1: o demo com desempate → 275
    ("abs_maior_ou_igual", None, 275, False),       # X3: '>=' reconta 276 (canário da ESPEC)
    ("abs_maior_que", None, 275, True),             # X3: '>' exato reconta 275
    ("abs_maior_que", "dmc.dmc", 274, False),       # X3′ 4: total errado com desempate
    ("abs_maior_que", "dmc.dmc", 277, False),
])
def test_x3_recontagem_do_dmc_selado(tmp_path, filtro, desempate, total, ok):
    inv = {**RECONT, "desempate": desempate,
           "filtros": [RECONT["filtros"][0], {"coluna": "meth.diff", filtro: "dmc.dmc_resumo.criterios.diff_pct"}]}
    ficha = _ficha_porta(tmp_path, [inv], {**RESUMO_DMC, "n_dmc": total},
                         todas_cpgs=SELADO / "dmc" / "todas_cpgs.tsv", dmc=SELADO / "dmc" / "dmc.tsv")
    falhas = conferir_invariantes(ficha)
    if ok:
        assert falhas == []
    else:
        assert len(falhas) == 1 and "recontagem em dmc.todas_cpgs" in falhas[0], falhas
        if filtro == "abs_maior_ou_igual":
            assert "= 276 ≠" in falhas[0], falhas


@pytest.mark.parametrize("no_dmc,total,ok", [
    (4, 4, True),    # o dmc decidiu que a de borda passa: conta
    (3, 4, False),   # o dmc não a tem: não conta, e 3 ≠ 4
    (3, 3, True),
])
def test_x3_desempate_da_linha_de_borda_sintetico(tmp_path, no_dmc, total, ok):
    """3 linhas certas e 1 de borda (meth.diff "15" exatos, q 0,01): a de borda conta só se a
    chave dela (chr:start) está no dmc."""
    certas = ["1\t10\t10\t*\t1e-5\t0.001\t40.5", "1\t20\t20\t*\t1e-5\t0.002\t-22", "2\t30\t30\t*\t1e-4\t0.01\t16"]
    borda = "2\t40\t40\t*\t1e-3\t0.01\t15"
    fora = ["3\t50\t50\t*\t0.5\t0.9\t40", "3\t60\t60\t*\t1e-5\t0.001\t14.9", "3\t70\t70\t*\tNA\tNA\tNA"]
    todas = _tsv_mk(tmp_path / "todas.tsv", certas + [borda] + fora)
    dmc = _tsv_mk(tmp_path / "dmc.tsv", certas + ([borda] if no_dmc == 4 else []))
    ficha = _ficha_porta(tmp_path, [dict(RECONT)], {**RESUMO_DMC, "n_dmc": total}, todas_cpgs=todas, dmc=dmc)
    falhas = conferir_invariantes(ficha)
    if ok:
        assert falhas == []
    else:
        assert len(falhas) == 1 and "certas 3, de borda 1, das quais 0 em dmc.dmc" in falhas[0], falhas


def test_x3_chave_do_desempate_ausente_na_principal_reprova(tmp_path):
    """X3′ 5: o dmc tem uma CpG que a tabela inteira não tem — o desempate não pode ser de
    outra execução."""
    certas = ["1\t10\t10\t*\t1e-5\t0.001\t40.5", "1\t20\t20\t*\t1e-5\t0.002\t-22"]
    todas = _tsv_mk(tmp_path / "todas.tsv", certas)
    dmc = _tsv_mk(tmp_path / "dmc.tsv", certas + ["9\t99\t99\t*\t1e-5\t0.001\t50"])
    ficha = _ficha_porta(tmp_path, [dict(RECONT)], {**RESUMO_DMC, "n_dmc": 2}, todas_cpgs=todas, dmc=dmc)
    falhas = conferir_invariantes(ficha)
    assert len(falhas) == 1 and "1 linha(s) de dmc.dmc ausente(s) em dmc.todas_cpgs" in falhas[0], falhas


def test_x3_tolerancia_so_com_desempate(tmp_path):
    """Sem desempate a recontagem é EXATA: 15.0000000001 passa em '> 15' (1 linha), e a borda
    15 não. Com desempate, as duas são borda (a 1e-9 relativo) e contam pelo dmc."""
    linhas = ["1\t10\t10\t*\t1e-5\t0.001\t15.0000000001", "1\t20\t20\t*\t1e-5\t0.001\t15"]
    todas = _tsv_mk(tmp_path / "todas.tsv", linhas)
    dmc = _tsv_mk(tmp_path / "dmc.tsv", linhas[:1])
    exata = {**RECONT, "desempate": None}
    assert conferir_invariantes(_ficha_porta(tmp_path, [exata], {**RESUMO_DMC, "n_dmc": 1},
                                             todas_cpgs=todas, dmc=dmc)) == []
    assert conferir_invariantes(_ficha_porta(tmp_path, [dict(RECONT)], {**RESUMO_DMC, "n_dmc": 1},
                                             todas_cpgs=todas, dmc=dmc)) == []
    falhas = conferir_invariantes(_ficha_porta(tmp_path, [dict(RECONT)], {**RESUMO_DMC, "n_dmc": 2},
                                               todas_cpgs=todas, dmc=dmc))
    assert "certas 0, de borda 2, das quais 1 em dmc.dmc" in falhas[0], falhas


def test_x3_contagem_linhas_soma_coluna_e_tabela_trocada(tmp_path):
    dmc = _tsv_mk(tmp_path / "dmc.tsv", ["1\t10\t10\t*\t1e-5\t0.001\t40", "1\t20\t20\t*\t1e-5\t0.002\t-22", ""])
    inter = tmp_path / "intersecao.tsv"
    inter.write_text("GENE\tN_DMC\tHIPER\tHIPO\nHOXA10\t2\t1\t1\nGATA3\t0\t0\t0\n", encoding="utf-8")
    invs = [{"tipo": "contagem_linhas", "porta": "dmc.dmc", "total": "dmc.dmc_resumo.n_dmc"},
            {"tipo": "soma_coluna", "porta": "dmc.intersecao", "coluna": "N_DMC",
             "total": "dmc.dmc_resumo.n_genes_painel_com_dmc"}]
    resumo = {**RESUMO_DMC, "n_dmc": 2, "n_genes_painel_com_dmc": 2}
    assert conferir_invariantes(_ficha_porta(tmp_path, invs, resumo, dmc=dmc, intersecao=inter)) == []  # controle
    # contagem_linhas: uma linha a menos (a linha vazia não conta)
    falhas = conferir_invariantes(_ficha_porta(tmp_path, invs, {**resumo, "n_dmc": 3}, dmc=dmc, intersecao=inter))
    assert len(falhas) == 1 and "invariante 1 (contagem_linhas): linhas de dado em dmc.dmc = 2 ≠" in falhas[0], falhas
    # soma_coluna: um N_DMC alterado
    inter_mexida = tmp_path / "inter2.tsv"
    inter_mexida.write_text(inter.read_text(encoding="utf-8").replace("HOXA10\t2", "HOXA10\t3"), encoding="utf-8")
    falhas = conferir_invariantes(_ficha_porta(tmp_path, invs, resumo, dmc=dmc, intersecao=inter_mexida))
    assert len(falhas) == 1 and "Σ N_DMC em dmc.intersecao = 3 ≠" in falhas[0], falhas
    # soma_coluna: valor que não é número reprova (não vira zero)
    inter_na = tmp_path / "inter3.tsv"
    inter_na.write_text(inter.read_text(encoding="utf-8").replace("GATA3\t0", "GATA3\tNA"), encoding="utf-8")
    falhas = conferir_invariantes(_ficha_porta(tmp_path, invs, resumo, dmc=dmc, intersecao=inter_na))
    assert len(falhas) == 1 and "valor não numérico na linha 3" in falhas[0], falhas
    # tabela trocada DEPOIS do envelope (a ficha guarda o sha da entrega)
    ficha = _ficha_porta(tmp_path, invs, resumo, dmc=dmc, intersecao=inter)
    dmc.write_text(dmc.read_text(encoding="utf-8") + "1\t30\t30\t*\t1e-5\t0.001\t30\n", encoding="utf-8")
    falhas = conferir_invariantes(ficha)
    assert len(falhas) == 1 and "tabela de dmc.dmc mudou depois do envelope" in falhas[0], falhas


def test_x3_porta_sem_tabela_na_ficha_reprova(tmp_path):
    ficha = _ficha_porta(tmp_path, [], RESUMO_DMC)
    ficha["invariantes"] = [{"tipo": "contagem_linhas", "porta": "dmc.dmc", "total": "dmc.dmc_resumo.n_dmc"}]
    falhas = conferir_invariantes(ficha)
    assert len(falhas) == 1 and "sem tabela na ficha" in falhas[0], falhas


def test_x3_invariante_por_porta_como_o_plano_grava(tmp_path):
    """No run real a invariante chega por model_dump(): as chaves ausentes valem None/""."""
    from omica.pipelines import Invariante

    certas = ["1\t10\t10\t*\t1e-5\t0.001\t40.5"]
    todas = _tsv_mk(tmp_path / "todas.tsv", certas)
    dmc = _tsv_mk(tmp_path / "dmc.tsv", certas)
    invs = [Invariante.model_validate(RECONT).model_dump(),
            Invariante.model_validate({"tipo": "contagem_linhas", "porta": "dmc.dmc",
                                       "total": "dmc.dmc_resumo.n_dmc"}).model_dump()]
    assert conferir_invariantes(_ficha_porta(tmp_path, invs, {**RESUMO_DMC, "n_dmc": 1},
                                             todas_cpgs=todas, dmc=dmc)) == []


# ============================================================ F6: fatos de lista de listas (X4)
def _ficha_ml(tmp_path: Path, resumo: dict, invariantes: list) -> dict:
    arq = tmp_path / "ml_resumo.json"
    arq.write_text(json.dumps(resumo), encoding="utf-8")
    plano = {"run_id": "r", "plano_sha256": "a" * 64, "escolhas": {}, "passos": [{"id": "cl", "selos": []}],
             "relatorio": {"figuras": [], "resumos": ["cl.ml_resumo"], "tabelas": [], "invariantes": invariantes}}
    return fatos_mod.gerar(plano, {"cl": {"portas": {"ml_resumo": {"caminho": arq.as_posix(), "sha256": _sha(arq)}}}})


SOMAS_CONFUSAO = [
    {"tipo": "soma", "partes": ["cl.ml_resumo.confusion.0.0", "cl.ml_resumo.confusion.0.1"], "total": "cl.ml_resumo.n_a"},
    {"tipo": "soma", "partes": ["cl.ml_resumo.confusion.1.0", "cl.ml_resumo.confusion.1.1"], "total": "cl.ml_resumo.n_b"},
]


def test_x4_matriz_de_confusao_vira_fato_por_celula_e_o_r3_reabre(tmp_path):
    ficha = _ficha_ml(tmp_path, {"confusion": [[100, 2], [3, 52]], "n_a": 102, "n_b": 55}, SOMAS_CONFUSAO)
    por_id = {f["id"]: f for f in ficha["fatos"]}
    fato = por_id["cl.ml_resumo.confusion.0.1"]
    assert fato["valor"] == 2 and fato["rotulo"] == "confusion: linha 1, coluna 2"
    assert {f"cl.ml_resumo.confusion.{i}.{j}" for i in (0, 1) for j in (0, 1)} <= set(por_id)
    from omica.relatorio.portao_numeros import conferir_fato

    assert conferir_fato(fato) is None  # R3 reabre pelo mesmo caminho
    assert conferir_invariantes(ficha) == []  # controle: as somas por linha fecham


def test_x4_confusao_com_linhas_trocadas_reprova_a_soma_por_linha(tmp_path):
    ficha = _ficha_ml(tmp_path, {"confusion": [[3, 52], [100, 2]], "n_a": 102, "n_b": 55}, SOMAS_CONFUSAO)
    falhas = conferir_invariantes(ficha)
    assert len(falhas) == 2 and falhas[0].startswith("invariante 1 (soma)"), falhas


def test_x4_teto_por_nivel(tmp_path, monkeypatch):
    monkeypatch.setattr(fatos_mod, "MAX_ITENS_LISTA", 2)
    ficha = _ficha_ml(tmp_path, {"m": [[1, 2, 3], [4, 5, 6], [7, 8, 9]]}, [])
    ids = {f["id"] for f in ficha["fatos"]}
    assert ids == {"cl.ml_resumo.m.0.0", "cl.ml_resumo.m.0.1", "cl.ml_resumo.m.1.0", "cl.ml_resumo.m.1.1"}


# ================================================= F6: alertas na ficha (X5) e plano antigo
def _ficha_alertas(tmp_path: Path, alertas: list, empates: int = 1) -> dict:
    arq = tmp_path / "diag.json"
    arq.write_text(json.dumps({"empates": empates}), encoding="utf-8")
    plano = {"run_id": "r", "plano_sha256": "a" * 64, "escolhas": {}, "passos": [{"id": "d", "selos": []}],
             "relatorio": {"resumos": ["d.diag"], "alertas": alertas}}
    return fatos_mod.gerar(plano, {"d": {"portas": {"diag": {"caminho": arq.as_posix(), "sha256": _sha(arq)}}}})


def test_x5_alerta_vale_pelo_fato(tmp_path):
    alertas = [{"texto": "Seleção exploratória.", "fato": "", "igual": None},
               {"texto": "Empates de importância zero.", "fato": "d.diag.empates", "igual": 1}]
    assert _ficha_alertas(tmp_path, alertas, 1)["alertas"] == ["Seleção exploratória.", "Empates de importância zero."]
    assert _ficha_alertas(tmp_path, alertas, 0)["alertas"] == ["Seleção exploratória."]


def test_x5_fato_do_alerta_ausente_impede_a_ficha(tmp_path):
    with pytest.raises(ValueError, match="alerta: fato 'd.diag.nao_existe' ausente da ficha"):
        _ficha_alertas(tmp_path, [{"texto": "x", "fato": "d.diag.nao_existe", "igual": 1}])


def test_plano_de_antes_da_f6_continua_com_ficha_e_invariantes(tmp_path):
    """Adendo §A3.4: um plano aprovado antes da F6 não tem `alertas`, nem `porta`/`coluna`/
    `desempate` nas invariantes, nem `abs_maior_que` nos filtros. Continua com ficha e portão."""
    plano, ficha = _run(tmp_path)
    assert "alertas" not in plano["relatorio"]
    assert all("porta" not in i and "desempate" not in i for i in plano["relatorio"]["invariantes"])
    assert all("abs_maior_que" not in f for i in plano["relatorio"]["invariantes"] for f in i.get("filtros", []))
    assert ficha["alertas"] == [] and ficha["tabelas_invariantes"] == {}
    assert conferir_invariantes(ficha) == []
    assert conferir(RASCUNHO_BOM, ficha, plano)["ok"]
