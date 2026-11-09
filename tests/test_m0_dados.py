"""test_m0_dados.py — os YAMLs REAIS da v2 multiespécie (frente D, passo D1; ESPEC §4 D1).

Confere especies/{homo_sapiens,mus_musculus,bos_taurus}.yaml e projetos/bos_taurus/{demo,
aula_granulosa_calor}/ contra o registry, os pipelines, os scripts selados, o golden da v1
(tests/golden/cmd_demo_v1.json) e os arquivos de demo do repositório.

Cada conferência é uma função que devolve a LISTA de problemas (vazia = ok):
  - controle: nos arquivos reais do repositório, a lista sai vazia;
  - canário: numa CÓPIA mutada em tmp, a lista acusa o defeito plantado — a prova de que a
    conferência morde (sem ela, o canário reprova).

Os dois amostras.tsv são GERADOS daqui, a partir do coldata e dos coverages de demo do
repositório — nunca digitados (ESPEC §4 D1). Também daqui sai a tabela de amostras do exemplo
da epigenômica, modules/epigenomica/data/amostras_cd4_demo.tsv (F6, adendo v2 §A9.3: amostra =
nome do coverage sem .bismark.cov.gz; condicao = o grupo do nome). Para regerar:

    PYTHONUTF8=1 python tests/test_m0_dados.py --gerar
"""
from __future__ import annotations

import csv
import json
import re
import shutil
import sys
from dataclasses import replace
from pathlib import Path
from typing import Callable, Mapping, Optional

if __name__ == "__main__":  # `python tests/test_m0_dados.py --gerar`, de qualquer pasta
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from omica import especies as especies_mod  # noqa: E402
from omica import subprojetos as subprojetos_mod  # noqa: E402
from omica.chaves_contexto import CHAVES_CONTEXTO, CHAVES_REF  # noqa: E402
from omica.config import REPO_ROOT  # noqa: E402
from omica.contrato import formatos  # noqa: E402
from omica.pipelines import Pipeline, carregar_pipelines  # noqa: E402
from omica.registry import Registry, load_registry  # noqa: E402

ESPECIES_REPO = REPO_ROOT / "especies"
PROJETOS_REPO = REPO_ROOT / "projetos"
COLDATA = REPO_ROOT / "modules" / "transcriptomica" / "demo_data" / "coldata_granulosa.tsv"
COUNTS = REPO_ROOT / "modules" / "transcriptomica" / "demo_data" / "counts_granulosa.tsv"
EPI_DEMO = REPO_ROOT / "modules" / "epigenomica" / "demo_data"
AMOSTRAS_CD4 = REPO_ROOT / "modules" / "epigenomica" / "data" / "amostras_cd4_demo.tsv"
GOLDEN = Path(__file__).resolve().parent / "golden" / "cmd_demo_v1.json"

ORDEM_ESPECIES = [("homo_sapiens", 1), ("mus_musculus", 2), ("bos_taurus", 3)]
SUBPROJETOS_DO_REPO = {"bos_taurus/demo", "bos_taurus/aula_granulosa_calor"}
TIPO_ESPERADO = {"bos_taurus/demo": "demo", "bos_taurus/aula_granulosa_calor": "aula"}

CAB_DEMO = ("amostra", "modulo", "grupo", "acesso", "raca", "tecido", "dataset")
CAB_AULA = ("amostra", "condicao", "acesso", "raca", "tecido", "estacao", "dataset")
CAB_CD4 = ("amostra", "condicao")
_SUFIXO_EPI = ".bismark.cov.gz"
# Tabelas FECHADAS (valor desconhecido = erro, nunca palpite). Raça: "Holstein", nunca
# "Holandês" — o mesmo vocabulário do sub03 e do manifest da epigenômica (crítica v2, médio 12).
RACA_DO_GRUPO_EPI = {"HF": "Holstein", "Nelore": "Nelore"}
ESTACAO_DA_CONDICAO = {"summer_heat": "verao", "winter_control": "inverno"}
DATASET_TRANSCRIPTOMICA = "GSE235171"
DATASET_EPIGENOMICA = "zenodo_cd4"

# Angus_granulosa_summer_heat_SRR24953469
_NOME_GRANULOSA = re.compile(
    r"(?P<raca>[A-Z][A-Za-z]+)_(?P<tecido>granulosa)_(?P<condicao>[a-z][a-z_]*)_(?P<acesso>SRR[0-9]+)", re.ASCII)
# FILTERED_H82_Nelore_ERS4410323_CD4.bismark.cov.gz → amostra H82_Nelore_ERS4410323
_NOME_EPI = re.compile(
    r"FILTERED_(?P<amostra>[A-Z0-9]+_(?P<grupo>[A-Za-z]+)_(?P<acesso>ERS[0-9]+))_(?P<tecido>CD4)\.bismark\.cov\.gz",
    re.ASCII)

