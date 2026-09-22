"""perfil_tss.py — metaperfil de metilação ao redor do TSS dos genes do painel.

Migração curada dos "Perfis TSS" da epigenome_platform (numpy/matplotlib
próprios, estilo deepTools). O TSS vem do painel_108.json versionado (início na
fita +, fim na fita −); a média de metilação por bin é comparada entre os dois
grupos e vira PNG + resumo.

Uso:
  python3 modules/epigenomica/scripts/perfil_tss.py \
      --dir modules/epigenomica/demo_data --painel modules/epigenomica/data/paineis/painel_108.json \
      --grupo-a Nelore --grupo-b HF --janela 2000 --bin 100 --saida <dir>
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

import numpy as np

from _comum import escrever_resumo, log


def _tss_por_cromossomo(painel: Path) -> dict[str, list[tuple[int, int, str]]]:
    dados = json.loads(painel.read_text(encoding="utf-8"))
    por_crom: dict[str, list[tuple[int, int, str]]] = {}
    for simbolo, g in dados["genes"].items():
        fita = 1 if g["fita"] >= 0 else -1
        tss = g["inicio"] if fita > 0 else g["fim"]
        por_crom.setdefault(str(g["cromossomo"]), []).append((tss, fita, simbolo))
    return por_crom


def _acumular(arquivos: list[Path], tss_map, janela: int, n_bins: int):
    soma = np.zeros(n_bins)
    conta = np.zeros(n_bins)
    for arq in arquivos:
        with gzip.open(arq, "rt", encoding="utf-8", errors="replace") as f:
            for linha in f:
                campos = linha.split("\t")
                if len(campos) < 6:
                    continue
                crom, pos, pct = campos[0], int(campos[1]), float(campos[3])
                for tss, fita, _ in tss_map.get(crom, ()):
                    # Na fita − o "upstream" fica em coordenada MAIOR: sem o sinal
                    # da fita, esses genes entram ESPELHADOS no metaperfil e o
                    # degrau de metilação no TSS some na média.
                    d = (pos - tss) * fita
                    if -janela <= d < janela:
                        b = int((d + janela) * n_bins // (2 * janela))
                        soma[b] += pct
                        conta[b] += 1
    with np.errstate(invalid="ignore"):
        return np.where(conta > 0, soma / conta, np.nan), int(conta.sum())


def main() -> None:
    p = argparse.ArgumentParser(description="Metaperfil de metilação ao redor do TSS")
    p.add_argument("--dir", required=True)
    p.add_argument("--painel", required=True, help="painel_108.json (TSS por gene)")
    p.add_argument("--grupo-a", default="Nelore")
    p.add_argument("--grupo-b", default="HF")
    p.add_argument("--janela", type=int, default=2000)
    p.add_argument("--bin", type=int, default=100)
    p.add_argument("--saida", required=True)
    args = p.parse_args()

    pasta, painel, saida = Path(args.dir), Path(args.painel), Path(args.saida)
    if not painel.exists():
        raise SystemExit(f"painel não encontrado: {painel}")
    saida.mkdir(parents=True, exist_ok=True)
    n_bins = (2 * args.janela) // args.bin

    todos = sorted(pasta.glob("*.cov.gz")) + sorted(pasta.glob("*.cov"))
    arq_a = [a for a in todos if args.grupo_a in a.name]
    arq_b = [a for a in todos if args.grupo_b in a.name]
    if not arq_a or not arq_b:
        raise SystemExit(f"amostras insuficientes: {args.grupo_a}={len(arq_a)}, {args.grupo_b}={len(arq_b)}")
    tss_map = _tss_por_cromossomo(painel)
    n_tss = sum(len(v) for v in tss_map.values())
    log(f"TSS do painel: {n_tss}; janela ±{args.janela} pb em {n_bins} bins; "
        f"{args.grupo_a}={len(arq_a)} amostras, {args.grupo_b}={len(arq_b)}")

    media_a, cpgs_a = _acumular(arq_a, tss_map, args.janela, n_bins)
    media_b, cpgs_b = _acumular(arq_b, tss_map, args.janela, n_bins)
    log(f"CpGs agregados: {args.grupo_a}={cpgs_a}, {args.grupo_b}={cpgs_b}")
    # Falso-done: sem CpG agregada a figura sai em branco e o job "concluiria".
    if cpgs_a == 0 or cpgs_b == 0:
        raise SystemExit(
            f"nenhuma CpG na janela ±{args.janela} pb ({args.grupo_a}={cpgs_a}, "
            f"{args.grupo_b}={cpgs_b}) — confira o painel e o assembly dos coverages")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    eixo_x = np.linspace(-args.janela, args.janela, n_bins)
    fig, ax = plt.subplots(figsize=(7, 4.2), dpi=120)
    ax.plot(eixo_x, media_a, label=f"{args.grupo_a} (n={len(arq_a)})", lw=2)
    ax.plot(eixo_x, media_b, label=f"{args.grupo_b} (n={len(arq_b)})", lw=2)
    ax.axvline(0, color="#5c6b7a", ls="--", lw=1)
    ax.set_xlabel("Distância ao TSS (pb)")
    ax.set_ylabel("Metilação média (%)")
    ax.set_title("Metaperfil de metilação — painel 108 (CpG, Bismark)")
    ax.legend()
    fig.tight_layout()
    png = saida / "tss_perfil.png"
    fig.savefig(png)
    log(f"Figura: {png}")

    escrever_resumo(saida, "tss_resumo.json", {
        "painel": painel.name,
        "n_tss": n_tss,
        "n_tss_fita_negativa": sum(1 for v in tss_map.values() for _, fita, _ in v if fita < 0),
        "janela_pb": args.janela,
        "bin_pb": args.bin,
        "grupo_a": {"nome": args.grupo_a, "n_amostras": len(arq_a), "cpgs_agregados": cpgs_a},
        "grupo_b": {"nome": args.grupo_b, "n_amostras": len(arq_b), "cpgs_agregados": cpgs_b},
        "figura_png": str(png),
    })


if __name__ == "__main__":
    main()
