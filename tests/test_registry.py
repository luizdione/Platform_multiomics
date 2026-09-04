"""Valida os manifests reais do repo — o CI quebra se um módulo apontar para
arquivo inexistente (classe de bug tab_references/manutencao-fantasma das
plataformas antigas) ou usar status_type desconhecido."""
from pathlib import Path

import pytest
import yaml

from omica.registry import MODULES_DIR, STATUS_TYPES, Manifest, load_registry

ESPERADOS = {"genomica", "epigenomica", "transcriptomica"}


def test_registry_carrega_sem_erros():
    reg = load_registry()
    assert not reg.erros, f"manifests inválidos: {reg.erros}"
    assert {m.id for m in reg.modules} >= ESPERADOS


def test_ordem_e_nomes_bilingues():
    reg = load_registry()
    ordens = [m.ordem for m in reg.modules]
    assert ordens == sorted(ordens)
    for m in reg.modules:
        assert m.nome_pt and m.nome_en


def test_status_types_conhecidos_em_todos_manifests():
    reg = load_registry()
    for m in reg.modules:
        for fase in m.fases:
            for etapa in fase.etapas:
                assert etapa.status_type in STATUS_TYPES


def test_manifest_com_referencia_quebrada_e_rejeitado(tmp_path: Path):
    pasta = tmp_path / "quebrado"
    (pasta).mkdir()
    manifesto = {
        "id": "quebrado",
        "nome_pt": "Quebrado",
        "nome_en": "Broken",
        "fases": [
            {
                "id": "f1",
                "nome": "F1",
                "etapas": [
                    {"key": "x", "desc": "aponta p/ script inexistente", "script": "nao_existe.py"}
                ],
            }
        ],
    }
    (pasta / "manifest.yaml").write_text(yaml.safe_dump(manifesto), encoding="utf-8")
    reg = load_registry(tmp_path)
    assert "quebrado" in reg.erros
    assert "referências quebradas" in reg.erros["quebrado"]


def test_runtime_invalido_e_rejeitado():
    with pytest.raises(Exception):
        Manifest.model_validate(
            {
                "id": "x",
                "nome_pt": "x",
                "nome_en": "x",
                "scripts": [
                    {"key": "s", "titulo": "s", "runtime": "magico:nuvem", "cmd": "echo oi"}
                ],
            }
        )


def test_honestidade_dos_status():
    # Fases 2-4 têm scripts reais; etapas ainda não entregues ficam "planejado"
    # com motivo no próprio desc (nf-core DinD, HOMER, MOFA2).
    reg = load_registry(MODULES_DIR)
    tra = reg.por_id("transcriptomica")
    assert tra is not None and not tra.em_construcao()
    assert {s.key for s in tra.scripts} == {
        "deseq2_demo", "volcano_demo", "qc_demo", "baixar_sra", "salmon_quant_demo",
    }
    assert any(w.id == "wf_de_granulosa" for w in tra.workflows)
    gen = reg.por_id("genomica")
    assert gen is not None and not gen.em_construcao()
    assert {s.key for s in gen.scripts} == {
        "extracao_demo", "frequencias_demo", "anotacao_demo",
        "coordenadas_painel", "integridade_vcf", "fst_racas", "vep_demo",
    }
    epi = reg.por_id("epigenomica")
    assert epi is not None and not epi.em_construcao()
    assert {s.key for s in epi.scripts} == {"dmc_demo", "intersecao_demo", "tss_demo",
                                            "motifs_homer_demo", "mofa_preparar_demo",
                                            "mofa_rodar_demo"}
    assert {w.id for w in epi.workflows} == {"wf_metilacao_demo", "wf_mofa_demo"}
    ml = reg.por_id("ml")
    assert ml is not None
    assert {s.key for s in ml.scripts} == {"ml_dataset_demo", "ml_classificar_demo",
                                           "ml_robustez_demo"}
    assert any(w.id == "wf_ml_aims" for w in ml.workflows)
    assert gen.results_spec and epi.results_spec and ml.results_spec


def test_painel_108_versionado_com_contagem_canonica():
    import json

    from omica.config import REPO_ROOT

    painel = json.loads((REPO_ROOT / "modules/epigenomica/data/paineis/painel_108.json")
                        .read_text(encoding="utf-8"))
    assert painel["_meta"]["n_genes"] == 108
    assert len(painel["genes"]) == 108  # fim das contagens 45/49/53
    bed = (REPO_ROOT / "modules/epigenomica/data/paineis/painel_108.bed") \
        .read_text(encoding="utf-8").splitlines()
    assert len(bed) == 108
