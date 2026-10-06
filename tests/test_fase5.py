"""Fase 5: página /skills offline, glossário fundido, /diagnostico, modo aula
executável e gate anti-vazamento."""
import json

from fastapi.testclient import TestClient

from omica.config import REPO_ROOT
from omica.publish.gate import auditar
from omica.web.app import _separar_frontmatter


def _client():
    from omica.web.app import app

    return TestClient(app)


def test_pagina_skills_so_traz_o_cabecalho():
    # a lista vem de /api/skills (busca no servidor, PT/EN); o índice inteiro
    # embutido pesava 330 KB por abertura e só casava em inglês
    r = _client().get("/skills")
    assert r.status_code == 200
    assert "815 skills" in r.text
    assert "dados-skills" not in r.text and "/static/skills.js" in r.text
    assert len(r.text) < 40_000


def test_indice_skills_versionado_integro():
    indice = json.loads((REPO_ROOT / "data" / "skills_index.json").read_text(encoding="utf-8"))
    assert indice["total"] == 815
    assert len(indice["dominios"]) == 19
    assert indice["total"] == sum(indice["dominios"].values())


def test_glossario_fundido():
    r = _client().get("/glossario")
    assert r.status_code == 200
    assert "Fst" in r.text and "methylKit" in r.text and "DESeq2" in r.text and "SHAP" in r.text
    dados = json.loads((REPO_ROOT / "didatica" / "glossario.json").read_text(encoding="utf-8"))
    assert dados["_meta"]["n_termos"] == len(dados["termos"])
    modulos = {t["modulo"] for t in dados["termos"]}
    assert {"genomica", "epigenomica", "transcriptomica", "ml", "core"} <= modulos


def test_pagina_diagnostico():
    r = _client().get("/diagnostico")
    assert r.status_code == 200
    assert "Manifests dos módulos" in r.text


def test_modo_aula_frontmatter_e_botoes():
    meta, corpo = _separar_frontmatter(
        (REPO_ROOT / "didatica/ajuda/03_aula_do_painel_ao_efeito.md").read_text(encoding="utf-8"))
    assert meta["roteiro_aula"] is True
    assert len(meta["passos"]) == 4
    assert meta["passos"][3]["tipo"] == "workflow"
    r = _client().get("/ajuda/03_aula_do_painel_ao_efeito.md")
    assert r.status_code == 200
    assert "Executar este passo" in r.text
    assert '/modulos/genomica/workflows/wf_painel_demo' in r.text


def test_doc_sem_frontmatter_continua_funcionando():
    meta, corpo = _separar_frontmatter("# Só um título\n\ncorpo")
    assert meta == {} and "título" in corpo


def test_gate_reprova_rastro_de_ia_e_manuscrito(tmp_path):
    (tmp_path / "analise.py").write_text("# gerado com Claude\nx = 1\n", encoding="utf-8")
    (tmp_path / "cover_letter_v2.md").write_text("Dear editor", encoding="utf-8")
    v = auditar(tmp_path)
    assert not v.aprovado
    assert any("[token]" in b for b in v.bloqueios)
    assert any("[arquivo]" in b for b in v.bloqueios)


def test_gate_aprova_arvore_limpa_com_aviso_de_path(tmp_path):
    (tmp_path / "fst.py").write_text("dados = 'E:\\\\data\\\\x.tsv'\n", encoding="utf-8")
    v = auditar(tmp_path)
    assert v.aprovado and v.avisos  # aviso de path não bloqueia
