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


# ============================================== F6: alertas (X5) e avisos do subprojeto (X5′)
def _com_alertas(monkeypatch, alertas: list[dict]) -> None:
    """O transcriptomica_de como o run o enxerga, com `relatorio.alertas` (o YAML real não tem)."""
    from omica import runs
    from omica.pipelines import Alerta

    original = runs._pipeline

    def falso(pid):
        p, m = original(pid)
        if pid == "transcriptomica_de":
            rel = p.relatorio.model_copy(update={"alertas": [Alerta.model_validate(a) for a in alertas]})
            p = p.model_copy(update={"relatorio": rel})
        return p, m

    monkeypatch.setattr(runs, "_pipeline", falso)


def _html(cfg, rid: str, rel: str = "transcriptomica") -> str:
    return (cfg.results_dir / rel / "runs" / rid / "relatorio" / "relatorio.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("igual,aparece", [(0, True), (1, False)])
def test_x5_alerta_que_vale_vai_a_mensagem_e_abre_o_html(api, lancados, cfg, monkeypatch, igual, aparece):
    """Fato valendo `igual` (n_down = 0 no resumo): o alerta aparece na mensagem e no HTML,
    escapado e fora do texto do redator. Valendo outra coisa: não aparece."""
    _com_alertas(monkeypatch, [{"texto": "Nenhum gene reprimido: <confira> o contraste.",
                                "fato": "de.de_resumo.n_down", "igual": igual},
                               {"texto": "Seleção exploratória."}])
    rid = _concluido(api, lancados)
    p = servico.pedido(rid)
    assert "ALERTAS (obrigatórios: declare cada um em limitações" in p["mensagem"]
    assert "- Seleção exploratória." in p["mensagem"]  # incondicional
    assert ("Nenhum gene reprimido" in p["mensagem"]) == aparece
    assert servico.receber(rid, BOM, stop_reason="end_turn")["ok"]
    html = _html(cfg, rid)
    assert ("Nenhum gene reprimido: &lt;confira&gt; o contraste." in html) == aparece
    assert "<confira>" not in html and "Seleção exploratória." in html
    assert html.index("Seleção exploratória.") < html.index("<h2>Resumo</h2>")  # abre o relatório


def test_x5_controle_sem_alertas_nao_ha_bloco(api, lancados, cfg):
    rid = _concluido(api, lancados)
    p = servico.pedido(rid)
    assert "ALERTAS" not in p["mensagem"] and "Contexto: bos_taurus/demo (assembly ARS-UCD1.3)." in p["mensagem"]
    servico.receber(rid, BOM, stop_reason="end_turn")
    assert 'class="alertas"' not in _html(cfg, rid)


def test_x5b_avisos_valem_pelo_modulo_do_plano():
    """O aviso do demo "epigenômica: painel_108 v1…" vale para a epigenômica e não para a
    genômica nem para a transcriptômica — sem editar o YAML. Aviso cujo prefixo não é módulo
    nenhum é geral."""
    from omica import contexto

    ctx = contexto.demo().para_plano()
    painel = next(a for a in ctx["avisos"] if "painel_108" in a)
    assert servico.avisos_que_valem({"modulo": "epigenomica", "contexto": ctx}) == [painel]
    assert servico.avisos_que_valem({"modulo": "genomica", "contexto": ctx}) == []
    assert servico.avisos_que_valem({"modulo": "transcriptomica", "contexto": ctx}) == []
    gerais = {**ctx, "avisos": ["Atenção: dado reamostrado", "sem dois-pontos", "GENÔMICA: só genômica", "ml: só ML"]}
    assert servico.avisos_que_valem({"modulo": "genomica", "contexto": gerais}) == [
        "Atenção: dado reamostrado", "sem dois-pontos", "GENÔMICA: só genômica"]
    # plano de antes da F6 (contexto sem `avisos`) e de antes da v2 (sem contexto): nenhum
    assert servico.avisos_que_valem({"modulo": "epigenomica", "contexto": {k: v for k, v in ctx.items()
                                                                           if k != "avisos"}}) == []
    assert servico.avisos_que_valem({"modulo": "epigenomica"}) == []


def test_x5b_mensagem_com_e_sem_avisos():
    from omica import contexto

    ficha = {"selos": {}, "fatos": [], "figuras": [], "alertas": []}
    base = {"pipeline": "p", "modulo": "epigenomica", "run_id": "r", "passos": [], "escolhas": {}}
    com = servico._mensagem(ficha, {**base, "contexto": contexto.demo().para_plano()}, {})
    assert "ALERTAS DO SUBPROJETO (obrigatórios" in com and "painel_108 v1" in com
    assert "Contexto: bos_taurus/demo (assembly ARS-UCD1.3)." in com
    sem = servico._mensagem(ficha, {**base, "modulo": "genomica", "contexto": contexto.demo().para_plano()}, {})
    assert "ALERTAS" not in sem and "painel_108" not in sem
    antigo = servico._mensagem(ficha, base, {})  # plano de antes da v2: sem contexto, sem bloco
    assert "Contexto:" not in antigo and "ALERTAS" not in antigo


def test_x5b_run_num_subprojeto_com_avisos(api, lancados, cfg):
    """De ponta a ponta, num rascunho bovino com avisos: o plano grava `contexto.avisos`, a
    mensagem traz os que valem para a transcriptômica e o HTML os abre, escapados."""
    from fixtures_v2 import escrever_subprojeto
    from test_runs import DEMO, REFERENCIA

    avisos = ["geral: dado <reamostrado>", "genomica: painel antigo", "transcriptômica: contagens de 2019"]
    escrever_subprojeto(cfg.projetos_dir, "bos_taurus", "com_avisos", assembly="ARS-UCD1.3",
                        modulos=["transcriptomica"], pipelines=["transcriptomica_de"], avisos=avisos)
    r = api.post("/api/v1/runs", json={"pipeline": "transcriptomica_de", "especie": "bos_taurus",
                                       "subprojeto": "com_avisos"})
    assert r.status_code == 201, r.text
    rid = r.json()["run_id"]
    for nome, arq in (("counts", "counts_granulosa.tsv"), ("coldata", "coldata_granulosa.tsv")):
        assert api.put(f"/api/v1/runs/{rid}/entradas/{nome}", json={"caminho": f"{DEMO}/{arq}"}).status_code == 200
    plano = api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {"referencia": REFERENCIA}}).json()
    assert plano["contexto"]["avisos"] == avisos
    assert api.post(f"/api/v1/runs/{rid}/aprovar", json={"plano_sha256": plano["plano_sha256"]}).status_code == 200
    de, volcano = plano["passos"]
    api.post(f"/api/v1/runs/{rid}/passos/de/executar")
    _concluir(lancados[-1]["job_id"], de["saida"], {"de_table.tsv": DE, "de_resumo.json": json.dumps(RESUMO_DE)})
    assert api.post(f"/api/v1/runs/{rid}/passos/volcano/executar").status_code == 202
    _concluir(lancados[-1]["job_id"], volcano["saida"],
              {"volcano.png": _png_bytes(), "volcano_resumo.json": json.dumps(RESUMO_VOLCANO)})
    assert api.get(f"/api/v1/runs/{rid}").json()["estado"] == "concluido"
    msg = servico.pedido(rid)["mensagem"]
    assert "Contexto: bos_taurus/com_avisos (assembly ARS-UCD1.3)." in msg
    assert "- geral: dado <reamostrado>" in msg and "- transcriptômica: contagens de 2019" in msg
    assert "painel antigo" not in msg
    assert servico.receber(rid, BOM, stop_reason="end_turn")["ok"]
    html = _html(cfg, rid, "projetos/bos_taurus/com_avisos/transcriptomica")
    assert "geral: dado &lt;reamostrado&gt;" in html and "Avisos do subprojeto" in html
    assert "painel antigo" not in html


