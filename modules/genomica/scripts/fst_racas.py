"""fst_racas.py — Fst (Hudson) entre duas raças com plink2, sobre os BCFs reais.

Migração curada do contraste taurino×indicino da genomes_platform (Fst
Weir&Cockerham/Hudson via plink2). Usa as listas REAIS de BioSamples de
E:\\data\\1000bg_metadata (mesmos IDs SAMEA*/SAMN* do header dos BCFs Run 8) —
o script primeiro INTERSECTA as listas com as amostras presentes no arquivo e
reporta quantas entram de cada lado.

Rótulos de raça (corrigidos em 2026-10-19): as listas vigentes vêm do campo Breed
do BioSample e são conferidas por E:\\data\\1000bg_metadata\\validar_rotulos.py
(ver LEIA-ME_ROTULOS.md). As listas antigas samples_nelore/samples_angus (112 ×
385) tinham os rótulos trocados e foram arquivadas; o par válido da demo é
Angus (102) × Brahman (55).

Uso:
  python3 modules/genomica/scripts/fst_racas.py \
      --vcf /data/1000bg_vcf/Chr14-Run8-TAUIND-public.bcf \
      --regiao 14:1-2500000 \
      --grupo-a Angus=/data/1000bg_metadata/samples_angus.txt \
      --grupo-b Brahman=/data/1000bg_metadata/samples_brahman.txt \
      --saida <dir>
"""
from __future__ import annotations

import argparse
import math
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


def top_por_fst(colunas: list[str], corpo: list[str], top: int, min_obs: int = 1
                ) -> tuple[list[str], int, int, int, int]:
    """Top-N variantes por Fst, DESCARTANDO o que não é comparável.

    ARMADILHA que este código existe para evitar: float("nan") NÃO levanta
    ValueError — Python aceita a string "nan". A versão anterior ordenava com
    chaves NaN, e como toda comparação com NaN é falsa a ordem saiu incoerente:
    no primeiro contraste real (o então Angus×Nelore, 497 amostras; as listas de
    raça dele foram invalidadas em 2026-10-19, mas o defeito não depende do par de
    raças), 4 das 5 primeiras linhas do "top 20" eram variantes com OBS_CT=0
    (nenhum genótipo observado) e Fst NaN, com o maior Fst verdadeiro (0,53)
    aparecendo só na 5ª linha.

    Descartar só OBS_CT==0 NÃO basta (revisão de 2026-09-14): no mesmo arquivo,
    uma variante com 4 genótipos observados de 497 tem Fst=1,0 e passaria a
    encabeçar a tabela publicada — o mesmo sintoma do bug do NaN (variante sem
    informação no topo), agora com um número que se lê como "fixação completa
    entre raças". Daí o limiar `min_obs`, declarado no resumo.

    Devolve (linhas_top, n_comparaveis, n_nao_finito, n_sem_obs, n_baixa_cobertura).
    """
    idx_fst = colunas.index([c for c in colunas if "FST" in c.upper()][0])
    idx_obs = colunas.index("OBS_CT") if "OBS_CT" in colunas else None
    avaliaveis: list[tuple[float, str]] = []
    n_nao_finito = n_sem_obs = n_baixa_cobertura = 0
    for linha in corpo:
        campos = linha.split()
        if idx_obs is not None:
            try:
                obs = int(float(campos[idx_obs]))
            except (ValueError, IndexError):
                n_sem_obs += 1
                continue
            if obs == 0:
                n_sem_obs += 1
                continue
            if obs < min_obs:
                n_baixa_cobertura += 1
                continue
        try:
            valor = float(campos[idx_fst])
        except (ValueError, IndexError):
            n_nao_finito += 1
            continue
        if not math.isfinite(valor):
            n_nao_finito += 1
            continue
        avaliaveis.append((valor, linha))
    avaliaveis.sort(key=lambda x: x[0], reverse=True)
    return ([l for _, l in avaliaveis[:top]], len(avaliaveis), n_nao_finito,
            n_sem_obs, n_baixa_cobertura)


