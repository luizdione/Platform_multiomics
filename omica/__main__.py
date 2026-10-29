"""CLI da plataforma: python -m omica <comando>.

Comandos:
  serve     inicia a UI/API (uvicorn) na porta OMICA_PORT
  doctor    diagnóstico da plataforma (--fix-soft: restart compose + reindex)
  reindex   regenera data/skills_index.json a partir do INDICE_CONSOLIDADO.md
            e atualiza a cópia do glossário de busca PT→EN (data/skills_glossario.json)
  versao    imprime a versão
  importar-subprojetos --dry-run --catalogo <yaml> [--saida <pasta>] [--json <relatorio.json>]
            confere um catálogo de subprojetos legados (v2, M5; nesta etapa SÓ --dry-run)
"""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="omica", description="Plataforma ômica — CLI")
    sub = parser.add_subparsers(dest="comando", required=True)
    sub.add_parser("serve", help="inicia a UI/API")
    p_doctor = sub.add_parser("doctor", help="diagnóstico da plataforma")
    p_doctor.add_argument("--fix-soft", action="store_true", help="restart compose + reindex das skills")
    sub.add_parser("reindex", help="regenera o índice das skills")
    p_gate = sub.add_parser("gate", help="gate anti-vazamento p/ repositório de cálculo")
    p_gate.add_argument("diretorio", help="árvore a auditar (repo de exportação)")
    p_selos = sub.add_parser(
        "selos", help="confere (ou regrava) o hash dos workflows selados")
    p_selos.add_argument(
        "--gravar", action="store_true",
        help="regrava hash_scripts nos manifests — use SÓ depois de re-executar "
             "o workflow de verdade; o selo afirma que o E2E rodou")
    sub.add_parser("versao", help="imprime a versão")
    p_importar = sub.add_parser(
        "importar-subprojetos",
        help="confere um catálogo de subprojetos legados (nesta etapa SÓ --dry-run)")
    p_importar.add_argument(
        "--dry-run", action="store_true",
        help="obrigatório nesta etapa: confere e relata, sem importar nada")
    p_importar.add_argument(
        "--catalogo", required=True,
        help="YAML do catálogo (FORA do repositório: traz perguntas de artigo)")
    p_importar.add_argument(
        "--saida", default=None,
        help="grava <saida>/<esp>/<slug>/{subprojeto.yaml,amostras.tsv} para revisão; "
             "recusado dentro do repositório, de GENOME_DATA ou de OMICA_DATA")
    p_importar.add_argument(
        "--json", default=None, dest="json_saida",
        help="relatório em JSON; mesma recusa do --saida")
    args = parser.parse_args(argv)

    if args.comando == "serve":
        import uvicorn

        from omica.config import get_config

        cfg = get_config()
        uvicorn.run("omica.web.app:app", host="0.0.0.0", port=cfg.porta, log_level="info")
        return 0
    if args.comando == "doctor":
        from omica.doctor import main as doctor_main

        return doctor_main(fix_soft=args.fix_soft)
    if args.comando == "reindex":
        from omica.skills.build_index import main as reindex_main

        return reindex_main()
    if args.comando == "gate":
        from omica.publish.gate import main as gate_main

        return gate_main(args.diretorio)
    if args.comando == "selos":
        from omica.selos import main as selos_main

        return selos_main(gravar=args.gravar)
    if args.comando == "importar-subprojetos":
        from omica.importador import main as importar_main

        return importar_main(dry_run=args.dry_run, catalogo=args.catalogo, saida=args.saida,
                             json_saida=args.json_saida)
    if args.comando == "versao":
        from omica import __version__

        print(__version__)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
