"""Formatos e checagem do ML (F6): ESPEC §2.2 + adendo v2 §A6.1.

Regra do projeto: todo portão é VISTO reprovando (canário conferido pelo MOTIVO — substring da
falha) e tem um controle que passa. Controles reais: as saídas seladas de
E:\\data\\omica\\results\\ml\\demo (genotipos 515 × 157, rotulos Angus 102 / Brahman 55,
shap_top e nucleo_estavel com 50 linhas) e o BCF do Chr14 do Run 8 × a tabela de exemplo (1842
amostras no VCF, 820 rotuladas), com skip sem o E:\\data. A tabela de exemplo é derivada aqui da
tabela-verdade, com a regra da decisão 14 (16 raças com n ≥ 10); quando o arquivo versionado
da F2 (modules/ml/data/rotulos_1000bg_racas.tsv) existe, é ele que vale.
"""
from __future__ import annotations

import csv
import re
from collections import Counter
from pathlib import Path

import pytest

from omica.config import REPO_ROOT
from omica.contrato import formatos
from test_formatos_genomica import escrever_tbi, escrever_vcf

E_ML = Path(r"E:\data\omica\results\ml\demo")
BCF14 = Path(r"E:\data\1000bg_vcf\Chr14-Run8-TAUIND-public.bcf")
VERDADE = Path(r"E:\data\1000bg_metadata\proveniencia_2026-10-13\rotulos_corrigidos_2026-10-13.tsv")
TABELA_F2 = REPO_ROOT / "modules" / "ml" / "data" / "rotulos_1000bg_racas.tsv"
DEMO_VCF_GEN = REPO_ROOT / "modules" / "genomica" / "demo_data" / "demo_bta14_inicio.vcf.gz"


@pytest.fixture(autouse=True)
def _cache_limpo():
    formatos._ler_vcf_cache.cache_clear()
    yield
    formatos._ler_vcf_cache.cache_clear()


def _exige(p: Path) -> Path:
    if not p.exists():
        pytest.skip(f"{p.name} ausente (dado real fora do repositório)")
    return p


def _reprova(v: formatos.Veredito, trecho: str) -> None:
    assert not v.ok and any(trecho in f for f in v.falhas), v.falhas


def _texto(tmp_path: Path, nome: str, conteudo: str) -> Path:
    p = tmp_path / nome
    p.write_text(conteudo, encoding="utf-8", newline="")
    return p


def _rotulos(tmp_path: Path, grupos: dict[str, int], *, extra: str = "", nome: str = "r.tsv",
             cab: str = "amostra\tgrupo") -> Path:
    linhas = [cab]
    k = 0
    for g, n in grupos.items():
        for _ in range(n):
            k += 1
            linhas.append(f"S{k:04d}\t{g}")
    return _texto(tmp_path, nome, "\n".join(linhas) + "\n" + extra)


def _tabela_exemplo(tmp_path: Path) -> Path:
    """A tabela de exemplo da decisão 14: as 16 raças com n ≥ 10 em breed_biosample (820
    amostras); os nomes que o argv recusa viram N_Dama, Red_Angus e Polish_Holstein-Friesian."""
    if TABELA_F2.exists():
        return TABELA_F2
    with _exige(VERDADE).open(encoding="utf-8", newline="") as f:
        linhas = list(csv.DictReader(f, delimiter="\t"))
    n = Counter(l["breed_biosample"] for l in linhas if l["breed_biosample"].strip())
    ge10 = {b for b, c in n.items() if c >= 10}
    p = tmp_path / "rotulos_exemplo.tsv"
    with p.open("w", encoding="utf-8", newline="\n") as f:
        f.write("amostra\tgrupo\tbreed_biosample\n")
        for l in linhas:
            if l["breed_biosample"] in ge10:
                f.write(f"{l['sample']}\t{re.sub(r'[^A-Za-z0-9_.-]+', '_', l['breed_biosample'])}\t"
                        f"{l['breed_biosample']}\n")
    return p


