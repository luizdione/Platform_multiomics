"""vep_anotacao.py — anotação funcional com Ensembl VEP (offline, cache local).

Roda na fila "vep" (imagem omica-vep, pinada em release_110.1): o último release
com bos_taurus em ARS-UCD1.2 — o VEP/cache atuais usam ARS-UCD2.0, incompatível
com os VCFs Run 8 (mesma armadilha do REST, documentada em 2026-09-02).
Cache offline: E:\\data\\omica\\refs\\vep (bos_taurus_vep_110_ARS-UCD1.2).

Uso:
  python3 modules/genomica/scripts/vep_anotacao.py \
      --entrada <vcf> --saida <dir> [--cache /data/omica/refs/vep]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from collections import Counter
from pathlib import Path

from _comum import escrever_resumo, log


def main() -> None:
    p = argparse.ArgumentParser(description="Anotação Ensembl VEP offline")
    p.add_argument("--entrada", required=True)
    p.add_argument("--saida", required=True)
    p.add_argument("--cache", default="/data/omica/refs/vep")
    p.add_argument("--especie", default="bos_taurus")
    args = p.parse_args()

    entrada, saida, cache = Path(args.entrada), Path(args.saida), Path(args.cache)
    if not entrada.exists():
        raise SystemExit(f"entrada não encontrada: {entrada} (rode a extração antes)")
    if not (cache / args.especie).exists():
        raise SystemExit(f"cache VEP ausente: {cache / args.especie} "
                         "(baixar bos_taurus_vep_110_ARS-UCD1.2 para refs/vep)")
    saida.mkdir(parents=True, exist_ok=True)

    tabela = saida / "vep.tsv"
    # --cache_version 110: binário moderno + cache pinado no último release
    # bos_taurus em ARS-UCD1.2 (ver docstring/Dockerfile.vep).
    cmd = ["vep", "-i", str(entrada), "--offline", "--cache",
           "--dir_cache", str(cache), "--cache_version", "110",
           "--species", args.especie,
           "--tab", "-o", str(tabela), "--stats_text", "--force_overwrite",
           "--no_check_variants_order"]
    log("  $ " + " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write((r.stderr or "")[-2000:])
        raise SystemExit(f"VEP falhou ({r.returncode})")

    consequencias: Counter[str] = Counter()
    variantes: set[str] = set()
    idx_cons = None
    with tabela.open(encoding="utf-8") as f:
        for linha in f:
            if linha.startswith("##"):
                continue
            campos = linha.rstrip("\n").split("\t")
            if linha.startswith("#"):
                idx_cons = campos.index("Consequence")
                continue
            if idx_cons is None or len(campos) <= idx_cons:
                continue
            variantes.add(campos[0])
            for c in campos[idx_cons].split(","):
                consequencias[c] += 1

    top = consequencias.most_common(15)
    efeitos = saida / "efeitos_vep.tsv"
    with efeitos.open("w", encoding="utf-8") as f:
        f.write("CONSEQUENCIA\tN\n")
        for cons, n in top:
            f.write(f"{cons}\t{n}\n")
    log(f"Variantes anotadas (VEP): {len(variantes)}; consequências distintas: {len(consequencias)}")

    escrever_resumo(saida, "vep_resumo.json", {
        "entrada": entrada.name,
        "release": "110 (ARS-UCD1.2 — pinado de propósito; ver docstring)",
        "n_variantes_anotadas": len(variantes),
        "n_consequencias_distintas": len(consequencias),
        "consequencia_mais_comum": top[0][0] if top else None,
        "tabela_vep": str(tabela),
        "tabela_efeitos": str(efeitos),
    })


if __name__ == "__main__":
    main()