# Cada ref bovina é o LITERAL que um script selado usa hoje (default do argparse ou constante):
# o cmd do demo só sai byte a byte igual ao da v1 se o YAML repetir exatamente esse valor.
LITERAIS_DOS_SCRIPTS: Mapping[str, tuple[str, str]] = {
    "fasta": ("modules/epigenomica/scripts/motifs_homer.py", 'p.add_argument("--genoma", default="{v}")'),
    "gtf": ("modules/genomica/scripts/coordenadas_painel.py", 'p.add_argument("--gtf", default="{v}")'),
    "cdna": ("modules/transcriptomica/scripts/salmon_quant.py", 'TRANSCRITOS_LEGADO = "{v}"'),
    "salmon_indice": ("modules/transcriptomica/scripts/salmon_quant.py", 'p.add_argument("--indice", default="{v}")'),
    "homer_preparsed": ("modules/epigenomica/scripts/motifs_homer.py",
                        'p.add_argument("--preparsed", default="{v}")'),
    "snpeff_db": ("modules/genomica/scripts/anotacao_snpeff.py", 'p.add_argument("--db", default="{v}")'),
    "snpeff_datadir": ("modules/genomica/scripts/anotacao_snpeff.py", 'p.add_argument("--datadir", default="{v}")'),
    "vep_cache": ("modules/genomica/scripts/vep_anotacao.py", 'p.add_argument("--cache", default="{v}")'),
    "vep_cache_version": ("modules/genomica/scripts/vep_anotacao.py", '"--cache_version", "{v}"'),
    "plink_chr_set": ("modules/genomica/scripts/fst_racas.py", '"--chr-set", "{v}"'),
}
# ...e o trecho que cada cmd do golden da v1 tem de conter (B5 troca o literal do manifest
# por {ref_*}; o cmd montado com o contexto do demo tem de continuar idêntico a este JSON)
TRECHOS_DO_GOLDEN: Mapping[str, tuple[str, ...]] = {
    "genomica/scripts_api/painel_bed": (" --gtf {gtf} ",),
    "genomica/scripts/coordenadas_painel": (" --gtf {gtf} ",),
    "genomica/scripts_api/snpeff": (" --db {snpeff_db} --datadir {snpeff_datadir} ",),
    "transcriptomica/scripts/salmon_quant_demo": (" --indice {salmon_indice} ",),
}


# ============================================================ leitura e geração

def _ler_tsv(caminho: Path) -> list[tuple[str, ...]]:
    """Linhas não vazias, já sem o fim de linha (LF ou CRLF: o autocrlf do Windows troca)."""
    with open(caminho, encoding="utf-8", newline="") as f:
        return [tuple(linha) for linha in csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE) if linha]


def _coldata() -> list[tuple[str, str]]:
    cab, *linhas = _ler_tsv(COLDATA)
    ia, ic = cab.index("amostra"), cab.index("condicao")
    return [(linha[ia], linha[ic]) for linha in linhas]


def _granulosa(amostra: str, condicao: str) -> re.Match:
    m = _NOME_GRANULOSA.fullmatch(amostra)
    if m is None or m["condicao"] != condicao:
        raise ValueError(f"coldata: amostra fora do padrão <raça>_granulosa_<condição>_<SRR>: {amostra!r}")
    return m


def linhas_demo_esperadas() -> list[tuple[str, ...]]:
    """amostras.tsv do demo: 8 da transcriptômica (coldata) + 6 da epigenômica (coverages)."""
    linhas: list[tuple[str, ...]] = [CAB_DEMO]
    for amostra, condicao in _coldata():
        m = _granulosa(amostra, condicao)
        linhas.append((amostra, "transcriptomica", condicao, m["acesso"], m["raca"], m["tecido"],
                       DATASET_TRANSCRIPTOMICA))
    for arq in sorted(EPI_DEMO.glob("*.bismark.cov.gz")):
        m = _NOME_EPI.fullmatch(arq.name)
        if m is None or m["grupo"] not in RACA_DO_GRUPO_EPI:
            raise ValueError(f"coverage de demo fora do padrão (ou grupo fora da tabela): {arq.name!r}")
        linhas.append((m["amostra"], "epigenomica", m["grupo"], m["acesso"], RACA_DO_GRUPO_EPI[m["grupo"]],
                       m["tecido"], DATASET_EPIGENOMICA))
    return linhas


def linhas_aula_esperadas() -> list[tuple[str, ...]]:
    """amostras.tsv da aula: as 8 do coldata, com amostra/condicao nas 2 primeiras colunas."""
    linhas: list[tuple[str, ...]] = [CAB_AULA]
    for amostra, condicao in _coldata():
        m = _granulosa(amostra, condicao)
        linhas.append((amostra, condicao, m["acesso"], m["raca"], m["tecido"], ESTACAO_DA_CONDICAO[condicao],
                       DATASET_TRANSCRIPTOMICA))
    return linhas


def linhas_cd4_esperadas() -> list[tuple[str, ...]]:
    """amostras_cd4_demo.tsv (coldata do exemplo da epigenômica, adendo v2 §A9.3): amostra =
    nome do coverage do demo sem .bismark.cov.gz (a regra do montar_coverages); condicao = o
    grupo do nome (_NOME_EPI). Exatamente 2 condições, 3 amostras em cada."""
    linhas: list[tuple[str, ...]] = [CAB_CD4]
    for arq in sorted(EPI_DEMO.glob("*" + _SUFIXO_EPI)):
        m = _NOME_EPI.fullmatch(arq.name)
        if m is None or m["grupo"] not in RACA_DO_GRUPO_EPI:
            raise ValueError(f"coverage de demo fora do padrão (ou grupo fora da tabela): {arq.name!r}")
        linhas.append((arq.name[:-len(_SUFIXO_EPI)], m["grupo"]))
    return linhas