# ================================================================ rotulos_amostras
def test_rotulos_amostras_controle_sintetico(tmp_path):
    v = formatos.validar("rotulos_amostras", _rotulos(tmp_path, {"Angus": 10, "Brahman": 12}))
    assert v.ok and v.perfil == {"n_amostras": 22, "n_niveis": 2, "niveis": {"Angus": 10, "Brahman": 12}}, v.falhas
    outras = _rotulos(tmp_path, {"A": 10, "B": 10}, cab="amostra\tgrupo\tnota", nome="o.tsv")
    texto = outras.read_text(encoding="utf-8").replace("\tA\n", "\tA\tlivre com espaço\n").replace(
        "\tB\n", "\tB\tx\n")
    outras.write_text(texto, encoding="utf-8")
    assert formatos.validar("rotulos_amostras", outras).ok  # colunas extras são ignoradas


def test_rotulos_amostras_tabela_de_exemplo_controle(tmp_path):
    v = formatos.validar("rotulos_amostras", _tabela_exemplo(tmp_path))
    assert v.ok, v.falhas
    assert v.perfil["n_amostras"] == 820 and v.perfil["n_niveis"] == 16
    assert v.perfil["niveis"]["Holstein"] == 187 and v.perfil["niveis"]["Angus"] == 102
    assert {"N_Dama", "Red_Angus", "Polish_Holstein-Friesian"} <= set(v.perfil["niveis"])
    assert formatos.valores_coluna(_tabela_exemplo(tmp_path), "grupo") is not None  # a escolha tem lista


@pytest.mark.parametrize("caso,trecho", [
    ("51_niveis", "51 grupos"),
    ("red_angus", "grupo:"),
    ("nove", "menos de 10 amostras"),
    ("id_numerico", "cara de número"),
    ("id_na", "token de NA"),
    ("repetida", "amostra repetida"),
    ("sem_grupo", "precisa das colunas 'amostra' e 'grupo'"),
    ("um_grupo", "≥2 grupos"),
    ("campos", "nº de campos diferente"),
    ("aspas", "aspas"),
])
def test_rotulos_amostras_canarios(tmp_path, caso, trecho):
    base = {"A": 10, "B": 10}
    if caso == "51_niveis":
        p = _rotulos(tmp_path, {f"G{i}": 10 for i in range(51)})
    elif caso == "red_angus":
        p = _rotulos(tmp_path, {"Red Angus": 10, "B": 10})
    elif caso == "nove":
        p = _rotulos(tmp_path, {"A": 9, "B": 10})
    elif caso == "id_numerico":
        p = _rotulos(tmp_path, base, extra="1001\tA\n")
    elif caso == "id_na":
        p = _rotulos(tmp_path, base, extra="NA\tA\n")
    elif caso == "repetida":
        p = _rotulos(tmp_path, base, extra="S0001\tA\n")
    elif caso == "sem_grupo":
        p = _rotulos(tmp_path, base, cab="amostra\traca")
    elif caso == "um_grupo":
        p = _rotulos(tmp_path, {"A": 20})
    elif caso == "campos":
        p = _rotulos(tmp_path, base, extra="S9999\tA\tsobra\n")
    else:
        p = _rotulos(tmp_path, base, extra='S9999\t"A"\n')
    _reprova(formatos.validar("rotulos_amostras", p), trecho)


def test_rotulos_amostras_nao_ecoa_conteudo(tmp_path):
    p = _rotulos(tmp_path, {"TOKEN=segredo": 10, "B": 10})
    v = formatos.validar("rotulos_amostras", p)
    assert not v.ok and "segredo" not in " ".join(v.falhas) + str(v.perfil)


