"""Professor Ômico: catálogo fiel ao registry, validação de plano contra o
catálogo (LLM nunca executa nada fora dele) e parsing robusto da resposta.
Nenhum teste chama o Ollama — o motor é opcional por contrato."""
import pytest

from omica import professor


def test_catalogo_contem_workflow_selado():
    cat = professor.catalogo()
    gen = next(m for m in cat["modulos"] if m["modulo"] == "genomica")
    wf = next(w for w in gen["workflows"] if w["id"] == "wf_painel_demo")
    assert wf["testado_em"] == "2026-09-02"
    assert {s["id"] for s in gen["scripts"]} >= {"extracao_demo", "fst_racas"}


def test_vitrine_so_mostra_testados():
    for item in professor.workflows_testados():
        assert item["wf"].testado_em


def test_plano_fora_do_catalogo_e_rejeitado():
    plano = professor.Plano(acao="rodar_workflow", modulo="genomica",
                            alvo="wf_que_nao_existe", explicacao="x")
    assert "fora do catálogo" in professor._validar_contra_catalogo(plano)
    plano2 = professor.Plano(acao="rodar_script", modulo="modulo_fantasma",
                             alvo="x", explicacao="x")
    assert "fora do catálogo" in professor._validar_contra_catalogo(plano2)


def test_plano_valido_passa():
    plano = professor.Plano(acao="rodar_workflow", modulo="genomica",
                            alvo="wf_painel_demo", explicacao="x")
    assert professor._validar_contra_catalogo(plano) is None
    assert professor._validar_contra_catalogo(
        professor.Plano(acao="explicar", explicacao="x")) is None


def test_extrair_json_ignora_think_e_texto_solto():
    bruto = '<think>vou pensar...</think>\nClaro! {"acao": "explicar", "explicacao": "oi"} fim'
    dados = professor._extrair_json(bruto)
    assert dados["acao"] == "explicar"
    with pytest.raises(ValueError):
        professor._extrair_json("<think>só pensamento</think>")
