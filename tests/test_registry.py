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
    # Módulos ainda não migrados ficam "em construção"; a genômica (Fase 2 em
    # curso) já tem scripts declarados e etapas fora de "planejado".
    reg = load_registry(MODULES_DIR)
    for mod_id in ("epigenomica", "transcriptomica"):
        m = reg.por_id(mod_id)
        assert m is not None and m.em_construcao() and not m.scripts
    gen = reg.por_id("genomica")
    assert gen is not None and not gen.em_construcao()
    assert {s.key for s in gen.scripts} == {"extracao_demo", "frequencias_demo", "anotacao_demo"}
    assert gen.results_spec  # UI só mostra número lido de arquivo