# =================================================================== lista_amostras
def test_lista_amostras_controle_e_chamada_direta_nao_levanta(tmp_path):
    """Crítica M5: o esboço copiado sem os auxiliares dava NameError (500 no polling) — a
    chamada DIRETA num arquivo válido não levanta."""
    p = _texto(tmp_path, "a.txt", "".join(f"SAMN{i:08d}\r\n" for i in range(12)))
    v = formatos.lista_amostras(p)
    assert v.ok and v.perfil["n_ids"] == 12 and v.perfil["crlf"], v.falhas
    assert formatos.validar("lista_amostras", p).ok


def test_lista_amostras_grupo_a_do_selado_controle(tmp_path):
    """grupo_a.txt: os 102 Angus da tabela selada do dataset."""
    angus = [l.split("\t")[0] for l in _exige(E_ML / "dataset" / "rotulos.tsv").read_text(encoding="utf-8")
             .splitlines()[1:] if l.endswith("\tAngus")]
    p = _texto(tmp_path, "grupo_a.txt", "\n".join(angus) + "\n")
    v = formatos.validar("lista_amostras", p)
    assert v.ok and v.perfil["n_ids"] == 102, v.falhas
    assert formatos.validar("lista_amostras", _exige(E_ML / "dataset" / "amostras.txt")).perfil["n_ids"] == 157


@pytest.mark.parametrize("conteudo,trecho", [
    ("\ufeffSAMN1\nSAMN2\n", "BOM"),
    ("#x\nSAMN1\nSAMN2\n", "linha '#'"),
    ("SAMN 1\nSAMN2\nSAMN3\n", "espaço/TAB interno"),
    ("SAMN1\nSAMN2\nSAMN1\n", "ID repetido"),
    ("SAMN1\n", "1 ID(s)"),
])
def test_lista_amostras_canarios(tmp_path, conteudo, trecho):
    _reprova(formatos.validar("lista_amostras", _texto(tmp_path, "l.txt", conteudo)), trecho)


def test_lista_amostras_teto_de_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(formatos, "_MAX_LISTA", 10)
    _reprova(formatos.validar("lista_amostras", _texto(tmp_path, "l.txt", "SAMN0001\nSAMN0002\n")), "mais de 10 bytes")


# ================================================================ matriz_marcadores
def _matriz(tmp_path: Path, linhas: list[str], cab: str = "variante\tA1\tA2\tA3") -> Path:
    return _texto(tmp_path, "m.tsv", cab + "\n" + "".join(l + "\n" for l in linhas))


def test_matriz_selada_controle():
    v = formatos.validar("matriz_marcadores", _exige(E_ML / "dataset" / "genotipos.tsv"))
    assert v.ok, v.falhas
    assert (v.perfil["n_marcadores"], v.perfil["n_amostras"]) == (515, 157)
    assert v.perfil["coluna_id"] == "variante" and v.perfil["codificacao_0_1_2"]


def test_matriz_sintetica_controle(tmp_path):
    v = formatos.validar("matriz_marcadores", _matriz(tmp_path, ["14:1\t0\t1\t2", "14:2\tNA\t1\t1", "14:3\t0.5\t\t1e-3"]))
    assert v.ok, v.falhas
    p = v.perfil
    assert (p["n_marcadores"], p["n_linhas_com_na"], p["n_linhas_constantes"], p["codificacao_0_1_2"]) == \
        (3, 2, 1, False)


@pytest.mark.parametrize("linhas,cab,trecho", [
    (["14:1\t0\t1\t2", "14:2\tNA\tNA\tNA"], None, "todas as amostras NA"),
    (["14:1\t0\t.\t2"], None, "nem decimal finito"),
    (["14:1\t0\tinf\t2"], None, "nem decimal finito"),
    (["14:1\t0\t1\t2", "14:1\t0\t1\t1"], None, "ID do marcador repetido"),
    (["14:1\t0\t1\t2\t1"], None, "um campo a menos"),
    ([], None, "0 marcadores"),
    (["14:1\t0"], "variante\tA1", "≥2 colunas de amostra"),
    (["14:1\t0\t1"], "variante\tA1\tA1", "repetidos no cabeçalho"),
    (["14:1\t0\t1\t1e999"], None, "nem decimal finito"),
    (["14:1\t0\t1\t1_0"], None, "nem decimal finito"),
])
def test_matriz_canarios(tmp_path, linhas, cab, trecho):
    _reprova(formatos.validar("matriz_marcadores", _matriz(tmp_path, linhas, cab or "variante\tA1\tA2\tA3")), trecho)


