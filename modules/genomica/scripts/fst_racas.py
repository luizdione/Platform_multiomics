"""fst_racas.py — Fst (Hudson) entre duas raças com plink2, sobre os BCFs reais.

Migração curada do contraste taurino×indicino da genomes_platform (Fst
Weir&Cockerham/Hudson via plink2). Usa as listas REAIS de BioSamples de
E:\\data\\1000bg_metadata (mesmos IDs SAMEA*/SAMN* do header dos BCFs Run 8) —
o script primeiro INTERSECTA as listas com as amostras presentes no arquivo e
reporta quantas entram de cada lado.

Uso:
  python3 modules/genomica/scripts/fst_racas.py \
      --vcf /data/1000bg_vcf/Chr14-Run8-TAUIND-public.bcf \
      --regiao 14:1-2500000 \
      --grupo-a Angus=/data/1000bg_metadata/samples_angus.txt \
      --grupo-b Nelore=/data/1000bg_metadata/samples_nelore.txt \
      --saida <dir>
"""
from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

from _comum import escrever_resumo, log, rodar


def _ler_grupo(arg: str) -> tuple[str, set[str]]:
    nome, _, caminho = arg.partition("=")
    arq = Path(caminho)
    if not nome or not caminho or not arq.exists():
        raise SystemExit(f"grupo inválido ou arquivo ausente: {arg!r} (formato Nome=/caminho/lista.txt)")
    ids = {l.strip() for l in arq.read_text(encoding="utf-8").splitlines() if l.strip()}
    return nome, ids


def main() -> None:
    p = argparse.ArgumentParser(description="Fst Hudson entre 2 grupos (plink2)")
    p.add_argument("--vcf", required=True, help="VCF/BCF com genótipos (Run 8)")
    p.add_argument("--regiao", default="", help="crom:ini-fim (ex.: 14:1-2500000); vazio = arquivo todo")
    p.add_argument("--grupo-a", required=True, help="Nome=/caminho/lista_biosamples.txt")
    p.add_argument("--grupo-b", required=True)
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--saida", required=True)
    args = p.parse_args()

    vcf, saida = Path(args.vcf), Path(args.saida)
    if not vcf.exists():
        raise SystemExit(f"VCF/BCF não encontrado: {vcf}")
    saida.mkdir(parents=True, exist_ok=True)
    nome_a, ids_a = _ler_grupo(args.grupo_a)
    nome_b, ids_b = _ler_grupo(args.grupo_b)

    no_arquivo = set(rodar(["bcftools", "query", "-l", str(vcf)]).split())
    usa_a, usa_b = sorted(ids_a & no_arquivo), sorted(ids_b & no_arquivo)
    log(f"{nome_a}: {len(usa_a)}/{len(ids_a)} amostras no arquivo; "
        f"{nome_b}: {len(usa_b)}/{len(ids_b)}")
    if len(usa_a) < 2 or len(usa_b) < 2:
        raise SystemExit("amostras insuficientes após interseção (ver 1000bg_metadata)")

    pheno = saida / "grupos.pheno"
    with pheno.open("w", encoding="utf-8", newline="\n") as f:
        f.write("#IID\tRACA\n")
        for sid in usa_a:
            f.write(f"{sid}\t{nome_a}\n")
        for sid in usa_b:
            f.write(f"{sid}\t{nome_b}\n")

    prefixo = saida / "fst"
    cmd = ["plink2", "--bcf" if vcf.suffix == ".bcf" else "--vcf", str(vcf),
           "--keep", str(pheno), "--pheno", str(pheno), "--pheno-name", "RACA",
           "--fst", "RACA", "method=hudson", "report-variants",
           "--allow-extra-chr", "--chr-set", "29",
           "--out", str(prefixo)]
    if args.regiao:
        m = re.match(r"^([\w.]+):(\d+)-(\d+)$", args.regiao)
        if not m:
            raise SystemExit(f"região inválida: {args.regiao!r} (use crom:ini-fim)")
        cmd += ["--chr", m.group(1), "--from-bp", m.group(2), "--to-bp", m.group(3)]
    rodar(cmd)

    resumo_plink = next(saida.glob("fst*.fst.summary"), None)
    var_plink = next(saida.glob("fst*.fst.var"), None)
    fst_global = None
    if resumo_plink:
        for linha in resumo_plink.read_text(encoding="utf-8").splitlines()[1:]:
            campos = linha.split()
            if len(campos) >= 3:
                try:
                    fst_global = float(campos[-1])
                except ValueError:
                    pass
    top_tsv = saida / "fst_top.tsv"
    n_variantes = 0
    if var_plink:
        linhas_var = var_plink.read_text(encoding="utf-8").splitlines()
        cab, corpo = linhas_var[0], linhas_var[1:]
        n_variantes = len(corpo)
        idx_fst = cab.split().index([c for c in cab.split() if "FST" in c.upper()][0])

        def _fst(l: str) -> float:
            try:
                return float(l.split()[idx_fst])
            except (ValueError, IndexError):
                return float("-inf")

        melhores = sorted(corpo, key=_fst, reverse=True)[: args.top]
        with top_tsv.open("w", encoding="utf-8") as f:
            f.write("\t".join(cab.split()) + "\n")
            for l in melhores:
                f.write("\t".join(l.split()) + "\n")

    log(f"Fst global ({nome_a}×{nome_b}, Hudson): {fst_global}; variantes avaliadas: {n_variantes}")
    escrever_resumo(saida, "fst_resumo.json", {
        "arquivo": vcf.name,
        "regiao": args.regiao or "arquivo inteiro",
        "grupo_a": {"nome": nome_a, "n_amostras": len(usa_a)},
        "grupo_b": {"nome": nome_b, "n_amostras": len(usa_b)},
        "metodo": "Hudson (plink2 --fst report-variants)",
        "fst_global": fst_global,
        "n_variantes": n_variantes,
        "top_tsv": str(top_tsv),
    })


if __name__ == "__main__":
    main()
