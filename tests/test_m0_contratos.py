"""M0 da v2 multiespécie — contratos (especificação §3, frente A, e itens da crítica v2).

Canário = o caso que TEM de reprovar; controle = o vizinho válido que tem de passar (sem ele,
um canário verde poderia ser só um loader que recusa tudo). Cada regra nova tem os dois.
"""
from __future__ import annotations

import builtins
import copy
import io
import os
import shutil
import sys
from pathlib import Path

import pytest
import yaml

from fixtures_v2 import apontar_catalogo, escrever_especie, escrever_subprojeto
from omica import chaves_contexto as cc
from omica import especies, selos, subprojetos
from omica.config import get_config, reset_config_cache
from omica.pipelines import Pipeline, carregar_pipelines, validar_contrato
from omica.registry import MODULES_DIR, Manifest, hash_dos_passos, load_registry

SELOS_V1 = {
    ("genomica", "wf_painel_demo"): "46124beee606ccc6",
    ("epigenomica", "wf_mofa_demo"): "89df0a07439b5f96",
    ("epigenomica", "wf_metilacao_demo"): "ef8e5355bc92acc7",
    ("transcriptomica", "wf_de_granulosa"): "ef51927233d11997",
    ("ml", "wf_ml_aims"): "9563a9c0e7dbc8de",
}
MODULOS_BOVINOS = ("genomica", "epigenomica", "transcriptomica", "ml")


# =============================================================== chaves_contexto (A1)

def test_chaves_contexto_lista_fechada():
    assert cc.CHAVES_REF == {"fasta", "gtf", "cdna", "salmon_indice", "homer_preparsed", "snpeff_db",
                             "snpeff_datadir", "vep_cache", "vep_cache_version", "plink_chr_set"}
    assert set(cc.CHAVES_CONTEXTO.values()) == {"caminho", "dir", "texto", "inteiro"}
    assert all(k.startswith(cc.PREFIXOS_CONTEXTO) for k in cc.CHAVES_CONTEXTO)
    assert cc.ID_DEMO == f"{cc.ESPECIE_LEGADA}/{cc.SLUG_DEMO}"
    with pytest.raises(TypeError):
        cc.CHAVES_CONTEXTO["ref_x"] = "caminho"  # imutável: ninguém amplia a lista em tempo de execução
    assert cc.eh_chave_contexto("ref_gtf") and cc.eh_chave_contexto("ref_bowtie")
    assert not cc.eh_chave_contexto("referencia") and not cc.eh_chave_contexto("saida")


@pytest.mark.parametrize("valor", ["bos_taurus\n", "Bos", "bo", "bos-taurus", "bos_taurus/x", "bós_taurus"])
def test_id_especie_canario_fullmatch(valor):
    assert not cc.ID_ESPECIE.fullmatch(valor)


def test_ids_controle():
    assert cc.ID_ESPECIE.fullmatch("bos_taurus") and cc.ID_ESPECIE.fullmatch("mus_musculus")
    assert cc.ID_SUBPROJETO.fullmatch("bos_taurus/demo")
    assert not cc.ID_SUBPROJETO.fullmatch("bos_taurus/../x")
    assert cc.SLUG_NU.fullmatch("sub03") and not cc.SLUG_NU.fullmatch("gen_sub03_rnaseq")


# ===================================================================== registry (A2)

def _manifest_base(mod_id: str = "teste") -> dict:
    return {
        "id": mod_id, "nome_pt": "Teste", "nome_en": "Test",
        "scripts": [{
            "key": "s1", "titulo": "S1", "runtime": "image:omica-bioinfo",
            "cmd": f"python3 modules/{mod_id}/scripts/x.py --entrada {{entrada}} --saida {{dados}}/results/{mod_id}/demo/s1",
            "params": [{"name": "entrada", "rotulo": "Entrada", "default": f"modules/{mod_id}/data/a.txt",
                        "tipo": "caminho"}],
        }],
        "scripts_api": [{
            "key": "a1", "titulo": "A1", "runtime": "image:omica-bioinfo",
            "cmd": f"python3 modules/{mod_id}/scripts/x.py --entrada {{entrada}} --saida {{saida}}",
            "params": [{"name": "entrada", "rotulo": "Entrada", "tipo": "caminho"}],
        }],
        "workflows": [],
    }


def _carregar_modulo(tmp_path: Path, dados: dict) -> tuple[object, str]:
    """Grava um módulo sintético em tmp/modules/<id> e devolve (manifest | None, erro)."""
    mods = tmp_path / "modules"
    pasta = mods / dados["id"]
    (pasta / "scripts").mkdir(parents=True, exist_ok=True)
    (pasta / "scripts" / "x.py").write_text("print('x')\n", encoding="utf-8")
    (pasta / "manifest.yaml").write_text(yaml.safe_dump(dados, allow_unicode=True), encoding="utf-8")
    reg = load_registry(mods)
    return reg.por_id(dados["id"]), reg.erros.get(dados["id"], "")


def test_registry_controle_modulo_sintetico(tmp_path):
    m, erro = _carregar_modulo(tmp_path, _manifest_base())
    assert erro == "" and m is not None
    s = m.scripts[0]
    assert s.especies == ["bos_taurus"] and s.requer_refs == [] and s.params[0].escopo == "livre"


def test_registry_canario_chave_desconhecida(tmp_path):
    dados = _manifest_base()
    dados["scripts"][0]["parms"] = dados["scripts"][0].pop("params")
    m, erro = _carregar_modulo(tmp_path, dados)
    assert m is None and "parms" in erro and "Extra inputs" in erro