# =================================================================== rotulos_grupos
def test_rotulos_grupos_selado_controle():
    v = formatos.validar("rotulos_grupos", _exige(E_ML / "dataset" / "rotulos.tsv"))
    assert v.ok, v.falhas
    assert v.perfil["niveis"] == {"Angus": 102, "Brahman": 55} and v.perfil["classe_positiva"] == "Brahman"


def test_rotulos_grupos_aviso_abaixo_de_10(tmp_path):
    v = formatos.validar("rotulos_grupos", _rotulos(tmp_path, {"A": 6, "B": 12}))
    assert v.ok and v.perfil["avisos"] and "< 10" in v.perfil["avisos"][0], v.falhas


@pytest.mark.parametrize("grupos,extra,cab,trecho", [
    ({"A": 10, "B": 10, "C": 10}, "", "amostra\tgrupo", "exatamente 2 grupos"),
    ({"A": 4, "B": 10}, "", "amostra\tgrupo", "com 4 amostra(s)"),
    ({"A": 10, "B": 10}, "1001\tA\n", "amostra\tgrupo", "cara de número"),
    ({"A": 10, "B": 10}, "", "grupo\tamostra", "'grupo' na 1ª coluna"),
    ({"A": 10, "B": 10}, "S0001\tA\n", "amostra\tgrupo", "ID de amostra repetido"),
    ({"A": 10, "B": 10}, "", "id\tgrupo", "'amostra' na 1ª coluna"),
])
def test_rotulos_grupos_canarios(tmp_path, grupos, extra, cab, trecho):
    _reprova(formatos.validar("rotulos_grupos", _rotulos(tmp_path, grupos, extra=extra, cab=cab)), trecho)


# ====================================================================== tabela_shap
def test_shap_selado_controle():
    v = formatos.validar("tabela_shap", _exige(E_ML / "classificacao" / "shap_top.tsv"))
    assert v.ok and v.perfil["n_linhas"] == 50, v.falhas


def test_shap_notacao_cientifica_passa(tmp_path):
    """L20: o .6g usa notação científica abaixo de 1e-4; '9.5e-05' depois de '0.00321808'."""
    p = _texto(tmp_path, "s.tsv", "VARIANTE\tSHAP_MEDIO_ABS\n14:1\t0.0224798\n14:2\t0.00321808\n14:3\t9.5e-05\n14:4\t0\n")
    v = formatos.validar("tabela_shap", p)
    assert v.ok and v.perfil == {"n_linhas": 4, "shap_max": 0.0224798}, v.falhas


@pytest.mark.parametrize("conteudo,trecho", [
    ("VARIANTE\tSHAP_MEDIO_ABS\n14:1\t-0.1\n", "≥ 0"),
    ("VARIANTE\tSHAP_MEDIO_ABS\n14:1\t0.1\n14:2\t0.2\n", "crescente"),
    ("variante\tshap_medio_abs\n14:1\t0.1\n", "cabeçalho"),
    ("VARIANTE\tSHAP_MEDIO_ABS\n14:1\t0.2\n14:1\t0.1\n", "VARIANTE repetida"),
    ("VARIANTE\tSHAP_MEDIO_ABS\n14:1\tinf\n", "decimal finito"),
    ("VARIANTE\tSHAP_MEDIO_ABS\n", "tabela sem linhas"),
])
def test_shap_canarios(tmp_path, conteudo, trecho):
    _reprova(formatos.validar("tabela_shap", _texto(tmp_path, "s.tsv", conteudo)), trecho)


