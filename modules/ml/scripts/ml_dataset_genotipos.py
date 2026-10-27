"""ml_dataset_genotipos.py — monta a matriz de genótipos 0/1/2 para o módulo ML.

Extrai, do BCF real do 1000 Bull Genomes, os genótipos das amostras de dois
grupos (listas de BioSamples) numa região, filtrando por MAF e AFINANDO a grade
(1 variante a cada `passo`) para manter o dataset didático e rápido. Roda na
fila "bioinfo" (bcftools no PATH); o passo seguinte (classificação) consome a
matriz na fila "ml".

Rótulos de raça (corrigidos em 2026-10-19): usar as listas de
E:\\data\\1000bg_metadata (campo Breed do BioSample, conferidas por
validar_rotulos.py). O par antigo Angus×Nelore (112 × 385) tinha rótulos
inválidos; o par válido da demo é Angus (102) × Brahman (55).

Uso:
  python3 modules/ml/scripts/ml_dataset_genotipos.py \
      --vcf /data/1000bg_vcf/Chr14-Run8-TAUIND-public.bcf --regiao 14:1-2500000 \
      --grupo-a Angus=/data/1000bg_metadata/samples_angus.txt \
      --grupo-b Brahman=/data/1000bg_metadata/samples_brahman.txt \
      --maf 0.2 --passo 15 --saida <dir>
"""
from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

from _comum import escrever_resumo, log, rodar

GT_MAP = {"0/0": "0", "0|0": "0", "1/1": "2", "1|1": "2",
          "0/1": "1", "1/0": "1", "0|1": "1", "1|0": "1"}


def _ler_grupo(arg: str) -> tuple[str, list[str]]:
    nome, _, caminho = arg.partition("=")
    arq = Path(caminho)
    if not nome or not arq.exists():
        raise SystemExit(f"grupo inválido: {arg!r} (formato Nome=/caminho/lista.txt)")
    return nome, sorted({l.strip() for l in arq.read_text(encoding="utf-8").splitlines() if l.strip()})


def main() -> None:
    p = argparse.ArgumentParser(description="Matriz de genótipos 0/1/2 para ML")
    p.add_argument("--vcf", required=True)
    p.add_argument("--regiao", required=True)
    p.add_argument("--grupo-a", required=True)
    p.add_argument("--grupo-b", required=True)
    p.add_argument("--maf", type=float, default=0.2, help="MAF mínimo (variantes informativas)")
    p.add_argument("--passo", type=int, default=15, help="mantém 1 variante a cada N (afinamento)")
    p.add_argument("--saida", required=True)
    args = p.parse_args()

    vcf, saida = Path(args.vcf), Path(args.saida)
    if not vcf.exists():
        raise SystemExit(f"VCF/BCF não encontrado: {vcf}")
    saida.mkdir(parents=True, exist_ok=True)
    nome_a, ids_a = _ler_grupo(args.grupo_a)
    nome_b, ids_b = _ler_grupo(args.grupo_b)

    no_arquivo = set(rodar(["bcftools", "query", "-l", str(vcf)]).split())
    usa_a = [s for s in ids_a if s in no_arquivo]
    usa_b = [s for s in ids_b if s in no_arquivo]
    amostras = usa_a + usa_b
    log(f"{nome_a}: {len(usa_a)} amostras; {nome_b}: {len(usa_b)}; MAF>={args.maf}; passo {args.passo}")
    if len(usa_a) < 10 or len(usa_b) < 10:
        raise SystemExit("amostras insuficientes para ML (mínimo 10 por grupo)")

    lista = saida / "amostras.txt"
    lista.write_text("\n".join(amostras) + "\n", encoding="utf-8", newline="\n")

    proc = subprocess.Popen(
        ["bcftools", "view", "-r", args.regiao, "-S", str(lista),
         "-q", f"{args.maf}:minor", "-m2", "-M2", "-v", "snps", "-Ou", str(vcf)],
        stdout=subprocess.PIPE)
    query = subprocess.Popen(
        ["bcftools", "query", "-f", "%CHROM:%POS[\t%GT]\n"],
        stdin=proc.stdout, stdout=subprocess.PIPE, text=True)
    assert proc.stdout is not None and query.stdout is not None
    proc.stdout.close()

    matriz = saida / "genotipos.tsv"
    n_vistas = n_mantidas = 0
    with matriz.open("w", encoding="utf-8", newline="\n") as f:
        f.write("variante\t" + "\t".join(amostras) + "\n")
        for linha in query.stdout:
            n_vistas += 1
            if (n_vistas - 1) % args.passo:
                continue
            campos = linha.rstrip("\n").split("\t")
            gts = [GT_MAP.get(re.sub(r":.*", "", g), "NA") for g in campos[1:]]
            f.write(campos[0] + "\t" + "\t".join(gts) + "\n")
            n_mantidas += 1
    if query.wait() != 0 or proc.wait() != 0:
        raise SystemExit("bcftools falhou na extração de genótipos")

    rotulos = saida / "rotulos.tsv"
    with rotulos.open("w", encoding="utf-8", newline="\n") as f:
        f.write("amostra\tgrupo\n")
        for s in usa_a:
            f.write(f"{s}\t{nome_a}\n")
        for s in usa_b:
            f.write(f"{s}\t{nome_b}\n")

    log(f"Variantes na região com MAF>={args.maf}: {n_vistas}; mantidas (1/{args.passo}): {n_mantidas}")
    escrever_resumo(saida, "dataset_resumo.json", {
        "vcf": vcf.name, "regiao": args.regiao,
        "grupo_a": {"nome": nome_a, "n": len(usa_a)},
        "grupo_b": {"nome": nome_b, "n": len(usa_b)},
        "maf_minimo": args.maf, "passo": args.passo,
        "n_variantes_candidatas": n_vistas, "n_variantes_matriz": n_mantidas,
        "matriz_tsv": str(matriz), "rotulos_tsv": str(rotulos),
    })


if __name__ == "__main__":
    main()
