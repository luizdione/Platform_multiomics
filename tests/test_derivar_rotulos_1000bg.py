"""tools/derivar_rotulos_1000bg.py: a tabela de exemplo do ml_aims_vcf é DERIVADA, nunca digitada
(ESPEC F6 §3.1.2 + adendo v2 §A8.2, críticas L10/L14).

Controle real (skip sem o E:\\data): a derivação nova a partir da fonte que a espécie declara
(`recursos.rotulos_raca`, md5 f5cbeca4…) é IGUAL ao arquivo versionado e à proveniência (menos a
data); 820 linhas, 16 grupos, Holstein 187, cabeçalho de 3 colunas sem `genetic_pole`; Angus e
Brahman são os conjuntos do rotulos.tsv selado.

Canários (sintéticos, rodam em qualquer máquina), cada um com o controle que passa: fonte em
`nunca_usar`, colisão de nomes depois do saneamento, nível que `_problema_nivel` recusa e a
conferência que acusa o arquivo versionado alterado.
"""
from __future__ import annotations

import csv
import importlib.util
import json
from collections import Counter
from pathlib import Path

import pytest

from omica.config import REPO_ROOT, reset_config_cache
from omica.contrato import formatos

E_DATA = Path(r"E:\data")
VERDADE_REAL = E_DATA / "1000bg_metadata" / "proveniencia_2026-10-13" / "rotulos_corrigidos_2026-10-13.tsv"
ROTULOS_SELADO = E_DATA / "omica" / "results" / "ml" / "demo" / "dataset" / "rotulos.tsv"
TABELA = REPO_ROOT / "modules" / "ml" / "data" / "rotulos_1000bg_racas.tsv"
PROV = REPO_ROOT / "modules" / "ml" / "data" / "rotulos_1000bg_racas.proveniencia.json"
MD5_FONTE = "f5cbeca441bded5bc05771dbc237a38d"