def main() -> None:
    p = argparse.ArgumentParser(description="Fst Hudson entre 2 grupos (plink2)")
    p.add_argument("--vcf", required=True, help="VCF/BCF com genótipos (Run 8)")
    p.add_argument("--regiao", default="", help="crom:ini-fim (ex.: 14:1-2500000); vazio = arquivo todo")
    p.add_argument("--grupo-a", required=True, help="Nome=/caminho/lista_biosamples.txt")
    p.add_argument("--grupo-b", required=True)
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--min-obs", type=int, default=None,
                   help="mínimo de genótipos observados por variante "
                        "(padrão: metade da coorte)")
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
    # plink2 nomeia a saída pelos grupos (fst.<A>.<B>.fst.var). Sem limpar, um
    # contraste ANTERIOR no mesmo diretório seria lido como se fosse este.
    for fossil in list(saida.glob("fst*.fst.var")) + list(saida.glob("fst*.fst.summary")):
        fossil.unlink()
    rodar(cmd)

    resumo_plink = next(saida.glob("fst*.fst.summary"), None)
    var_plink = next(saida.glob("fst*.fst.var"), None)
    if resumo_plink is None or var_plink is None:
        raise SystemExit("plink2 não produziu .fst.summary/.fst.var — ver fst.log")
    fst_global = None
    for linha in resumo_plink.read_text(encoding="utf-8").splitlines()[1:]:
        campos = linha.split()
        if len(campos) >= 3:
            try:
                fst_global = float(campos[-1])
            except ValueError:
                pass
    if fst_global is None or not math.isfinite(fst_global):
        raise SystemExit(f"Fst global ausente ou não finito em {resumo_plink.name}")

    top_tsv = saida / "fst_top.tsv"
    linhas_var = var_plink.read_text(encoding="utf-8").splitlines()
    cab, corpo = linhas_var[0], linhas_var[1:]
    n_variantes = len(corpo)
    colunas = cab.split()
    n_coorte = len(usa_a) + len(usa_b)
    min_obs = args.min_obs if args.min_obs is not None else max(2, math.ceil(0.5 * n_coorte))
    melhores, n_comparaveis, n_nao_finito, n_sem_obs, n_baixa = top_por_fst(
        colunas, corpo, args.top, min_obs)
    if not n_comparaveis:
        raise SystemExit("nenhuma variante com Fst finito — contraste sem informação útil")
    with top_tsv.open("w", encoding="utf-8") as f:
        f.write("\t".join(colunas) + "\n")
        for l in melhores:
            f.write("\t".join(l.split()) + "\n")

    log(f"Fst global ({nome_a}×{nome_b}, Hudson): {fst_global}; variantes avaliadas: {n_variantes}")
    log(f"Com Fst finito e OBS_CT≥{min_obs}: {n_comparaveis} — descartadas "
        f"{n_nao_finito} não finitas (NaN), {n_sem_obs} sem genótipo observado "
        f"(OBS_CT=0) e {n_baixa} com cobertura abaixo do limiar")
    escrever_resumo(saida, "fst_resumo.json", {
        "arquivo": vcf.name,
        "regiao": args.regiao or "arquivo inteiro",
        "grupo_a": {"nome": nome_a, "n_amostras": len(usa_a)},
        "grupo_b": {"nome": nome_b, "n_amostras": len(usa_b)},
        "metodo": "Hudson (plink2 --fst report-variants)",
        "fst_global": fst_global,
        "n_variantes": n_variantes,
        "n_variantes_fst_finito": n_comparaveis,
        "n_descartadas_nan": n_nao_finito,
        "n_descartadas_sem_genotipo": n_sem_obs,
        "n_descartadas_baixa_cobertura": n_baixa,
        "min_obs_efetivo": min_obs,
        "nota_top": (f"top ordenado apenas entre variantes com Fst finito e "
                     f"OBS_CT ≥ {min_obs} (metade da coorte de {n_coorte} amostras)"),
        "top_tsv": str(top_tsv),
    })


if __name__ == "__main__":
    main()
