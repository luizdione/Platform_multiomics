"""Contrato de passagem entre passos (F2 da integração com o n8n).

Dois portões determinísticos, cada um com canário (o que PRECISA reprovar) e controle
negativo (o que precisa passar):
- omica/contrato/formatos.py: "este arquivo está no formato que o próximo script consome?";
- omica/pipelines.py: "o formato que um passo produz é o que o seguinte consome?" — no load,
  antes de qualquer run.

Genômica (F5, especificação G1 §1.2/§3 e crítica): Entrada.modos e as regras C1–C4, o
acoplamento painel → porta (C6), os selos intactos depois do scripts_api (C7) e um canário
por invariante do relatório (crítica #5d: as três são verdadeiras por construção dos
scripts — sem canário, um portão que nunca reprova). Os formatos estão em
tests/test_formatos_genomica.py.
"""
from __future__ import annotations

import json
import re
import struct
import typing
import zlib
from pathlib import Path

import pytest

from omica.config import REPO_ROOT
from omica.contrato import formatos
from omica.pipelines import (Checagem, Entrada, Pipeline, carregar_pipelines, envio_formulario, spec_api,
                             validar_contrato)
from omica.registry import load_registry

DEMO = REPO_ROOT / "modules" / "transcriptomica" / "demo_data"
COUNTS = DEMO / "counts_granulosa.tsv"
COLDATA = DEMO / "coldata_granulosa.tsv"


def _tsv(tmp_path: Path, nome: str, linhas: list[list[str]]) -> Path:
    arq = tmp_path / nome
    arq.write_text("\n".join("\t".join(l) for l in linhas) + "\n", encoding="utf-8")
    return arq


def _png(tmp_path: Path, nome: str = "fig.png", lado: int = 64) -> Path:
    """PNG mínimo válido (stdlib), grande o bastante para não parecer vazio."""
    def bloco(tipo: bytes, dados: bytes) -> bytes:
        return struct.pack(">I", len(dados)) + tipo + dados + struct.pack(">I", zlib.crc32(tipo + dados))

    cru = b"".join(b"\x00" + bytes((x * 7 + y * 13) % 256 for x in range(lado * 3)) for y in range(lado))
    dados = (b"\x89PNG\r\n\x1a\n" + bloco(b"IHDR", struct.pack(">IIBBBBB", lado, lado, 8, 2, 0, 0, 0))
             + bloco(b"IDAT", zlib.compress(cru, 0)) + bloco(b"IEND", b""))
    arq = tmp_path / nome
    arq.write_bytes(dados)
    return arq


# ------------------------------------------------------------- controles (passam)
def test_demo_real_passa_nos_validadores():
    vc = formatos.validar("contagens_genes", COUNTS)
    vd = formatos.validar("coldata", COLDATA)
    assert vc.ok, vc.falhas
    assert vd.ok, vd.falhas
    assert vc.perfil["n_genes"] == 20543 and vc.perfil["n_amostras"] == 8
    assert sum(vd.perfil["niveis"].values()) == 8 and len(vd.perfil["niveis"]) == 2
    assert formatos.contagens_e_coldata(COUNTS, COLDATA).ok


def test_valores_coluna_devolve_so_valores_reais():
    niveis = formatos.valores_coluna(COLDATA, "condicao")
    assert sorted(niveis) == sorted(formatos.coldata(COLDATA).perfil["niveis"])
    assert formatos.valores_coluna(COLDATA, "nao_existe") is None
    # coluna de DADO (muitos valores distintos) não vira dropdown
    assert formatos.valores_coluna(COUNTS, "gene_id") is None


def test_tabela_de_resumo_e_png_validos(tmp_path):
    de = _tsv(tmp_path, "de.tsv", [["gene_id", "gene_name", "baseMean", "log2FoldChange", "pvalue", "padj"],
                                   ["G1", "g1", "10", "2.5", "0.001", "0.01"],
                                   ["G2", "NA", "3", "NA", "NA", "NA"]])
    v = formatos.validar("tabela_de", de)
    assert v.ok and v.perfil == {"n_genes": 2, "n_padj_na": 1}
    resumo = tmp_path / "r.json"
    resumo.write_text('{"n_deg": 3}', encoding="utf-8")
    assert formatos.validar("resumo_json", resumo).ok
    assert formatos.validar("figura_png", _png(tmp_path)).ok