@pytest.mark.parametrize("onde", ["topo", "fase", "etapa", "param", "porta", "workflow"])
def test_registry_canario_forbid_em_toda_classe(tmp_path, onde):
    dados = _manifest_base()
    dados["fases"] = [{"id": "f1", "nome": "F1", "etapas": [{"key": "e1", "desc": "E1"}]}]
    dados["scripts_api"][0]["produz"] = {"saida_x": {"arquivo": "x.tsv", "formato": "resumo_json"}}
    dados["scripts"].append({**copy.deepcopy(dados["scripts"][0]), "key": "s2"})
    dados["workflows"] = [{"id": "wf", "titulo": "WF", "passos": ["s1", "s2"]}]
    alvo = {"topo": dados, "fase": dados["fases"][0], "etapa": dados["fases"][0]["etapas"][0],
            "param": dados["scripts"][0]["params"][0], "porta": dados["scripts_api"][0]["produz"]["saida_x"],
            "workflow": dados["workflows"][0]}[onde]
    m, erro = _carregar_modulo(tmp_path, copy.deepcopy(dados))
    assert erro == "", erro  # controle: a estrutura completa é válida
    alvo["campo_digitado_errado"] = 1
    m, erro = _carregar_modulo(tmp_path, dados)
    assert m is None and "campo_digitado_errado" in erro


def _com_cmd_api(cmd_fim: str, **spec_extra) -> dict:
    dados = _manifest_base()
    api = dados["scripts_api"][0]
    api["cmd"] = f"python3 modules/teste/scripts/x.py --entrada {{entrada}} {cmd_fim} --saida {{saida}}"
    api.update(spec_extra)
    return dados


def test_registry_controle_ref_declarada(tmp_path):
    m, erro = _carregar_modulo(tmp_path, _com_cmd_api("--gtf {ref_gtf}", requer_refs=["ref_gtf"]))
    assert erro == "" and m.scripts_api[0].requer_refs == ["ref_gtf"]


@pytest.mark.parametrize("cmd_fim,extra,trecho", [
    ("--gtf {ref_gtf}", {}, "sem declarar em requer_refs"),
    ("--x {ref_bowtie}", {"requer_refs": ["ref_bowtie"]}, "fora da lista fechada"),
    ("--x {ref_bowtie}", {}, "fora da lista fechada"),
    ("--gtf {ref.gtf}", {}, "não é um nome simples"),
    ("--y {fantasma}", {}, "placeholder sem parâmetro"),
    ("", {"requer_refs": ["ref_gtff"]}, "requer_refs fora de CHAVES_CONTEXTO"),
    ("", {"especies": []}, "especies vazio"),
    ("", {"especies": ["Bos"]}, "especies vazio ou fora do padrão"),
])
def test_registry_canarios_contexto(tmp_path, cmd_fim, extra, trecho):
    m, erro = _carregar_modulo(tmp_path, _com_cmd_api(cmd_fim, **extra))
    assert m is None and trecho in erro, erro


@pytest.mark.parametrize("nome", ["ref_x", "saida", "dados", "sub_amostras", "esp_assembly"])
def test_registry_canario_parametro_reservado(tmp_path, nome):
    dados = _manifest_base()
    dados["scripts_api"][0]["params"].append({"name": nome, "rotulo": "X", "default": "1"})
    m, erro = _carregar_modulo(tmp_path, dados)
    assert m is None and f"parâmetro {nome!r} usa nome reservado" in erro, erro


def test_registry_canario_placeholder_orfao_em_scripts(tmp_path):
    dados = _manifest_base()
    dados["scripts"][0]["cmd"] += " --log {log_dir}"
    m, erro = _carregar_modulo(tmp_path, dados)
    assert m is None and "placeholder sem parâmetro ['log_dir']" in erro


def test_registry_canario_modulo_reservado(tmp_path):
    m, erro = _carregar_modulo(tmp_path, _manifest_base("projetos"))
    assert m is None and "id de módulo reservado" in erro
    m, erro = _carregar_modulo(tmp_path, _manifest_base("projetosx"))
    assert erro == ""  # controle: só o id exato é reservado


@pytest.mark.parametrize("validado_em,ok", [
    (["sub03"], False), (["bos_taurus"], False), (["bos_taurus/../x"], False),
    (["bos_taurus/demo"], True), (["bos_taurus/demo", "homo_sapiens/teste_rna"], True),
])
def test_registry_validado_em(tmp_path, validado_em, ok):
    dados = _manifest_base()
    dados["scripts"].append({**copy.deepcopy(dados["scripts"][0]), "key": "s2"})
    dados["workflows"] = [{"id": "wf", "titulo": "WF", "passos": ["s1", "s2"], "validado_em": validado_em}]
    m, erro = _carregar_modulo(tmp_path, dados)
    assert (erro == "") is ok, erro
    if not ok:
        assert "validado_em fora do padrão" in erro


def test_registry_validado_em_default_e_o_demo():
    for m in load_registry().modules:
        for wf in m.workflows:
            assert wf.validado_em == [cc.ID_DEMO]


def test_registry_controle_manifests_reais():
    reg = load_registry()
    assert reg.erros == {}
    assert [m.id for m in reg.modules] == ["genomica", "epigenomica", "transcriptomica", "ml"]
    tr = reg.por_id("transcriptomica")
    api = {s.key: s for s in tr.scripts_api}
    assert api["deseq2"].especies == api["volcano"].especies == ["bos_taurus", "homo_sapiens", "mus_musculus"]
    # o resto continua só bovino (default)
    assert all(s.especies == ["bos_taurus"] for m in reg.modules for s in m.scripts)


def test_campos_novos_fora_do_hash():
    """Os campos novos, injetados em memória em TODO spec/param/workflow, não mexem nos 5 selos."""
    for pasta in sorted(p for p in MODULES_DIR.iterdir() if p.is_dir()):
        bruto = yaml.safe_load((pasta / "manifest.yaml").read_text(encoding="utf-8"))
        for spec in [*bruto.get("scripts", []), *bruto.get("scripts_api", [])]:
            spec["especies"] = ["bos_taurus", "homo_sapiens"]
            spec["requer_refs"] = ["ref_gtf"]
            for p in spec.get("params", []):
                p["escopo"] = "especie"
        for wf in bruto.get("workflows", []):
            wf["validado_em"] = ["bos_taurus/demo", "bos_taurus/outro"]
        m = Manifest.model_validate(bruto)
        for wf in m.workflows:
            if (m.id, wf.id) in SELOS_V1:
                assert hash_dos_passos(m, wf) == SELOS_V1[(m.id, wf.id)], (m.id, wf.id)


# ==================================================================== pipelines (A3)

