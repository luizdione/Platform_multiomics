"""integridade_vcf.py — checagem de saúde dos VCFs/BCFs pesados (E:\\data).

Migração curada do check_vcf_integrity.py da genomes_platform: para cada
arquivo do diretório verifica índice (.tbi/.csi), header legível e primeiro
registro legível — checagem LEVE (segundos por cromossomo, não estatística
completa). Gera tabela TSV + resumo JSON.

Uso:
  python3 modules/genomica/scripts/integridade_vcf.py --dir /data/1000bg_vcf --saida <dir>
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from _comum import escrever_resumo, log


def _primeiro_registro_ok(caminho: Path) -> bool:
    proc = subprocess.Popen(["bcftools", "view", "-H", str(caminho)],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        linha = proc.stdout.readline() if proc.stdout else ""
        return bool(linha.strip() and "\t" in linha)
    finally:
        proc.kill()
        proc.wait()


def _header_ok(caminho: Path) -> bool:
    r = subprocess.run(["bcftools", "view", "-h", str(caminho)],
                       capture_output=True, text=True)
    return r.returncode == 0 and "#CHROM" in r.stdout


def _indice(caminho: Path) -> tuple[bool, bool, bool]:
    """(existe, é mais novo que o dado, o bcftools consegue USAR).

    Conferir só a existência do .tbi/.csi é falso-verde: `bcftools view -H` lê o
    arquivo em sequência e nunca toca no índice, então índice velho (gerado antes
    de o VCF ser reescrito) ou truncado passava como "OK" — e só quebrava na hora
    da consulta por região, no meio de um pipeline.
    """
    candidatos = [Path(str(caminho) + s) for s in (".tbi", ".csi")]
    candidatos += [caminho.with_suffix(caminho.suffix + s) for s in (".tbi", ".csi")]
    achado = next((c for c in candidatos if c.exists()), None)
    if achado is None:
        return False, False, False
    atual = achado.stat().st_mtime >= caminho.stat().st_mtime
    r = subprocess.run(["bcftools", "index", "--stats", str(caminho)],
                       capture_output=True, text=True)
    return True, atual, (r.returncode == 0 and bool(r.stdout.strip()))


def main() -> None:
    p = argparse.ArgumentParser(description="Integridade leve de VCFs/BCFs + índices")
    p.add_argument("--dir", required=True, help="diretório com *.vcf.gz / *.bcf")
    p.add_argument("--saida", required=True)
    args = p.parse_args()

    pasta, saida = Path(args.dir), Path(args.saida)
    if not pasta.exists():
        raise SystemExit(f"diretório não encontrado: {pasta}")
    saida.mkdir(parents=True, exist_ok=True)

    arquivos = sorted(list(pasta.glob("*.vcf.gz")) + list(pasta.glob("*.bcf")))
    if not arquivos:
        raise SystemExit(f"nenhum VCF/BCF em {pasta}")
    log(f"Checando {len(arquivos)} arquivos em {pasta}")

    linhas, problemas = [], []
    for arq in arquivos:
        indice, idx_atual, idx_usavel = _indice(arq)
        header = _header_ok(arq)
        registro = _primeiro_registro_ok(arq) if header else False
        ok = indice and idx_atual and idx_usavel and header and registro
        if not ok:
            problemas.append(arq.name)
        linhas.append((arq.name, arq.suffix.lstrip("."),
                       round(arq.stat().st_size / 1e9, 2),
                       "sim" if indice else "NAO",
                       "sim" if idx_atual else "VELHO",
                       "sim" if idx_usavel else "NAO",
                       "sim" if header else "NAO",
                       "sim" if registro else "NAO",
                       "OK" if ok else "PROBLEMA"))
        log(f"  {arq.name}: {'OK' if ok else 'PROBLEMA'}")

    tsv = saida / "integridade.tsv"
    with tsv.open("w", encoding="utf-8") as f:
        f.write("ARQUIVO\tFORMATO\tGB\tINDICE\tINDICE_ATUAL\tINDICE_USAVEL\t"
                "HEADER\tREGISTRO\tVEREDITO\n")
        for l in linhas:
            f.write("\t".join(str(x) for x in l) + "\n")

    escrever_resumo(saida, "integridade_resumo.json", {
        "diretorio": str(pasta),
        "n_arquivos": len(arquivos),
        "n_ok": len(arquivos) - len(problemas),
        "n_problemas": len(problemas),
        "checagens": ["indice existe", "indice mais novo que o dado",
                      "indice usável (bcftools index --stats)", "header", "1º registro"],
        "problemas": problemas,
        "tabela_tsv": str(tsv),
    })
    if problemas:
        log(f"ATENÇÃO: {len(problemas)} arquivo(s) com problema — ver integridade.tsv")


if __name__ == "__main__":
    main()