# --------------------------------------------------------------- canários (reprovam)
_GN = ["gene_id", "gene_name"]


@pytest.mark.parametrize("linhas,trecho", [
    ([["gene", "gene_name", "a", "b"], ["G1", "g1", "1", "2"]], "gene_id"),          # cabeçalho errado
    ([[*_GN, "a"], ["G1", "g1", "1"]], "≥2 colunas"),                                # uma amostra só
    ([[*_GN, "a", "b"], ["G1", "g1", "1", "-2"]], "contagem inválida"),              # negativa
    ([[*_GN, "a", "b"], ["G1", "g1", "1", "x"]], "não numérico"),                    # texto
    ([[*_GN, "a", "b"], ["G1", "g1", "1", "inf"]], "contagem inválida"),             # infinito
    ([[*_GN, "a", "b"], ["G1", "g1", "1", "2"], ["G1", "g1", "3", "4"]], "repetido"),  # gene duplicado
    ([[*_GN, "a", "a"], ["G1", "g1", "1", "2"]], "repetidos"),                       # amostra duplicada
    ([[*_GN, "a", "b"], ["G1", "g1", "1"]], "campos"),                               # linha curta
    ([["gene_id", "a", "b"], ["G1", "1", "2"]], "gene_name"),                        # sem gene_name (#18)
])
def test_contagens_canarios(tmp_path, linhas, trecho):
    v = formatos.validar("contagens_genes", _tsv(tmp_path, "c.tsv", linhas))
    assert not v.ok and any(trecho in f for f in v.falhas), v.falhas


@pytest.mark.parametrize("linhas,trecho", [
    ([["amostra", "grupo"], ["a", "x"]], "'condicao'"),
    ([["amostra", "condicao"], ["a", "x"], ["b", "x"]], "exatamente 2 condições"),
    ([["amostra", "condicao"], ["a", "x"], ["b", "x"], ["c", "y"]], "réplicas"),
    ([["amostra", "condicao"], ["a", "x"], ["a", "x"], ["c", "y"], ["d", "y"]], "repetidas"),
])
def test_coldata_canarios(tmp_path, linhas, trecho):
    v = formatos.validar("coldata", _tsv(tmp_path, "cd.tsv", linhas))
    assert not v.ok and any(trecho in f for f in v.falhas), v.falhas


def test_tabela_de_com_padj_fora_de_0_1_reprova(tmp_path):
    de = _tsv(tmp_path, "de.tsv", [["gene_id", "gene_name", "baseMean", "log2FoldChange", "pvalue", "padj"],
                                   ["G1", "g1", "10", "2.5", "0.001", "1.5"]])
    v = formatos.validar("tabela_de", de)
    assert not v.ok and "padj fora de [0,1]" in v.falhas[0]


@pytest.mark.parametrize("conteudo", ["[1, 2]", "{}", "não é json"])
def test_resumo_json_canarios(tmp_path, conteudo):
    arq = tmp_path / "r.json"
    arq.write_text(conteudo, encoding="utf-8")
    assert not formatos.validar("resumo_json", arq).ok


def test_png_falso_ou_vazio_reprova(tmp_path):
    falso = tmp_path / "falso.png"
    falso.write_bytes(b"GIF89a" + b"\x00" * 4000)
    assert not formatos.validar("figura_png", falso).ok
    minusculo = tmp_path / "mini.png"
    minusculo.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 10)
    assert not formatos.validar("figura_png", minusculo).ok


def test_formato_desconhecido_ausente_ou_vazio_nunca_e_ok(tmp_path):
    vazio = tmp_path / "vazio.tsv"
    vazio.write_text("", encoding="utf-8")
    assert not formatos.validar("formato_inventado", COUNTS).ok
    assert not formatos.validar("coldata", tmp_path / "nao_existe.tsv").ok
    assert not formatos.validar("coldata", vazio).ok
    assert not formatos.validar("coldata", tmp_path).ok  # diretório