GERADOS: Mapping[str, Callable[[], list[tuple[str, ...]]]] = {
    "demo": linhas_demo_esperadas,
    "aula_granulosa_calor": linhas_aula_esperadas,
}


def _gravar_tsv(arq: Path, linhas: list[tuple[str, ...]]) -> Path:
    arq.write_text("".join("\t".join(linha) + "\n" for linha in linhas), encoding="utf-8", newline="\n")
    return arq


def gerar(raiz: Path = PROJETOS_REPO, cd4: Optional[Path] = None) -> list[Path]:
    """Grava os dois amostras.tsv e o amostras_cd4_demo.tsv (UTF-8, LF, TAB). Só o autor roda,
    pela linha de comando. `cd4` None = o arquivo do repositório quando `raiz` é a do
    repositório; numa raiz tmp, `<raiz>/amostras_cd4_demo.tsv` (o teste nunca grava no repo)."""
    gravados = []
    for slug, gerador in GERADOS.items():
        gravados.append(_gravar_tsv(raiz / "bos_taurus" / slug / "amostras.tsv", gerador()))
    if cd4 is None:
        cd4 = AMOSTRAS_CD4 if raiz == PROJETOS_REPO else raiz / AMOSTRAS_CD4.name
    gravados.append(_gravar_tsv(cd4, linhas_cd4_esperadas()))
    return gravados


# ============================================================ conferências (problemas)

def problemas_especies(validas: Mapping[str, especies_mod.Especie], erros: Mapping[str, str],
                       reg: Registry) -> list[str]:
    p = [f"erro de carga especies/{arq}: {msg}" for arq, msg in erros.items()]
    ordem = [(e.id, e.ordem) for e in validas.values()]
    if ordem != ORDEM_ESPECIES:
        p.append(f"ordem das espécies: {ordem} (esperado {ORDEM_ESPECIES})")
    ids_registry = sorted(m.id for m in reg.modules)
    bos = validas.get("bos_taurus")
    if bos is None or sorted(bos.modulos) != ids_registry or len(set(bos.modulos)) != len(bos.modulos):
        p.append(f"bos_taurus.modulos {bos.modulos if bos else None} != módulos do registry {ids_registry}")
    for esp in ("homo_sapiens", "mus_musculus"):
        e = validas.get(esp)
        if e is None or e.modulos != ["transcriptomica"]:
            p.append(f"{esp}.modulos {e.modulos if e else None} != ['transcriptomica']")
            continue
        for asm in e.assemblies:
            est = especies_mod.estado(e, asm)
            if est.total or est.estado != "sem_referencia":
                p.append(f"{esp}/{asm} declara referência de arquivo ({est.total}): nada humano ou murino "
                         "é baixado nesta etapa")
    return p


def problemas_especies_declaradas(validas: Mapping[str, especies_mod.Especie], reg: Registry,
                                  pipelines: Mapping[str, Pipeline]) -> list[str]:
    """Toda espécie citada em `especies:` (scripts, scripts_api, pipelines) tem YAML e tem o
    módulo do spec entre os seus."""
    citadas = [(f"{m.id}.{s.key}", m.id, s.especies) for m in reg.modules for s in [*m.scripts, *m.scripts_api]]
    citadas += [(f"pipeline {pid}", pl.modulo, pl.especies) for pid, pl in pipelines.items()]
    p = []
    for rotulo, modulo, declaradas in citadas:
        for esp in declaradas:
            if esp not in validas:
                p.append(f"{rotulo}: espécie {esp!r} sem especies/{esp}.yaml")
            elif modulo not in validas[esp].modulos:
                p.append(f"{rotulo}: declara {esp}, que não tem o módulo {modulo}")
    return p


def problemas_literais(refs: Mapping[str, str], golden: Optional[Mapping[str, str]] = None) -> list[str]:
    """Cada ref bovina = o literal do script selado e o trecho do cmd do golden da v1."""
    p = []
    if set(LITERAIS_DOS_SCRIPTS) != set(CHAVES_REF):
        p.append(f"LITERAIS_DOS_SCRIPTS {sorted(LITERAIS_DOS_SCRIPTS)} != CHAVES_REF {sorted(CHAVES_REF)}: "
                 "chave de ref nova precisa do seu literal aqui")
    faltam = sorted(set(CHAVES_REF) - set(refs))
    if faltam:
        p.append(f"faltam refs bovinas: {faltam}")
    for chave, (script, modelo) in LITERAIS_DOS_SCRIPTS.items():
        if chave not in refs:
            continue
        texto = (REPO_ROOT / script).read_text(encoding="utf-8")
        if modelo.format(v=refs[chave]) not in texto:
            p.append(f"ref {chave}={refs[chave]!r} difere do literal de {script} ({modelo})")
    golden = golden if golden is not None else json.loads(GOLDEN.read_text(encoding="utf-8"))
    for chave_golden, trechos in TRECHOS_DO_GOLDEN.items():
        for trecho in trechos:
            try:
                esperado = trecho.format(**refs)
            except KeyError:
                continue  # a ref ausente já foi acusada acima
            if esperado not in golden.get(chave_golden, ""):
                p.append(f"golden {chave_golden}: o cmd da v1 não contém {esperado.strip()!r}")
    return p