@pytest.fixture(scope="module")
def ferramenta():
    spec = importlib.util.spec_from_file_location("derivar_rotulos_1000bg",
                                                  REPO_ROOT / "tools" / "derivar_rotulos_1000bg.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _genome_data(monkeypatch, raiz: Path) -> None:
    monkeypatch.setenv("GENOME_DATA", str(raiz))
    reset_config_cache()


def _verdade(pasta: Path, racas: dict[str, int], nome: str = "verdade.tsv") -> Path:
    """Tabela-verdade sintética com as colunas da real (CRLF, como a real)."""
    pasta.mkdir(parents=True, exist_ok=True)
    linhas = ["sample\tbreed_biosample\tstudy\tgenetic_pole"]
    k = 0
    for raca, n in racas.items():
        for _ in range(n):
            k += 1
            linhas.append(f"SAMN{k:06d}\t{raca}\tPRJ\ttaurine_pole")
    p = pasta / nome
    p.write_bytes(("\r\n".join(linhas) + "\r\n").encode("utf-8"))
    return p


def _rodar(ferramenta, *argv: str) -> tuple[int, str]:
    try:
        return ferramenta.main(list(argv)), ""
    except SystemExit as e:
        return 1, str(e.code)


# ============================================================ controle real
def test_versionado_e_a_derivacao_da_fonte_declarada(ferramenta, monkeypatch, tmp_path):
    if not VERDADE_REAL.exists():
        pytest.skip("tabela-verdade ausente (dado real fora do repositório)")
    _genome_data(monkeypatch, E_DATA)
    valor, no_disco = ferramenta.declarada()
    assert valor == "/data/1000bg_metadata/proveniencia_2026-10-13/rotulos_corrigidos_2026-10-13.tsv"
    assert no_disco.resolve() == VERDADE_REAL.resolve()
    # derivação nova, com o --verdade no default (a espécie), num arquivo novo
    saida = tmp_path / "rotulos_1000bg_racas.tsv"
    assert _rodar(ferramenta, "--saida", str(saida)) == (0, "")
    assert saida.read_bytes() == TABELA.read_bytes().replace(b"\r\n", b"\n")
    nova = json.loads(ferramenta.arquivo_proveniencia(saida).read_text(encoding="utf-8"))
    versionada = json.loads(PROV.read_text(encoding="utf-8"))
    assert {k: v for k, v in nova.items() if k != "data"} == {k: v for k, v in versionada.items() if k != "data"}
    assert versionada["fonte"] == "especies/bos_taurus.yaml#recursos.rotulos_raca"
    assert versionada["fonte_md5"] == MD5_FONTE
    # e a conferência sobre o versionado aprova
    assert _rodar(ferramenta, "--conferir") == (0, "")


def test_tabela_versionada_tem_a_forma_da_espec():
    """820 linhas de dado, 16 grupos, Holstein 187 (a raça declarada, não os 301 da categoria
    reconciliada), cabeçalho de 3 colunas sem genetic_pole, ordem (grupo, amostra), LF no
    arquivo gravado (o autocrlf pode trocar no checkout) e o validador do formato aprovando."""
    bruto = TABELA.read_bytes().replace(b"\r\n", b"\n")
    linhas = bruto.decode("utf-8").split("\n")
    assert linhas[0] == "amostra\tgrupo\tbreed_biosample" and linhas[-1] == ""
    dados = [l.split("\t") for l in linhas[1:-1]]
    assert len(dados) == 820
    n = Counter(g for _, g, _ in dados)
    assert len(n) == 16 and n["Holstein"] == 187 and n["Angus"] == 102 and n["Brahman"] == 55
    assert min(n.values()) >= 10
    assert [(g, a) for a, g, _ in dados] == sorted((g, a) for a, g, _ in dados)
    assert {(g, r) for _, g, r in dados if g != r} == {
        ("N_Dama", "N'Dama"), ("Red_Angus", "Red Angus"), ("Polish_Holstein-Friesian", "Polish Holstein-Friesian")}
    v = formatos.validar("rotulos_amostras", TABELA)
    assert v.ok and v.perfil["n_amostras"] == 820 and v.perfil["n_niveis"] == 16, v.falhas
    prov = json.loads(PROV.read_text(encoding="utf-8"))
    assert prov["n_por_grupo"] == dict(sorted(n.items())) and prov["n_amostras"] == 820 and prov["n_grupos"] == 16
    assert "genetic_pole" not in linhas[0]


def test_angus_e_brahman_sao_os_do_selado():
    if not ROTULOS_SELADO.exists():
        pytest.skip("rotulos.tsv selado ausente (dado real fora do repositório)")
    with ROTULOS_SELADO.open(encoding="utf-8", newline="") as f:
        selado = [(l["amostra"], l["grupo"]) for l in csv.DictReader(f, delimiter="\t")]
    with TABELA.open(encoding="utf-8", newline="") as f:
        exemplo = [(l["amostra"], l["grupo"]) for l in csv.DictReader(f, delimiter="\t")]
    for g in ("Angus", "Brahman"):
        assert {a for a, x in exemplo if x == g} == {a for a, x in selado if x == g}, g


# =========================================================== canários sintéticos
def test_fonte_em_nunca_usar_aborta(ferramenta, monkeypatch, tmp_path):
    """L14: um caminho de `nunca_usar` (rótulos antigos/arquivados) nunca vira fonte, nem como
    arquivo nem dentro de uma pasta proibida. Controle: a mesma tabela fora dali passa."""
    _genome_data(monkeypatch, tmp_path)
    racas = {"Angus": 12, "Brahman": 10}
    proibido = _verdade(tmp_path / "1000bg_vcf", racas, nome="amostras_racas.csv")
    dentro = _verdade(tmp_path / "1000bg_metadata" / "_ARQUIVO_INVALIDOS_rotulos_SAMN_2026-04-28", racas)
    for p in (proibido, dentro):
        rc, msg = _rodar(ferramenta, "--verdade", str(p), "--saida", str(tmp_path / "x.tsv"))
        assert rc == 1 and "nunca_usar" in msg, msg
        assert not (tmp_path / "x.tsv").exists()
    ok = _verdade(tmp_path / "outra", racas)
    assert _rodar(ferramenta, "--verdade", str(ok), "--saida", str(tmp_path / "x.tsv")) == (0, "")
    prov = json.loads((tmp_path / "x.proveniencia.json").read_text(encoding="utf-8"))
    assert prov["fonte"] == "argumento --verdade" and prov["n_por_grupo"] == {"Angus": 12, "Brahman": 10}


def test_fonte_declarada_ausente_aborta(ferramenta, monkeypatch, tmp_path):
    _genome_data(monkeypatch, tmp_path)
    rc, msg = _rodar(ferramenta, "--saida", str(tmp_path / "x.tsv"))
    assert rc == 1 and "não encontrada" in msg


@pytest.mark.parametrize("racas, trecho", [
    ({"Red Angus": 10, "Red_Angus": 11, "Angus": 10}, "colisão de nomes"),
    ({"Red Angus": 10, "Red/Angus": 10, "Angus": 10}, "colisão de nomes"),
    ({"1001": 10, "Angus": 10}, "recusado"),          # o R leria o nível como número
    ({"vs_Angus": 10, "Angus": 10}, "recusado"),      # o contraste do DESeq2 ficaria ambíguo
    ({"-Angus": 10, "Brahman": 10}, "recusado"),      # começa com '-': viraria opção do argv
])
def test_colisao_e_nivel_recusado_abortam(ferramenta, monkeypatch, tmp_path, racas, trecho):
    _genome_data(monkeypatch, tmp_path)
    p = _verdade(tmp_path / "v", racas)
    rc, msg = _rodar(ferramenta, "--verdade", str(p), "--saida", str(tmp_path / "x.tsv"))
    assert rc == 1 and trecho in msg, msg
    assert not (tmp_path / "x.tsv").exists() and not (tmp_path / "x.proveniencia.json").exists()


def test_controle_saneamento_e_corte_de_10(ferramenta, monkeypatch, tmp_path):
    """Controle dos canários acima: nomes que só o saneamento muda (sem colisão) passam; raça com
    9 e raça vazia ficam fora; a saída sai ordenada por (grupo, amostra), com LF."""
    _genome_data(monkeypatch, tmp_path)
    p = _verdade(tmp_path / "v", {"Red Angus": 10, "N'Dama": 11, "Gir": 9, "": 30, "Angus": 10})
    saida = tmp_path / "x.tsv"
    assert _rodar(ferramenta, "--verdade", str(p), "--saida", str(saida)) == (0, "")
    bruto = saida.read_bytes()
    assert b"\r" not in bruto
    linhas = bruto.decode("utf-8").splitlines()
    assert linhas[0] == "amostra\tgrupo\tbreed_biosample"
    assert Counter(l.split("\t")[1] for l in linhas[1:]) == {"Angus": 10, "N_Dama": 11, "Red_Angus": 10}
    chaves = [(l.split("\t")[1], l.split("\t")[0]) for l in linhas[1:]]
    assert chaves == sorted(chaves)


def test_conferir_acusa_versionado_alterado(ferramenta, monkeypatch, tmp_path):
    """--conferir: aprova a derivação intacta (com CRLF do checkout, inclusive) e reprova uma
    linha trocada ou a proveniência mexida."""
    _genome_data(monkeypatch, tmp_path)
    p = _verdade(tmp_path / "v", {"Angus": 10, "Brahman": 12})
    saida = tmp_path / "x.tsv"
    assert _rodar(ferramenta, "--verdade", str(p), "--saida", str(saida))[0] == 0
    conferir = ("--verdade", str(p), "--saida", str(saida), "--conferir")
    texto = saida.read_bytes()
    saida.write_bytes(texto.replace(b"\n", b"\r\n"))
    assert _rodar(ferramenta, *conferir)[0] == 0
    saida.write_bytes(texto.replace(b"\tBrahman\tBrahman\n", b"\tAngus\tBrahman\n", 1))
    assert _rodar(ferramenta, *conferir)[0] == 1
    saida.write_bytes(texto)
    prov = ferramenta.arquivo_proveniencia(saida)
    d = json.loads(prov.read_text(encoding="utf-8"))
    prov.write_text(json.dumps({**d, "fonte_md5": "0" * 32}), encoding="utf-8")
    assert _rodar(ferramenta, *conferir)[0] == 1