def test_amostra_do_coldata_ausente_da_matriz_reprova_a_checagem_cruzada(tmp_path):
    cd = _tsv(tmp_path, "cd.tsv", [["amostra", "condicao"], ["a", "x"], ["b", "x"],
                                   ["c", "y"], ["fantasma", "y"]])
    ct = _tsv(tmp_path, "ct.tsv", [["gene_id", "gene_name", "a", "b", "c", "extra"],
                                   ["G1", "g1", "1", "2", "3", "4"]])
    v = formatos.contagens_e_coldata(ct, cd)
    assert not v.ok and "fantasma" in v.falhas[0]


def test_bom_utf8_reprova_em_vez_de_passar_torto(tmp_path):
    """O R leria o cabeçalho como 'X.U.FEFF.gene_id': o portão não pode aprovar o que o
    consumidor vai ler errado. Quem limpa BOM é o adaptador (F3), não o validador."""
    arq = tmp_path / "bom.tsv"
    arq.write_bytes("﻿gene_id\ta\tb\nG1\t1\t2\n".encode("utf-8"))
    assert not formatos.validar("contagens_genes", arq).ok


# ------------------------------------------------------------ contrato do pipeline
def _pipeline_real() -> dict:
    validos, erros = carregar_pipelines()
    assert erros == {}, erros
    return validos["transcriptomica_de"].model_dump()


def _validar(dados: dict) -> list[str]:
    m = load_registry().por_id(dados["modulo"])
    return validar_contrato(Pipeline.model_validate(dados), m)


def test_pipeline_real_tem_contrato_fechado():
    assert _validar(_pipeline_real()) == []


def test_scripts_api_nao_aparecem_na_ui_nem_mudam_o_selo():
    """A seção nova é invisível para quem lê `scripts` e não entra no hash do selo."""
    from omica.registry import hash_dos_passos

    m = load_registry().por_id("transcriptomica")
    assert {s.key for s in m.scripts_api} == {"deseq2", "volcano"}
    assert not {s.key for s in m.scripts} & {"deseq2", "volcano"}
    wf = next(w for w in m.workflows if w.id == "wf_de_granulosa")
    assert hash_dos_passos(m, wf) == wf.hash_scripts


def _mexer(dados: dict, passo: str, campo: str, chave: str, valor) -> dict:
    alvo = next(x for x in dados["passos"] if x["id"] == passo)
    if valor is None:
        alvo[campo].pop(chave, None)
    else:
        alvo[campo][chave] = valor
    return dados


@pytest.mark.parametrize("mexida,trecho", [
    # formato errado: o volcano consome tabela_de, a entrada é contagens_genes
    (lambda d: _mexer(d, "volcano", "liga", "de", "entrada.counts"), "consome 'tabela_de'"),
    # porta inexistente
    (lambda d: _mexer(d, "volcano", "liga", "de", "de.nao_existe"), "não produz a porta"),
    # referência a passo POSTERIOR (o DE lendo o volcano)
    (lambda d: _mexer(d, "de", "liga", "counts", "volcano.volcano_png"), "POSTERIOR"),
    # parâmetro obrigatório sem valor
    (lambda d: _mexer(d, "de", "liga", "referencia", None), "ficou sem valor"),
    # escolha do autor alimentando parâmetro que consome ARQUIVO
    (lambda d: _mexer(d, "de", "liga", "counts", "escolha.referencia"), "não pode alimentá-lo"),
    # escolha inexistente
    (lambda d: _mexer(d, "de", "liga", "referencia", "escolha.nao_existe"), "escolha inexistente"),
    # caminho literal num parâmetro que consome formato (furaria o validador)
    (lambda d: _mexer(_mexer(d, "volcano", "liga", "de", None), "volcano", "params", "de",
                      "modules/transcriptomica/demo_data/counts_granulosa.tsv"), "só pode vir por liga"),
    # "=passo.param" apontando para o nada
    (lambda d: _mexer(d, "volcano", "params", "padj", "=de.nao_existe"), "passo anterior"),
    # parâmetro que o script não tem
    (lambda d: _mexer(d, "de", "params", "flag_inventada", "1"), "não é parâmetro"),
])
def test_contrato_quebrado_reprova_no_load(mexida, trecho):
    erros = _validar(mexida(_pipeline_real()))
    assert any(trecho in e for e in erros), erros