def problemas_subprojetos(validos: Mapping[str, subprojetos_mod.Registro], erros: Mapping[str, str],
                          especies: Mapping[str, especies_mod.Especie],
                          pipelines: Mapping[str, Pipeline]) -> list[str]:
    p = [f"erro de carga {rotulo}: {msg}" for rotulo, msg in erros.items()]
    if set(validos) != SUBPROJETOS_DO_REPO:
        p.append(f"subprojetos do repositório: {sorted(validos)} (esperado {sorted(SUBPROJETOS_DO_REPO)})")
    for sid, reg in validos.items():
        s = reg.sub
        if reg.origem != "repo" or s.privado:
            p.append(f"{sid}: origem {reg.origem}, privado={s.privado} (no repositório, nunca privado)")
        if sid in TIPO_ESPERADO and s.tipo != TIPO_ESPERADO[sid]:
            p.append(f"{sid}: tipo {s.tipo!r} (esperado {TIPO_ESPERADO[sid]!r})")
        if s.parametros:
            p.append(f"{sid}: parametros não vazio {s.parametros} (o default bovino vale; no demo, é o que "
                     "mantém o cmd byte a byte)")
        modulos = s.modulos or especies[s.especie].modulos
        for pid in s.pipelines:
            pl = pipelines.get(pid)
            if pl is None:
                p.append(f"{sid}: pipeline {pid!r} inexistente")
                continue
            if s.especie not in pl.especies:
                p.append(f"{sid}: pipeline {pid} não declara a espécie {s.especie} (declara {pl.especies})")
            if pl.modulo not in modulos:
                p.append(f"{sid}: pipeline {pid} é do módulo {pl.modulo}, fora de {modulos}")
        declarados = [*(f"datasets.{n}" for n in s.datasets), *(f"dados_legados[{i}]" for i in range(len(s.dados_legados)))]
        for rotulo, caminho in zip(declarados, [*s.datasets.values(), *s.dados_legados]):
            # /data/... vive em E:\data (o conftest isola GENOME_DATA): quem confere é o importador/E2E
            if not caminho.startswith("/data/") and not especies_mod.no_disco(caminho).exists():
                p.append(f"{sid}: {rotulo} {caminho!r} não existe no repositório")
        arq = reg.pasta / s.amostras
        if not arq.is_file():
            p.append(f"{sid}: {s.amostras} ausente")
            continue
        cab, *linhas = _ler_tsv(arq)
        if not cab or cab[0] != "amostra":
            p.append(f"{sid}: a 1ª coluna de {s.amostras} tem de ser 'amostra' (achei {cab[:1]})")
        nomes = [linha[0] for linha in linhas]
        if len(set(nomes)) != len(nomes):
            p.append(f"{sid}: amostras repetidas em {s.amostras}")
        if any(len(linha) != len(cab) for linha in linhas):
            p.append(f"{sid}: linha com nº de campos diferente do cabeçalho em {s.amostras}")
            continue
        colunas = {c: {linha[i] for linha in linhas} for i, c in enumerate(cab)}
        if "dataset" in colunas and colunas["dataset"] - set(s.datasets):
            p.append(f"{sid}: dataset {sorted(colunas['dataset'] - set(s.datasets))} fora de datasets {sorted(s.datasets)}")
        if "modulo" in colunas and colunas["modulo"] - set(modulos):
            p.append(f"{sid}: modulo {sorted(colunas['modulo'] - set(modulos))} fora de {modulos}")
        for c in s.contrastes:
            valores = colunas.get(c.coluna)
            if valores is None or c.a == c.b or {c.a, c.b} - valores:
                p.append(f"{sid}: contraste {c.id} ({c.coluna}: {c.a} × {c.b}) não existe em {s.amostras}")
    return p


def problemas_amostras(projetos: Path) -> list[str]:
    """Os amostras.tsv são exatamente o que os geradores derivam dos arquivos do repositório."""
    p = []
    for slug, gerador in GERADOS.items():
        arq = projetos / "bos_taurus" / slug / "amostras.tsv"
        if not arq.is_file():
            p.append(f"{slug}: amostras.tsv ausente")
            continue
        real, esperado = _ler_tsv(arq), gerador()
        if real != esperado:
            n = next((i for i, (a, b) in enumerate(zip(real, esperado)) if a != b), min(len(real), len(esperado)))
            p.append(f"{slug}: amostras.tsv difere do gerado (linha {n + 1}: {real[n] if n < len(real) else None} "
                     f"× {esperado[n] if n < len(esperado) else None}); regere com --gerar")
    return p


def _tudo(especies_dir: Path, projetos_dir: Path,
          transformar_pipelines: Optional[Callable[[dict], dict]] = None) -> list[str]:
    reg = load_registry()
    pipelines, erros_pl = carregar_pipelines()
    assert erros_pl == {} and reg.erros == {}
    if transformar_pipelines is not None:
        pipelines = transformar_pipelines(dict(pipelines))
    validas, erros = especies_mod.carregar_especies(especies_dir, reg=reg)
    validos, erros_sub = subprojetos_mod.carregar_subprojetos(validas, [("repo", projetos_dir)], reg=reg)
    bos = validas.get("bos_taurus")
    refs = bos.assemblies[bos.assembly_padrao].refs if bos else {}
    return (problemas_especies(validas, erros, reg)
            + problemas_especies_declaradas(validas, reg, pipelines)
            + problemas_literais(refs)
            + problemas_subprojetos(validos, erros_sub, validas, pipelines)
            + problemas_amostras(projetos_dir))


# ============================================================ controles (arquivos reais)