# ============================================================ tabela_nucleo_estavel
def test_nucleo_selado_controle_com_alerta_de_empate():
    v = formatos.validar("tabela_nucleo_estavel", _exige(E_ML / "robustez" / "nucleo_estavel.tsv"))
    assert v.ok and v.perfil["n_linhas"] == 50 and v.perfil["alerta_empate"] is True, v.falhas
    assert v.perfil["recorrencia"] == {"5/5": 50} and v.perfil["n_freq_ge_0_80"] == 50


def test_nucleo_sem_empate(tmp_path):
    p = _texto(tmp_path, "n.tsv", "MARCADOR\tFREQ_BOOTSTRAP\tRECORRENCIA_FOLDS\n14:1\t1.000\t5/5\n14:2\t0.700\t3/5\n")
    v = formatos.validar("tabela_nucleo_estavel", p)
    assert v.ok and v.perfil["alerta_empate"] is False and v.perfil["n_freq_ge_0_80"] == 1


_CAB_NUC = "MARCADOR\tFREQ_BOOTSTRAP\tRECORRENCIA_FOLDS\n"


@pytest.mark.parametrize("conteudo,trecho", [
    (_CAB_NUC + "14:1\t1.2\t5/5\n", "fora de [0,1]"),
    (_CAB_NUC + "14:1\t1.000\t6/5\n", "fora de '[0-5]/5'"),
    (_CAB_NUC + "14:1\t1.000\t5/5\n14:1\t0.900\t5/5\n", "MARCADOR repetido"),
    (_CAB_NUC + "14:1\t0.500\t5/5\n14:2\t0.900\t5/5\n", "crescente"),
    ("MARCADOR\tFREQ\tRECORRENCIA\n14:1\t1\t5/5\n", "cabeçalho"),
])
def test_nucleo_canarios(tmp_path, conteudo, trecho):
    _reprova(formatos.validar("tabela_nucleo_estavel", _texto(tmp_path, "n.tsv", conteudo)), trecho)


# ======================================================================= vcf_e_rotulos
def _vcf_com_amostras(tmp_path: Path, amostras: list[str], *, gt: bool = True) -> Path:
    cab = [b"##fileformat=VCFv4.2\n", b"##contig=<ID=14,length=1000000>\n",
           b'##INFO=<ID=AF,Number=A,Type=Float,Description="af">\n']
    if gt:
        cab.append(b'##FORMAT=<ID=GT,Number=1,Type=String,Description="gt">\n')
    cab.append(("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t" + "\t".join(amostras) + "\n").encode())
    reg = [(f"14\t{100 + 10 * i}\t.\tA\tG\t.\tPASS\tAF=0.5\tGT\t" + "\t".join("0/1" for _ in amostras)
            + "\n").encode() for i in range(5)]
    p = tmp_path / "amostras.vcf.gz"
    regs = escrever_vcf(p, cab + reg)
    escrever_tbi(Path(f"{p}.tbi"), regs, mtime_de=p)
    return p


def test_vcf_e_rotulos_controle_sintetico(tmp_path):
    rot = _rotulos(tmp_path, {"A": 10, "B": 10})
    vcf = _vcf_com_amostras(tmp_path, [f"S{k:04d}" for k in range(1, 26)])
    v = formatos.vcf_e_rotulos(vcf, rot)
    assert v.ok, v.falhas
    assert v.perfil == {"n_amostras_vcf": 25, "n_rotuladas": 20, "n_vcf_sem_rotulo": 5,
                        "niveis_no_vcf": {"A": 10, "B": 10}}


def test_vcf_e_rotulos_bcf_chr14_x_tabela_de_exemplo(tmp_path):
    v = formatos.vcf_e_rotulos(_exige(BCF14), _tabela_exemplo(tmp_path))
    assert v.ok, v.falhas
    assert (v.perfil["n_amostras_vcf"], v.perfil["n_rotuladas"]) == (1842, 820)
    assert sum(v.perfil["niveis_no_vcf"].values()) == 820