def test_relatorio_com_porta_do_formato_errado_reprova():
    d = _pipeline_real()
    d["relatorio"]["figuras"] = ["de.de_resumo"]  # resumo não é figura
    assert any("não é figura_png" in e for e in _validar(d))


def test_checagem_com_campos_errados_reprova():
    d = _pipeline_real()
    d["checagens_entrada"] = [{"tipo": "contagens_e_coldata", "contagens": "counts"}]
    assert any("exige" in e for e in _validar(d))


def test_entrada_com_formato_desconhecido_reprova():
    d = _pipeline_real()
    d["entradas"]["counts"]["formato"] = "planilha_qualquer"
    assert any("formato desconhecido" in e for e in _validar(d))


def _escolha(d: dict, valores_de: str) -> dict:
    d["escolhas_autor"]["referencia"]["valores_de"] = valores_de
    return d


def _tabela(d: dict, **mudancas) -> dict:
    d["relatorio"]["tabelas"][0].update(mudancas)
    # recontagem só vale em tabela declarada: sem ela, o erro seria outro
    d["relatorio"]["invariantes"] = [i for i in d["relatorio"]["invariantes"] if i["tipo"] != "recontagem"]
    return d


@pytest.mark.parametrize("mexida,trecho", [
    # #19: entrada tabela_de marcada adaptavel — o adaptador não conhece o formato
    (lambda d: d["entradas"]["coldata"].update(formato="tabela_de") or d, "adaptador não conhece"),
    # (1) entrada que ninguém consome, mas que o upload passaria a exigir
    (lambda d: d["entradas"].update(extra={"formato": "coldata", "rotulo": "x"}) or d, "nenhum passo nem checagem"),
    # escolha que nenhum passo usa, mas que o plano passaria a exigir
    (lambda d: d["escolhas_autor"].update(outra={"rotulo": "x", "valores_de": "coldata.condicao"}) or d,
     "nenhum passo a usa"),
    # (2) valores_de: coluna inexistente, sem coluna, coluna de dado e coluna sem nível
    (lambda d: _escolha(d, "coldata.condicaoo"), "não é coluna de nível"),
    (lambda d: _escolha(d, "coldata"), "não é coluna de nível"),
    (lambda d: _escolha(d, "counts.gene_id"), "não é coluna de nível"),
    (lambda d: _escolha(d, "coldata.amostra"), "não é coluna de nível"),
    # (7) checagem com as entradas trocadas: reprovaria TODO run com 409
    (lambda d: d.update(checagens_entrada=[{"tipo": "contagens_e_coldata", "contagens": "coldata",
                                            "coldata": "counts"}]) or d, "exige formato"),
    # (4) tabela em porta que não é tabela, e colunas/chave/ordenação que o validador não garante
    (lambda d: _tabela(d, passo="volcano", porta="volcano_png"), "não é tabela"),
    (lambda d: _tabela(d, chave="gene_inexistente"), "não são garantidas"),
    # resíduo do #20: gene_name é garantida mas NÃO única (o demo tem 39 repetidos) — o portão
    # de números só reprovava depois do run inteiro
    (lambda d: _tabela(d, chave="gene_name"), "não é coluna ÚNICA"),
    (lambda d: _tabela(d, chave="padj"), "não é coluna ÚNICA"),
    (lambda d: _tabela(d, colunas=["gene_name", "fold"]), "não são garantidas"),
    (lambda d: _tabela(d, top={"n": 10, "ordenar": "pval"}), "não são garantidas"),
    (lambda d: _tabela(d, top={"n": "abc", "ordenar": "padj"}), "top.n"),
    (lambda d: _tabela(d, top={"n": 0}), "top.n"),
    (lambda d: _tabela(d, top={"n": 5, "desc": True}), "campos desconhecidos"),
])
def test_contrato_malformado_reprova_no_load_f2_20(mexida, trecho):
    """Revisão F2 #20: cada um destes carregava 'válido' e nunca rodava (ou rodava torto)."""
    erros = _validar(mexida(_pipeline_real()))
    assert any(trecho in e for e in erros), erros