def test_controle_tudo_real():
    assert _tudo(ESPECIES_REPO, PROJETOS_REPO) == []


def test_especies_reais_na_ordem_e_com_os_modulos():
    validas, erros = especies_mod.carregar_especies()   # ESPECIES_DIR padrão = especies/ do repo
    assert erros == {}
    assert [(e.id, e.ordem) for e in validas.values()] == ORDEM_ESPECIES
    reg = load_registry()
    assert sorted(validas["bos_taurus"].modulos) == sorted(m.id for m in reg.modules) and len(reg.modules) == 4
    assert validas["homo_sapiens"].modulos == ["transcriptomica"] == validas["mus_musculus"].modulos
    assert validas["bos_taurus"].assembly_padrao == "ARS-UCD1.3"     # decisão D-1
    assert validas["bos_taurus"].parametros == {}


def test_subprojetos_reais():
    validas, _ = especies_mod.carregar_especies()
    validos, erros = subprojetos_mod.carregar_subprojetos(validas)   # raízes padrão: repo + <dados> (tmp)
    assert erros == {}
    assert set(validos) == SUBPROJETOS_DO_REPO
    assert all(r.origem == "repo" and not r.sub.privado and r.sub.executavel for r in validos.values())
    demo, aula = validos["bos_taurus/demo"].sub, validos["bos_taurus/aula_granulosa_calor"].sub
    assert (demo.tipo, demo.status, demo.parametros) == ("demo", "valido", {})
    assert (aula.tipo, aula.status, aula.pipelines) == ("aula", "valido", ["transcriptomica_de"])
    # crítica v2, médio 12: a validade do demo diz que o selo é do código e cita o painel 108 v1
    assert "não validade científica" in demo.validade and "painel 108 v1" in demo.validade
    assert any("13 loci errados" in a for a in demo.avisos)


def test_amostras_demo_14_linhas_iguais_ao_coldata_e_aos_coverages():
    reg = subprojetos_mod.carregar_subprojetos(especies_mod.carregar_especies()[0])[0]["bos_taurus/demo"]
    assert subprojetos_mod.n_amostras(reg) == 14
    cab, *linhas = _ler_tsv(PROJETOS_REPO / "bos_taurus" / "demo" / "amostras.tsv")
    assert cab == CAB_DEMO
    tra = [linha for linha in linhas if linha[1] == "transcriptomica"]
    epi = [linha for linha in linhas if linha[1] == "epigenomica"]
    assert len(tra) == 8 and len(epi) == 6
    assert [(linha[0], linha[2]) for linha in tra] == _coldata()
    # derivação independente da do gerador: nome do arquivo sem o prefixo e o sufixo
    arquivos = sorted(a.name for a in EPI_DEMO.glob("*.bismark.cov.gz"))
    assert len(arquivos) == 6
    assert sorted(linha[0] for linha in epi) == sorted(
        a.removeprefix("FILTERED_").removesuffix("_CD4.bismark.cov.gz") for a in arquivos)
    assert {linha[4] for linha in linhas} == {"Angus", "Nelore", "Holstein"}   # nunca "Holandês"
    assert {linha[6] for linha in linhas} == {DATASET_TRANSCRIPTOMICA, DATASET_EPIGENOMICA}


def test_amostras_aula_iguais_ao_coldata_e_servem_de_coldata():
    arq = PROJETOS_REPO / "bos_taurus" / "aula_granulosa_calor" / "amostras.tsv"
    cab, *linhas = _ler_tsv(arq)
    assert cab == CAB_AULA and len(linhas) == 8
    assert [(linha[0], linha[1]) for linha in linhas] == _coldata()
    assert {(linha[1], linha[5]) for linha in linhas} == set(ESTACAO_DA_CONDICAO.items())
    # amostra/condicao nas colunas do coldata: a tabela da aula passa no portão do DESeq2
    assert formatos.coldata(arq).ok
    v = formatos.contagens_e_coldata(COUNTS, arq)
    assert v.ok, v.falhas


def test_bovino_pronta_e_parcial_com_refs_falsas(tmp_path):
    """Canário da ESPEC §4 D1: com as 7 refs de arquivo falsas em tmp GENOME_DATA o bovino fica
    `pronta`; sem uma, `parcial`. Controle: nada no disco → `sem_referencia`."""
    bos = especies_mod.carregar_especies()[0]["bos_taurus"]
    refs = bos.assemblies["ARS-UCD1.3"].refs
    arquivos = {k: v for k, v in refs.items() if CHAVES_CONTEXTO[f"ref_{k}"] in ("caminho", "dir")}
    assert len(arquivos) == 7
    assert especies_mod.estado(bos).estado == "sem_referencia"            # controle: disco vazio
    for k, v in arquivos.items():
        alvo = especies_mod.no_disco(v)
        assert alvo.resolve().is_relative_to(tmp_path.resolve())          # conftest: GENOME_DATA = tmp
        if CHAVES_CONTEXTO[f"ref_{k}"] == "dir":
            alvo.mkdir(parents=True, exist_ok=True)
        else:
            alvo.parent.mkdir(parents=True, exist_ok=True)
            alvo.write_bytes(b"x")
    est = especies_mod.estado(bos)
    assert (est.estado, est.presentes, est.total, est.faltando) == ("pronta", 7, 7, ())
    especies_mod.no_disco(arquivos["gtf"]).unlink()
    est = especies_mod.estado(bos)
    assert (est.estado, est.presentes, est.faltando) == ("parcial", 6, ("gtf",))