def test_vcf_e_rotulos_demo_sem_gt_reprova_por_format_gt(tmp_path):
    """E2E-4(a): o demo da genômica só tem sítios. A substring é a do canário do adendo."""
    _reprova(formatos.vcf_e_rotulos(DEMO_VCF_GEN, _rotulos(tmp_path, {"A": 10, "B": 10})), "FORMAT/GT")
    sem_gt = _vcf_com_amostras(tmp_path, [f"S{k:04d}" for k in range(1, 21)], gt=False)
    _reprova(formatos.vcf_e_rotulos(sem_gt, _rotulos(tmp_path, {"A": 10, "B": 10}, nome="r2.tsv")), "FORMAT/GT")


def test_vcf_e_rotulos_amostra_ausente_reprova(tmp_path):
    rot = _rotulos(tmp_path, {"A": 10, "B": 10})
    vcf = _vcf_com_amostras(tmp_path, [f"S{k:04d}" for k in range(1, 20)])  # falta S0020
    v = formatos.vcf_e_rotulos(vcf, rot)
    _reprova(v, "1 amostra(s) rotulada(s) ausente(s)")
    assert len(v.falhas) == 1  # só a primeira falha aplicável


def test_vcf_e_rotulos_bcf_real_com_id_inexistente(tmp_path):
    tabela = _tabela_exemplo(tmp_path).read_text(encoding="utf-8")
    p = _texto(tmp_path, "r_extra.tsv", tabela + "SAMN99999999\tAngus\tAngus\n")
    _reprova(formatos.vcf_e_rotulos(_exige(BCF14), p), "ausente(s) do cabeçalho do VCF")


def test_vcf_e_rotulos_ordem_das_falhas(tmp_path):
    """L19: sem GT reporta só o FORMAT/GT, mesmo com amostras ausentes."""
    sem_gt = _vcf_com_amostras(tmp_path, ["X1", "X2"], gt=False)
    v = formatos.vcf_e_rotulos(sem_gt, _rotulos(tmp_path, {"A": 10, "B": 10}))
    assert not v.ok and len(v.falhas) == 1 and "FORMAT/GT" in v.falhas[0]


def test_vcf_e_rotulos_vcf_ilegivel_e_rotulos_ruins(tmp_path):
    ruim = tmp_path / "ilegivel.vcf.gz"
    ruim.write_bytes(b"\x1f\x8b\x08\x00" + b"\x00" * 100)
    _reprova(formatos.vcf_e_rotulos(ruim, _rotulos(tmp_path, {"A": 10, "B": 10})), "vcf:")
    _reprova(formatos.vcf_e_rotulos(DEMO_VCF_GEN, _rotulos(tmp_path, {"A": 9, "B": 10}, nome="r9.tsv")), "rotulos:")


def test_ler_vcf_guarda_as_amostras():
    assert formatos._ler_vcf(DEMO_VCF_GEN)["amostras"] == ()
    if BCF14.exists():
        assert len(formatos._ler_vcf(BCF14)["amostras"]) == 1842


# ===================================================================== sincronia ML
def test_colunas_do_ml_em_sincronia():
    assert formatos.COLUNAS_DE_NIVEL["rotulos_amostras"] == ("grupo",)
    assert formatos.COLUNAS_CHAVE["rotulos_amostras"] == ("amostra",)
    for f in ("rotulos_amostras", "lista_amostras", "matriz_marcadores", "rotulos_grupos", "tabela_shap",
              "tabela_nucleo_estavel"):
        assert f in formatos.VALIDADORES and f in formatos.EXTENSOES, f
    assert formatos.EXTENSOES["lista_amostras"] == ".txt"
    assert formatos.FORMATOS_CHECAGEM["vcf_e_rotulos"] == {"vcf": "vcf_indexado", "rotulos": "rotulos_amostras"}