def test_chave_gene_name_tem_repetidos_no_demo():
    """Por que a chave precisa ser ÚNICA e não só garantida: no demo, gene_name se repete (o
    portão de números exige achar a chave exatamente 1 vez). Controle: gene_id não se repete."""
    linhas = [l.split("\t") for l in COUNTS.read_text(encoding="utf-8").splitlines()[1:]]
    nomes = [l[1] for l in linhas]
    ids = [l[0] for l in linhas]
    assert len(set(nomes)) < len(nomes)
    assert len(set(ids)) == len(ids)


def test_param_repetido_aceita_o_default_do_passo_anterior():
    """'=de.padj' com o DE usando o default: o propor_plano resolve o valor (setdefault), então
    o contrato não pode recusar. Controle: parâmetro que não existe continua recusado."""
    d = _pipeline_real()
    de = next(x for x in d["passos"] if x["id"] == "de")
    de["params"].pop("padj")
    assert _validar(d) == []
    _mexer(d, "volcano", "params", "lfc", "=de.lfc_inexistente")
    assert any("passo anterior" in e for e in _validar(d))


def test_volcano_do_pipeline_le_o_contraste_do_de():
    """Revisão F2 #17: o volcano do pipeline recebe o resumo do DE (que traz o contraste) por
    liga tipada, e o script dele não é o volcano_plot.py selado de rótulos fixos."""
    from omica.pipelines import spec_api
    from omica.proveniencia import alvo_do_cmd

    m = load_registry().por_id("transcriptomica")
    spec = spec_api(m, "volcano")
    assert spec.consome == {"de": "tabela_de", "resumo": "resumo_json"}
    assert alvo_do_cmd(spec.cmd).endswith("volcano_api.py")
    volcano = next(x for x in _pipeline_real()["passos"] if x["id"] == "volcano")
    assert volcano["liga"]["resumo"] == "de.de_resumo"