# ============================================================ canários (cópias mutadas)

def _copia(tmp_path: Path) -> tuple[Path, Path]:
    esp, proj = tmp_path / "copia" / "especies", tmp_path / "copia" / "projetos"
    shutil.copytree(ESPECIES_REPO, esp)
    shutil.copytree(PROJETOS_REPO, proj)
    return esp, proj


def _trocar(arq: Path, velho: str, novo: str) -> None:
    texto = arq.read_text(encoding="utf-8")
    assert texto.count(velho) == 1, f"mutação ambígua ou ausente em {arq.name}: {velho!r}"
    arq.write_text(texto.replace(velho, novo), encoding="utf-8", newline="")


def _em(rel: str, velho: str, novo: str) -> Callable[[Path, Path], None]:
    def mutar(esp: Path, proj: Path) -> None:
        raiz = esp if rel.startswith("especies/") else proj
        _trocar(raiz / rel.split("/", 1)[1], velho, novo)
    return mutar


def _apagar(rel: str) -> Callable[[Path, Path], None]:
    def mutar(esp: Path, proj: Path) -> None:
        (esp if rel.startswith("especies/") else proj).joinpath(rel.split("/", 1)[1]).unlink()
    return mutar


def _subprojeto_a_mais(esp: Path, proj: Path) -> None:
    from fixtures_v2 import escrever_subprojeto

    escrever_subprojeto(proj, "bos_taurus", "extra_x", status="valido", assembly="ARS-UCD1.3")


def _ultima_linha_fora(rel: str) -> Callable[[Path, Path], None]:
    def mutar(esp: Path, proj: Path) -> None:
        arq = proj / rel.split("/", 1)[1]
        linhas = arq.read_text(encoding="utf-8").splitlines(keepends=True)
        arq.write_text("".join(linhas[:-1]), encoding="utf-8", newline="")
    return mutar


_DEMO = "projetos/bos_taurus/demo/subprojeto.yaml"
_AULA = "projetos/bos_taurus/aula_granulosa_calor/subprojeto.yaml"
_DEMO_TSV = "projetos/bos_taurus/demo/amostras.tsv"
_AULA_TSV = "projetos/bos_taurus/aula_granulosa_calor/amostras.tsv"

CANARIOS = {
    # espécies
    "ordem_trocada": (_em("especies/homo_sapiens.yaml", "ordem: 1", "ordem: 4"), "ordem das espécies"),
    "bovino_sem_ml": (_em("especies/bos_taurus.yaml", "modulos: [genomica, epigenomica, transcriptomica, ml]",
                          "modulos: [genomica, epigenomica, transcriptomica]"), "bos_taurus.modulos"),
    "camundongo_com_ml": (_em("especies/mus_musculus.yaml", "modulos: [transcriptomica]",
                              "modulos: [transcriptomica, ml]"), "mus_musculus.modulos"),
    "humano_com_ref_de_arquivo": (_em("especies/homo_sapiens.yaml", "refs: {}", "refs: {gtf: /data/hg38/genes.gtf.gz}"),
                                  "declara referência de arquivo"),
    "camundongo_sem_yaml": (_apagar("especies/mus_musculus.yaml"), "sem especies/mus_musculus.yaml"),
    "especie_com_campo_desconhecido": (_em("especies/bos_taurus.yaml", "ordem: 3", "ordem: 3\ncampo_novo: 1"),
                                       "erro de carga especies/bos_taurus.yaml"),
    "ref_bovina_fora_do_literal": (_em("especies/bos_taurus.yaml", "genes.gtf.gz", "genes.gtf"),
                                   "ref gtf='/data/snpeff_data/ARS-UCD1.3.113/genes.gtf'"),
    # subprojetos
    "subprojeto_a_mais": (_subprojeto_a_mais, "subprojetos do repositório"),
    "aula_com_pipeline_de_outro_modulo": (_em(_AULA, "pipelines: [transcriptomica_de]",
                                              "pipelines: [transcriptomica_de, genomica_painel]"),
                                          "pipeline genomica_painel é do módulo genomica"),
    "aula_com_pipeline_inexistente": (_em(_AULA, "pipelines: [transcriptomica_de]", "pipelines: [transcriptomica_xx]"),
                                      "pipeline 'transcriptomica_xx' inexistente"),
    "demo_com_parametros": (_em(_DEMO, "parametros: {}                       # vazio DE PROPÓSITO",
                                'parametros: {"transcriptomica.deseq2.padj": "0.1"}  #'),
                            "bos_taurus/demo: parametros não vazio"),
    "dataset_inexistente": (_em(_AULA, "demo_data/counts_granulosa.tsv", "demo_data/counts_xx.tsv"),
                            "datasets.GSE235171 'modules/transcriptomica/demo_data/counts_xx.tsv' não existe"),
    "contraste_inexistente": (_em(_AULA, "a: summer_heat", "a: summer_hot"), "contraste verao_vs_inverno"),
    "dataset_fora_de_datasets": (_em(_AULA, "  GSE235171: modules", "  GSE999: modules"), "fora de datasets"),
    "demo_privado": (_em(_DEMO, "privado: false", "privado: true"), "erro de carga repo:bos_taurus/demo"),
    "demo_tipo_trocado": (_em(_DEMO, "tipo: demo", "tipo: aula"), "bos_taurus/demo: tipo 'aula'"),
    # amostras.tsv (gerados, nunca digitados)
    "demo_raca_holandes": (_em(_DEMO_TSV, "HF\tERS4410317\tHolstein", "HF\tERS4410317\tHolandês"),
                           "demo: amostras.tsv difere do gerado"),
    "demo_sem_a_ultima_linha": (_ultima_linha_fora(_DEMO_TSV), "demo: amostras.tsv difere do gerado"),
    "aula_condicao_trocada": (_em(_AULA_TSV, "SRR24953469\tsummer_heat", "SRR24953469\twinter_control"),
                              "aula_granulosa_calor: amostras.tsv difere do gerado"),
    "demo_linha_com_campo_a_mais": (_em(_DEMO_TSV, "ERS4410319\tHolstein\tCD4\tzenodo_cd4\n",
                                        "ERS4410319\tHolstein\tCD4\tzenodo_cd4\textra\n"),
                                    "nº de campos diferente"),
}


