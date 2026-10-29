"""chaves_contexto.py — a LISTA FECHADA de placeholders de contexto da v2 multiespécie.

Um cmd de manifest pode citar, além dos parâmetros e de {dados}/{saida}, valores que o
SERVIDOR resolve a partir de (espécie, subprojeto): a referência da assembly (`ref_*`), os
arquivos do subprojeto (`sub_*`) e a própria assembly (`esp_*`). Nada disso vem do cliente.

Fica num módulo SEM import de `omica.*` de propósito: o registry valida os cmds no load com
estes nomes, e espécies/subprojetos/contexto também os usam — sem ciclo de import.

Os nomes são PLANOS (`ref_gtf`, nunca `ref.gtf`): o `str.format` do dispatch lê `{ref.gtf}`
como getattr, e `registry.campos_do_cmd` já recusa campo que não é nome simples.
"""
from __future__ import annotations

import re
from types import MappingProxyType
from typing import Mapping

ID_DEMO = "bos_taurus/demo"
ESPECIE_LEGADA = "bos_taurus"
SLUG_DEMO = "demo"

# nome PLANO → `tipo` com que dispatch._validar_valor confere o valor resolvido
CHAVES_CONTEXTO: Mapping[str, str] = MappingProxyType({
    # caminho
    "ref_fasta": "caminho",
    "ref_gtf": "caminho",
    "ref_cdna": "caminho",
    "sub_amostras": "caminho",
    "sub_painel_bed": "caminho",
    # dir
    "ref_salmon_indice": "dir",
    "ref_homer_preparsed": "dir",
    "ref_snpeff_datadir": "dir",
    "ref_vep_cache": "dir",
    # texto
    "ref_snpeff_db": "texto",
    "esp_assembly": "texto",
    # inteiro
    "ref_vep_cache_version": "inteiro",
    "ref_plink_chr_set": "inteiro",
})
PREFIXOS_CONTEXTO = ("ref_", "sub_", "esp_")
# preenchidos pelo servidor (OMICA_DATA e a pasta do run): nunca nome de parâmetro
RESERVADOS = frozenset({"dados", "saida"})
# refs de uma assembly no YAML da espécie: chave SEM prefixo ("gtf") ↔ placeholder "ref_gtf"
CHAVES_REF = frozenset(k[4:] for k in CHAVES_CONTEXTO if k.startswith("ref_"))

# Sempre com fullmatch (sem âncoras: nada de `$` aceitando '\n' final) e re.ASCII.
ID_ESPECIE = re.compile(r"[a-z][a-z0-9_]{2,31}", re.ASCII)
SLUG = re.compile(r"[a-z][a-z0-9_]{2,47}", re.ASCII)
# "sub03" e afins: as numerações das plataformas antigas colidem entre si (genomes × epigenoma)
SLUG_NU = re.compile(r"sub[0-9]+", re.ASCII)
ID_SUBPROJETO = re.compile(r"(?P<esp>[a-z][a-z0-9_]{2,31})/(?P<slug>[a-z][a-z0-9_]{2,47})", re.ASCII)
# results/projetos/<esp>/<slug>/ é a árvore dos subprojetos: um módulo com esse id a invadiria
MODULOS_RESERVADOS = frozenset({"projetos"})


def eh_chave_contexto(nome: str) -> bool:
    """True para um nome com prefixo de contexto (`ref_`, `sub_`, `esp_`), conhecido ou não.

    É o critério de "parece contexto": um placeholder assim fora de CHAVES_CONTEXTO é
    recusado no load (erro de digitação ou chave de outra versão), nunca tratado como
    parâmetro comum."""
    return isinstance(nome, str) and nome.startswith(PREFIXOS_CONTEXTO)