def test_pipeline_ruim_nao_derruba_os_outros(tmp_path):
    import shutil

    shutil.copy(REPO_ROOT / "pipelines" / "transcriptomica_de.yaml", tmp_path / "transcriptomica_de.yaml")
    (tmp_path / "quebrado.yaml").write_text("id: quebrado\nmodulo: transcriptomica\n", encoding="utf-8")
    (tmp_path / "outro_nome.yaml").write_text(
        (REPO_ROOT / "pipelines" / "transcriptomica_de.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    validos, erros = carregar_pipelines(tmp_path)
    assert set(validos) == {"transcriptomica_de"}
    assert set(erros) == {"quebrado.yaml", "outro_nome.yaml"}
    assert "difere do nome do arquivo" in erros["outro_nome.yaml"]


# ================================================================ genômica (F5)
GENOMICA = ("genomica_painel", "genomica_regioes")


def _pipeline_gen(pid: str = "genomica_painel") -> dict:
    validos, erros = carregar_pipelines()
    assert erros == {}, erros
    return validos[pid].model_dump()


def _m_gen():
    return load_registry().por_id("genomica")


def test_pipelines_da_genomica_carregam_com_contrato_fechado():
    validos, erros = carregar_pipelines()
    assert erros == {} and set(GENOMICA) <= set(validos), erros
    for pid in GENOMICA:
        assert _validar(_pipeline_gen(pid)) == []
    p = validos["genomica_painel"]
    assert p.entradas["vcf"].modos == ["copia", "referencia"] and p.entradas["painel"].modos == ["copia"]
    assert not p.entradas["painel"].adaptavel  # G1 §0.8: a adaptação mudaria o stem
    assert [x.id for x in p.passos] == ["painel_bed", "extracao", "frequencias", "snpeff", "vep"]
    r = validos["genomica_regioes"]
    assert {c.tipo for c in r.checagens_entrada} == {"vcf_tem_info_af", "vcf_contigs_ars_ucd", "vcf_e_bed"}


def test_literal_das_checagens_em_sincronia_com_formatos():
    assert set(typing.get_args(Checagem.model_fields["tipo"].annotation)) == set(formatos.CHECAGENS)


def test_envio_formulario():
    """A fonte única do campo do formulário: índice obrigatório ou referência = caminho."""
    validos, _ = carregar_pipelines()
    envios = {pid: {n: envio_formulario(e) for n, e in validos[pid].entradas.items()} for pid in validos}
    assert envios["genomica_painel"] == {"painel": "arquivo", "vcf": "caminho"}
    assert envios["genomica_regioes"] == {"regioes": "arquivo", "vcf": "caminho"}
    assert envios["transcriptomica_de"] == {"counts": "arquivo", "coldata": "arquivo"}
    # formato com IRMAOS vai por caminho mesmo só com cópia (o upload não leva o índice)
    assert envio_formulario(Entrada(formato="vcf_indexado", rotulo="x")) == "caminho"
    assert envio_formulario(Entrada(formato="coldata", rotulo="x", modos=["copia", "referencia"])) == "caminho"


def test_exemplos_das_entradas_passam_no_formato_e_no_nome():
    """O `exemplo` de cada entrada é o placeholder do formulário: tem de passar no validador
    e na regra de nome (do arquivo e de origem), como o registro o julgaria."""
    validos, _ = carregar_pipelines()
    for pid, p in validos.items():
        for nome, e in p.entradas.items():
            arq = REPO_ROOT / e.exemplo
            v = formatos.validar_com_nome(e.formato, arq, nome_origem=arq.name)
            assert v.ok, (pid, nome, v.falhas)


def _entrada(d: dict, nome: str, **campos) -> dict:
    d["entradas"][nome].update(campos)
    return d


@pytest.mark.parametrize("mexida,trecho", [
    # C1: sem modo a entrada nunca seria aceita; repetido é erro de digitação
    (lambda d: _entrada(d, "vcf", modos=[]), "modos vazio ou repetido"),
    (lambda d: _entrada(d, "vcf", modos=["copia", "copia"]), "modos vazio ou repetido"),
    # C2: referência só em formato referenciável (o painel é texto pequeno: vai copiado)
    (lambda d: _entrada(d, "painel", modos=["copia", "referencia"]), "não é referenciável"),
    # C3: adaptável + referência gravaria '<nome>.adaptado.tsv' dentro da raiz de referência
    (lambda d: _entrada(d, "vcf", adaptavel=True), "adaptavel não combina com modo referencia"),
    # C5: checagem com o campo apontando para a entrada do formato errado
    (lambda d: d.update(checagens_entrada=[{"tipo": "vcf_tem_info_af", "vcf": "painel"}]) or d, "exige formato"),
    (lambda d: d.update(checagens_entrada=[{"tipo": "vcf_e_bed", "vcf": "vcf"}]) or d, "exige"),
], ids=["c1_vazio", "c1_repetido", "c2", "c3", "c5_formato", "c5_campos"])
def test_contrato_genomica_canarios(mexida, trecho):
    erros = _validar(mexida(_pipeline_gen()))
    assert any(trecho in e for e in erros), erros


def test_modo_invalido_nao_carrega():
    d = _entrada(_pipeline_gen(), "vcf", modos=["copia", "link"])
    with pytest.raises(ValueError):
        Pipeline.model_validate(d)


def _manifest_com_porta(chave: str, porta: str, arquivo: str):
    m = _m_gen().model_copy(deep=True)
    spec = next(s for s in m.scripts_api if s.key == chave)
    spec.produz[porta].arquivo = arquivo
    return m


@pytest.mark.parametrize("chave,porta,arquivo,trecho", [
    # C4: porta bed_regioes com outro nome: o bcftools leria 1-based
    ("painel_bed", "bed", "regioes.txt", "1-based"),
    ("extracao", "vcf_indice", "extraido.vcf.gz.idx", ".tbi"),
    ("extracao", "vcf", "extraido.vcf", ".vcf.gz"),
    ("painel_bed", "bed", "../painel.bed", "nome simples"),
    ("painel_bed", "bed", "/tmp/painel.bed", "nome simples"),
    ("painel_bed", "bed", "sub/painel.bed", "nome simples"),
])
def test_c4_nome_da_porta_reprova_no_load(chave, porta, arquivo, trecho):
    p = Pipeline.model_validate(_pipeline_gen())
    assert validar_contrato(p, _m_gen()) == []  # controle: o manifest real
    erros = validar_contrato(p, _manifest_com_porta(chave, porta, arquivo))
    assert any(trecho in e and f"porta {porta}" in e for e in erros), erros


def _acoplamento_painel(p: Pipeline, m) -> list[str]:
    """C6 (não expressável no contrato): o coordenadas_painel.py nomeia as saídas pelo STEM do
    arquivo de entrada, e a cópia da entrada chama-se '<nome da entrada>.txt' — a entrada
    ligada a `painel` tem de ser copiada (nunca referência nem adaptada) e as portas têm de
    ser '<nome>.bed' e '<nome>_resumo.json'."""
    erros = []
    for x in p.passos:
        spec = spec_api(m, x.script)
        if spec is None or "coordenadas_painel.py" not in spec.cmd:
            continue
        origem, _, nome = x.liga.get("painel", "").partition(".")
        if origem != "entrada":
            erros.append(f"{x.id}: painel não vem de uma entrada")
            continue
        e = p.entradas[nome]
        if e.modos != ["copia"] or e.adaptavel:
            erros.append(f"{x.id}: a entrada {nome} tem de ser só copiada (o stem da cópia é o nome da entrada)")
        portas = {pt.arquivo for pt in spec.produz.values()}
        if portas != {f"{nome}.bed", f"{nome}_resumo.json"}:
            erros.append(f"{x.id}: portas {sorted(portas)} ≠ as que o script grava com a entrada {nome!r}")
    return erros


def test_painel_nome_acoplado():
    """C6: o pipeline com a entrada renomeada (painel → genes) CARREGA — o contrato não vê —,
    mas o script gravaria genes.bed e a porta painel.bed sairia 'arquivo não gerado'."""
    m = _m_gen()
    d = _pipeline_gen()
    assert _acoplamento_painel(Pipeline.model_validate(d), m) == []
    d["entradas"]["genes"] = d["entradas"].pop("painel")
    next(x for x in d["passos"] if x["id"] == "painel_bed")["liga"]["painel"] = "entrada.genes"
    p = Pipeline.model_validate(d)
    assert validar_contrato(p, m) == []
    assert any("≠" in e for e in _acoplamento_painel(p, m))
    d2 = _entrada(_pipeline_gen(), "painel", adaptavel=False, modos=["copia", "referencia"])
    assert any("só copiada" in e for e in _acoplamento_painel(Pipeline.model_validate(d2), m))


def test_selos_vigentes_depois_do_scripts_api_da_genomica():
    """C7: o scripts_api não entra no hash e não tem linha de selo (o selos.regravar grava pela
    ordem das linhas `hash_scripts:`)."""
    from omica import selos
    from omica.registry import MODULES_DIR, hash_dos_passos

    estado = selos.conferir()
    assert len(estado) == 5 and not any(s["vencido"] for s in estado), estado
    painel = next(s for s in estado if s["workflow"] == "wf_painel_demo")
    assert painel["gravado"] == painel["atual"] == "46124beee606ccc6"
    m = _m_gen()
    assert {s.key for s in m.scripts_api} == {"painel_bed", "extracao", "frequencias", "snpeff", "vep"}
    assert not {s.key for s in m.scripts} & {s.key for s in m.scripts_api}
    wf = next(w for w in m.workflows if w.id == "wf_painel_demo")
    assert hash_dos_passos(m, wf) == wf.hash_scripts
    texto = (MODULES_DIR / "genomica" / "manifest.yaml").read_text(encoding="utf-8")
    secao = texto.split("\nscripts_api:\n", 1)[1].split("\nworkflows:\n", 1)[0]
    assert not re.search(r"(?m)^\s*(hash_scripts|testado_por|testado_em):", secao)


def test_cmd_da_genomica_confere_com_o_argparse_dos_scripts():
    """As flags de cada cmd existem no argparse do script (os scripts são selados: o manifest
    é que se ajusta). Canário: uma flag inventada é pega."""
    def flags_do_script(cmd: str) -> set[str]:
        alvo = next(t for t in cmd.split() if t.endswith(".py"))
        return set(re.findall(r'add_argument\("(--[a-z][a-z-]*)"', (REPO_ROOT / alvo).read_text(encoding="utf-8")))

    for spec in _m_gen().scripts_api:
        usadas = set(re.findall(r"(?<!\S)(--[a-z][a-z-]*)", spec.cmd))
        assert usadas <= flags_do_script(spec.cmd), (spec.key, usadas - flags_do_script(spec.cmd))
    vep = spec_api(_m_gen(), "vep")
    assert set(re.findall(r"(?<!\S)(--[a-z][a-z-]*)", vep.cmd + " --cachee x")) - flags_do_script(vep.cmd) == {"--cachee"}


def test_nenhum_scripts_api_cita_literal_sob_dados():
    """Crítica #10: '/data/omica/...' é {dados} no container — um literal assim contorna por
    texto a recusa de {dados} num scripts_api (o --cache do VEP; o default do script é igual)."""
    for m in load_registry().modules:
        for spec in m.scripts_api:
            assert "/data/omica" not in spec.cmd, (m.id, spec.key)


# ---------------------------------------------- crítica #5d: um canário por invariante
# Números do E2E-2 (genomica_painel sobre o demo, especificação §5.2)
_RESUMOS_E2E2 = {
    "painel_bed.painel_resumo": {"n_simbolos": 14, "n_localizados": 13, "pad_pb": 15000},
    "extracao.extracao_resumo": {"n_regioes": 13, "n_variantes_extraidas": 2365},
    "frequencias.frequencias_resumo": {"n_variantes_com_af": 2287, "n_sem_af": 78, "n_multialelicos": 130,
                                       "n_maf_acima_do_corte": 166},
    "snpeff.anotacao_resumo": {"n_variantes_anotadas": 2365, "n_efeitos_distintos": 30,
                               "efeito_mais_comum": "intron_variant"},
    "vep.vep_resumo": {"n_variantes_anotadas": 2287, "n_consequencias_distintas": 20},
}


def _ficha(tmp_path: Path, p: Pipeline, resumos: dict) -> dict:
    """Ficha de fatos (relatorio/fatos.py) de um run fictício com resumos sintéticos."""
    from omica.relatorio import fatos

    envelopes: dict = {}
    for ref in p.relatorio.resumos:
        passo, porta = ref.split(".")
        arq = tmp_path / f"{passo}.{porta}.json"
        arq.write_text(json.dumps(resumos[ref]), encoding="utf-8")
        envelopes.setdefault(passo, {"portas": {}})["portas"][porta] = {"caminho": str(arq), "sha256": "0" * 64}
    for t in p.relatorio.tabelas:
        arq = tmp_path / f"{t.passo}.{t.porta}.tsv"
        arq.write_text(f"{t.chave}\tN\nintron_variant\t9\nupstream_gene_variant\t4\n", encoding="utf-8")
        envelopes.setdefault(t.passo, {"portas": {}})["portas"][t.porta] = {"caminho": str(arq), "sha256": "0" * 64}
    plano = {"run_id": "r", "plano_sha256": "0" * 64, "escolhas": {}, "relatorio": p.relatorio.model_dump(),
             "passos": [{"id": x.id, "selos": []} for x in p.passos]}
    return fatos.gerar(plano, envelopes)


def _refs(inv) -> list[str]:
    return [*inv.partes, inv.total, inv.a, inv.b]


@pytest.mark.parametrize("pid", GENOMICA)
def test_invariantes_da_genomica_cada_um_reprova_sozinho(tmp_path, pid):
    from omica.relatorio.portao_numeros import conferir_invariantes

    p = carregar_pipelines()[0][pid]
    resumos = {ref: dict(v) for ref, v in _RESUMOS_E2E2.items() if ref in p.relatorio.resumos}
    assert conferir_invariantes(_ficha(tmp_path, p, resumos)) == []  # controle: os números do E2E-2
    invs = p.relatorio.invariantes
    assert invs
    for i, inv in enumerate(invs, start=1):
        outros = {r for j, x in enumerate(invs, start=1) if j != i for r in _refs(x)}
        ref = next(r for r in _refs(inv) if r and r not in outros)  # mexe só nesta invariante
        resumo, _, campo = ref.rpartition(".")
        mexido = {k: dict(v) for k, v in resumos.items()}
        mexido[resumo][campo] += 1
        (tmp_path / f"c{i}").mkdir()
        falhas = conferir_invariantes(_ficha(tmp_path / f"c{i}", p, mexido))
        assert len(falhas) == 1 and falhas[0].startswith(f"invariante {i} "), (ref, falhas)
