"""M4 (núcleo) da v2 multiespécie — runs com contexto (especificação §5 B3/B4, frente B).

Um run tem (espécie, subprojeto). Sem os dois, é do demo, com aviso (o formulário do n8n);
fora do demo grava em results/projetos/<esp>/<slug>/...; o sha do contexto entra no plano
aprovado e é conferido no aprovar e no executar. Os jobs são FINGIDOS pelo fixture `lancados`
de test_runs (que monta o cmd com o contexto, como o lançamento real).

Os runs fora do demo usam a aula do repositório (bos_taurus/aula_granulosa_calor, só lida) e
rascunhos em <OMICA_DATA>/projetos (tmp pelo conftest) — o que precisa mudar um subprojeto
muda um rascunho, nunca o repositório. A API v1 ainda não recebe espécie/subprojeto (frente
P): o run é criado por `runs.criar`, e o resto do ciclo passa pela API, como o n8n faz.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
import yaml

from fixtures_v2 import escrever_subprojeto
from omica import contexto, runs
from omica.config import get_config
from omica.jobs import dispatch, queue
from omica.relatorio import servico
from test_relatorio_servico import BOM, DE, RESUMO_DE, RESUMO_VOLCANO
from test_runs import DEMO, REFERENCIA, _concluir, _png_bytes, api, lancados  # noqa: F401 (fixtures)

AULA = ("bos_taurus", "aula_granulosa_calor")
HUMANO = ("homo_sapiens", "teste_rna")


def _rascunho(especie: str = "homo_sapiens", slug: str = "teste_rna", **kw) -> Path:
    kw.setdefault("assembly", "GRCh38" if especie == "homo_sapiens" else "ARS-UCD1.3")
    return escrever_subprojeto(get_config().projetos_dir, especie, slug, **kw)


def _erro(r) -> str:
    return r.json()["detail"]["erro"]


def _runs_no_disco() -> list[str]:
    return [rid for rid, _ in runs._runs_no_disco()]


def _criar(pipeline: str = "transcriptomica_de", especie=None, subprojeto=None) -> dict:
    return runs.criar(pipeline, especie, subprojeto)


def _com_entradas(api, especie=None, subprojeto=None) -> str:
    rid = _criar("transcriptomica_de", especie, subprojeto)["run_id"]
    for nome, arq in (("counts", "counts_granulosa.tsv"), ("coldata", "coldata_granulosa.tsv")):
        r = api.put(f"/api/v1/runs/{rid}/entradas/{nome}", json={"caminho": f"{DEMO}/{arq}"})
        assert r.status_code == 200, r.text
    return rid


def _planejado(api, especie=None, subprojeto=None) -> tuple[str, dict]:
    rid = _com_entradas(api, especie, subprojeto)
    r = api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {"referencia": REFERENCIA}})
    assert r.status_code == 200, r.text
    return rid, r.json()


def _aprovado(api, especie=None, subprojeto=None) -> tuple[str, dict]:
    rid, plano = _planejado(api, especie, subprojeto)
    r = api.post(f"/api/v1/runs/{rid}/aprovar", json={"plano_sha256": plano["plano_sha256"]})
    assert r.status_code == 200 and r.json()["estado"] == "aprovado", r.text
    return rid, plano


def _executar(api, rid: str, passo: str = "de"):
    return api.post(f"/api/v1/runs/{rid}/passos/{passo}/executar")


def _run_json(cfg, rid: str) -> Path:
    return cfg.work_dir / "runs" / rid / "run.json"


def _editar_run(cfg, rid: str, mudar) -> None:
    arq = _run_json(cfg, rid)
    dados = json.loads(arq.read_text(encoding="utf-8"))
    mudar(dados)
    arq.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")


def _editar_yaml(arq: Path, mudar) -> None:
    dados = yaml.safe_load(arq.read_text(encoding="utf-8"))
    mudar(dados)
    arq.write_text(yaml.safe_dump(dados, allow_unicode=True, sort_keys=False), encoding="utf-8")


# =================================================================== criar (B3)

def test_criar_sem_contexto_e_demo_com_aviso_e_layout_legado(cfg):
    r = _criar()
    rid = r["run_id"]
    assert r["avisos"] == ["contexto_padrao_bos_taurus_demo"]
    assert (r["especie"], r["subprojeto"], r["assembly"]) == ("bos_taurus", "demo", "ARS-UCD1.3")
    assert r["resultados"] == f"transcriptomica/runs/{rid}"
    assert r["contexto_sha256"] == contexto.demo().sha256
    gravado = json.loads(_run_json(cfg, rid).read_text(encoding="utf-8"))
    assert "avisos" not in gravado and gravado["resultados"] == r["resultados"]  # o aviso é da resposta
    assert runs.pasta_resultados(gravado) == cfg.results_dir / "transcriptomica" / "runs" / rid


def test_criar_com_contexto_sem_aviso_e_layout_de_projetos(cfg):
    r = _criar("transcriptomica_de", *AULA)
    rid = r["run_id"]
    assert "avisos" not in r and (r["especie"], r["subprojeto"]) == AULA
    assert r["resultados"] == f"projetos/bos_taurus/aula_granulosa_calor/transcriptomica/runs/{rid}"
    assert runs.pasta_resultados(r) == (cfg.results_dir / "projetos" / "bos_taurus" / "aula_granulosa_calor"
                                        / "transcriptomica" / "runs" / rid)
    # explícito no demo: o mesmo layout legado, sem aviso
    d = _criar("transcriptomica_de", "bos_taurus", "demo")
    assert "avisos" not in d and d["resultados"] == f"transcriptomica/runs/{d['run_id']}"


def test_criar_canarios(cfg):
    _rascunho()
    _rascunho("bos_taurus", "velho", status="invalido", validade="rótulos errados", modulos=["transcriptomica"])
    _rascunho("bos_taurus", "so_rna", modulos=["transcriptomica"])
    casos = [
        (("transcriptomica_de", "homo_sapiens", None), 422, "contexto_incompleto"),
        (("transcriptomica_de", None, "teste_rna"), 422, "contexto_incompleto"),
        (("transcriptomica_de", "xx_nada", "demo"), 404, "especie_inexistente"),
        (("transcriptomica_de", "homo_sapiens", "nao_existe"), 404, "subprojeto_inexistente"),
        (("transcriptomica_de", "bos_taurus", "velho"), 409, "subprojeto_nao_executavel"),
        (("genomica_painel", "homo_sapiens", "teste_rna"), 422, "pipeline_fora_da_especie"),
        (("genomica_painel", *AULA), 422, "pipeline_fora_do_subprojeto"),
        (("genomica_painel", "bos_taurus", "so_rna"), 422, "modulo_fora_do_subprojeto"),
    ]
    for args, status, erro in casos:
        with pytest.raises(runs.ErroRun) as e:
            runs.criar(*args)
        assert (e.value.status, e.value.erro) == (status, erro), args
    with pytest.raises(runs.ErroRun) as e:
        runs.criar("genomica_painel", "homo_sapiens", "teste_rna")
    assert e.value.detalhe["especies"] == ["bos_taurus"] and e.value.detalhe["especie"] == "homo_sapiens"
    assert _runs_no_disco() == []  # nenhuma recusa deixa run.json
    # controles: o mesmo pipeline onde ele cabe
    assert _criar("transcriptomica_de", "homo_sapiens", "teste_rna")["especie"] == "homo_sapiens"
    assert _criar("transcriptomica_de", "bos_taurus", "so_rna")["subprojeto"] == "so_rna"
    assert _criar("genomica_painel", "bos_taurus", "demo")["modulo"] == "genomica"


# ============================================================= ciclo fora do demo

def test_fluxo_humano_grava_em_projetos_e_executa(api, lancados, cfg):
    _rascunho()
    rid, plano = _aprovado(api, *HUMANO)
    ctx = contexto.resolver(*HUMANO)
    assert plano["contexto"] == ctx.para_plano()
    base = f"{cfg.data_dir.as_posix()}/results/projetos/homo_sapiens/teste_rna/transcriptomica/runs/{rid}"
    de, volcano = plano["passos"]
    assert (de["saida"], volcano["saida"]) == (f"{base}/de", f"{base}/volcano")
    assert de["cmd"].endswith(f"--saida {base}/de") and f"--de {base}/de/de_table.tsv" in volcano["cmd"]
    # o plano_sha256 é o do plano COM o contexto (é ele que o autor aprova)
    assert runs._sha_canonico({k: v for k, v in plano.items() if k != "plano_sha256"}) == plano["plano_sha256"]

    assert _executar(api, rid).status_code == 202
    (ch,) = lancados
    assert ch["saida"] == de["saida"] and queue.read_status(ch["job_id"])["cmd"] == de["cmd"]
    _concluir(ch["job_id"], de["saida"], {"de_table.tsv": DE, "de_resumo.json": json.dumps(RESUMO_DE)})
    assert _executar(api, rid, "volcano").status_code == 202
    assert queue.read_status(lancados[1]["job_id"])["cmd"] == volcano["cmd"]


def test_meta_do_job_real_leva_o_contexto(api, monkeypatch, cfg):
    """Só o enqueue é fingido: lancar_script de verdade grava espécie e subprojeto no status."""
    chamadas: list[dict] = []

    def falso(func_path, prefixo, mem_gb=0.5, timeout_s=3600, fila=queue.FILA_DEFAULT, **meta):
        job_id = queue.novo_job_id(prefixo)
        queue.write_status(job_id, "queued", mem_gb=mem_gb, func=func_path, fila=fila, **meta)
        chamadas.append({"job_id": job_id, **meta})
        return job_id, "rq"

    monkeypatch.setattr(dispatch.queue, "enqueue", falso)
    rid, plano = _aprovado(api, *AULA)
    assert _executar(api, rid).status_code == 202
    (job,) = chamadas
    assert (job["especie"], job["subprojeto"], job["run_id"]) == (*AULA, rid)
    assert job["cmd"] == plano["passos"][0]["cmd"]


# ======================================================= contexto mudou → 409

def test_canario_amostras_mudou_entre_plano_e_aprovar(api, cfg):
    pasta = _rascunho()
    rid, plano = _planejado(api, *HUMANO)
    amostras = pasta / "amostras.tsv"
    original = amostras.read_bytes()
    amostras.write_bytes(original.replace(b"A2\tt", b"A2\tc"))
    r = api.post(f"/api/v1/runs/{rid}/aprovar", json={"plano_sha256": plano["plano_sha256"]})
    assert r.status_code == 409 and _erro(r) == "contexto_mudou", r.text
    assert r.json()["detail"]["esperado"] == plano["contexto"]["sha256"]
    # controle: restaurado (mesmo com CRLF, como num checkout do Windows), aprova
    amostras.write_bytes(original.replace(b"\n", b"\r\n"))
    r = api.post(f"/api/v1/runs/{rid}/aprovar", json={"plano_sha256": plano["plano_sha256"]})
    assert r.status_code == 200 and r.json()["estado"] == "aprovado", r.text


def _manifest_com_cmd_mudado(monkeypatch):
    """O registry visto pelo executar com o cmd do deseq2 alterado (o que dá manifest_mudou)."""
    from omica.registry import load_registry

    reg = load_registry()
    spec = next(s for s in reg.por_id("transcriptomica").scripts_api if s.key == "deseq2")
    spec.cmd = spec.cmd.replace("--lfc {lfc}", "--lfc {lfc} --extra")
    monkeypatch.setattr(runs, "load_registry", lambda *a, **k: reg)


def test_canario_subprojeto_mudou_depois_de_aprovar_e_contexto_mudou(api, lancados, monkeypatch, cfg):
    pasta = _rascunho()
    rid, _ = _aprovado(api, *HUMANO)
    _editar_yaml(pasta / "subprojeto.yaml", lambda d: d.update(pergunta="Outra pergunta, mudada depois?"))
    _manifest_com_cmd_mudado(monkeypatch)  # o manifest TAMBÉM mudou: o motivo tem de ser o contexto
    r = _executar(api, rid)
    assert r.status_code == 409 and _erro(r) == "contexto_mudou", r.text
    assert not lancados


def test_controle_so_o_manifest_mudou_e_manifest_mudou(api, lancados, monkeypatch, cfg):
    _rascunho()
    rid, _ = _aprovado(api, *HUMANO)
    _manifest_com_cmd_mudado(monkeypatch)
    r = _executar(api, rid)
    assert r.status_code == 409 and _erro(r) == "manifest_mudou", r.text
    assert not lancados


def test_canario_subprojeto_invalidado_depois_de_aprovar(api, lancados, cfg):
    pasta = _rascunho()
    rid, _ = _aprovado(api, *HUMANO)
    _editar_yaml(pasta / "subprojeto.yaml", lambda d: d.update(status="invalido", validade="rótulos trocados"))
    r = _executar(api, rid)
    assert r.status_code == 409 and _erro(r) == "subprojeto_nao_executavel", r.text
    assert r.json()["detail"]["status_subprojeto"] == "invalido" and not lancados


def test_canario_plano_reconfere_a_regra_de_execucao(api, cfg):
    """O subprojeto deixou de listar o pipeline entre o criar e o plano."""
    pasta = _rascunho()
    rid = _com_entradas(api, *HUMANO)
    _editar_yaml(pasta / "subprojeto.yaml", lambda d: d.update(pipelines=["outro_pipeline"]))
    r = api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {"referencia": REFERENCIA}})
    assert r.status_code == 422 and _erro(r) == "pipeline_fora_do_subprojeto", r.text


def test_canario_default_bovino_de_escopo_especie_fora_de_bos_taurus(api, monkeypatch, cfg):
    """Um parâmetro de escopo `especie` que o pipeline não fixa: o plano humano recusa o
    default bovino (422 parametro_recusado); o demo usa o default (controle). O pipeline real
    fixa padj/lfc em `params`, então a cópia em memória tira o padj do passo `de`."""
    _rascunho()
    original = runs._pipeline

    def com_escopo(pipeline_id):
        p, m = original(pipeline_id)
        p, m = p.model_copy(deep=True), m.model_copy(deep=True)
        p.passos[0].params.pop("padj")
        spec = next(s for s in m.scripts_api if s.key == "deseq2")
        next(par for par in spec.params if par.name == "padj").escopo = "especie"
        return p, m

    monkeypatch.setattr(runs, "_pipeline", com_escopo)
    rid = _com_entradas(api, *HUMANO)
    r = api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {"referencia": REFERENCIA}})
    assert r.status_code == 422 and _erro(r) == "parametro_recusado", r.text
    assert "é bovino" in r.json()["detail"]["detalhe"]
    _, plano = _planejado(api)
    assert plano["passos"][0]["valores"]["padj"] == "0.05"  # o default, explícito no plano
    assert plano["passos"][1]["valores"]["padj"] == "0.05"  # e o "=de.padj" do volcano o segue


# =================================================================== run legado

def test_plano_legado_sem_contexto_executa(api, lancados, cfg):
    """Um run aprovado antes do upgrade: run.json sem os campos da v2 e plano sem `contexto`
    (com o plano_sha256 recalculado, como a v1 o gravava). Executa com o contexto do demo e o
    cmd remontado é idêntico ao aprovado (I8)."""
    rid, plano = _aprovado(api)
    pasta = cfg.work_dir / "runs" / rid
    legado = {k: v for k, v in plano.items() if k not in ("contexto", "plano_sha256")}
    legado["plano_sha256"] = runs._sha_canonico(legado)
    (pasta / "plano.json").write_text(json.dumps(legado, ensure_ascii=False, indent=2), encoding="utf-8")

    def para_v1(d):
        for k in ("especie", "subprojeto", "assembly", "contexto_sha256", "resultados"):
            d.pop(k, None)
        d["plano_sha256"] = legado["plano_sha256"]

    _editar_run(cfg, rid, para_v1)
    r = _executar(api, rid)
    assert r.status_code == 202, r.text
    (ch,) = lancados
    assert queue.read_status(ch["job_id"])["cmd"] == plano["passos"][0]["cmd"]
    assert ch["saida"].endswith(f"/results/transcriptomica/runs/{rid}/de")


# ======================================================== pasta_resultados (médio 6)

def test_pasta_resultados_canarios(cfg):
    r = _criar("transcriptomica_de", *AULA)
    rid = r["run_id"]
    run = json.loads(_run_json(cfg, rid).read_text(encoding="utf-8"))
    assert runs.pasta_resultados(run).as_posix().endswith(f"projetos/bos_taurus/aula_granulosa_calor/"
                                                          f"transcriptomica/runs/{rid}")
    canarios = [
        {"resultados": "../../x"},
        {"resultados": f"transcriptomica/runs/{rid}"},                       # o layout do demo
        {"especie": "homo_sapiens"},                                        # outro subprojeto, campo velho
        {"especie": "homo_sapiens", "subprojeto": "teste_rna",
         "resultados": f"projetos/homo_sapiens/teste_rna/transcriptomica/runs/{rid}"},  # coerente, mas não era
        {"especie": "../x"},
        {"subprojeto": None},                                               # só um dos dois
        {"modulo": "../transcriptomica"},
        {"run_id": "../" + rid},
    ]
    for mudanca in canarios[:3] + canarios[4:]:
        with pytest.raises(runs.ErroRun) as e:
            runs.pasta_resultados({**run, **mudanca})
        assert (e.value.status, e.value.erro) == (409, "run_corrompido"), mudanca
    # o 4º é coerente consigo mesmo: sozinho, o run.json não tem como desmenti-lo; quem o
    # desmente é o plano aprovado (o relatório passa o plano)
    forjado = {**run, **canarios[3]}
    assert "homo_sapiens/teste_rna" in runs.pasta_resultados(forjado).as_posix()
    plano_aula = {"modulo": "transcriptomica", "contexto": {"especie": "bos_taurus", "subprojeto": "aula_granulosa_calor"}}
    with pytest.raises(runs.ErroRun) as e:
        runs.pasta_resultados(forjado, plano_aula)
    assert e.value.erro == "run_corrompido"
    assert runs.pasta_resultados(run, plano_aula) == runs.pasta_resultados(run)  # controle
    with pytest.raises(runs.ErroRun):
        runs.pasta_resultados(run, {"modulo": "transcriptomica"})  # plano legado é do demo
    # controle: run de antes da v2 (sem os campos) é do demo
    legado = {k: v for k, v in run.items() if k not in ("especie", "subprojeto", "resultados")}
    assert runs.pasta_resultados(legado) == cfg.results_dir / "transcriptomica" / "runs" / rid


def test_run_corrompido_nao_planeja_nem_quebra_a_lista(api, cfg):
    rid = _com_entradas(api, *AULA)
    _editar_run(cfg, rid, lambda d: d.update(resultados="../../x"))
    r = api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {"referencia": REFERENCIA}})
    assert r.status_code == 409 and _erro(r) == "run_corrompido", r.text
    (item,) = runs.listar()
    assert item["run_id"] == rid and item["tem_relatorio"] is False


# ================================================================ relatório (B4)

def _concluido(api, lancados, especie=None, subprojeto=None) -> str:
    rid, plano = _aprovado(api, especie, subprojeto)
    de, volcano = plano["passos"]
    assert _executar(api, rid).status_code == 202
    _concluir(lancados[0]["job_id"], de["saida"], {"de_table.tsv": DE, "de_resumo.json": json.dumps(RESUMO_DE)})
    assert _executar(api, rid, "volcano").status_code == 202
    _concluir(lancados[1]["job_id"], volcano["saida"],
              {"volcano.png": _png_bytes(), "volcano_resumo.json": json.dumps(RESUMO_VOLCANO)})
    assert api.get(f"/api/v1/runs/{rid}").json()["estado"] == "concluido"
    return rid


def test_relatorio_de_run_fora_do_demo_vai_para_projetos(api, lancados, cfg):
    rid = _concluido(api, lancados, *AULA)
    servico.pedido(rid)
    r = servico.receber(rid, BOM, modelo="claude-opus-5-5", stop_reason="end_turn")
    assert r["ok"] and r["url"] == f"/relatorios/{rid}/"
    pasta = (cfg.results_dir / "projetos" / "bos_taurus" / "aula_granulosa_calor" / "transcriptomica"
             / "runs" / rid / "relatorio")
    assert (pasta / "relatorio.html").is_file() and (pasta / "figuras" / "volcano.png").is_file()
    assert not (cfg.results_dir / "transcriptomica" / "runs" / rid).exists()  # nada no layout do demo
    assert [x["tem_relatorio"] for x in runs.listar(*AULA)] == [True]


@pytest.mark.parametrize("forja", ["so_resultados", "coerente"])
def test_relatorio_canario_run_corrompido(api, lancados, cfg, forja):
    """O run.json da aula reescrito para mandar o relatório à pasta de OUTRO subprojeto: só o
    campo `resultados` (o recálculo acusa) ou os três campos coerentes entre si (o plano
    aprovado acusa — médio 6)."""
    rid = _concluido(api, lancados, *AULA)
    servico.pedido(rid)
    outro = {"resultados": f"projetos/homo_sapiens/teste_rna/transcriptomica/runs/{rid}"}
    if forja == "coerente":
        outro.update(especie="homo_sapiens", subprojeto="teste_rna")
    _editar_run(cfg, rid, lambda d: d.update(outro))
    with pytest.raises(servico.ErroRelatorio) as e:
        servico.receber(rid, BOM, modelo="m", stop_reason="end_turn")
    assert (e.value.status, e.value.erro) == (409, "run_corrompido")
    assert not (cfg.results_dir / "projetos" / "homo_sapiens").exists()
    # a tentativa não foi gasta: o registro de tentativas nem existe
    assert not (cfg.work_dir / "runs" / rid / "relatorio" / "tentativas.json").exists()


# ========================================================================= listar

def test_listar_filtra_e_run_legado_conta_como_demo(cfg):
    _rascunho()
    demo = _criar()["run_id"]
    aula = _criar("transcriptomica_de", *AULA)["run_id"]
    humano = _criar("transcriptomica_de", *HUMANO)["run_id"]
    legado = _criar()["run_id"]
    _editar_run(cfg, legado, lambda d: [d.pop(k) for k in ("especie", "subprojeto", "assembly",
                                                           "contexto_sha256", "resultados")])
    agora = time.time()
    for i, rid in enumerate((demo, aula, humano, legado)):  # ordem de criação explícita
        _editar_run(cfg, rid, lambda d, t=agora + i: d.update(criado_em=t))
    todos = runs.listar()
    assert [x["run_id"] for x in todos] == [legado, humano, aula, demo]
    assert set(todos[0]) == {"run_id", "pipeline", "modulo", "estado", "criado_em", "especie",
                             "subprojeto", "tem_relatorio"}
    assert (todos[0]["especie"], todos[0]["subprojeto"]) == ("bos_taurus", "demo")
    assert [x["run_id"] for x in runs.listar("bos_taurus")] == [legado, aula, demo]
    assert [x["run_id"] for x in runs.listar("bos_taurus", "demo")] == [legado, demo]
    assert [x["run_id"] for x in runs.listar("homo_sapiens")] == [humano]
    assert [x["run_id"] for x in runs.listar(subprojeto="aula_granulosa_calor")] == [aula]
    assert runs.listar("mus_musculus") == [] and len(runs.listar(limite=2)) == 2
    assert all(x["tem_relatorio"] is False for x in todos)
    # um run.json com espécie fora do padrão não entra na lista (e não a quebra)
    _editar_run(cfg, humano, lambda d: d.update(especie="../x"))
    assert [x["run_id"] for x in runs.listar()] == [legado, aula, demo]


def test_pasta_resultados_confere_o_layout_mesmo_se_o_calculo_regredir(cfg, monkeypatch):
    """3ª camada: o <rel> calculado também tem de casar o padrão da especificação — uma
    regressão em contexto.rel_resultados_de não leva a saída para fora de results/."""
    run = _criar("transcriptomica_de", *AULA)
    monkeypatch.setattr(contexto, "rel_resultados_de", lambda *a: "../../fora")
    with pytest.raises(runs.ErroRun) as e:
        runs.pasta_resultados({k: v for k, v in run.items() if k != "resultados"})
    assert e.value.erro == "run_corrompido"