# ============================================ adendo §A3.4: plano aprovado antes da F6
_CHAVES_F6_INVARIANTE = ("porta", "coluna", "desempate")


def _como_antes_da_f6(rid: str) -> list[str]:
    """Reescreve o plano APROVADO como a F6 o encontraria num run de antes dela: sem
    `relatorio.alertas`, sem `porta`/`coluna`/`desempate` nas invariantes, sem `abs_maior_que`
    nos filtros e sem `contexto.avisos`, com o plano_sha256 recalculado sobre isso (é o que o
    autor aprovou então). Devolve as chaves tiradas (o teste não passa no vazio)."""
    from omica import runs

    arq = runs._pasta(rid) / "plano.json"
    plano = runs._ler(arq)
    tiradas: list[str] = []
    rel = plano["relatorio"]
    if rel.pop("alertas", None) is not None:
        tiradas.append("relatorio.alertas")
    for inv in rel.get("invariantes", []):
        for k in _CHAVES_F6_INVARIANTE:
            if k in inv:
                inv.pop(k)
                tiradas.append(f"invariantes.{k}")
        for flt in inv.get("filtros", []):
            if "abs_maior_que" in flt:
                flt.pop("abs_maior_que")
                tiradas.append("filtros.abs_maior_que")
    if "avisos" in plano.get("contexto", {}):
        plano["contexto"].pop("avisos")
        tiradas.append("contexto.avisos")
    plano["plano_sha256"] = runs._sha_canonico(runs._sem_sha(plano))
    runs._gravar(arq, plano)
    run = runs._carregar(rid)
    run["plano_sha256"] = plano["plano_sha256"]
    runs._gravar_run(rid, run)
    return sorted(set(tiradas))


def test_plano_aprovado_antes_da_f6_executa_e_tem_relatorio(api, lancados, cfg):
    """Adendo §A3.4: os campos novos do plano são lidos com `.get` — um plano aprovado antes da
    F6, sem eles, continua executável (envelope, entrega) e com relatório (ficha, invariantes,
    portão, HTML), sem bloco de alertas."""
    rid, plano = _aprovado(api)
    assert _como_antes_da_f6(rid) == ["contexto.avisos", "filtros.abs_maior_que", "invariantes.coluna",
                                      "invariantes.desempate", "invariantes.porta", "relatorio.alertas"]
    de, volcano = plano["passos"]
    assert api.post(f"/api/v1/runs/{rid}/passos/de/executar").status_code == 202
    _concluir(lancados[0]["job_id"], de["saida"], {"de_table.tsv": DE, "de_resumo.json": json.dumps(RESUMO_DE)})
    assert api.post(f"/api/v1/runs/{rid}/passos/volcano/executar").status_code == 202
    _concluir(lancados[1]["job_id"], volcano["saida"],
              {"volcano.png": _png_bytes(), "volcano_resumo.json": json.dumps(RESUMO_VOLCANO)})
    assert api.get(f"/api/v1/runs/{rid}").json()["estado"] == "concluido"
    p = servico.pedido(rid)  # ficha + invariantes (409 se alguma reprovasse)
    assert "Contexto: bos_taurus/demo" in p["mensagem"] and "ALERTAS" not in p["mensagem"]
    assert servico.receber(rid, BOM, stop_reason="end_turn")["ok"]
    assert 'class="alertas"' not in _html(cfg, rid)
