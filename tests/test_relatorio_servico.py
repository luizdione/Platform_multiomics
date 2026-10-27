"""Serviço do relatório (F4): run concluído → pedido ao redator → rascunho → portão → HTML.
O redator é simulado (o JSON que o Claude devolveria); nada chama a API da Anthropic.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from omica.relatorio import servico
from test_runs import _aprovado, _concluir, _png_bytes, api, lancados  # noqa: F401 (fixtures)

DE = ("gene_id\tgene_name\tbaseMean\tlog2FoldChange\tpvalue\tpadj\n"
      "ENSBTAG00000000001\tGENE1\t10\t2.5\t0.001\t0.01\n"
      "ENSBTAG00000000002\tGENE2\t5\t-1.2\t0.2\tNA\n")
RESUMO_DE = {"contraste": "condicao_summer_heat_vs_winter_control", "criterios": {"padj": 0.05, "lfc": 1},
             "n_deg_padj": 1, "n_deg_padj_e_lfc": 1, "n_up": 1, "n_down": 0}
RESUMO_VOLCANO = {"n_pontos": 1, "n_significativos": 1, "criterios": {"padj": 0.05, "lfc": 1.0}}

BOM = {"secoes": [
    {"id": "resumo", "titulo": "Resumo",
     "paragrafos": ["No contraste {{f:de.de_resumo.contraste}}, {{f:de.de_resumo.n_up}} gene ficou mais "
                    "expresso no verão; o principal foi {{f:de.de_table.top1.gene_name}}."]},
    {"id": "resultados", "titulo": "Resultados", "paragrafos": ["{{fig:volcano.volcano_png}}"]}],
    "legendas": [{"fig": "volcano.volcano_png", "texto": "Volcano do contraste verão contra inverno."}]}
RUIM = {"secoes": [{"id": "resumo", "titulo": "Resumo", "paragrafos": ["Foram 2393 genes."]}], "legendas": []}


def _concluido(api, lancados, resumo_de=RESUMO_DE) -> str:
    rid, plano = _aprovado(api)
    de, volcano = plano["passos"]
    api.post(f"/api/v1/runs/{rid}/passos/de/executar")
    _concluir(lancados[0]["job_id"], de["saida"], {"de_table.tsv": DE, "de_resumo.json": json.dumps(resumo_de)})
    assert api.post(f"/api/v1/runs/{rid}/passos/volcano/executar").status_code == 202
    _concluir(lancados[1]["job_id"], volcano["saida"],
              {"volcano.png": _png_bytes(), "volcano_resumo.json": json.dumps(RESUMO_VOLCANO)})
    assert api.get(f"/api/v1/runs/{rid}").json()["estado"] == "concluido"
    return rid


def test_pedido_entrega_prompt_ficha_e_esquema(api, lancados):
    rid = _concluido(api, lancados)
    p = servico.pedido(rid)
    assert "Regra absoluta" in p["sistema"] and p["esquema"]["additionalProperties"] is False
    assert "de.de_resumo.n_up = 1" in p["mensagem"] and "volcano.volcano_png" in p["mensagem"]
    assert "ENSBTAG" in p["mensagem"]  # o redator VÊ os valores, só não pode escrevê-los


def test_pedido_antes_de_concluir_e_409(api, lancados):
    rid, _ = _aprovado(api)
    with pytest.raises(servico.ErroRelatorio) as e:
        servico.pedido(rid)
    assert e.value.status == 409 and e.value.erro == "run_nao_concluido"


def test_resultados_incoerentes_nao_viram_texto(api, lancados):
    rid = _concluido(api, lancados, resumo_de={**RESUMO_DE, "n_down": 3})  # 1 + 3 ≠ 1
    with pytest.raises(servico.ErroRelatorio) as e:
        servico.pedido(rid)
    assert e.value.erro == "invariantes_reprovados"


def test_rascunho_aprovado_vira_html_com_proveniencia(api, lancados, cfg):
    rid = _concluido(api, lancados)
    servico.pedido(rid)
    r = servico.receber(rid, BOM, modelo="claude-opus-5-5", stop_reason="end_turn")
    assert r["ok"] and r["url"] == f"/relatorios/{rid}/"
    pasta = cfg.results_dir / "transcriptomica" / "runs" / rid / "relatorio"
    html = (pasta / "relatorio.html").read_text(encoding="utf-8")
    assert "GENE1" in html and "Content-Security-Policy" in html and (pasta / "figuras" / "volcano.png").exists()
    prov = json.loads((pasta / "proveniencia.json").read_text(encoding="utf-8"))
    assert prov["modelo_redator"] == "claude-opus-5-5" and len(prov["numeros"]) == 3


def test_duas_reprovacoes_automaticas_e_depois_so_o_autor(api, lancados):
    rid = _concluido(api, lancados)
    servico.pedido(rid)
    r1 = servico.receber(rid, RUIM, stop_reason="end_turn")
    assert not r1["ok"] and r1["falhas"][0]["regra"] == "R1" and r1["restam_automaticas"] == 1
    r2 = servico.receber(rid, RUIM, stop_reason="end_turn")
    assert not r2["ok"] and r2["restam_automaticas"] == 0
    with pytest.raises(servico.ErroRelatorio) as e:
        servico.receber(rid, BOM, stop_reason="end_turn")
    assert e.value.erro == "tentativas_esgotadas"
    # a edição do autor passa pelo MESMO portão
    assert not servico.receber(rid, RUIM, autor=True)["ok"]
    assert servico.receber(rid, BOM, autor=True)["ok"]


@pytest.mark.parametrize("stop", ["max_tokens", "refusal"])
def test_resposta_incompleta_do_redator_nao_e_rascunho(api, lancados, stop):
    rid = _concluido(api, lancados)
    servico.pedido(rid)
    r = servico.receber(rid, BOM, stop_reason=stop)
    assert not r["ok"] and r["falhas"][0]["regra"] == "API"


def test_rascunho_fora_do_esquema_reprova(api, lancados):
    rid = _concluido(api, lancados)
    servico.pedido(rid)
    r = servico.receber(rid, {**BOM, "comentario": "extra"}, stop_reason="end_turn")
    assert not r["ok"] and r["falhas"][0]["regra"] == "esquema"


def test_prompt_versionado_em_cada_tentativa_e_na_proveniencia(api, lancados, cfg):
    """O prompt do redator é lido a cada pedido e vale para todos os módulos: cada tentativa e o
    relatório guardam o sha256 da versão usada (mudar o prompt muda o relatório da
    transcriptômica também). E a regra do UTR está lá: '5' UTR' à mão reprova no portão."""
    import hashlib

    rid = _concluido(api, lancados)
    p = servico.pedido(rid)
    esperado = hashlib.sha256(p["sistema"].encode("utf-8")).hexdigest()
    assert p["prompt_sha256"] == esperado
    assert "5_prime_UTR_variant" in p["sistema"] and "nunca escreva 5'" in p["sistema"]
    servico.receber(rid, BOM, modelo="claude-opus-5-5", stop_reason="end_turn")
    trabalho = cfg.work_dir / "runs" / rid / "relatorio"
    (t,) = json.loads((trabalho / "tentativas.json").read_text(encoding="utf-8"))
    assert t["prompt_sha256"] == esperado
    prov = json.loads((cfg.results_dir / "transcriptomica" / "runs" / rid / "relatorio" / "proveniencia.json")
                      .read_text(encoding="utf-8"))
    assert prov["prompt_sha256"] == esperado


def test_prompt_declara_o_limite_de_paragrafos_do_validador():
    """A saída estruturada da API não aceita limite de tamanho no esquema: o limite de
    parágrafos por seção só existe no Rascunho. O prompt tem de dizer o MESMO número, senão o
    redator gasta tentativas (a genômica, com listas longas, escreveu 26 numa seção)."""
    from omica.relatorio.esquema import Secao

    limite = next(m.max_length for m in Secao.model_fields["paragrafos"].metadata if hasattr(m, "max_length"))
    assert f"no máximo {limite} parágrafos" in servico.PROMPT_SISTEMA.read_text(encoding="utf-8")
