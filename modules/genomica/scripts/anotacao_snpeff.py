"""anotacao_snpeff.py — anotação funcional de variantes com SnpEff.

Migração curada (Fase 2) que CORRIGE o bug da plataforma antiga: o caminho do
jar é `/opt/snpEff/snpEff.jar` (S maiúsculo — em Linux o case importa; o módulo
legado chamava /opt/snpeff e falhava sempre). O banco custom fica FORA da
imagem, em {dados_pesados}/snpeff_data (E:\\data\\snpeff_data no host), passado
via -dataDir.

Uso:
  python3 modules/genomica/scripts/anotacao_snpeff.py \
      --entrada <vcf> --saida <dir> [--db ARS-UCD1.3.113] [--datadir /data/snpeff_data]
"""
from __future__ import annotations

import argparse
import gzip
import subprocess
import sys
from collections import Counter
from pathlib import Path

from _comum import escrever_resumo, log

SNPEFF_JAR = "/opt/snpEff/snpEff.jar"  # case correto — ver docstring


def main() -> None:
    p = argparse.ArgumentParser(description="Anotação funcional SnpEff")
    p.add_argument("--entrada", required=True)
    p.add_argument("--saida", required=True)
    p.add_argument("--db", default="ARS-UCD1.3.113")
    p.add_argument("--datadir", default="/data/snpeff_data")
    p.add_argument("--xmx", default="4g")
    args = p.parse_args()

    entrada, saida = Path(args.entrada), Path(args.saida)
    if not entrada.exists():
        raise SystemExit(f"entrada não encontrada: {entrada} (rode a extração antes)")
    if not Path(SNPEFF_JAR).exists():
        raise SystemExit(f"SnpEff não encontrado em {SNPEFF_JAR} (rodar na imagem omica-bioinfo)")
    if not (Path(args.datadir) / args.db).exists():
        raise SystemExit(f"banco {args.db} não encontrado em {args.datadir}")
    saida.mkdir(parents=True, exist_ok=True)

    anotado = saida / "anotado.vcf.gz"
    log(f"SnpEff {args.db} sobre {entrada.name}…")
    # Banco custom fora do snpEff.config da imagem: registra o genome na própria
    # chamada (-configOption) — o legado nunca fez isso e o endpoint dele nunca
    # rodou (config sem a entrada ARS-UCD1.3.113.genome, verificado 2026-09-02).
    cmd = ["java", f"-Xmx{args.xmx}", "-jar", SNPEFF_JAR, "ann",
           "-dataDir", args.datadir, "-noStats",
           "-configOption", f"{args.db}.genome={args.db}",
           args.db, str(entrada)]
    log("  $ " + " ".join(cmd))
    with gzip.open(anotado, "wt", encoding="utf-8") as destino:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        efeitos: Counter[str] = Counter()
        n_var = 0
        assert proc.stdout is not None
        for linha in proc.stdout:
            destino.write(linha)
            if linha.startswith("#"):
                continue
            n_var += 1
            if "ANN=" in linha:
                ann = linha.split("ANN=", 1)[1].split(";", 1)[0].split("\t", 1)[0]
                primeiro = ann.split(",", 1)[0].split("|")
                if len(primeiro) > 1:
                    efeitos[primeiro[1]] += 1
        rc = proc.wait()
        if rc != 0:
            sys.stderr.write((proc.stderr.read() or "")[-2000:])
            raise SystemExit(f"SnpEff falhou ({rc})")

    top = efeitos.most_common(15)
    tsv = saida / "efeitos.tsv"
    with tsv.open("w", encoding="utf-8") as f:
        f.write("EFEITO\tN\n")
        for efeito, n in top:
            f.write(f"{efeito}\t{n}\n")
    log(f"Variantes anotadas: {n_var}; efeitos distintos: {len(efeitos)}")

    escrever_resumo(saida, "anotacao_resumo.json", {
        "entrada": entrada.name,
        "banco": args.db,
        "n_variantes_anotadas": n_var,
        "n_efeitos_distintos": len(efeitos),
        "efeito_mais_comum": top[0][0] if top else None,
        "vcf_anotado": str(anotado),
        "tabela_tsv": str(tsv),
    })


if __name__ == "__main__":
    main()
