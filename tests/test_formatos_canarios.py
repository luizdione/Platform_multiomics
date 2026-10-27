"""Canários da revisão adversarial da F2 (2026-10-19) nos validadores de formato.

Cada teste diz o achado que fecha. A regra é a do projeto: todo portão precisa ser VISTO
reprovando (canário) e o caso legítimo vizinho precisa continuar passando (controle). O
consumidor real — o read.delim do R (expressao_deseq2.R) e o split('\\t') do volcano — não
roda aqui; o que ele faz com cada entrada foi conferido à parte, no R 4.5.3 (env rbio_omics),
e está citado em cada teste.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tracemalloc
from pathlib import Path

import pytest

from omica.config import REPO_ROOT
from omica.contrato import formatos

DEMO = REPO_ROOT / "modules" / "transcriptomica" / "demo_data"
COUNTS = DEMO / "counts_granulosa.tsv"
COLDATA = DEMO / "coldata_granulosa.tsv"
SCRIPTS = REPO_ROOT / "modules" / "transcriptomica" / "scripts"
CAB_DE = ["gene_id", "gene_name", "baseMean", "log2FoldChange", "pvalue", "padj"]


def _tsv(tmp_path: Path, nome: str, linhas: list[list[str]], fim: str = "\n") -> Path:
    arq = tmp_path / nome
    arq.write_text("\n".join("\t".join(l) for l in linhas) + fim, encoding="utf-8")
    return arq


def _coldata(tmp_path: Path, pares: list[tuple[str, str]], nome: str = "cd.tsv") -> Path:
    return _tsv(tmp_path, nome, [["amostra", "condicao"], *[list(p) for p in pares]])


def _counts(tmp_path: Path, amostras: list[str], nome: str = "ct.tsv", n_genes: int = 3) -> Path:
    return _tsv(tmp_path, nome, [["gene_id", "gene_name", *amostras],
                                 *[[f"G{g}", f"g{g}", *[str(g + i) for i in range(len(amostras))]]
                                   for g in range(n_genes)]])


def _reprova(v: formatos.Veredito, trecho: str) -> None:
    assert not v.ok and any(trecho in f for f in v.falhas), v.falhas


# ------------------------------------------------- #6 / #28: csv.Error não escapa
@pytest.mark.parametrize("formato", ["contagens_genes", "coldata", "tabela_de"])
def test_campo_gigante_reprova_em_vez_de_levantar(tmp_path, formato):
    """Um PUT com JSON de itens como text/plain (uma linha de 272 KB sem TAB) estourava o
    field_size_limit do csv: _csv.Error virava 500 com a entrada já trocada no disco."""
    itens = json.dumps([{"gene_id": f"G{i}", "s1": i} for i in range(9000)])
    assert len(itens) > 131072
    arq = tmp_path / "json_como_tsv.tsv"
    arq.write_text(itens, encoding="utf-8")
    v = formatos.validar(formato, arq)
    assert not v.ok and v.falhas
    v_direto = formatos.VALIDADORES[formato](arq)  # chamada direta também não levanta
    assert not v_direto.ok
    assert formatos.valores_coluna(arq, "s1") is None


def test_coldata_com_campo_de_200_mil_caracteres_reprova(tmp_path):
    arq = _coldata(tmp_path, [("a", "x"), ("b", "x"), ("c", "y"), ("d", "y" + "z" * 200_000)])
    _reprova(formatos.validar("coldata", arq), "TSV ilegível")
    assert formatos.valores_coluna(arq, "condicao") is None


def test_aspas_sem_fechamento_reprovam_e_campo_no_limite_ainda_le(tmp_path):
    """Aspa aberta num gene_name: o csv antigo juntava o resto do arquivo num campo só e
    estourava. Controle: campo de 131000 caracteres (abaixo do limite) é lido e julgado."""
    ruim = _tsv(tmp_path, "ct.tsv", [["gene_id", "gene_name", "a", "b"], ["G1", '"abc', "1", "2"],
                                     ["G2", "g2", "3", "4"]])
    _reprova(formatos.validar("contagens_genes", ruim), "aspas")
    longo = _coldata(tmp_path, [("a", "x"), ("b", "x"), ("c", "y"), ("d" * 131000, "y")], nome="longo.tsv")
    v = formatos.validar("coldata", longo)
    assert v.ok, v.falhas


def test_linha_gigante_reprova_sem_ler_a_linha_inteira(tmp_path, monkeypatch):
    monkeypatch.setattr(formatos, "_MAX_LINHA", 1000)
    arq = _tsv(tmp_path, "ct.tsv", [["gene_id", "gene_name", "a", "b"], ["G1", "g" * 5000, "1", "2"]])
    _reprova(formatos.validar("contagens_genes", arq), "mais de 1000 caracteres")
    assert formatos.valores_coluna(arq, "gene_name") is None
    # controle: o demo (cabeçalho de ~370 caracteres) continua passando com o teto apertado
    assert formatos.validar("contagens_genes", COUNTS).ok


# ------------------------------------------------------ #3: leitura em fluxo
def _pico(func) -> int:
    tracemalloc.start()
    try:
        func()
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def test_valores_coluna_para_no_51o_valor_sem_ler_o_arquivo(tmp_path):
    """GET /runs/{id} chama valores_coluna a cada polling: antes, um coldata grande era
    reparseado inteiro (~10x o tamanho em RAM) para devolver None."""
    arq = _tsv(tmp_path, "cd.tsv", [["amostra", "condicao"], *[[f"s{i}", f"nivel_{i}"] for i in range(200_000)]])
    tamanho = arq.stat().st_size
    pico = _pico(lambda: formatos.valores_coluna(arq, "condicao"))
    assert formatos.valores_coluna(arq, "condicao") is None
    assert pico < tamanho / 10, (pico, tamanho)
    # controle: coluna de nível de verdade continua devolvendo os valores
    assert formatos.valores_coluna(COLDATA, "condicao") == ["summer_heat", "winter_control"]


def test_coldata_gigante_para_no_teto_de_amostras(tmp_path, monkeypatch):
    monkeypatch.setattr(formatos, "_MAX_AMOSTRAS", 100)
    arq = _tsv(tmp_path, "cd.tsv", [["amostra", "condicao"], *[[f"s{i}", "x" if i % 2 else "y"] for i in range(200_000)]])
    tamanho = arq.stat().st_size
    pico = _pico(lambda: formatos.validar("coldata", arq))
    _reprova(formatos.validar("coldata", arq), "mais de 100 amostras")
    assert pico < tamanho / 10, (pico, tamanho)


def test_matriz_e_lida_em_fluxo(tmp_path):
    """Matriz válida: o pico fica no conjunto de gene_id (para achar repetidos), não nas linhas
    inteiras. Antes: ~10x o tamanho do arquivo."""
    n = 60_000
    amostras = [f"s{i}" for i in range(12)]
    arq = tmp_path / "ct.tsv"
    with arq.open("w", encoding="utf-8") as f:
        f.write("\t".join(["gene_id", "gene_name", *amostras]) + "\n")
        for g in range(n):
            f.write("\t".join([f"ENSBTAG{g:011d}", f"GENE{g}", *[str((g * 7 + i) % 5000) for i in range(12)]]) + "\n")
    tamanho = arq.stat().st_size
    v = None

    def rodar():
        nonlocal v
        v = formatos.validar("contagens_genes", arq)

    pico = _pico(rodar)
    assert v.ok and v.perfil["n_genes"] == n, v.falhas
    assert pico < 2 * tamanho, (pico, tamanho)


def test_teto_de_genes(tmp_path, monkeypatch):
    monkeypatch.setattr(formatos, "_MAX_GENES", 2)
    _reprova(formatos.validar("contagens_genes", _counts(tmp_path, ["a", "b"], n_genes=3)), "mais de 2 linhas")
    assert formatos.validar("contagens_genes", _counts(tmp_path, ["a", "b"], nome="ok.tsv", n_genes=2)).ok


def test_teto_conta_linhas_vazias(tmp_path, monkeypatch):
    """Resíduo do #3 (exp1 do verificador, caso 4): 4 linhas válidas + 30 milhões de '\\n' davam
    ok=True em 13 s, e valores_coluna mais 12 s — o teto só contava linha com dado. Agora conta
    a linha FÍSICA. Teto apertado aqui para o teste ser instantâneo; o efeito é o mesmo."""
    monkeypatch.setattr(formatos, "_MAX_AMOSTRAS", 100)
    monkeypatch.setattr(formatos, "_MAX_GENES", 100)
    validas = [["amostra", "condicao"], ["a", "x"], ["b", "x"], ["c", "y"], ["d", "y"]]
    inundado = _tsv(tmp_path, "cd.tsv", validas, fim="\n" * 500)
    _reprova(formatos.validar("coldata", inundado), "mais de 100 amostras")
    assert formatos.valores_coluna(inundado, "condicao") is None
    matriz = _counts(tmp_path, ["a", "b"], nome="ct.tsv", n_genes=2)
    matriz.write_text(matriz.read_text(encoding="utf-8") + "\n" * 500, encoding="utf-8")
    _reprova(formatos.validar("contagens_genes", matriz), "mais de 100 linhas")
    de = _tsv(tmp_path, "de.tsv", [CAB_DE, ["G1", "g1", "1", "1", "0.1", "0.2"]], fim="\n" * 500)
    _reprova(formatos.validar("tabela_de", de), "mais de 100 linhas")
    # controle: as mesmas tabelas com poucas linhas vazias (o R também as pula) passam
    poucas = _tsv(tmp_path, "cd_ok.tsv", validas, fim="\n" * 50)
    assert formatos.validar("coldata", poucas).ok
    assert formatos.valores_coluna(poucas, "condicao") == ["x", "y"]


# -------------------------------------- #14: nome que o R lê como número (POSIÇÃO)
# Conferido no R 4.5.3 (type.convert, o que o read.delim usa): estes viram número, lógico,
# complexo ou NA...
R_NAO_TEXTO = ["3", "03", "1.0", "1e1", "1E5", "0x1A", "0X1a", "0x1p3", "0x1.8p1", "0x.8", "Inf", "inf",
               "INF", "-inf", "+Inf", "infinity", "Infinity", "INFINITY", "NaN", "nan", "-NaN", "NA", "T",
               "F", "TRUE", "FALSE", "1i", "2i", "1+2i", "1-2i", "1e3i", "Inf+1i", "NaNi", ".5", "5.", "+3",
               "-0", "1e-400", "99999999999", "1.e1", ".1e1", "00", "0.", "infi", "inf+infi", "+.5",
               "-.5e-3", "0xABCp-2", "0xp1", "NaN+NaNi", "2.5i", "-2i", "0x1i"]
# ...e estes ficam texto
R_TEXTO = ["infinit", "na", "N/A", "t", "f", "True", "False", "tRUE", "yes", "i", "+i", "1+i", "1_0", "1e",
           "1e+", "1d3", "1L", "-", "+", ".", "e5", "a5", "3a", "S1", "1.2.3", "1..2", "--1", "+-1", "1e5.5",
           "0x1g", ".e1", "Inf1", "nanx", "1 2", "1,5", "١٢", "x1", "1+inf", "1e1e1", "1p3",
           "TRUE1", "1+1", "1-", "1i+"]
# divergências conhecidas, todas para o lado que REPROVA: texto no R 4.5.3, recusado aqui
CONSERVADOR = ["0x", "true", "false"]


@pytest.mark.parametrize("valor", R_NAO_TEXTO + CONSERVADOR)
def test_o_que_o_r_converte_e_reconhecido(valor):
    assert formatos.r_le_como_nao_texto(valor)


@pytest.mark.parametrize("valor", R_TEXTO)
def test_o_que_o_r_le_como_texto_passa(valor):
    assert not formatos.r_le_como_nao_texto(valor)


def test_amostras_numericas_reprovam_matriz_coldata_e_cruzamento(tmp_path):
    """Caso r3 da revisão: matriz 1..6, coldata 3,4=A / 5,6=B. O R lê a coluna amostra como
    inteiro e `bruto[, coldata$amostra]` pega as colunas 3..6 POR POSIÇÃO (as amostras 1..4):
    DESeq2 roda até o fim com o contraste de sinal invertido (up 0 / down 30 contra up 30 /
    down 0 do controle com nomes a1..a6)."""
    ct = _counts(tmp_path, ["1", "2", "3", "4", "5", "6"])
    cd = _coldata(tmp_path, [("3", "A"), ("4", "A"), ("5", "B"), ("6", "B")])
    _reprova(formatos.validar("contagens_genes", ct), "por POSIÇÃO")
    _reprova(formatos.validar("coldata", cd), "por POSIÇÃO")
    assert not formatos.contagens_e_coldata(ct, cd).ok
    # controle r3b: os mesmos números com nomes a1..a6
    ct2 = _counts(tmp_path, [f"a{i}" for i in range(1, 7)], nome="ct2.tsv")
    cd2 = _coldata(tmp_path, [("a3", "A"), ("a4", "A"), ("a5", "B"), ("a6", "B")], nome="cd2.tsv")
    v = formatos.contagens_e_coldata(ct2, cd2)
    assert v.ok and v.perfil["colunas_ignoradas"] == ["a1", "a2"], v.falhas


@pytest.mark.parametrize("niveis", [("1.0", "2.0"), ("01", "02"), ("T", "F"), ("NA", "B")])
def test_condicao_que_o_r_converte_reprova(tmp_path, niveis):
    """Caso r12: condições 1.0/2.0 viram os níveis '1'/'2' no R; a referência '1.0', que o
    dropdown oferecia, quebrava o relevel ("'ref' deve ser um nível existente")."""
    a, b = niveis
    _reprova(formatos.validar("coldata", _coldata(tmp_path, [("s1", a), ("s2", a), ("s3", b), ("s4", b)])),
             "condição")


# --------------------------------- #15: não normalizar o que o read.delim não normaliza
@pytest.mark.parametrize("pares", [
    [("s1", "A"), ("s2", "A"), ("s3", "A "), ("s4", "A "), ("s5", "B"), ("s6", "B")],  # r6: 3 níveis no R
    [("s1", "A"), ("s2", "A"), ("s3", "B "), ("s4", "B ")],                            # r5: 'B' não existe no R
    [("s1", "A"), ("s2", "A"), ("s3", "B"), ("s4 ", "B")],                             # amostra 'S1 ' ≠ 'S1'
])
def test_celula_com_espaco_reprova_em_vez_de_ser_aparada(tmp_path, pares):
    """r6: o portão fundia 'A' e 'A ' num nível com 4 réplicas; o R criava [A] [A ] [B] e o
    contraste usava metade dos controles, com exit 0 e o MESMO rótulo de contraste."""
    _reprova(formatos.validar("coldata", _coldata(tmp_path, pares)), "espaço no início/fim")


def test_linha_so_com_tabs_reprova_e_linha_vazia_de_verdade_passa(tmp_path):
    """r4: duas linhas só com TAB no fim da matriz (resíduo do Excel). O portão as pulava; o R
    as lê como genes com NA e para em 'matriz de contagens com NA/Inf'."""
    linhas = [["gene_id", "gene_name", "a", "b"], ["G1", "g1", "1", "2"], ["G2", "g2", "3", "4"]]
    ruim = _tsv(tmp_path, "ruim.tsv", linhas, fim="\n\t\t\t\n\t\t\t\n")
    _reprova(formatos.validar("contagens_genes", ruim), "gene_id vazio")
    cd = _tsv(tmp_path, "cd.tsv", [["amostra", "condicao"], ["a", "x"], ["b", "x"], ["c", "y"], ["d", "y"]],
              fim="\n\t\n")
    _reprova(formatos.validar("coldata", cd), "vazia")
    # controles: linha realmente vazia (o R também pula) e cabeçalho com espaço (o R apara)
    assert formatos.validar("contagens_genes", _tsv(tmp_path, "ok.tsv", linhas, fim="\n\n\n")).ok
    com_espaco = [[" gene_id ", "gene_name", "a", "b"], ["G1", "g1", "1", "2"]]
    assert formatos.validar("contagens_genes", _tsv(tmp_path, "esp.tsv", com_espaco)).ok


@pytest.mark.parametrize("nome", ["S1 ", "S1\x0b", " S1", "S1\x1c", "S1\x85"])
def test_cabecalho_so_apara_espaco_como_o_r(tmp_path, nome):
    """Resíduo do #15: o str.strip() aparava NBSP, \\x0b, \\x1c–\\x1f, \\x85 e U+2000–U+3000; o
    read.delim só apara espaço no cabeçalho. Conferido no R 4.5.3: colnames fica 'S1\\xa0' e
    bruto[, cd$amostra] para em 'colunas indefinidas selecionadas' — depois da aprovação."""
    ct = _tsv(tmp_path, "ct.tsv", [["gene_id", "gene_name", nome, "S2"], ["G1", "g1", "1", "2"]])
    _reprova(formatos.validar("contagens_genes", ct), "espaço no início/fim")
    estranho = nome.replace("S1", "")
    cd = _tsv(tmp_path, "cd.tsv", [["amostra", "condicao" + estranho], ["a", "x"], ["b", "x"], ["c", "y"], ["d", "y"]])
    _reprova(formatos.validar("coldata", cd), "precisa das colunas")


def test_cabecalho_com_espaco_ascii_e_aparado_como_no_r(tmp_path):
    """Controle: ' S2 ' vira 'S2' no R (r7 do verificador) — e aqui também."""
    ct = _tsv(tmp_path, "ct.tsv", [["gene_id", "gene_name", " S1", "S2 "], ["G1", "g1", "1", "2"]])
    v = formatos.validar("contagens_genes", ct)
    assert v.ok and v.perfil["amostras"] == ["S1", "S2"], v.falhas


@pytest.mark.parametrize("celula", ["10 ", " 10", "10\x1c", "10 "])
def test_contagem_com_espaco_unicode_reprova(tmp_path, celula):
    """O type.convert do R lê '10\\xa0' como TEXTO (conferido): a coluna inteira vira character
    e o script para em 'matriz de contagens com NA/Inf'. O portão aprovava (strip() do Python)."""
    ct = _tsv(tmp_path, "ct.tsv", [["gene_id", "gene_name", "a", "b"], ["G1", "g1", celula, "2"]])
    _reprova(formatos.validar("contagens_genes", ct), "não numérico")


@pytest.mark.parametrize("celula", [" 30", "30 ", "30\x0c", "\x0b30", "\x0c30"])
def test_contagem_com_espaco_ascii_passa_como_no_r(tmp_path, celula):
    """Controle: estes o type.convert do R 4.5.3 lê como número (o isspace do C)."""
    ct = _tsv(tmp_path, "ct.tsv", [["gene_id", "gene_name", "a", "b"], ["G1", "g1", celula, "2"]])
    assert formatos.validar("contagens_genes", ct).ok


@pytest.mark.parametrize("gene", ["G1 ", " G1", "G1 ", "G\x071"])
def test_gene_id_com_espaco_ou_controle_reprova(tmp_path, gene):
    """M20 do verificador: a checagem existia (citada no #15) e nenhum teste a via reprovar."""
    ct = _tsv(tmp_path, "ct.tsv", [["gene_id", "gene_name", "a", "b"], [gene, "g1", "1", "2"],
                                   ["G2", "g2", "3", "4"]])
    _reprova(formatos.validar("contagens_genes", ct), "gene_id com espaço em volta ou caractere de controle")


def test_aspas_no_meio_da_celula_reprovam(tmp_path):
    """No R 4.5.3, `x"y` abre um campo entre aspas que engole as linhas seguintes (o read.delim
    leu 0 linhas num teste de 2); o csv do Python lia `x"y` literal."""
    cd = _coldata(tmp_path, [('s"1', "x"), ("s2", "x"), ("s3", "y"), ("s4", "y")])
    _reprova(formatos.validar("coldata", cd), "aspas")


def test_coldata_com_campo_a_mais_reprova(tmp_path):
    """Com um campo a mais na 1ª linha de dado, o read.table usa a 1ª coluna como row.names:
    'amostra' passa a ser a condição e 'condicao' o campo extra (conferido no R)."""
    cd = _tsv(tmp_path, "cd.tsv", [["amostra", "condicao"], ["s1", "x", "extra"], ["s2", "x"],
                                   ["s3", "y"], ["s4", "y"]])
    _reprova(formatos.validar("coldata", cd), "nº de campos")


# --------------------------------------------- #16: exatamente 2 condições
def test_tres_condicoes_reprovam_duas_passam(tmp_path):
    """r7: ctrl/frio/calor com referência ctrl → o R testa frio_vs_ctrl só porque 'frio' vem
    depois de 'calor' no alfabeto; o efeito plantado em calor nunca é testado (0 DEGs, exit 0)."""
    tres = [("s1", "ctrl"), ("s2", "ctrl"), ("s3", "frio"), ("s4", "frio"), ("s5", "calor"), ("s6", "calor")]
    _reprova(formatos.validar("coldata", _coldata(tmp_path, tres)), "exatamente 2 condições")
    duas = [p for p in tres if p[1] != "frio"]
    assert formatos.validar("coldata", _coldata(tmp_path, duas, nome="duas.tsv")).ok


# ------------------------------------------- #18: gene_name exigido na matriz
def test_matriz_sem_gene_name_reprova_e_demo_passa(tmp_path):
    """r2: gene_id + amostras passava no portão; o R rodava o DESeq inteiro e morria em
    setNames(bruto$gene_name, ...) ("atributo em um NULL"), sem gravar nada."""
    linhas = [l.split("\t") for l in COUNTS.read_text(encoding="utf-8").splitlines()]
    sem_nome = _tsv(tmp_path, "r2.tsv", [[l[0], *l[2:]] for l in linhas])
    _reprova(formatos.validar("contagens_genes", sem_nome), "2ª coluna deve ser 'gene_name'")
    assert not formatos.contagens_e_coldata(sem_nome, COLDATA).ok
    assert formatos.validar("contagens_genes", COUNTS).ok  # controle r0


# ------------------------------------- #19: tabela_de lida como o volcano lê
@pytest.mark.parametrize("linhas,trecho", [
    ([["gene_id", "baseMean", "log2FoldChange", "pvalue", "padj"], ["G1", "1", "1", "0.1", "0.2"]],
     "colunas ausentes"),                                                              # v1: sem gene_name
    ([[f'"{c}"' for c in CAB_DE], ["G1", "g1", "1", "1", "0.1", "0.2"]], "colunas ausentes"),  # v3: cabeçalho entre aspas
    ([CAB_DE, ["G1", '"A\tB"', "1", "100.5", "0.1", "1e-10"]], "nº de campos"),        # v4: TAB dentro de aspas
    ([[" gene_id", *CAB_DE[1:]], ["G1", "g1", "1", "1", "0.1", "0.2"]], "colunas ausentes"),  # o volcano não apara
])
def test_tabela_de_que_o_volcano_nao_le(tmp_path, linhas, trecho):
    _reprova(formatos.validar("tabela_de", _tsv(tmp_path, "de.tsv", linhas)), trecho)


def test_tabela_de_no_layout_do_r_passa(tmp_path):
    """Controle v0: o que o expressao_deseq2.R grava (write.table quote=FALSE)."""
    de = _tsv(tmp_path, "de.tsv", [CAB_DE, ["ENSBTAG1", "HSPA1A", "120.5", "-3.2", "1e-9", "2e-7"],
                                   ["ENSBTAG2", "NA", "3", "NA", "NA", "NA"]])
    v = formatos.validar("tabela_de", de)
    assert v.ok and v.perfil == {"n_genes": 2, "n_padj_na": 1}, v.falhas


# --------------------- #21 / #32: nível que o sanitizador do plano recusaria
NIVEIS_RECUSADOS = ["estresse térmico", "pré-parto", "Controle (sham)", "-80C", "dia/0", "T=0", "verão",
                    "heat stress", "37ºC", "a..b", "x:y"]


@pytest.mark.parametrize("nivel", NIVEIS_RECUSADOS)
def test_condicao_fora_do_argv_reprova_ja_na_entrada(tmp_path, nivel):
    """O portão aprovava, o dropdown oferecia, e o plano devolvia 422 parametro_recusado — o
    run morria depois do upload e da escolha. Agora o coldata reprova com o porquê."""
    cd = _coldata(tmp_path, [("s1", "controle"), ("s2", "controle"), ("s3", nivel), ("s4", nivel)])
    _reprova(formatos.validar("coldata", cd), "linha de comando")


@pytest.mark.parametrize("par", [("controle", "tratado"), ("verao", "inverno"), ("heat_stress", "thermo-neutral"),
                                 ("summer_heat", "winter_control"), ("D0", "D7.5")])
def test_condicao_ascii_passa(tmp_path, par):
    a, b = par
    cd = _coldata(tmp_path, [("s1", a), ("s2", a), ("s3", b), ("s4", b)])
    assert formatos.validar("coldata", cd).ok


def test_todo_nivel_aceito_passa_no_sanitizador_do_plano(tmp_path):
    """Sincronia com dispatch._validar_valor (o formatos não o importa de propósito): o que o
    coldata aceita como nível, o plano aceita como referência; o que o canário recusa, o
    sanitizador ou o R também recusariam."""
    from omica.jobs import dispatch
    from omica.pipelines import spec_api
    from omica.registry import load_registry

    spec = spec_api(load_registry().por_id("transcriptomica"), "deseq2")
    referencia = next(p for p in spec.params if p.name == "referencia")
    aceitos = ["controle", "tratado", "verao", "heat_stress", "thermo-neutral", "D7.5", "summer_heat",
               "a.b-c_d", "X1", "_x"]
    for nivel in aceitos:
        cd = _coldata(tmp_path, [("s1", nivel), ("s2", nivel), ("s3", "zz"), ("s4", "zz")], nome=f"{len(nivel)}.tsv")
        assert formatos.validar("coldata", cd).ok, nivel
        assert dispatch._validar_valor(referencia, nivel) == nivel
    for nivel in NIVEIS_RECUSADOS:
        if nivel in ("x:y",):  # ':' o sanitizador aceita; o portão recusa por ser mais estrito
            continue
        with pytest.raises(ValueError):
            dispatch._validar_valor(referencia, nivel)


# --------------------- #17 (resíduo): nível que deixa o nome do contraste ambíguo
@pytest.mark.parametrize("nivel", ["tratado_vs", "vs_ctrl", "vs_", "_vs", "a.b_vs"])
def test_condicao_com_vs_na_borda_reprova(tmp_path, nivel):
    """Com 'tratado_vs' × 'ctrl' o DESeq2 nomeia 'condicao_tratado_vs_vs_ctrl' — o mesmo nome de
    'tratado' × 'vs_ctrl'. O volcano rotulava 'tratado vs vs_ctrl; referência = vs_ctrl', uma
    referência que não existe. O coldata recusa o nível na entrada, dizendo o porquê."""
    cd = _coldata(tmp_path, [("s1", "outro"), ("s2", "outro"), ("s3", nivel), ("s4", nivel)])
    _reprova(formatos.validar("coldata", cd), "'_vs'")


@pytest.mark.parametrize("nivel", ["vs", "versus", "vsx", "xvs", "a_vsb", "a_vs_b", "VS_x", "x_VS"])
def test_condicao_com_vs_fora_da_borda_passa(tmp_path, nivel):
    """Controle: só a borda 'vs_'/'_vs' é ambígua ('a_vs_b' dá 2+ '_vs_' e o volcano usa o nome
    cru; o split do volcano diferencia caixa)."""
    cd = _coldata(tmp_path, [("s1", "outro"), ("s2", "outro"), ("s3", nivel), ("s4", nivel)])
    assert formatos.validar("coldata", cd).ok


# ------------------------------- #20 (resíduo): gene_id é a chave do relatório
@pytest.mark.parametrize("linhas,trecho", [
    ([CAB_DE, ["G1", "g1", "1", "1", "0.1", "0.2"], ["G1", "g2", "1", "1", "0.1", "0.2"]], "gene_id repetido"),
    ([CAB_DE, ["", "g1", "1", "1", "0.1", "0.2"]], "gene_id vazio"),
])
def test_tabela_de_com_chave_repetida_ou_vazia_reprova(tmp_path, linhas, trecho):
    """O portão de números exige achar a chave exatamente 1 vez: repetida, o run inteiro rodava
    para reprovar só no relatório."""
    _reprova(formatos.validar("tabela_de", _tsv(tmp_path, "de.tsv", linhas)), trecho)


def test_colunas_chave_sao_garantidas_e_reprovam_repetidas(tmp_path):
    """Sincronia: toda coluna-chave é coluna garantida, e o validador do formato reprova o
    valor repetido nela (senão a promessa de COLUNAS_CHAVE seria só um nome)."""
    for formato, chaves in formatos.COLUNAS_CHAVE.items():
        assert set(chaves) <= set(formatos.COLUNAS_GARANTIDAS[formato]), formato
    ct = _tsv(tmp_path, "ct.tsv", [["gene_id", "gene_name", "a", "b"], ["G1", "g", "1", "2"], ["G1", "g", "3", "4"]])
    _reprova(formatos.validar("contagens_genes", ct), "gene_id repetido")
    cd = _coldata(tmp_path, [("a", "x"), ("a", "x"), ("c", "y"), ("d", "y")])
    _reprova(formatos.validar("coldata", cd), "amostras repetidas")


# --------------------- portão que reprova em vez de levantar (resumo, OSError)
_RESUMOS_HOSTIS = {
    "lista_aninhada": lambda: "[" * 200_000 + "]" * 200_000,
    "objeto_com_lista_aninhada": lambda: '{"a": ' + "[" * 200_000 + "]" * 200_000 + "}",
    "numero_de_5000_digitos": lambda: '{"n": ' + "9" * 5000 + "}",
}


@pytest.mark.parametrize("caso", sorted(_RESUMOS_HOSTIS))  # id curto: o conteúdo no id estoura o env do Windows
def test_resumo_json_aninhado_ou_numero_gigante_reprova(tmp_path, caso):
    """Resíduo (exp1 do verificador): '[' repetido 200 mil vezes levantava RecursionError no
    validar — o envelope roda no estado() do run, e o GET virava 500. O número de 5000 dígitos
    estoura o limite do int do Python (ValueError)."""
    arq = tmp_path / "r.json"
    arq.write_text(_RESUMOS_HOSTIS[caso](), encoding="utf-8")
    assert not formatos.validar("resumo_json", arq).ok
    assert not formatos.resumo_json(arq).ok  # chamada direta também


@pytest.mark.parametrize("formato", sorted(formatos.VALIDADORES))
def test_chamada_direta_em_diretorio_reprova(tmp_path, formato):
    """Resíduo: o decorador não pegava OSError — formatos.coldata(<diretório>) levantava
    PermissionError (Windows) / IsADirectoryError, ao contrário do que a docstring prometia."""
    v = formatos.VALIDADORES[formato](tmp_path)
    assert not v.ok and v.falhas
    assert tmp_path.name not in " ".join(v.falhas)  # só o tipo do erro, nunca o caminho


# ------------------------------------------------ #42: checagens sem canário
@pytest.mark.parametrize("linhas,trecho", [
    ([CAB_DE, ["G1", "g1", "10", "2.5", "1.7", "0.01"]], "pvalue fora de [0,1]"),      # M12
    ([CAB_DE, ["G1", "g1", "10", "inf", "0.01", "0.02"]], "não finito"),               # M13
    ([CAB_DE, ["G1", "g1", "10", "abc", "0.01", "0.02"]], "não numérico"),             # M35
    ([CAB_DE, ["G1", "g1", "10", "2.5"]], "nº de campos"),                             # campo ausente
    ([CAB_DE], "tabela vazia"),                                                        # M15
    ([["gene_id", "gene_name", "baseMean", "log2FoldChange", "padj"], ["G1", "g", "1", "1", "0.1"]],
     "colunas ausentes"),                                                              # M20
])
def test_tabela_de_canarios(tmp_path, linhas, trecho):
    _reprova(formatos.validar("tabela_de", _tsv(tmp_path, "de.tsv", linhas)), trecho)


@pytest.mark.parametrize("linhas,trecho", [
    ([["gene_id", "gene_name", "a", "b"]], "nenhum gene"),                             # M14
    ([["gene_id", "gene_name", "a", "b"], ["", "g", "1", "2"]], "gene_id vazio"),      # M34
    ([["gene_id", "gene_name", "a", "b"], ["G1", "g", "NA", "2"]], "contagem inválida"),
    ([["gene_id", "gene_name", "a", "b"], ["G1", "g", "1_000", "2"]], "não numérico"),  # float() aceita, o R não
    ([["gene_id", "gene_name", "a", ""], ["G1", "g", "1", "2"]], "nome de amostra vazio"),
])
def test_contagens_canarios_novos(tmp_path, linhas, trecho):
    _reprova(formatos.validar("contagens_genes", _tsv(tmp_path, "c.tsv", linhas)), trecho)


def test_coldata_linha_curta_reprova(tmp_path):
    """M33: a linha curta era descartada em silêncio."""
    cd = _tsv(tmp_path, "cd.tsv", [["amostra", "condicao"], ["a", "x"], ["b", "x"], ["c", "y"], ["d", "y"], ["e"]])
    _reprova(formatos.validar("coldata", cd), "nº de campos")


def test_contagens_decimais_passam_e_sao_contadas(tmp_path):
    """Controle: decimal NÃO reprova (o script arredonda declarando), mas é contado."""
    v = formatos.validar("contagens_genes", _tsv(tmp_path, "c.tsv", [["gene_id", "gene_name", "a", "b"],
                                                                     ["G1", "g", "1.5", "2"]]))
    assert v.ok and v.perfil["n_valores_nao_inteiros"] == 1, v.falhas


# ------------------------------------- #1 (defesa em profundidade): sem eco do arquivo
def test_falhas_nao_ecoam_conteudo_do_arquivo(tmp_path):
    segredo = "sk-live-" + "9" * 50
    cred = tmp_path / "credenciais.txt"
    cred.write_text(f"API_KEY={segredo}\nsenha=hunter2\n", encoding="utf-8")
    v = formatos.validar("coldata", cred)
    texto = json.dumps(v.to_dict(), ensure_ascii=False)
    assert not v.ok and "hunter2" not in texto and segredo not in texto
    # resíduo do #42: antes voltava 'API_KEY=sk-live-' (40 caracteres da 1ª linha)
    assert "API_KEY" not in texto and "<omitido>" in texto

    planilha = _tsv(tmp_path, "p.tsv", [["gene_id", "gene_name", "paciente_A", "paciente_B"],
                                        ["G1", "g", "positivo", "1"], ["G1", "g", "2", "3"]])
    v = formatos.validar("contagens_genes", planilha)
    texto = json.dumps(v.to_dict(), ensure_ascii=False)
    assert not v.ok and "positivo" not in texto and "'G1'" not in texto
    assert "amostras" not in v.perfil  # perfil de arquivo reprovado não carrega o cabeçalho


def test_nomes_de_coluna_nas_falhas_sao_limitados(tmp_path):
    cab = [f"coluna_{i}" for i in range(30)] + ["x\x07y"]
    v = formatos.validar("coldata", _tsv(tmp_path, "cd.tsv", [["tem\x01controle", *cab], ["a"] * 32]))
    falha = v.falhas[0]
    assert "(+22 colunas)" in falha and "coluna_8'" in falha and "coluna_9" not in falha
    assert "32 coluna(s)" in falha
    # nome fora do padrão de identificador nem truncado volta (antes: 'tem?controle')
    assert "\x01" not in falha and "controle" not in falha and "'<omitido>'" in falha


SEGREDO_1A_LINHA = "TOKEN_OMICA=a1b2c3d4e5f6g7h8i9j0k1l2"


@pytest.mark.parametrize("formato", ["coldata", "contagens_genes"])
def test_primeira_linha_de_um_arquivo_qualquer_nao_volta_na_falha(tmp_path, formato):
    """Resíduo do #42 (exp1 do verificador): um arquivo de uma linha só com
    'TOKEN_OMICA=<valor>' (36 caracteres, abaixo do corte de 40) voltava INTEIRO na falha, e uma
    1ª linha de 10 campos separados por TAB devolvia 360 caracteres."""
    um = tmp_path / "um.txt"
    um.write_text(SEGREDO_1A_LINHA + "\n", encoding="utf-8")
    texto = json.dumps(formatos.validar(formato, um).to_dict(), ensure_ascii=False)
    assert "a1b2c3d4e5f6" not in texto and "TOKEN_OMICA" not in texto
    dez = tmp_path / "dez.txt"
    campos = [f"chave{i}=valor_secreto_{i:02d}" for i in range(10)]
    dez.write_text("\t".join(campos) + "\n", encoding="utf-8")
    texto = json.dumps(formatos.validar(formato, dez).to_dict(), ensure_ascii=False)
    assert "valor_secreto" not in texto
    # controle: num cabeçalho de tabela de verdade, o nome-identificador continua ajudando
    tabela = _tsv(tmp_path, "t.tsv", [["Gene_ID", "sample", "grupo"], ["G1", "1", "x"]])
    falha = formatos.validar(formato, tabela).falhas[0]
    assert "Gene_ID" in falha


# ------------------------------------- #17: o volcano do pipeline rotula pelo contraste
def _rodar_volcano(tmp_path: Path, contraste) -> subprocess.CompletedProcess:
    de = _tsv(tmp_path, "de_table.tsv", [CAB_DE,
                                         ["ENSBTAG1", "HSPA1A", "120.5", "-3.2", "1e-9", "2e-7"],
                                         ["ENSBTAG2", "GENE2", "50", "1.4", "1e-4", "1e-3"],
                                         ["ENSBTAG3", "GENE3", "10", "0.1", "0.5", "NA"]])
    resumo = tmp_path / "de_resumo.json"
    resumo.write_text(json.dumps({} if contraste is None else {"contraste": contraste}), encoding="utf-8")
    return subprocess.run([sys.executable, str(SCRIPTS / "volcano_api.py"), "--de", str(de), "--resumo",
                           str(resumo), "--padj", "0.05", "--lfc", "1", "--saida", str(tmp_path / "volcano")],
                          capture_output=True, text=True, cwd=REPO_ROOT)


@pytest.mark.parametrize("contraste,esperado,proibido", [
    # referência = summer_heat (a 1ª opção do dropdown): o DESeq2 calcula winter_control vs summer_heat
    ("condicao_winter_control_vs_summer_heat", "winter_control vs summer_heat", "summer_heat vs winter_control"),
    ("condicao_summer_heat_vs_winter_control", "summer_heat vs winter_control", "winter_control vs summer_heat"),
])
def test_volcano_api_rotula_pelo_contraste_calculado(tmp_path, contraste, esperado, proibido):
    pytest.importorskip("matplotlib")
    r = _rodar_volcano(tmp_path, contraste)
    assert r.returncode == 0, r.stderr
    resumo = json.loads((tmp_path / "volcano" / "volcano_resumo.json").read_text(encoding="utf-8"))
    assert resumo["contraste"] == contraste
    assert esperado in resumo["rotulo_x"] and proibido not in resumo["rotulo_x"]
    assert "granulosa" not in resumo["titulo"] and esperado in resumo["titulo"]
    assert formatos.validar("figura_png", tmp_path / "volcano" / "volcano.png").ok


@pytest.mark.parametrize("contraste,lido", [
    ("condicao_tratado_vs_vs_ctrl", None),        # 'tratado_vs' × 'ctrl' ou 'tratado' × 'vs_ctrl'?
    ("condicao_a_vs_b_vs_c", None),               # 2 '_vs_' não sobrepostos: já era cru
    ("condicao_vs_vs_ctrl", "vs vs ctrl"),         # controle: nível 'vs' — um '_vs_' só, sem ambiguidade
    ("condicao_ctrl_vs_vs", "ctrl vs vs"),
])
def test_volcano_api_nao_divide_contraste_ambiguo(tmp_path, contraste, lido):
    """Resíduo do #17: com um só '_vs_' no split, 'tratado_vs_vs_ctrl' saía 'tratado vs vs_ctrl;
    referência = vs_ctrl' — referência que não existe. As ocorrências sobrepostas contam: com
    mais de uma, o rótulo é o nome cru (que nunca está invertido)."""
    pytest.importorskip("matplotlib")
    r = _rodar_volcano(tmp_path, contraste)
    assert r.returncode == 0, r.stderr
    resumo = json.loads((tmp_path / "volcano" / "volcano_resumo.json").read_text(encoding="utf-8"))
    if lido is None:
        assert resumo["rotulo_x"] == f"log2 fold change ({contraste})"
        assert "referência" not in resumo["rotulo_x"]
    else:
        assert lido in resumo["rotulo_x"] and "referência" in resumo["rotulo_x"]


def test_volcano_api_sem_contraste_falha_em_vez_de_chutar(tmp_path):
    pytest.importorskip("matplotlib")
    r = _rodar_volcano(tmp_path, None)
    assert r.returncode != 0 and "contraste" in r.stderr
    assert not (tmp_path / "volcano" / "volcano.png").exists()
