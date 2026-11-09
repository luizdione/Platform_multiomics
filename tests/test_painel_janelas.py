"""painel_janelas.py (F6, ESPEC §3.2.2): painel de genes JSON → BED de janelas ±pad.

Controle: com o painel_108.json e pad 15000 a saída é byte a byte o painel_108.bed versionado
(sha256 170e413f…, decisão 8 da ESPEC) e passa no validador da porta (painel_bed). Canários:
pad negativo; e as três regras da linha — ordem com o cromossomo como TEXTO, início nunca
negativo, ensembl_id opcional (no painel_genes_json o campo pode faltar).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from omica.config import REPO_ROOT
from omica.contrato import formatos

SCRIPTS = REPO_ROOT / "modules" / "epigenomica" / "scripts"
SCRIPT = SCRIPTS / "painel_janelas.py"
PAINEL = REPO_ROOT / "modules" / "epigenomica" / "data" / "paineis" / "painel_108.json"
SHA_BED_108 = "170e413f7643fbf18d754068677d3db6ac95fba397d583e5162a8b575d95e757"


def rodar(painel: Path, pad: str, saida: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), "--painel", str(painel), "--pad", pad, "--saida", str(saida)],
                          capture_output=True, text=True, encoding="utf-8", cwd=REPO_ROOT,
                          env={**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})


def _painel(tmp_path: Path, genes: dict) -> Path:
    arq = tmp_path / "painel.json"
    arq.write_text(json.dumps({"genes": genes}), encoding="utf-8")
    return arq


def test_controle_painel_108_igual_ao_bed_versionado(tmp_path):
    r = rodar(PAINEL, "15000", tmp_path)
    assert r.returncode == 0, r.stderr
    bed = tmp_path / "janelas.bed"
    assert hashlib.sha256(bed.read_bytes()).hexdigest() == SHA_BED_108
    resumo = json.loads((tmp_path / "janelas_resumo.json").read_text(encoding="utf-8"))
    assert {k: v for k, v in resumo.items() if k != "gerado_em"} == {
        "n_genes": 108, "n_janelas": 108, "pad_pb": 15000, "n_fita_negativa": 45, "n_sem_ensembl": 17,
        "n_cromossomos": 27}
    v = formatos.validar("painel_bed", bed)
    assert v.ok and v.perfil["n_janelas"] == 108, v.falhas
    # os números do resumo batem com o que o validador do painel conta na entrada
    vp = formatos.validar("painel_genes_json", PAINEL)
    assert (vp.perfil["n_genes"], vp.perfil["n_fita_negativa"], vp.perfil["n_sem_ensembl"]) == (108, 45, 17)


def test_canario_pad_negativo(tmp_path):
    r = rodar(PAINEL, "-1", tmp_path)
    assert r.returncode != 0 and "--pad negativo" in r.stderr, r.stderr
    assert not (tmp_path / "janelas.bed").exists()


def test_regras_da_linha(tmp_path):
    """Três genes que exercitam as três regras: '10' antes de '2' (texto, como o BED
    versionado), início − pad negativo vira 0, ensembl_id ausente vira coluna vazia."""
    genes = {
        "GENE_B": {"cromossomo": 2, "inicio": 5000, "fim": 6000, "fita": 1, "ensembl_id": "ENSBTAG1"},
        "GENE_A": {"cromossomo": "10", "inicio": 40000, "fim": 41000, "fita": -1},
        "GENE_C": {"cromossomo": 2, "inicio": 100, "fim": 900, "fita": 1, "ensembl_id": ""},
    }
    painel = _painel(tmp_path, genes)
    assert formatos.validar("painel_genes_json", painel).ok
    r = rodar(painel, "1000", tmp_path / "s")
    assert r.returncode == 0, r.stderr
    bed = (tmp_path / "s" / "janelas.bed").read_bytes()
    assert bed == (b"10\t39000\t42000\tGENE_A\t\n"
                   b"2\t0\t1900\tGENE_C\t\n"
                   b"2\t4000\t7000\tGENE_B\tENSBTAG1\n")
    assert formatos.validar("painel_bed", tmp_path / "s" / "janelas.bed").ok
    resumo = json.loads((tmp_path / "s" / "janelas_resumo.json").read_text(encoding="utf-8"))
    assert (resumo["n_fita_negativa"], resumo["n_sem_ensembl"], resumo["n_cromossomos"]) == (1, 2, 2)


def test_canario_painel_sem_genes(tmp_path):
    arq = tmp_path / "p.json"
    arq.write_text(json.dumps({"_meta": {}}), encoding="utf-8")
    r = rodar(arq, "15000", tmp_path / "s")
    assert r.returncode != 0 and "painel sem 'genes'" in r.stderr, r.stderr