@pytest.mark.parametrize("caso", sorted(CANARIOS))
def test_canarios_copia_mutada(tmp_path, caso):
    mutar, trecho = CANARIOS[caso]
    esp, proj = _copia(tmp_path)
    assert _tudo(esp, proj) == []           # controle: a cópia intocada passa
    mutar(esp, proj)
    problemas = "\n".join(_tudo(esp, proj))
    assert trecho in problemas, problemas


def test_canario_pipeline_sem_a_especie(tmp_path):
    """Pipeline do subprojeto que não declara a espécie dele (só dá para plantar no objeto: os
    pipelines reais declaram todos o bovino)."""
    esp, proj = _copia(tmp_path)

    def sem_bovino(pls: dict) -> dict:
        pl = pls["transcriptomica_de"]
        pls["transcriptomica_de"] = pl.model_copy(update={"especies": ["homo_sapiens", "mus_musculus"]})
        return pls

    problemas = "\n".join(_tudo(esp, proj, sem_bovino))
    assert "pipeline transcriptomica_de não declara a espécie bos_taurus" in problemas, problemas


@pytest.mark.parametrize("mudanca", [{"origem": "dados"}, {"privado": True}])
def test_canario_subprojeto_privado_ou_fora_do_repo(mudanca):
    """O loader já recusa `privado: true` no repositório (regra 7; canário demo_privado). Esta
    conferência pega o que o loader aceita: um registro vindo de <dados>, ou privado, entre os
    subprojetos tidos como do repositório."""
    validas, _ = especies_mod.carregar_especies()
    validos, erros = subprojetos_mod.carregar_subprojetos(validas, [("repo", PROJETOS_REPO)])
    pipelines = carregar_pipelines()[0]
    assert problemas_subprojetos(validos, erros, validas, pipelines) == []    # controle
    reg = validos["bos_taurus/aula_granulosa_calor"]
    if "origem" in mudanca:
        validos["bos_taurus/aula_granulosa_calor"] = replace(reg, origem=mudanca["origem"])
    else:
        validos["bos_taurus/aula_granulosa_calor"] = replace(reg, sub=reg.sub.model_copy(update=mudanca))
    assert any("nunca privado" in x for x in problemas_subprojetos(validos, erros, validas, pipelines))


@pytest.mark.parametrize("chave", sorted(LITERAIS_DOS_SCRIPTS))
def test_canario_cada_ref_bovina_amarrada_ao_literal(chave):
    refs = dict(especies_mod.carregar_especies()[0]["bos_taurus"].assemblies["ARS-UCD1.3"].refs)
    assert problemas_literais(refs) == []    # controle
    refs[chave] += "9"
    assert any(f"ref {chave}=" in x for x in problemas_literais(refs))


def test_canario_ref_bovina_ausente_e_golden_divergente():
    refs = dict(especies_mod.carregar_especies()[0]["bos_taurus"].assemblies["ARS-UCD1.3"].refs)
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    sem_gtf = {k: v for k, v in refs.items() if k != "gtf"}
    assert "faltam refs bovinas: ['gtf']" in problemas_literais(sem_gtf)
    golden["genomica/scripts_api/snpeff"] = golden["genomica/scripts_api/snpeff"].replace("ARS-UCD1.3.113", "X")
    assert any("golden genomica/scripts_api/snpeff" in x for x in problemas_literais(refs, golden))


def test_canario_aula_com_3_condicoes_reprova_no_portao(tmp_path):
    arq = tmp_path / "amostras.tsv"
    texto = (PROJETOS_REPO / "bos_taurus" / "aula_granulosa_calor" / "amostras.tsv").read_text(encoding="utf-8")
    arq.write_text(texto.replace("SRR24953469\tsummer_heat", "SRR24953469\tspring", 1), encoding="utf-8")
    assert not formatos.coldata(arq).ok


def test_gerar_reproduz_os_arquivos_do_repositorio(tmp_path):
    """O gerador, numa árvore tmp, grava exatamente o que está versionado (a menos do fim de
    linha, que o autocrlf do Windows pode trocar) — os dois amostras.tsv e o
    amostras_cd4_demo.tsv."""
    for slug in GERADOS:
        (tmp_path / "bos_taurus" / slug).mkdir(parents=True)
    gravados = gerar(tmp_path)
    assert gravados[-1] == tmp_path / AMOSTRAS_CD4.name   # numa raiz tmp, nada vai ao repositório
    for arq in gravados:
        versionado = AMOSTRAS_CD4 if arq.name == AMOSTRAS_CD4.name else PROJETOS_REPO / arq.relative_to(tmp_path)
        assert arq.read_bytes() == versionado.read_bytes().replace(b"\r\n", b"\n")
        assert b"\r" not in arq.read_bytes()


