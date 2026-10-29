"""fixtures_v2.py — helpers da v2 multiespécie para os testes (só FUNÇÕES, nenhuma fixture
pytest: cada teste decide o que montar). Usados pelas frentes A, B, P e W:

    from fixtures_v2 import apontar_catalogo, escrever_especie, escrever_subprojeto

Tudo grava em pastas temporárias; nada aqui toca especies/ nem projetos/ do repositório.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Optional

import yaml


def escrever_especie(pasta: Path, id: str = "homo_sapiens", *, refs: Optional[dict[str, str]] = None,
                     modulos: Iterable[str] = ("transcriptomica",), assembly: str = "GRCh38",
                     prefixo: str = "ENSG", ordem: int = 1, **extra: Any) -> Path:
    """Grava <pasta>/<id>.yaml com uma espécie mínima e VÁLIDA (uma assembly). `extra`
    sobrescreve/acrescenta chaves de topo (inclusive inválidas, para os canários)."""
    pasta.mkdir(parents=True, exist_ok=True)
    dados: dict[str, Any] = {
        "id": id,
        "nome_cientifico": f"Especie teste {id}",
        "nome_exibido": f"Teste ({id})",
        "nome_exibido_en": f"Test ({id})",
        "taxon_ids": [1],
        "ordem": ordem,
        "assembly_padrao": assembly,
        "assemblies": {assembly: {"fonte": "teste", "contigs": "sem_chr", "prefixo_gene": prefixo,
                                  "refs": dict(refs or {})}},
        "modulos": list(modulos),
    }
    dados.update(extra)
    arq = pasta / f"{id}.yaml"
    arq.write_text(yaml.safe_dump(dados, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return arq


def escrever_subprojeto(raiz: Path, especie: str, slug: str, *, status: str = "draft",
                        assembly: str = "GRCh38", modulos: Iterable[str] = ("transcriptomica",),
                        pipelines: Iterable[str] = (), tipo: str = "pesquisa",
                        amostras_tsv: Optional[str] = "amostra\tcondicao\nA1\tc\nA2\tt\n",
                        **extra: Any) -> Path:
    """Grava <raiz>/<especie>/<slug>/subprojeto.yaml (+ amostras.tsv, se `amostras_tsv` não for
    None) e devolve a PASTA. `extra` sobrescreve/acrescenta campos (inclusive inválidos)."""
    pasta = raiz / especie / slug
    pasta.mkdir(parents=True, exist_ok=True)
    dados: dict[str, Any] = {
        "id": f"{especie}/{slug}",
        "especie": especie,
        "assembly": assembly,
        "titulo": f"Teste {slug}",
        "pergunta": f"Pergunta de teste do subprojeto {slug}?",
        "tipo": tipo,
        "status": status,
        "modulos": list(modulos),
        "pipelines": list(pipelines),
    }
    dados.update(extra)
    (pasta / "subprojeto.yaml").write_text(yaml.safe_dump(dados, allow_unicode=True, sort_keys=False),
                                           encoding="utf-8")
    if amostras_tsv is not None:
        (pasta / "amostras.tsv").write_text(amostras_tsv, encoding="utf-8", newline="\n")
    return pasta


def apontar_catalogo(monkeypatch, especies_dir: Path, projetos_repo: Path) -> None:
    """Aponta os loaders para pastas de teste: especies/ e projetos/ do REPO viram tmp. A raiz
    <dados> (OMICA_DATA/projetos) já é tmp pelo conftest."""
    from omica import especies, subprojetos

    monkeypatch.setattr(especies, "ESPECIES_DIR", especies_dir)
    monkeypatch.setattr(subprojetos, "PROJETOS_REPO", projetos_repo)