def test_pipeline_transcriptomica_de_tem_as_3_especies():
    validos, erros = carregar_pipelines()
    assert erros == {}
    assert validos["transcriptomica_de"].especies == ["bos_taurus", "homo_sapiens", "mus_musculus"]
    assert validos["genomica_painel"].especies == ["bos_taurus"]  # default


def test_pipeline_canario_especie_alem_dos_scripts():
    validos, _ = carregar_pipelines()
    gen = load_registry().por_id("genomica")
    p = validos["genomica_painel"]
    assert validar_contrato(p, gen) == []  # controle
    humano = p.model_copy(update={"especies": ["homo_sapiens"]})
    problemas = validar_contrato(humano, gen)
    assert any("não declara as espécies ['homo_sapiens']" in x for x in problemas), problemas


@pytest.mark.parametrize("especies_p", [[], ["Bos"], ["bos_taurus", "x"]])
def test_pipeline_canario_especies_fora_do_padrao(especies_p):
    validos, _ = carregar_pipelines()
    tr = load_registry().por_id("transcriptomica")
    p = validos["transcriptomica_de"].model_copy(update={"especies": especies_p})
    assert any("especies vazio ou fora do padrão" in x for x in validar_contrato(p, tr))


def test_pipeline_canario_saida_escapada():
    """`{{saida}}` é TEXTO para o str.format: o run gravaria no caminho literal "{saida}". A busca
    de texto `"{saida}" in cmd` aprovava; o parser de campos reprova (resíduo do achado #37)."""
    validos, _ = carregar_pipelines()
    tr = load_registry().por_id("transcriptomica")
    p = validos["transcriptomica_de"]
    assert validar_contrato(p, tr) == []  # controle
    tr2 = tr.model_copy(deep=True)
    deseq = next(s for s in tr2.scripts_api if s.key == "deseq2")
    assert "--saida {saida}" in deseq.cmd
    deseq.cmd = deseq.cmd.replace("--saida {saida}", "--saida {{saida}}")
    problemas = validar_contrato(p, tr2)
    assert any("não grava em {saida}" in x for x in problemas), problemas


def test_pipeline_forbid_continua(tmp_path):
    bruto = yaml.safe_load((Path(__file__).resolve().parents[1] / "pipelines" / "transcriptomica_de.yaml")
                           .read_text(encoding="utf-8"))
    Pipeline.model_validate(bruto)  # controle
    bruto["especie"] = ["homo_sapiens"]  # singular: digitação errada
    with pytest.raises(Exception, match="especie"):
        Pipeline.model_validate(bruto)


# ============================================================= config e selos (A4)

@pytest.mark.parametrize("valor,esperado", [
    ("1", True), ("true", True), ("SIM", True), (" yes ", True),
    ("0", False), ("nao", False), ("false", False), ("2", False),
])
def test_config_permitir_rascunho(monkeypatch, valor, esperado):
    monkeypatch.setenv("OMICA_PERMITIR_RASCUNHO", valor)
    reset_config_cache()
    assert get_config().permitir_rascunho is esperado


def test_config_rascunho_desligado_por_padrao():
    cfg = get_config()
    assert cfg.permitir_rascunho is False
    assert cfg.projetos_dir == cfg.data_dir / "projetos"


def test_ensure_dirs_nao_cria_projetos(cfg):
    assert cfg.results_dir.is_dir()  # controle: o fixture rodou ensure_dirs
    assert not cfg.projetos_dir.exists()


def _modules_com_parms(tmp_path: Path) -> Path:
    copia = tmp_path / "repo" / "modules"
    shutil.copytree(MODULES_DIR, copia, ignore=shutil.ignore_patterns("demo_data", "__pycache__"))
    arq = copia / "ml" / "manifest.yaml"
    texto = arq.read_text(encoding="utf-8")
    assert "\n    params:" in texto
    arq.write_text(texto.replace("\n    params:", "\n    parms:", 1), encoding="utf-8")
    return copia


def test_selos_canario_manifest_invalido(tmp_path, capsys):
    copia = _modules_com_parms(tmp_path)
    assert "ml" in selos.erros_de_carga(copia)
    antes = (copia / "ml" / "manifest.yaml").read_bytes()
    assert selos.main(modules_dir=copia) == 1
    saida = capsys.readouterr().out
    assert "manifest inválido ml:" in saida
    # no --gravar também: nada é regravado
    assert selos.main(gravar=True, modules_dir=copia) == 1
    assert "nada foi regravado" in capsys.readouterr().out
    assert (copia / "ml" / "manifest.yaml").read_bytes() == antes
    assert (copia / "genomica" / "manifest.yaml").read_bytes() == (MODULES_DIR / "genomica" / "manifest.yaml").read_bytes()


def test_selos_controle_real(capsys):
    assert selos.erros_de_carga() == {}
    assert selos.main() == 0
    saida = capsys.readouterr().out
    assert saida.count("válido") == 5 and "vencido" not in saida