# ============================================================ amostras_cd4_demo.tsv (F6, §A9.3)
_ERS = re.compile(r"ERS[0-9]+", re.ASCII)


def problemas_cd4(cd4: Path, demo_tsv: Path) -> list[str]:
    """Sincronia do coldata do exemplo da epigenômica com o amostras.tsv do demo: cada linha
    corresponde a UMA linha `epigenomica` do demo, com o mesmo acesso ERS e condicao == grupo;
    e o arquivo é o que o gerador deriva dos coverages (nunca digitado)."""
    p = []
    real = _ler_tsv(cd4)
    if real != linhas_cd4_esperadas():
        p.append("amostras_cd4_demo.tsv difere do gerado; regere com --gerar")
    cab, *linhas = real
    if tuple(cab) != CAB_CD4:
        return p + [f"cabeçalho de amostras_cd4_demo.tsv: {cab} (esperado {CAB_CD4})"]
    _, *demo = _ler_tsv(demo_tsv)
    epi = {(linha[3], linha[2]) for linha in demo if linha[1] == "epigenomica"}
    vistos = []
    for amostra, condicao in linhas:
        ers = _ERS.findall(amostra)
        if len(ers) != 1:
            p.append(f"cd4: {amostra} sem um acesso ERS no nome")
            continue
        if (ers[0], condicao) not in epi:
            p.append(f"cd4: {ers[0]} com condição {condicao!r} sem linha epigenomica igual (acesso, grupo) no demo")
        vistos.append(ers[0])
    if sorted(vistos) != sorted(a for a, _ in epi):
        p.append(f"cd4: acessos {sorted(vistos)} ≠ os da epigenômica no demo {sorted(a for a, _ in epi)}")
    return p


def test_amostras_cd4_controle_gerado_e_em_sincronia_com_o_demo():
    assert problemas_cd4(AMOSTRAS_CD4, PROJETOS_REPO / "bos_taurus" / "demo" / "amostras.tsv") == []
    # a tabela serve de coldata do DMC e casa com os coverages do demo (0 arquivos sem amostra)
    assert formatos.coldata(AMOSTRAS_CD4).ok
    v = formatos.coverages_e_amostras(EPI_DEMO, AMOSTRAS_CD4)
    assert v.ok, v.falhas
    assert v.perfil["por_condicao"] == {"HF": 3, "Nelore": 3} and v.perfil["n_arquivos_sem_amostra"] == 0


@pytest.mark.parametrize("velho, novo, trecho", [
    ("ERS4410317_CD4\tHF", "ERS4410317_CD4\tNelore", "ERS4410317 com condição 'Nelore'"),   # condição trocada
    ("FILTERED_H82_HF_ERS4410318_CD4", "FILTERED_H82_HF_ERS4410399_CD4", "ERS4410399"),       # acesso trocado
    ("amostra\tcondicao", "amostra\tgrupo", "cabeçalho"),
], ids=["condicao_trocada", "acesso_trocado", "cabecalho"])
def test_amostras_cd4_canarios(tmp_path, velho, novo, trecho):
    arq = tmp_path / AMOSTRAS_CD4.name
    texto = AMOSTRAS_CD4.read_text(encoding="utf-8")
    assert texto.count(velho) == 1
    arq.write_text(texto.replace(velho, novo), encoding="utf-8")
    problemas = "\n".join(problemas_cd4(arq, PROJETOS_REPO / "bos_taurus" / "demo" / "amostras.tsv"))
    assert "difere do gerado" in problemas and trecho in problemas, problemas


def test_amostras_cd4_canario_demo_sem_a_linha(tmp_path):
    """O lado do demo também morde: sem a linha epigenomica de um acesso, a sincronia reprova."""
    demo = tmp_path / "amostras.tsv"
    linhas = (PROJETOS_REPO / "bos_taurus" / "demo" / "amostras.tsv").read_text(encoding="utf-8").splitlines()
    demo.write_text("\n".join(x for x in linhas if "ERS4410325" not in x) + "\n", encoding="utf-8")
    assert any("ERS4410325" in x for x in problemas_cd4(AMOSTRAS_CD4, demo))


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Regera os amostras.tsv de projetos/bos_taurus/{demo,aula_granulosa_calor} "
                                             "e modules/epigenomica/data/amostras_cd4_demo.tsv.")
    ap.add_argument("--gerar", action="store_true", help="grava os arquivos (sem a flag, só confere)")
    args = ap.parse_args()
    if args.gerar:
        for caminho in gerar():
            print(f"gravado: {caminho.relative_to(REPO_ROOT).as_posix()}")
    else:
        dif = problemas_amostras(PROJETOS_REPO)
        dif += problemas_cd4(AMOSTRAS_CD4, PROJETOS_REPO / "bos_taurus" / "demo" / "amostras.tsv") \
            if AMOSTRAS_CD4.is_file() else ["amostras_cd4_demo.tsv ausente"]
        print("\n".join(dif) or "amostras.tsv em dia com o gerador")
        sys.exit(1 if dif else 0)