def test_selos_stdout_cp1252_nao_quebra(monkeypatch):
    """Crítica v2, bloqueante 3: no stdout cp1252 do host o print de "✅" levantava
    UnicodeEncodeError e `python -m omica selos` saía com 1 com os 5 selos válidos."""
    bruto = io.BytesIO()
    fluxo = io.TextIOWrapper(bruto, encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", fluxo)
    assert selos.main() == 0
    fluxo.flush()
    texto = bruto.getvalue().decode("cp1252")
    assert "\\u2705" in texto and texto.count("válido") == 5


# =================================================================== espécies (A5)

def _esp_dir(tmp_path: Path) -> Path:
    return tmp_path / "especies_repo"


def _carregar_esp(tmp_path: Path):
    return especies.carregar_especies(_esp_dir(tmp_path))


def test_especies_controle(tmp_path):
    d = _esp_dir(tmp_path)
    escrever_especie(d, "homo_sapiens", ordem=1)
    escrever_especie(d, "mus_musculus", assembly="GRCm39", prefixo="ENSMUSG", ordem=2,
                     refs={"plink_chr_set": "19"})
    escrever_especie(d, "bos_taurus", assembly="ARS-UCD1.3", prefixo="ENSBTAG", ordem=3,
                     modulos=MODULOS_BOVINOS, refs={"gtf": "/data/snpeff/genes.gtf.gz", "snpeff_db": "ARS-UCD1.3.113",
                                                    "vep_cache_version": "110"},
                     parametros={"transcriptomica.deseq2.padj": "0.1"},
                     recursos={"vcf": "/data/1000bg_vcf"}, nunca_usar=["/data/x/amostras_racas.csv"],
                     nao_referencia=["modules/genomica/data/paineis/termotolerancia.txt"])
    validas, erros = _carregar_esp(tmp_path)
    assert erros == {}
    assert list(validas) == ["homo_sapiens", "mus_musculus", "bos_taurus"]  # por `ordem`
    asm = validas["bos_taurus"].assemblies["ARS-UCD1.3"]
    assert asm.refs["gtf"] == "/data/snpeff/genes.gtf.gz"


def test_especies_le_a_pasta_na_chamada(tmp_path, monkeypatch):
    d = _esp_dir(tmp_path)
    escrever_especie(d, "homo_sapiens")
    monkeypatch.setattr(especies, "ESPECIES_DIR", d)
    validas, erros = especies.carregar_especies()
    assert list(validas) == ["homo_sapiens"] and erros == {}


def _sem_chave(arq: Path, chave: str) -> None:
    dados = yaml.safe_load(arq.read_text(encoding="utf-8"))
    dados.pop(chave)
    arq.write_text(yaml.safe_dump(dados), encoding="utf-8")


@pytest.mark.parametrize("caso", [
    "id_difere", "ref_bowtie", "ref_etc_passwd", "ref_chave", "ref_absoluta_do_host", "ref_inteiro_texto",
    "assembly_padrao_fora", "assembly_padrao_ausente", "campo_desconhecido", "campo_assembly",
    "modulo_reservado", "equivalencia_fora", "recurso_etc_shadow", "nunca_usar_pontos", "nao_referencia_relativa",
    "parametro_chave_fora_do_padrao", "parametro_com_chave", "parametro_inexistente", "parametro_valor_invalido",
    "parametro_modulo_fora", "parametro_script_inexistente",
])
def test_especies_canarios(tmp_path, caso):
    d = _esp_dir(tmp_path)
    escrever_especie(d, "mus_musculus", assembly="GRCm39", ordem=2)  # vizinho válido
    base = dict(pasta=d, id="homo_sapiens")
    if caso == "id_difere":
        arq = escrever_especie(**base)
        arq.rename(d / "humano.yaml")
    elif caso == "ref_bowtie":
        escrever_especie(**base, refs={"bowtie": "/data/x"})
    elif caso == "ref_etc_passwd":
        escrever_especie(**base, refs={"gtf": "/etc/passwd"})
    elif caso == "ref_chave":
        escrever_especie(**base, refs={"gtf": "/data/{x}/genes.gtf"})
    elif caso == "ref_absoluta_do_host":
        # o sanitizador aceita a pasta de dados absoluta; o YAML não (no_disco não a leria)
        escrever_especie(**base, refs={"gtf": f"{get_config().data_dir.as_posix()}/genes.gtf"})
    elif caso == "ref_inteiro_texto":
        escrever_especie(**base, refs={"vep_cache_version": "cento e dez"})
    elif caso == "assembly_padrao_fora":
        escrever_especie(**base, assembly_padrao="GRCh99")
    elif caso == "assembly_padrao_ausente":
        _sem_chave(escrever_especie(**base), "assembly_padrao")
    elif caso == "campo_desconhecido":
        escrever_especie(**base, estado="pronta")  # o estado é calculado, nunca declarado
    elif caso == "campo_assembly":
        escrever_especie(**base, assemblies={"GRCh38": {"contigs": "chr", "prefixo_gene": "ENSG", "ref": {}}})
    elif caso == "modulo_reservado":
        escrever_especie(**base, modulos=["transcriptomica", "projetos"])
    elif caso == "equivalencia_fora":
        escrever_especie(**base, assemblies={"GRCh38": {
            "contigs": "chr", "prefixo_gene": "ENSG",
            "equivalencias": {"bowtie": {"assembly": "hg19", "relacao": "identica"}}}})
    elif caso == "recurso_etc_shadow":
        escrever_especie(**base, recursos={"x": "/etc/shadow"})
    elif caso == "nunca_usar_pontos":
        escrever_especie(**base, nunca_usar=["/data/../etc/shadow"])
    elif caso == "nao_referencia_relativa":
        escrever_especie(**base, nao_referencia=["../fora/x.txt"])
    elif caso == "parametro_chave_fora_do_padrao":
        escrever_especie(**base, parametros={"deseq2.padj": "0.1"})
    elif caso == "parametro_com_chave":
        escrever_especie(**base, parametros={"transcriptomica.deseq2.padj": "{x}"})
    elif caso == "parametro_inexistente":
        escrever_especie(**base, parametros={"transcriptomica.deseq2.padjj": "0.1"})
    elif caso == "parametro_valor_invalido":
        escrever_especie(**base, parametros={"transcriptomica.deseq2.padj": "muito"})
    elif caso == "parametro_modulo_fora":
        escrever_especie(**base, parametros={"genomica.painel_bed.pad": "100"})
    elif caso == "parametro_script_inexistente":
        escrever_especie(**base, parametros={"transcriptomica.deseq3.padj": "0.1"})
    validas, erros = _carregar_esp(tmp_path)
    assert "homo_sapiens" not in validas
    assert len(erros) == 1, erros
    assert "mus_musculus" in validas  # uma espécie ruim não derruba a outra


def test_no_disco(tmp_path):
    assert especies.no_disco("/data/a/b.txt") == get_config().genome_data / "a" / "b.txt"
    assert especies.no_disco("modules/genomica/data") == especies.REPO_ROOT / "modules" / "genomica" / "data"
    for ruim in ["/etc/shadow", "/data", "/data/../etc/shadow", "modules/../../x", "C:/Windows/x",
                 "projetos/a/../../x", "../x", "/data/a b", "/data/.oculto", "/data/a\\b"]:
        with pytest.raises(ValueError):
            especies.no_disco(ruim)


def _humano_com_refs(tmp_path: Path, refs: dict) -> especies.Especie:
    escrever_especie(_esp_dir(tmp_path), "homo_sapiens", refs=refs)
    validas, erros = _carregar_esp(tmp_path)
    assert erros == {}
    return validas["homo_sapiens"]


def test_estado_pronta_parcial_sem_referencia(tmp_path):
    # GENOME_DATA = tmp_path (conftest): /data/x → tmp_path/x
    e = _humano_com_refs(tmp_path, {"gtf": "/data/ref/genes.gtf.gz", "salmon_indice": "/data/ref/salmon",
                                    "snpeff_db": "GRCh38.113", "plink_chr_set": "22"})
    assert especies.estado(e) == especies.EstadoEspecie("sem_referencia", 0, 2, ("gtf", "salmon_indice"))
    (tmp_path / "ref").mkdir()
    (tmp_path / "ref" / "genes.gtf.gz").write_bytes(b"x")
    assert especies.estado(e) == especies.EstadoEspecie("parcial", 1, 2, ("salmon_indice",))
    (tmp_path / "ref" / "salmon").mkdir()
    assert especies.estado(e, "GRCh38") == especies.EstadoEspecie("pronta", 2, 2, ())
    with pytest.raises(ValueError):
        especies.estado(e, "GRCh99")


def test_estado_sem_refs_de_arquivo(tmp_path):
    e = _humano_com_refs(tmp_path, {"plink_chr_set": "19", "snpeff_db": "x"})
    assert especies.estado(e) == especies.EstadoEspecie("sem_referencia", 0, 0, ())


def test_estado_so_faz_stat(tmp_path, monkeypatch):
    e = _humano_com_refs(tmp_path, {"gtf": "/data/ref/genes.gtf.gz", "salmon_indice": "/data/ref/salmon"})
    (tmp_path / "ref" / "salmon").mkdir(parents=True)
    (tmp_path / "ref" / "genes.gtf.gz").write_bytes(b"x")
    get_config()  # a config entra no cache antes das trocas

    def proibido(*a, **k):
        raise AssertionError("estado() leu conteúdo de arquivo")

    monkeypatch.setattr(Path, "read_bytes", proibido)
    monkeypatch.setattr(Path, "read_text", proibido)
    monkeypatch.setattr(Path, "open", proibido)
    monkeypatch.setattr(builtins, "open", proibido)
    assert especies.estado(e).estado == "pronta"


# ================================================================ subprojetos (A5)

def _catalogo(tmp_path: Path) -> tuple[dict, Path, Path]:
    d = _esp_dir(tmp_path)
    escrever_especie(d, "homo_sapiens", ordem=1)
    escrever_especie(d, "bos_taurus", assembly="ARS-UCD1.3", prefixo="ENSBTAG", ordem=3, modulos=MODULOS_BOVINOS)
    validas, erros = especies.carregar_especies(d)
    assert erros == {}
    return validas, tmp_path / "projetos_repo", get_config().projetos_dir


def _carregar_sub(esp, repo, dados):
    return subprojetos.carregar_subprojetos(esp, [("repo", repo), ("dados", dados)])


def _demo(repo: Path) -> Path:
    return escrever_subprojeto(repo, "bos_taurus", "demo", status="valido", tipo="demo", assembly="ARS-UCD1.3",
                               modulos=MODULOS_BOVINOS, validade="selos 5/5")


def test_subprojetos_controle(tmp_path):
    esp, repo, dados = _catalogo(tmp_path)
    _demo(repo)
    escrever_subprojeto(repo, "bos_taurus", "aula_x", status="valido", tipo="aula", assembly="ARS-UCD1.3",
                        legado=["sub01_aula"], parametros={"transcriptomica.deseq2.padj": "0.1"},
                        datasets={"GSE1": "modules/transcriptomica/demo_data/counts_granulosa.tsv"},
                        dados_legados=["/data/omica/results/transcriptomica/demo"])
    escrever_subprojeto(dados, "homo_sapiens", "teste_rna", privado=True)  # rascunho humano em <dados>
    escrever_subprojeto(dados, "homo_sapiens", "velho", status="invalido", validade="dados trocados",
                        substituido_por="homo_sapiens/teste_rna", privado=True)
    escrever_subprojeto(dados, "homo_sapiens", "revisar", status="a_revisar", privado=True)
    validos, erros = _carregar_sub(esp, repo, dados)
    assert erros == {}
    assert set(validos) == {"bos_taurus/demo", "bos_taurus/aula_x", "homo_sapiens/teste_rna",
                            "homo_sapiens/velho", "homo_sapiens/revisar"}
    assert validos["bos_taurus/demo"].origem == "repo" and validos["homo_sapiens/teste_rna"].origem == "dados"
    assert validos["homo_sapiens/teste_rna"].sub.executavel
    assert not validos["homo_sapiens/velho"].sub.executavel
    assert validos["homo_sapiens/revisar"].sub.executavel
    assert validos["bos_taurus/aula_x"].sub.slug == "aula_x"
    assert subprojetos.n_amostras(validos["homo_sapiens/teste_rna"]) == 2


def test_subprojetos_raizes_na_chamada(tmp_path, monkeypatch):
    esp, repo, dados = _catalogo(tmp_path)
    _demo(repo)
    apontar_catalogo(monkeypatch, _esp_dir(tmp_path), repo)
    assert subprojetos.raizes() == [("repo", repo), ("dados", dados)]
    validos, erros = subprojetos.carregar_subprojetos(esp)
    assert list(validos) == ["bos_taurus/demo"] and erros == {}


@pytest.mark.parametrize("caso", [
    "assembly_de_outra_especie", "slug_nu", "executavel_declarado", "id_com_pontos", "invalido_sem_validade",
    "substituido_com_valido", "parametros_com_chave", "parametro_inexistente", "privado_no_repo",
    "painel_fora", "amostras_fora", "dataset_etc_shadow", "dados_legados_pontos", "modulo_fora_da_especie",
    "especie_inexistente", "id_difere_do_caminho", "titulo_longo", "titulo_controle", "pergunta_curta",
])
def test_subprojetos_canarios(tmp_path, caso):
    esp, repo, dados = _catalogo(tmp_path)
    _demo(repo)  # vizinho válido: tem de continuar carregado
    kw: dict = {}
    slug, especie, raiz = "alvo_x", "bos_taurus", repo
    if caso == "assembly_de_outra_especie":
        kw = {"assembly": "GRCh38"}
    elif caso == "slug_nu":
        slug = "sub03"
    elif caso == "executavel_declarado":
        kw = {"executavel": True}
    elif caso == "id_com_pontos":
        kw = {"id": "bos_taurus/../x"}
    elif caso == "invalido_sem_validade":
        kw = {"status": "invalido"}
    elif caso == "substituido_com_valido":
        kw = {"status": "valido", "substituido_por": "bos_taurus/demo"}
    elif caso == "parametros_com_chave":
        kw = {"parametros": {"transcriptomica.deseq2.padj": "{ref_gtf}"}}
    elif caso == "parametro_inexistente":
        kw = {"parametros": {"transcriptomica.deseq2.padjj": "0.1"}}
    elif caso == "privado_no_repo":
        kw = {"privado": True}
    elif caso == "painel_fora":
        kw = {"painel": "../x.bed"}
    elif caso == "amostras_fora":
        kw = {"amostras": "../amostras.tsv"}
    elif caso == "dataset_etc_shadow":
        kw = {"datasets": {"x": "/etc/shadow"}}
    elif caso == "dados_legados_pontos":
        kw = {"dados_legados": ["/data/omica/../../etc"]}
    elif caso == "modulo_fora_da_especie":
        especie, kw = "homo_sapiens", {"assembly": "GRCh38", "modulos": ["genomica"]}
    elif caso == "especie_inexistente":
        especie, kw = "mus_musculus", {"assembly": "GRCm39"}
    elif caso == "id_difere_do_caminho":
        kw = {"id": "bos_taurus/outro_nome"}
    elif caso == "titulo_longo":
        kw = {"titulo": "t" * 201}
    elif caso == "titulo_controle":
        kw = {"titulo": "linha\x07sino"}
    elif caso == "pergunta_curta":
        kw = {"pergunta": "curta?"}
    kw.setdefault("assembly", "ARS-UCD1.3")
    if caso in ("modulo_fora_da_especie", "especie_inexistente"):
        raiz = dados
        kw["privado"] = True
    escrever_subprojeto(raiz, especie, slug, **kw)
    validos, erros = _carregar_sub(esp, repo, dados)
    assert f"{especie}/{slug}" not in validos
    assert len(erros) == 1, erros
    assert "bos_taurus/demo" in validos


def _sub_bruto(**kw) -> dict:
    bruto = {"id": "bos_taurus/aula_x", "especie": "bos_taurus", "assembly": "ARS-UCD1.3",
             "titulo": "Teste", "pergunta": "Pergunta de teste do subprojeto?", "tipo": "pesquisa",
             "status": "draft", "modulos": ["transcriptomica"], "pipelines": []}
    bruto.update(kw)
    return bruto


@pytest.mark.parametrize("campo,valor", [
    ("amostras", "sub/amostras.tsv"), ("amostras", "x/../../amostras.tsv"), ("amostras", "amostras.tsv\n"),
    ("painel", "a/../b.bed"), ("painel", "sub/p.bed"), ("painel", "p.bed\n"),
])
def test_subprojeto_arquivo_simples_e_ancorado(campo, valor):
    """Médio 10b: `amostras`/`painel` são nome simples por re.FULLMATCH. Estes valores começam
    com caractere válido, então só a âncora no fim os recusa (`.match` os deixaria passar)."""
    subprojetos.Subprojeto.model_validate(_sub_bruto(amostras="amostras.tsv", painel="painel.bed"))  # controle
    with pytest.raises(Exception, match=campo):
        subprojetos.Subprojeto.model_validate(_sub_bruto(**{campo: valor}))


def test_subprojetos_controle_pergunta_multilinha(tmp_path):
    esp, repo, dados = _catalogo(tmp_path)
    escrever_subprojeto(repo, "bos_taurus", "aula_x", assembly="ARS-UCD1.3", status="valido",
                        pergunta="Primeira linha da pergunta\nsegunda linha\tcom tab.")
    validos, erros = _carregar_sub(esp, repo, dados)
    assert erros == {} and "bos_taurus/aula_x" in validos


def test_subprojetos_demo_so_no_repo(tmp_path):
    esp, repo, dados = _catalogo(tmp_path)
    _demo(repo)
    _demo(dados)
    validos, erros = _carregar_sub(esp, repo, dados)
    assert validos["bos_taurus/demo"].origem == "repo"
    assert list(erros) == ["dados:bos_taurus/demo"]


def test_subprojetos_demo_fora_do_repo_recusado_mesmo_sozinho(tmp_path):
    esp, repo, dados = _catalogo(tmp_path)
    escrever_subprojeto(dados, "bos_taurus", "demo", status="valido", tipo="demo", assembly="ARS-UCD1.3")
    validos, erros = _carregar_sub(esp, repo, dados)
    assert "bos_taurus/demo" not in validos and "reservado ao repositório" in erros["dados:bos_taurus/demo"]


def test_subprojetos_id_duplicado_em_dados(tmp_path):
    esp, repo, dados = _catalogo(tmp_path)
    escrever_subprojeto(repo, "homo_sapiens", "teste_rna", status="valido")
    escrever_subprojeto(dados, "homo_sapiens", "teste_rna", privado=True)
    validos, erros = _carregar_sub(esp, repo, dados)
    assert validos["homo_sapiens/teste_rna"].origem == "repo"
    assert "id duplicado" in erros["dados:homo_sapiens/teste_rna"]


@pytest.mark.parametrize("alias", ["homo_sapiens/teste_rna", "HOMO_SAPIENS/teste_rna", "sub01", "SUB01"])
def test_subprojetos_alias_colide(tmp_path, alias):
    esp, repo, dados = _catalogo(tmp_path)
    escrever_subprojeto(repo, "homo_sapiens", "teste_rna", status="valido", legado=["sub01"])
    escrever_subprojeto(repo, "homo_sapiens", "zz_outro", status="valido", legado=[alias])
    validos, erros = _carregar_sub(esp, repo, dados)
    assert "homo_sapiens/teste_rna" in validos and "homo_sapiens/zz_outro" not in validos
    assert "colide" in erros["repo:homo_sapiens/zz_outro"]


def test_subprojetos_alias_controle(tmp_path):
    esp, repo, dados = _catalogo(tmp_path)
    escrever_subprojeto(repo, "homo_sapiens", "teste_rna", status="valido", legado=["genomes:sub01"])
    escrever_subprojeto(repo, "homo_sapiens", "zz_outro", status="valido", legado=["epigenoma:SUB01", "sub02 (plano B)"])
    validos, erros = _carregar_sub(esp, repo, dados)
    assert erros == {} and len(validos) == 2


def test_subprojetos_ignora_temporaria_e_reporta_pasta_sem_yaml(tmp_path):
    esp, repo, dados = _catalogo(tmp_path)
    (dados / "homo_sapiens" / ".tmp-novo-abc123").mkdir(parents=True)
    (dados / "homo_sapiens" / ".tmp-novo-abc123" / "subprojeto.yaml").write_text("id: x\n", encoding="utf-8")
    (dados / "homo_sapiens" / "sem_yaml").mkdir()
    validos, erros = _carregar_sub(esp, repo, dados)
    assert validos == {}
    assert list(erros) == ["dados:homo_sapiens/sem_yaml"]


def _link_de_pasta(link: Path, alvo: Path) -> str:
    try:
        os.symlink(alvo, link, target_is_directory=True)
        return "symlink"
    except (OSError, NotImplementedError):
        if os.name != "nt":
            pytest.skip("sem permissão para criar link simbólico")
        import _winapi

        _winapi.CreateJunction(str(alvo), str(link))
        return "junction"


def test_subprojetos_canario_pasta_link(tmp_path):
    esp, repo, dados = _catalogo(tmp_path)
    fora = escrever_subprojeto(tmp_path / "fora", "homo_sapiens", "teste_rna", status="valido")
    (repo / "homo_sapiens").mkdir(parents=True)
    _link_de_pasta(repo / "homo_sapiens" / "teste_rna", fora)
    validos, erros = _carregar_sub(esp, repo, dados)
    assert "homo_sapiens/teste_rna" not in validos
    assert "link simbólico ou resolve fora da raiz" in erros["repo:homo_sapiens/teste_rna"]


def test_subprojetos_canario_amostras_link(tmp_path):
    esp, repo, dados = _catalogo(tmp_path)
    pasta = escrever_subprojeto(repo, "homo_sapiens", "teste_rna", status="valido", amostras_tsv=None)
    segredo = tmp_path / "segredo.tsv"
    segredo.write_text("amostra\nx\n", encoding="utf-8")
    try:
        os.symlink(segredo, pasta / "amostras.tsv")
    except (OSError, NotImplementedError):
        pytest.skip("sem permissão para criar link simbólico de arquivo")
    validos, erros = _carregar_sub(esp, repo, dados)
    assert "homo_sapiens/teste_rna" not in validos and "link simbólico" in erros["repo:homo_sapiens/teste_rna"]


def test_n_amostras(tmp_path):
    esp, repo, dados = _catalogo(tmp_path)
    escrever_subprojeto(repo, "homo_sapiens", "a_tres", status="valido", amostras_tsv="amostra\tc\nA\tx\n\nB\ty\r\nC\tz")
    escrever_subprojeto(repo, "homo_sapiens", "b_sem", status="valido", amostras_tsv=None)
    validos, _ = _carregar_sub(esp, repo, dados)
    assert subprojetos.n_amostras(validos["homo_sapiens/a_tres"]) == 3
    assert subprojetos.n_amostras(validos["homo_sapiens/b_sem"]) is None


# ============================================================ criar_rascunho (A5)

@pytest.fixture()
def rascunho(tmp_path, monkeypatch):
    """Catálogo de teste com a flag LIGADA; devolve (repo, dados)."""
    esp_dir = _esp_dir(tmp_path)
    escrever_especie(esp_dir, "homo_sapiens", ordem=1)
    escrever_especie(esp_dir, "bos_taurus", assembly="ARS-UCD1.3", prefixo="ENSBTAG", ordem=3, modulos=MODULOS_BOVINOS)
    repo = tmp_path / "projetos_repo"
    apontar_catalogo(monkeypatch, esp_dir, repo)
    monkeypatch.setenv("OMICA_PERMITIR_RASCUNHO", "1")
    reset_config_cache()
    return repo, get_config().projetos_dir


def _criar(slug="teste_rna", **kw):
    args = dict(titulo="Matriz de teste", pergunta="Quais genes mudam entre c e t?", assembly="GRCh38",
                amostras=b"amostra\tcondicao\nA1\tc\nA2\tt\n")
    args.update(kw)
    return subprojetos.criar_rascunho(kw.pop("especie", "homo_sapiens"), slug,
                                      **{k: v for k, v in args.items() if k != "especie"})


def test_rascunho_controle_grava_e_recarrega(rascunho):
    repo, dados = rascunho
    reg = _criar()
    assert reg.origem == "dados" and reg.pasta == dados / "homo_sapiens" / "teste_rna"
    assert (reg.sub.status, reg.sub.privado, reg.sub.tipo) == ("draft", True, "pesquisa")
    validos, erros = subprojetos.carregar_subprojetos(especies.carregar_especies()[0])
    assert erros == {}
    sub = validos["homo_sapiens/teste_rna"].sub
    assert (sub.status, sub.privado, sub.executavel) == ("draft", True, True)
    assert subprojetos.n_amostras(validos["homo_sapiens/teste_rna"]) == 2
    assert [p.name for p in (dados / "homo_sapiens").iterdir()] == ["teste_rna"]  # sem temporária


def test_rascunho_canario_flag_desligada(rascunho, monkeypatch):
    monkeypatch.setenv("OMICA_PERMITIR_RASCUNHO", "0")
    reset_config_cache()

    def proibido(*a, **k):
        raise AssertionError("E/S antes de conferir a flag")

    monkeypatch.setattr(subprojetos, "carregar_especies", proibido)
    monkeypatch.setattr(subprojetos, "carregar_subprojetos", proibido)
    with pytest.raises(subprojetos.RascunhoRecusado) as e:
        _criar()
    assert e.value.erro == "rascunho_desligado"
    assert not get_config().projetos_dir.exists()


@pytest.mark.parametrize("kw,erro", [
    ({"slug": "sub03"}, "slug_invalido"),
    ({"slug": "Teste"}, "slug_invalido"),
    ({"slug": "te"}, "slug_invalido"),
    ({"slug": "../x"}, "slug_invalido"),
    ({"especie": "mus_musculus"}, "especie_inexistente"),
    ({"assembly": "ARS-UCD1.3"}, "assembly_invalida"),
    ({"pergunta": "curta?"}, "pergunta_curta"),
    ({"pergunta": "p" * 2001}, "pergunta_invalida"),
    ({"pergunta": "Pergunta com\x1b[31m escape ANSI"}, "pergunta_invalida"),
    ({"titulo": ""}, "titulo_invalido"),
    ({"titulo": "t" * 201}, "titulo_invalido"),
    ({"titulo": "duas\nlinhas"}, "titulo_invalido"),
    ({"amostras": b"amostra\tc\nA1\t{ref_gtf}\n"}, "amostras_invalidas"),
    ({"amostras": b"amostra\tc\n" + b"A\tx\n" * 300_000}, "amostras_invalidas"),
    ({"amostras": "amostra\tc\nÁ\tx\n".encode("latin-1")}, "amostras_invalidas"),
    ({"amostras": b"amostra\tc\rA1\tx\r"}, "amostras_invalidas"),
    ({"amostras": b"sample\tc\nA1\tx\n"}, "amostras_invalidas"),
    ({"amostras": b"amostra\tc\nA1\x00\tx\n"}, "amostras_invalidas"),
])
def test_rascunho_canarios(rascunho, kw, erro):
    repo, dados = rascunho
    with pytest.raises(subprojetos.RascunhoRecusado) as e:
        _criar(**kw)
    assert e.value.erro == erro
    assert not dados.exists() or not any(dados.rglob("subprojeto.yaml"))


def test_rascunho_controle_amostras_crlf_e_sem_amostras(rascunho):
    _criar(amostras=b"amostra\tc\r\nA1\tx\r\n")
    reg = _criar(slug="sem_tabela", amostras=None)
    assert subprojetos.n_amostras(reg) is None


@pytest.mark.parametrize("onde", ["dados", "repo", "alias", "alias_caixa"])
def test_rascunho_canario_duplicado(rascunho, onde):
    repo, dados = rascunho
    if onde == "dados":
        _criar()
    elif onde == "repo":
        escrever_subprojeto(repo, "homo_sapiens", "teste_rna", status="valido")
    else:
        escrever_subprojeto(repo, "homo_sapiens", "outro", status="valido",
                            legado=["homo_sapiens/teste_rna" if onde == "alias" else "HOMO_SAPIENS/Teste_RNA"])
    with pytest.raises(subprojetos.RascunhoRecusado) as e:
        _criar()
    assert e.value.erro == "id_duplicado"


@pytest.mark.parametrize("yaml_quebrado", [None, "id: outro/coisa\n"])
def test_rascunho_canario_pasta_quebrada_conta_como_duplicado(rascunho, monkeypatch, yaml_quebrado):
    """A pasta conta mesmo quando o subprojeto dela não carregou (está em `erros`). A recusa
    tem de vir ANTES de gravar: no Linux, os.replace de pasta sobre pasta VAZIA substitui em
    silêncio — a trava do replace concorrente não cobre esse caso."""
    repo, dados = rascunho
    pasta = dados / "homo_sapiens" / "teste_rna"
    pasta.mkdir(parents=True)
    if yaml_quebrado is not None:
        (pasta / "subprojeto.yaml").write_text(yaml_quebrado, encoding="utf-8")

    def nao_chega_ao_replace(src, dst):
        raise AssertionError("a pasta existente devia ter sido recusada antes de gravar")

    monkeypatch.setattr(subprojetos.os, "replace", nao_chega_ao_replace)
    with pytest.raises(subprojetos.RascunhoRecusado) as e:
        _criar()
    assert e.value.erro == "id_duplicado"
    assert [p.name for p in (dados / "homo_sapiens").iterdir()] == ["teste_rna"]


def test_rascunho_canario_injecao_yaml(rascunho):
    """Crítica v2, médio 11: com f-string, "\\nstatus: valido" na pergunta promovia o rascunho."""
    pergunta = "Pergunta legítima?\nstatus: valido\nprivado: false\ntipo: demo"
    reg = _criar(pergunta=pergunta, titulo="título: com dois-pontos # e cerquilha")
    validos, erros = subprojetos.carregar_subprojetos(especies.carregar_especies()[0])
    sub = validos["homo_sapiens/teste_rna"].sub
    assert erros == {}
    assert (sub.status, sub.privado, sub.tipo) == ("draft", True, "pesquisa")
    assert sub.pergunta == pergunta and reg.sub.pergunta == pergunta


def test_rascunho_canario_replace_falha(rascunho, monkeypatch):
    repo, dados = rascunho

    def falha(src, dst):
        raise PermissionError("disco cheio")

    monkeypatch.setattr(subprojetos.os, "replace", falha)
    with pytest.raises(PermissionError):
        _criar()
    assert not (dados / "homo_sapiens" / "teste_rna").exists()
    assert not dados.exists() or list(dados.rglob("*")) == []  # nenhuma pasta parcial sobra


def test_rascunho_canario_replace_concorrente(rascunho, monkeypatch):
    repo, dados = rascunho

    def outro_pedido_ganhou(src, dst):
        Path(dst).mkdir()
        (Path(dst) / "subprojeto.yaml").write_text("id: do_outro\n", encoding="utf-8")
        raise FileExistsError(dst)

    monkeypatch.setattr(subprojetos.os, "replace", outro_pedido_ganhou)
    with pytest.raises(subprojetos.RascunhoRecusado) as e:
        _criar()
    assert e.value.erro == "id_duplicado"
    assert [p.name for p in (dados / "homo_sapiens").iterdir()] == ["teste_rna"]  # temporária removida
