"""PASTA no run (F6, ESPEC §1.A e adendo v2 §A3): entrada de pasta (cópia por caminho) e porta de
pasta (subpasta de {saida}), mais as extensões X1, X2, X6 e X7 e o --conferir do golden (L12).

Os 3 pipelines da F6 (epigenomica_dmc/mofa, ml_aims_vcf) são da F2/F3: aqui o pipeline é de
TESTE, montado em memória sobre o manifest real da epigenômica com dois scripts_api fingidos
(`t_montar`: pasta + coldata → porta de pasta `coverages` + resumo; `t_consumir`: porta de pasta
→ resumo), num rascunho bovino tmp (`bos_taurus/pasta_teste`). O que se testa é o NÚCLEO do run
(runs.py, script_job.py, o envelope), que é o mesmo para os pipelines reais. Os jobs são
fingidos como em test_runs.py; o worker simulado atesta entradas e saídas com as MESMAS funções
do worker (script_job.atestar_entradas / atestar_saidas).

Canários (cada um com o controle irmão que passa; a substring da mensagem é conferida):
  E1  cópia do demo: 6 membros, 439.723 B, nomes e mtime exatos, mesma árvore da origem
  E2  422 com o corpo do arquivo inexistente: pasta no lugar de arquivo, arquivo no lugar de
      pasta, membro recusado, fora das raízes
  E3  413 (bytes, membros, teto agregado do validador) sem sobra em raw/runs/<id>; 413 no
      re-registro deixa a pasta antiga intacta
  E4  re-registro troca a pasta sem deixar .velho.*/.registro.*; upload dá 422
      entrada_exige_caminho sem ler o corpo
  E5  409 entrada_alterada no executar: membro novo, removido, renomeado, 1 byte com o mtime
      devolvido (controle: notas.txt passa)
  E6  (em test_runs_referencia::test_r12…) e aqui: membros/regra mexidos no plano → plano_adulterado;
      sha None num membro → entrada_nao_atestavel
  E7  worker: sem atestado falha fechado; regra trocada no status → "outro arquivo"; pasta
      alterada antes do Popen → error sem rodar
  E8  GET do run com membros_amostra, sem membros
  E9  componente-pai trocado entre o portão do caminho e a cópia → 422, nada em raw/runs/<id>
      (Linux: symlink; Windows: junção); "troca e volta" (destrocado depois do 4º membro, a
      conferência pós-cópia passa) → 422 pela identidade no abrir_pasta, antes do 1º membro
  P2  envelope da porta de pasta: ausente, membro de tentativa anterior, membro trocado depois
      do fim do job, worker sem sha, porta junção/symlink (controle: montar simulado aprova)
  P3  _entrega_intacta falso com membro novo depois da entrega
  P4  sha da pasta do worker == sha do envelope
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import sys
import time
import types
from pathlib import Path

import pytest
import yaml

from fixtures_v2 import escrever_subprojeto
from omica import runs
from omica.config import REPO_ROOT, get_config
from omica.contrato import diretorio, formatos
from omica.jobs import dispatch, queue, script_job
from omica.pipelines import Pipeline, validar_contrato
from omica.registry import ScriptSpec, load_registry
from test_runs import _aprovado as _aprovado_rna  # noqa: F401
from test_runs import _concluir, api  # noqa: F401 (fixtures)

DEMO_COV = REPO_ROOT / "modules" / "epigenomica" / "demo_data"
DEMO_REL = "modules/epigenomica/demo_data"
PID = "epi_pasta_teste"
SUB = ("bos_taurus", "pasta_teste")
CRU = {"Content-Type": "application/octet-stream"}
REGRA = {"leitura": r"\.cov(?:\.gz)?\Z", "fixos": None}
SUFIXO = ".bismark.cov.gz"


def _spec(**kw) -> ScriptSpec:
    return ScriptSpec.model_validate({"titulo": "teste F6", "runtime": "image:omica-methyl", "mem_gb": 1.0, **kw})


def _manifest():
    """O manifest REAL da epigenômica com dois scripts_api de teste. O cmd aponta para um script
    que existe (a proveniência tira o sha dele); o script nunca roda (jobs fingidos)."""
    m = load_registry().por_id("epigenomica").model_copy(deep=True)
    script = "python modules/epigenomica/scripts/perfil_tss.py"
    m.scripts_api = [*m.scripts_api,
                     _spec(key="t_montar",
                           cmd=f"{script} --cov {{coverages}} --amostras {{amostras}} --a {{grupo_a}} "
                               "--b {grupo_b} --saida {saida}",
                           params=[{"name": "coverages", "rotulo": "pasta", "tipo": "dir"},
                                   {"name": "amostras", "rotulo": "amostras", "tipo": "caminho"},
                                   {"name": "grupo_a", "rotulo": "A"}, {"name": "grupo_b", "rotulo": "B"}],
                           consome={"coverages": "coverages_bismark", "amostras": "coldata"},
                           produz={"coverages": {"arquivo": "coverages", "formato": "coverages_por_condicao"},
                                   "montar_resumo": {"arquivo": "montar_resumo.json", "formato": "resumo_json"}}),
                     _spec(key="t_consumir", cmd=f"{script} --cov {{coverages}} --saida {{saida}}",
                           params=[{"name": "coverages", "rotulo": "pasta", "tipo": "dir"}],
                           consome={"coverages": "coverages_por_condicao"},
                           produz={"resumo": {"arquivo": "consumir_resumo.json", "formato": "resumo_json"}})]
    return m


PIPELINE = {
    "id": PID, "modulo": "epigenomica", "titulo": "Pasta (teste F6)",
    "entradas": {"coverages": {"formato": "coverages_bismark", "rotulo": "pasta de coverages"},
                 "amostras": {"formato": "coldata", "rotulo": "amostras"}},
    "checagens_entrada": [{"tipo": "coverages_e_amostras", "coverages": "coverages", "amostras": "amostras"}],
    "escolhas_autor": {"grupo_a": {"rotulo": "A", "valores_de": "amostras.condicao"},
                       "grupo_b": {"rotulo": "B", "valores_de": "amostras.condicao", "diferente_de": "grupo_a"}},
    "passos": [{"id": "montar", "script": "t_montar",
                "liga": {"coverages": "entrada.coverages", "amostras": "entrada.amostras",
                         "grupo_a": "escolha.grupo_a", "grupo_b": "escolha.grupo_b"}},
               {"id": "consumir", "script": "t_consumir", "liga": {"coverages": "montar.coverages"}}],
}


class _Registro:
    """O registry real, com o manifest de teste no lugar do da epigenômica (o executar relê o
    manifest pelo registry e confere o cmd contra o plano)."""

    def __init__(self, real, m) -> None:
        self._real, self._m = real, m

    def por_id(self, mod_id):
        return self._m if mod_id == "epigenomica" else self._real.por_id(mod_id)


@pytest.fixture()
def pasta(monkeypatch, cfg, api):
    m = _manifest()
    p = Pipeline.model_validate(PIPELINE)
    assert validar_contrato(p, m) == []  # controle do contrato: o pipeline de teste é fechado
    original = runs._pipeline
    monkeypatch.setattr(runs, "_pipeline", lambda pid: (p, m) if pid == PID else original(pid))
    real = runs.load_registry
    monkeypatch.setattr(runs, "load_registry", lambda *a, **k: _Registro(real(*a, **k), m))
    escrever_subprojeto(cfg.projetos_dir, *SUB, assembly="ARS-UCD1.3", modulos=["epigenomica"])
    return types.SimpleNamespace(p=p, m=m)


@pytest.fixture()
def lancados(monkeypatch, pasta):
    """dispatch.lancar_script fingido (como em test_runs), com o manifest de teste."""
    chamadas: list[dict] = []

    def falso(mod_id, script_key, valores=None, extra_meta=None, api=False, saida=None, *, ctx=None):
        spec = next(s for s in (pasta.m if mod_id == "epigenomica" else load_registry().por_id(mod_id)).scripts_api
                    if s.key == script_key)
        job_id = queue.novo_job_id(f"{mod_id}-{script_key}")
        queue.write_status(job_id, "queued", modulo=mod_id, script=script_key,
                           cmd=dispatch.montar_cmd(spec, valores, saida=saida, ctx=ctx, mod_id=mod_id),
                           runtime=spec.runtime, fila=dispatch.fila_para_runtime(spec.runtime),
                           mem_gb=spec.mem_gb, **(extra_meta or {}))
        chamadas.append({"script": script_key, "valores": dict(valores or {}), "saida": saida,
                         "job_id": job_id, **(extra_meta or {})})
        return job_id, "thread"

    monkeypatch.setattr(runs.dispatch, "lancar_script", falso)
    return chamadas


# ----------------------------------------------------------------------- helpers
def _erro(r) -> str:
    return r.json()["detail"]["erro"]


def _demo_membros() -> list[Path]:
    return sorted(DEMO_COV.glob("*.cov.gz"))


def _caixa(nome: str, membros: list[Path] | None = None) -> Path:
    """Uma pasta na caixa de entrada (<dados>/entradas/<nome>) com cópias dos coverages do
    demo (copy2: o mtime vem junto)."""
    d = get_config().data_dir / "entradas" / nome
    d.mkdir(parents=True, exist_ok=True)
    for arq in (_demo_membros() if membros is None else membros):
        shutil.copy2(arq, d / arq.name)
    return d


def _amostras() -> Path:
    """coldata dos 6 coverages do demo (HF × Nelore), na caixa de entrada."""
    arq = get_config().data_dir / "entradas" / "amostras_cd4.tsv"
    arq.parent.mkdir(parents=True, exist_ok=True)
    linhas = ["amostra\tcondicao"]
    for m in _demo_membros():
        stem = m.name[:-len(SUFIXO)]
        linhas.append(f"{stem}\t{'HF' if '_HF_' in stem else 'Nelore'}")
    arq.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    return arq


def _run(api) -> str:
    r = api.post("/api/v1/runs", json={"pipeline": PID, "especie": SUB[0], "subprojeto": SUB[1]})
    assert r.status_code == 201, r.text
    return r.json()["run_id"]


def _por_caminho(api, rid: str, nome: str, caminho) -> object:
    texto = caminho if isinstance(caminho, str) else Path(caminho).as_posix()
    return api.put(f"/api/v1/runs/{rid}/entradas/{nome}", json={"caminho": texto})


def _registrado(api, cov=DEMO_REL) -> str:
    rid = _run(api)
    r = _por_caminho(api, rid, "coverages", cov)
    assert r.status_code == 200, r.text
    assert r.json()["entradas"]["coverages"]["veredito"]["ok"], r.json()["entradas"]["coverages"]["veredito"]
    r = _por_caminho(api, rid, "amostras", _amostras())
    assert r.status_code == 200, r.text
    assert all(c["ok"] for c in r.json()["checagens"]), r.json()["checagens"]
    return rid


def _plano(api, rid: str, a: str = "HF", b: str = "Nelore"):
    return api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {"grupo_a": a, "grupo_b": b}})


def _aprovado(api, cov=DEMO_REL) -> tuple[str, dict]:
    rid = _registrado(api, cov)
    r = _plano(api, rid)
    assert r.status_code == 200, r.text
    plano = r.json()
    r = api.post(f"/api/v1/runs/{rid}/aprovar", json={"plano_sha256": plano["plano_sha256"]})
    assert r.status_code == 200, r.text
    return rid, plano


def _executar(api, rid: str, passo: str = "montar"):
    return api.post(f"/api/v1/runs/{rid}/passos/{passo}/executar")


def _envelope(api, rid: str, passo: str = "montar") -> dict:
    r = api.get(f"/api/v1/runs/{rid}/passos/{passo}/envelope")
    assert r.status_code == 200, r.text
    return r.json()


def _copia(rid: str) -> Path:
    return get_config().raw_dir / "runs" / rid / "coverages"


def _gerar_porta(saida: str) -> Path:
    """O que o `montar` real grava: <saida>/coverages/<condicao>__<amostra>.bismark.cov.gz."""
    d = Path(saida) / "coverages"
    d.mkdir(parents=True, exist_ok=True)
    for m in _demo_membros():
        stem = m.name[:-len(SUFIXO)]
        shutil.copyfile(m, d / formatos.nome_por_condicao("HF" if "_HF_" in stem else "Nelore", stem))
    return d


def _montar_entregue(api, lancados, **kw) -> tuple[str, dict, Path]:
    rid, plano = _aprovado(api)
    assert _executar(api, rid).status_code == 202
    saida = plano["passos"][0]["saida"]
    d = _gerar_porta(saida)
    _concluir(lancados[-1]["job_id"], saida, {"montar_resumo.json": json.dumps({"n_amostras": 6})}, **kw)
    return rid, plano, d


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _corpo_vigiado(lidos: list[str]):
    lidos.append("bloco")
    raise AssertionError("o servidor leu o corpo de um upload que ia recusar")
    yield b""  # pragma: no cover


# ====================================================================== contrato
def test_pipeline_de_teste_tem_contrato_fechado_e_envio_por_caminho(pasta):
    from omica.pipelines import envio_formulario

    assert envio_formulario(pasta.p.entradas["coverages"]) == "caminho"
    assert envio_formulario(pasta.p.entradas["amostras"]) == "arquivo"


# =========================================================================== E1
def test_e1_copia_do_demo_preserva_nomes_mtime_e_arvore(api, pasta, cfg):
    rid = _run(api)
    r = _por_caminho(api, rid, "coverages", DEMO_REL)
    assert r.status_code == 200, r.text
    reg = runs._carregar(rid)["entradas"]["coverages"]
    assert (reg["tipo"], reg["modo"], reg["n_membros"], reg["bytes"]) == ("diretorio", "copia", 6, 439_723)
    assert reg["regra"] == REGRA and reg["origem"] == f"caminho:{DEMO_REL}"
    assert sorted(reg["membros"]) == [m.name for m in _demo_membros()]
    copia = _copia(rid)
    assert Path(reg["caminho"]) == copia and sorted(x.name for x in copia.iterdir()) == sorted(reg["membros"])
    for m in _demo_membros():
        assert (copia / m.name).stat().st_mtime_ns == m.stat().st_mtime_ns, m.name
        assert reg["membros"][m.name] == {"bytes": m.stat().st_size, "sha256": _sha(m)}
    origem = diretorio.sha_diretorio(DEMO_COV, REGRA, 1 << 30)
    assert reg["sha256"] == origem["sha256"] == diretorio.sha_diretorio(copia, REGRA, 1 << 30)["sha256"]


# =========================================================================== E2
def _inexistente(api, rid: str) -> dict:
    r = _por_caminho(api, rid, "coverages", get_config().data_dir / "entradas" / "nao_existe")
    assert r.status_code == 422 and _erro(r) == "caminho_recusado", r.text
    return r.json()


@pytest.mark.parametrize("caso", ["pasta_no_lugar_de_arquivo", "arquivo_no_lugar_de_pasta", "membro_recusado",
                                  "fora_das_raizes", "controle"])
def test_e2_recusas_de_pasta_tem_o_corpo_do_inexistente(api, pasta, cfg, tmp_path, caso):
    rid = _run(api)
    referencia = _inexistente(api, rid)
    assert "arquivo ou pasta existente" in referencia["detail"]["detalhe"]
    if caso == "pasta_no_lugar_de_arquivo":
        r = _por_caminho(api, rid, "amostras", _caixa("lote"))
    elif caso == "arquivo_no_lugar_de_pasta":
        r = _por_caminho(api, rid, "coverages", _amostras())
    elif caso == "membro_recusado":
        d = _caixa("lote")
        shutil.copy2(_demo_membros()[0], d / ".oculto.cov.gz")
        r = _por_caminho(api, rid, "coverages", d)
    elif caso == "fora_das_raizes":
        fora = tmp_path / "fora_da_caixa"
        fora.mkdir()
        shutil.copy2(_demo_membros()[0], fora / _demo_membros()[0].name)
        r = _por_caminho(api, rid, "coverages", fora)
    else:
        r = _por_caminho(api, rid, "coverages", _caixa("lote"))
        assert r.status_code == 200, r.text
        return
    assert r.status_code == 422 and r.json() == referencia, r.text
    assert runs._carregar(rid)["entradas"] == {}


def test_e2_membro_recusado_sem_eco_do_nome(api, pasta):
    """O nome fora do padrão nunca volta (o 422 é o de sempre, e o registro não existe)."""
    rid = _run(api)
    d = _caixa("lote")
    shutil.copy2(_demo_membros()[0], d / "com espaço.cov.gz")
    r = _por_caminho(api, rid, "coverages", d)
    assert r.status_code == 422 and "espaço" not in r.text and _erro(r) == "caminho_recusado"


# =========================================================================== E3
def test_e3_teto_de_bytes_413_sem_sobra(api, pasta, cfg, monkeypatch):
    rid = _run(api)
    monkeypatch.setattr(runs, "_MAX_ENTRADA", 200_000)
    r = _por_caminho(api, rid, "coverages", DEMO_REL)
    assert r.status_code == 413 and _erro(r) == "entrada_grande_demais", r.text
    assert r.json()["detail"]["limite_bytes"] == 200_000
    raw = cfg.raw_dir / "runs" / rid
    assert not raw.exists() or list(raw.iterdir()) == []
    assert runs._carregar(rid)["entradas"] == {}


def test_e3_teto_de_membros_413(api, pasta, monkeypatch):
    rid = _run(api)
    monkeypatch.setattr(diretorio, "MAX_MEMBROS", 3)
    r = _por_caminho(api, rid, "coverages", DEMO_REL)
    assert r.status_code == 413 and _erro(r) == "entrada_grande_demais", r.text
    assert r.json()["detail"]["limite_membros"] == 3


def test_e3_teto_agregado_do_validador_413(api, pasta, monkeypatch):
    """O teto agregado de linhas (diretorio.MAX_LINHAS_PASTA) estoura no VALIDADOR, que o
    devolve em perfil["teto"]: é tamanho, não formato — 413, e nada é registrado."""
    rid = _run(api)
    monkeypatch.setattr(diretorio, "MAX_LINHAS_PASTA", 1000)
    r = _por_caminho(api, rid, "coverages", DEMO_REL)
    assert r.status_code == 413 and _erro(r) == "entrada_grande_demais", r.text
    assert "limite_linhas" in r.json()["detail"], r.text
    assert runs._carregar(rid)["entradas"] == {}


def test_e3_413_no_re_registro_deixa_a_pasta_antiga(api, pasta, cfg, monkeypatch):
    rid = _run(api)
    assert _por_caminho(api, rid, "coverages", DEMO_REL).status_code == 200
    antes = runs._carregar(rid)["entradas"]["coverages"]
    monkeypatch.setattr(runs, "_MAX_ENTRADA", 200_000)
    r = _por_caminho(api, rid, "coverages", _caixa("outra"))
    assert r.status_code == 413, r.text
    assert runs._carregar(rid)["entradas"]["coverages"] == antes
    assert diretorio.sha_diretorio(_copia(rid), REGRA, 1 << 30)["sha256"] == antes["sha256"]
    assert sorted(x.name for x in (cfg.raw_dir / "runs" / rid).iterdir()) == ["coverages"]


# =========================================================================== E4
def test_e4_re_registro_troca_a_pasta_sem_sobras(api, pasta, cfg):
    rid = _run(api)
    assert _por_caminho(api, rid, "coverages", DEMO_REL).status_code == 200
    menor = _caixa("quatro", _demo_membros()[:2] + _demo_membros()[3:5])
    r = _por_caminho(api, rid, "coverages", menor)
    assert r.status_code == 200, r.text
    reg = runs._carregar(rid)["entradas"]["coverages"]
    assert reg["n_membros"] == 4 and sorted(x.name for x in _copia(rid).iterdir()) == sorted(reg["membros"])
    sobras = [x.name for x in (cfg.raw_dir / "runs" / rid).iterdir() if x.name != "coverages"]
    assert sobras == [], sobras


def test_e4_upload_de_pasta_e_422_sem_ler_o_corpo(api, pasta):
    rid = _run(api)
    lidos: list[str] = []
    r = api.put(f"/api/v1/runs/{rid}/entradas/coverages", content=_corpo_vigiado(lidos), headers=CRU)
    assert (r.status_code, _erro(r), lidos) == (422, "entrada_exige_caminho", []), r.text
    # controle: o upload do coldata (arquivo) é lido e aceito
    r = api.put(f"/api/v1/runs/{rid}/entradas/amostras", content=_amostras().read_bytes(), headers=CRU)
    assert r.status_code == 200, r.text


# =========================================================================== E5
def _mexer_copia(copia: Path, como: str) -> None:
    membros = sorted(copia.iterdir())
    if como == "novo":
        shutil.copyfile(membros[0], copia / "extra.cov.gz")
    elif como == "removido":
        membros[-1].unlink()
    elif como == "renomeado":
        membros[0].rename(copia / f"X{membros[0].name}")
    elif como == "byte":
        alvo = membros[1]
        st = alvo.stat()
        dados = bytearray(alvo.read_bytes())
        dados[-12] ^= 0xFF
        alvo.write_bytes(bytes(dados))
        os.utime(alvo, ns=(st.st_atime_ns, st.st_mtime_ns))
    elif como == "notas":
        (copia / "notas.txt").write_text("anotação do autor\n", encoding="utf-8")


@pytest.mark.parametrize("como", ["novo", "removido", "renomeado", "byte", "notas"])
def test_e5_pasta_mexida_depois_da_aprovacao(api, pasta, lancados, como):
    rid, _ = _aprovado(api)
    _mexer_copia(_copia(rid), como)
    r = _executar(api, rid)
    if como == "notas":  # controle: fora da regra de leitura, não é membro
        assert r.status_code == 202, r.text
        return
    assert r.status_code == 409 and _erro(r) == "entrada_alterada", r.text
    assert r.json()["detail"]["entrada"] == "coverages" and lancados == []


# =========================================================================== E6
@pytest.mark.parametrize("campo", ["membros", "regra", "controle"])
def test_e6_membros_ou_regra_mexidos_no_plano_e_adulteracao(api, pasta, lancados, cfg, campo):
    rid, _ = _aprovado(api)
    arq = cfg.work_dir / "runs" / rid / "plano.json"
    plano = json.loads(arq.read_text(encoding="utf-8"))
    ent = plano["entradas"]["coverages"]
    assert ent["tipo"] == "diretorio" and len(ent["membros"]) == 6 and ent["n_membros"] == 6
    if campo == "membros":
        nome = sorted(ent["membros"])[0]
        ent["membros"][nome]["sha256"] = "0" * 64
    elif campo == "regra":
        ent["regra"] = {"leitura": r"\.cov\Z", "fixos": None}
    arq.write_text(json.dumps(plano), encoding="utf-8")
    r = _executar(api, rid)
    if campo == "controle":
        assert r.status_code == 202, r.text
    else:
        assert r.status_code == 409 and _erro(r) == "plano_adulterado", r.text


@pytest.mark.parametrize("mexida", ["sha_none_num_membro", "sem_membros", "controle"])
def test_e6_membro_sem_sha_nao_e_atestavel(api, pasta, mexida):
    rid = _registrado(api)
    run = runs._carregar(rid)
    reg = run["entradas"]["coverages"]
    if mexida == "sha_none_num_membro":
        reg["membros"][sorted(reg["membros"])[2]]["sha256"] = None
    elif mexida == "sem_membros":
        reg["membros"] = {}
    runs._gravar_run(rid, run)
    r = _plano(api, rid)
    assert r.status_code == 200, r.text
    r = api.post(f"/api/v1/runs/{rid}/aprovar", json={"plano_sha256": r.json()["plano_sha256"]})
    if mexida == "controle":
        assert r.status_code == 200, r.text
    else:
        assert r.status_code == 409 and _erro(r) == "entrada_nao_atestavel", r.text


# =========================================================================== E7
def test_e7_worker_sem_atestado_da_pasta_falha_fechado(api, pasta, lancados):
    rid, _, _ = _montar_entregue(api, lancados, com_entradas=False)
    falhas = _envelope(api, rid)["portao"]["falhas"]
    assert any("o worker não atestou as entradas" in f for f in falhas), falhas


def test_e7_regra_trocada_no_status_e_outro_arquivo(api, pasta, lancados):
    rid, plano = _aprovado(api)
    assert _executar(api, rid).status_code == 202
    job = lancados[-1]
    assert job["entradas"]["coverages"]["tipo"] == "diretorio" and job["entradas"]["coverages"]["regra"] == REGRA
    forjado = {**job["entradas"], "coverages": {**job["entradas"]["coverages"],
                                                 "regra": {"leitura": r"\.cov\.gz\Z", "fixos": None}}}
    saida = plano["passos"][0]["saida"]
    _gerar_porta(saida)
    _concluir(job["job_id"], saida, {"montar_resumo.json": "{\"n\": 1}"}, com_entradas=False,
              sha_entradas=script_job.atestar_entradas(forjado, completo=True),
              entradas_execucao=script_job.atestar_entradas(forjado, completo=False))
    falhas = _envelope(api, rid)["portao"]["falhas"]
    assert any("coverages: o worker atestou outro arquivo" in f for f in falhas), falhas
    # a árvore é a MESMA (a regra nova casa todos os membros .cov.gz): só a regra difere
    assert not any("difere da aprovada" in f for f in falhas), falhas


def test_e7_controle_worker_com_atestado_da_pasta_aprova(api, pasta, lancados):
    rid, _, _ = _montar_entregue(api, lancados)
    env = _envelope(api, rid)
    assert env["portao"]["ok"], env["portao"]


class _Parar(Exception):
    pass


@pytest.mark.parametrize("alterada", [True, False], ids=["alterada", "controle"])
def test_e7_pasta_alterada_antes_do_popen_nao_roda(monkeypatch, cfg, tmp_path, alterada):
    """Pelo caminho real da fila (queue.executar_por_caminho): a pasta conferida na largada
    difere da aprovada → error SEM chamar o Popen, e a mensagem leva só o nome da entrada."""
    d = tmp_path / "coverages"
    d.mkdir()
    for m in _demo_membros()[:3]:
        shutil.copy2(m, d / m.name)
    arv = diretorio.sha_diretorio(d, REGRA, 1 << 30)
    esp = {"caminho": d.as_posix(), "modo": "copia", "formato": "coverages_bismark", "bytes": arv["bytes"],
           "sha256": arv["sha256"], "irmaos": {}, "candidatos_irmaos": [], "tipo": "diretorio", "regra": REGRA}
    job = queue.novo_job_id("epigenomica-t_montar")
    queue.write_status(job, "queued", modulo="epigenomica", entradas={"coverages": esp},
                       cmd="python modules/epigenomica/scripts/perfil_tss.py --saida x")
    if alterada:
        shutil.copy2(_demo_membros()[4], d / _demo_membros()[4].name)
    chamadas: list = []

    def popen(*a, **k):
        chamadas.append(a)
        raise _Parar

    monkeypatch.setattr(script_job, "subprocess", types.SimpleNamespace(Popen=popen, PIPE=-1, STDOUT=-2))
    with pytest.raises((RuntimeError, _Parar)):
        queue.executar_por_caminho("omica.jobs.script_job.executar", job)
    st = queue.read_status(job)
    assert st["status"] == "error" and st["entradas_execucao"]["coverages"]["tipo"] == "diretorio"
    if alterada:
        assert chamadas == [] and "entrada alterada entre a aprovação e a execução: coverages" in st["error"]
        assert d.as_posix() not in st["error"]
    else:
        assert len(chamadas) == 1


# =========================================================================== E8
def test_e8_get_do_run_traz_amostra_dos_membros(api, pasta):
    rid = _registrado(api)
    ent = api.get(f"/api/v1/runs/{rid}").json()["entradas"]["coverages"]
    assert "membros" not in ent and ent["membros_amostra"] == sorted(m.name for m in _demo_membros())
    assert ent["n_membros"] == 6
    assert len(runs._carregar(rid)["entradas"]["coverages"]["membros"]) == 6  # o run.json guarda tudo


def test_e8_amostra_tem_teto(api, pasta, monkeypatch):
    monkeypatch.setattr(runs, "_MAX_MEMBROS_AMOSTRA", 2)
    rid = _registrado(api)
    ent = api.get(f"/api/v1/runs/{rid}").json()["entradas"]["coverages"]
    assert len(ent["membros_amostra"]) == 2


# =========================================================================== E9
def _trocar_por_link(alvo: Path, destino: Path) -> None:
    """Troca a pasta `alvo` por um link para `destino`: symlink (Linux) ou junção (Windows)."""
    alvo.rename(alvo.with_name(alvo.name + "_original"))
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(destino), str(alvo))
    else:
        os.symlink(destino, alvo, target_is_directory=True)


def _destrocar(alvo: Path) -> None:
    """Desfaz o `_trocar_por_link`: tira o link (na junção, o rmdir tira o ponto de reanálise,
    não o destino) e devolve a pasta original ao lugar — mesmo inode, mesmo caminho."""
    if os.name == "nt":
        os.rmdir(alvo)
    else:
        alvo.unlink()
    alvo.with_name(alvo.name + "_original").rename(alvo)


def test_e9_componente_pai_trocado_entre_o_portao_e_a_copia(api, pasta, cfg, monkeypatch):
    """Adendo §A3.2 (L8): entre o `_fonte_da_entrada` (que resolve e guarda a identidade) e a
    cópia, o componente-PAI vira link para <dados>/work, onde há uma pasta com o mesmo nome e
    conteúdo válido. 422 caminho_recusado e nada em raw/runs/<id>. Controle: sem a troca, 200."""
    rid = _run(api)
    _caixa("lote/cd4")
    falsa = cfg.work_dir / "cd4"
    falsa.mkdir(parents=True)
    for m in _demo_membros()[:4]:
        shutil.copy2(m, falsa / m.name)
    original = runs._fonte_da_entrada
    trocou: list[bool] = []

    def gancho(caminho, ent):
        saida = original(caminho, ent)
        if ent.formato in formatos.DIRETORIOS and not trocou:
            _trocar_por_link(cfg.data_dir / "entradas" / "lote", cfg.work_dir)
            trocou.append(True)
        return saida

    monkeypatch.setattr(runs, "_fonte_da_entrada", gancho)
    r = _por_caminho(api, rid, "coverages", cfg.data_dir / "entradas" / "lote" / "cd4")
    assert trocou and r.status_code == 422 and _erro(r) == "caminho_recusado", r.text
    raw = cfg.raw_dir / "runs" / rid
    assert not raw.exists() or list(raw.iterdir()) == []
    assert runs._carregar(rid)["entradas"] == {}
    # controle: o mesmo caminho, sem a troca (o original está em lote_original)
    monkeypatch.setattr(runs, "_fonte_da_entrada", original)
    r = _por_caminho(api, rid, "coverages", cfg.data_dir / "entradas" / "lote_original" / "cd4")
    assert r.status_code == 200, r.text


def test_e9_troca_e_volta_so_a_conferencia_do_abrir_pasta_segura(api, pasta, cfg, monkeypatch):
    """Adendo §A3.2 (L8), o caso que a conferência DEPOIS da cópia não pega: o componente-pai
    vira link para <dados>/work logo depois do portão do caminho e é DESTROCADO depois do 4º
    membro copiado — resolvida de novo, a pasta dá o mesmo caminho e a mesma identidade, e o que
    foi copiado é a pasta falsa. Só a identidade (st_dev, st_ino) e a raiz conferidas no
    `abrir_pasta` seguram: 422 caminho_recusado pela identidade, antes do 1º membro, nada em
    raw/runs/<id>. Mutante `abrir_pasta(fonte)` (sem identidade nem raiz) → 200 com os 4 membros
    da falsa; o E9 acima continua verde nele, porque sem a volta a pós-cópia pega.
    Controle: os mesmos ganchos sem a troca → 200 com os 6 membros, nenhuma recusa."""
    rid = _run(api)
    lote = _caixa("lote/cd4").parent
    falsa = cfg.work_dir / "cd4"  # fora das raízes de entrada: mesmo nome, conteúdo válido
    falsa.mkdir(parents=True)
    for m in _demo_membros()[:4]:
        shutil.copy2(m, falsa / m.name)
    original_fonte, original_copiar, original_abrir = runs._fonte_da_entrada, runs._copiar_membro, diretorio.abrir_pasta
    estado = {"trocar": True, "trocou": False, "destrocou": False, "copias": 0, "recusas": []}

    def gancho_fonte(caminho, ent):
        saida = original_fonte(caminho, ent)
        if ent.formato in formatos.DIRETORIOS and estado["trocar"] and not estado["trocou"]:
            _trocar_por_link(lote, cfg.work_dir)
            estado["trocou"] = True
        return saida

    def gancho_copiar(fd, destino, teto):
        n = original_copiar(fd, destino, teto)
        estado["copias"] += 1
        if estado["copias"] == 4 and estado["trocou"] and not estado["destrocou"]:
            _destrocar(lote)  # a volta: a conferência pós-cópia vê o caminho e o inode originais
            estado["destrocou"] = True
        return n

    def espiao_abrir(caminho, **kw):  # só registra o motivo da recusa (o 422 não o ecoa)
        try:
            return original_abrir(caminho, **kw)
        except diretorio.ErroMembros as e:
            estado["recusas"].append(str(e))
            raise

    monkeypatch.setattr(runs, "_fonte_da_entrada", gancho_fonte)
    monkeypatch.setattr(runs, "_copiar_membro", gancho_copiar)
    monkeypatch.setattr(diretorio, "abrir_pasta", espiao_abrir)
    r = _por_caminho(api, rid, "coverages", lote / "cd4")
    assert estado["trocou"], estado
    assert r.status_code == 422 and _erro(r) == "caminho_recusado", (estado, r.status_code, r.text[:300])
    assert any("a pasta mudou entre a conferência do caminho e a leitura" in m for m in estado["recusas"]), estado
    assert estado["copias"] == 0 and not estado["destrocou"], estado  # recusou antes do 1º membro
    raw = cfg.raw_dir / "runs" / rid
    assert not raw.exists() or list(raw.iterdir()) == []
    assert runs._carregar(rid)["entradas"] == {}
    # controle: a pasta original de volta ao lugar, os mesmos ganchos sem a troca
    _destrocar(lote)
    estado.update(trocar=False, trocou=False, copias=0, recusas=[])
    r = _por_caminho(api, rid, "coverages", lote / "cd4")
    assert r.status_code == 200, r.text
    assert estado["copias"] == 6 and estado["recusas"] == [], estado
    assert r.json()["entradas"]["coverages"]["n_membros"] == 6


def test_diretorio_continua_python_310():
    """script_job importa o diretorio.py, e o omica-vep (jammy) importa o script_job."""
    fonte = (REPO_ROOT / "omica" / "contrato" / "diretorio.py").read_text(encoding="utf-8")
    ast.parse(fonte, feature_version=(3, 10))


# ======================================================================= porta (P)
def test_plano_congela_a_porta_de_pasta_com_a_regra(api, pasta, lancados):
    rid, plano = _aprovado(api)
    porta = plano["passos"][0]["portas"]["coverages"]
    assert porta["tipo"] == "diretorio" and porta["regra"] == REGRA
    assert porta["caminho"] == f"{plano['passos'][0]['saida']}/coverages"
    assert "tipo" not in plano["passos"][0]["portas"]["montar_resumo"]
    # o passo seguinte recebe a SUBPASTA no argv (tipo dir)
    assert plano["passos"][1]["valores"]["coverages"] == porta["caminho"]
    assert _executar(api, rid).status_code == 202
    assert lancados[-1]["portas"]["coverages"] == {"caminho": porta["caminho"], "regra": REGRA}
    assert lancados[-1]["portas"]["montar_resumo"].endswith("/montar_resumo.json")


def test_p2_controle_montar_simulado_aprova_e_o_seguinte_executa(api, pasta, lancados):
    rid, _, d = _montar_entregue(api, lancados)
    env = _envelope(api, rid)
    assert env["portao"]["ok"], env["portao"]
    info = env["portas"]["coverages"]
    assert info["tipo"] == "diretorio" and info["n_membros"] == 6 and info["validado"]
    assert info["sha256"] == diretorio.sha_diretorio(d, REGRA, 1 << 30)["sha256"]
    assert _executar(api, rid, "consumir").status_code == 202


@pytest.mark.parametrize("caso,trecho", [
    ("ausente", "coverages: pasta não gerada"),
    ("anterior", "arquivo anterior ao início do job"),
    ("trocado_depois", "coverages: difere do que o job deixou ao terminar"),
    ("sem_sha_do_worker", "coverages: o worker não atestou esta porta"),
    ("vazia", "coverages: pasta sem membros"),
    ("porta_link", "coverages: pasta não gerada"),
    ("controle", None),
])
def test_p2_envelope_da_porta_de_pasta(api, pasta, lancados, cfg, caso, trecho):
    rid, plano = _aprovado(api)
    assert _executar(api, rid).status_code == 202
    job, saida = lancados[-1]["job_id"], plano["passos"][0]["saida"]
    resumo = {"montar_resumo.json": json.dumps({"n_amostras": 6})}
    extra: dict = {}
    if caso == "anterior":
        d = _gerar_porta(saida)
        velho = time.time() - 3600
        primeiro = sorted(d.iterdir())[0]
        os.utime(primeiro, (velho, velho))
    elif caso == "porta_link":
        # a porta é um link (junção no Windows) para uma pasta VÁLIDA fora de {saida}
        alvo = _gerar_porta(str(cfg.work_dir / "x"))
        Path(saida).mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            import _winapi

            _winapi.CreateJunction(str(alvo), str(Path(saida) / "coverages"))
        else:
            os.symlink(alvo, Path(saida) / "coverages", target_is_directory=True)
    elif caso == "vazia":
        (Path(saida) / "coverages").mkdir(parents=True)
    elif caso != "ausente":
        _gerar_porta(saida)
    if caso == "sem_sha_do_worker":
        extra["sha_saidas"] = {"montar_resumo": None}
    _concluir(job, saida, resumo, **extra)
    if caso == "trocado_depois":
        alvo = sorted((Path(saida) / "coverages").iterdir())[0]
        st = alvo.stat()
        dados = bytearray(alvo.read_bytes())
        dados[-12] ^= 0xFF
        alvo.write_bytes(bytes(dados))
        os.utime(alvo, ns=(st.st_atime_ns, st.st_mtime_ns))
    env = _envelope(api, rid)
    if trecho is None:
        assert env["portao"]["ok"], env["portao"]
        return
    assert not env["portao"]["ok"] and any(trecho in f for f in env["portao"]["falhas"]), env["portao"]


def test_p2_membro_symlink_na_porta_reprova(api, pasta, lancados, tmp_path):
    """Membro symlink dentro da porta (o Windows sem modo de desenvolvedor não cria symlink:
    pula; o E2E e o container exercitam)."""
    rid, plano = _aprovado(api)
    assert _executar(api, rid).status_code == 202
    saida = plano["passos"][0]["saida"]
    d = _gerar_porta(saida)
    alvo = sorted(d.iterdir())[0]
    fora = tmp_path / "fora.bismark.cov.gz"
    shutil.move(alvo, fora)
    try:
        os.symlink(fora, alvo)
    except (OSError, NotImplementedError):
        pytest.skip("sem permissão para criar symlink neste host")
    _concluir(lancados[-1]["job_id"], saida, {"montar_resumo.json": "{\"n\": 1}"})
    falhas = _envelope(api, rid)["portao"]["falhas"]
    assert any("membro é symlink" in f for f in falhas), falhas


def test_p3_membro_novo_depois_da_entrega_desfaz_a_entrega_leve(api, pasta, lancados):
    rid, _, d = _montar_entregue(api, lancados)
    assert _envelope(api, rid)["portao"]["ok"]
    job = runs._carregar(rid)["passos"]["montar"]["job_id"]
    assert runs._entrega_intacta(rid, "montar", job)  # controle: entregue e intacta
    shutil.copyfile(_demo_membros()[0], d / f"HF__novo{SUFIXO}")
    assert not runs._entrega_intacta(rid, "montar", job)
    falhas = _envelope(api, rid)["portao"]["falhas"]
    assert any("sha256 diferente do envelope entregue" in f or "difere do que o job deixou" in f for f in falhas)


def test_p4_sha_do_worker_e_o_do_envelope(api, pasta, lancados):
    rid, plano, d = _montar_entregue(api, lancados)
    env = _envelope(api, rid)
    status = queue.read_status(lancados[-1]["job_id"])
    assert status["sha_saidas"]["coverages"] == env["portas"]["coverages"]["sha256"] is not None
    porta = plano["passos"][0]["portas"]["coverages"]
    assert script_job.atestar_saidas({"c": {"caminho": porta["caminho"], "regra": REGRA}})["c"] == \
        env["portas"]["coverages"]["sha256"]
    # regra que não é dict ou pasta ausente: None, nunca exceção
    assert script_job.atestar_saidas({"c": {"caminho": porta["caminho"], "regra": "x"},
                                      "d": {"caminho": porta["caminho"] + "_nada", "regra": REGRA}}) == \
        {"c": None, "d": None}


def test_abortar_apaga_a_copia_da_pasta(api, pasta, cfg):
    rid = _registrado(api)
    assert _copia(rid).is_dir()
    assert api.post(f"/api/v1/runs/{rid}/abortar").status_code == 200
    assert not (cfg.raw_dir / "runs" / rid).exists()


# =================================================================== extensões X
@pytest.mark.parametrize("a,b,status", [("HF", "HF", 422), ("Nelore", "Nelore", 422), ("HF", "Nelore", 200)])
def test_x1_escolhas_iguais_dao_422(api, pasta, a, b, status):
    rid = _registrado(api)
    r = _plano(api, rid, a, b)
    assert r.status_code == status, r.text
    if status == 422:
        assert r.json()["detail"] == {"erro": "escolhas_iguais", "escolha": "grupo_b", "outra": "grupo_a"}


@pytest.mark.parametrize("resumo", ['{"a": NaN}', '{"a": Infinity}', '{"a": -Infinity}', '{"a": 1e999}'])
def test_x2_envelope_com_resumo_nao_finito_e_200_com_null(api, lancados, resumo):
    """X2: o JSONResponse do Starlette serializa com allow_nan=False — o GET do envelope e o
    /aguardar (o polling do n8n) viravam 500. Agora 200, com null, e a porta reprova.

    O canário que MORDE é o do `runs.envelope` direto (prova por mutação): nas rotas, o
    response_model que o FastAPI infere do `-> dict[str, Any]` já serializa NaN como null
    (pydantic, mode="json"), e a checagem HTTP sozinha passaria com a regra revertida. A regra
    vale no dado (o envelope é JSON estrito para todo consumidor, não só para esta rota); a
    checagem HTTP fica como o aceite do adendo (200 com null, TestClient)."""
    from test_runs import DE_OK

    rid, plano = _aprovado_rna(api)
    assert api.post(f"/api/v1/runs/{rid}/passos/de/executar").status_code == 202
    _concluir(lancados[-1]["job_id"], plano["passos"][0]["saida"], {"de_table.tsv": DE_OK, "de_resumo.json": resumo})
    direto = runs.envelope(rid, "de")
    assert direto["resumos"]["de_resumo"] == {"a": None}, direto["resumos"]
    json.dumps(direto, allow_nan=False)  # JSON estrito: NaN/Infinity aqui levantaria ValueError
    for url in (f"/api/v1/runs/{rid}/passos/de/envelope", f"/api/v1/runs/{rid}/passos/de/aguardar?segundos=0"):
        r = api.get(url)
        assert r.status_code == 200, (url, r.text)
        env = r.json()
        assert env["resumos"]["de_resumo"] == {"a": None}
        assert any("número não finito" in f for f in env["portao"]["falhas"]), env["portao"]


def test_x2_controle_resumo_finito(api, lancados):
    from test_runs import DE_OK

    rid, plano = _aprovado_rna(api)
    assert api.post(f"/api/v1/runs/{rid}/passos/de/executar").status_code == 202
    _concluir(lancados[-1]["job_id"], plano["passos"][0]["saida"],
              {"de_table.tsv": DE_OK, "de_resumo.json": '{"a": 1.5}'})
    env = api.get(f"/api/v1/runs/{rid}/passos/de/envelope").json()
    assert env["resumos"]["de_resumo"] == {"a": 1.5} and env["portao"]["ok"], env["portao"]


def test_x6_compose_sem_bytecode_em_todo_servico_com_build():
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    com_build = {n: s for n, s in compose["services"].items() if "build" in s}
    assert len(com_build) == 6
    for nome, s in com_build.items():
        assert str(s["environment"].get("PYTHONDONTWRITEBYTECODE")) == "1", nome


@pytest.mark.parametrize("parametros,status", [({"transcriptomica.deseq2.padj": "0.1"}, 422), ({}, 200)])
def test_x7_parametro_fixo_no_pipeline_e_no_contexto_e_422(api, cfg, parametros, status):
    """X7 (crítica M3): o pipeline fixa `padj` no passo `de`; o subprojeto também o sobrescreve
    → 422 parametro_fixo_no_pipeline no plano. Controle: o mesmo rascunho sem `parametros`."""
    from test_runs import DEMO

    escrever_subprojeto(cfg.projetos_dir, "bos_taurus", "x_fixo", assembly="ARS-UCD1.3",
                        modulos=["transcriptomica"], pipelines=["transcriptomica_de"], parametros=parametros)
    r = api.post("/api/v1/runs", json={"pipeline": "transcriptomica_de", "especie": "bos_taurus",
                                       "subprojeto": "x_fixo"})
    assert r.status_code == 201, r.text
    rid = r.json()["run_id"]
    for nome, arq in (("counts", "counts_granulosa.tsv"), ("coldata", "coldata_granulosa.tsv")):
        assert api.put(f"/api/v1/runs/{rid}/entradas/{nome}", json={"caminho": f"{DEMO}/{arq}"}).status_code == 200
    r = api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {"referencia": "winter_control"}})
    assert r.status_code == status, r.text
    if status == 422:
        assert r.json()["detail"] == {"erro": "parametro_fixo_no_pipeline", "passo": "de", "parametros": ["padj"]}


# ================================================================== golden (L12)
def _golden(monkeypatch, tmp_path, gravado: dict, gerado: dict) -> int:
    sys.path.insert(0, str(REPO_ROOT / "tests" / "golden"))
    try:
        import gerar_golden_v1 as g
    finally:
        sys.path.pop(0)
    arq = tmp_path / "golden.json"
    arq.write_text(json.dumps(gravado), encoding="utf-8")
    monkeypatch.setattr(g, "SAIDA_JSON", arq)
    monkeypatch.setattr(g, "_isolar_ambiente", lambda tmp: None)
    monkeypatch.setattr(g, "gerar", lambda: dict(gerado))
    return g.main(["--conferir"])


def test_l12_golden_conferir_compara_so_as_chaves_gravadas(monkeypatch, tmp_path, capsys):
    gravado = {"m/scripts/a": "cmd a", "m/scripts_api/b": "cmd b"}
    # controle: spec nova (depois da v1) é listada e não reprova
    assert _golden(monkeypatch, tmp_path, gravado, {**gravado, "m/scripts_api/novo": "cmd n"}) == 0
    assert "fora do I2" in capsys.readouterr().out
    # canários: chave gravada que mudou ou sumiu reprova
    assert _golden(monkeypatch, tmp_path, gravado, {**gravado, "m/scripts/a": "cmd A"}) == 1
    assert "DIVERGE" in capsys.readouterr().out
    assert _golden(monkeypatch, tmp_path, gravado, {"m/scripts/a": "cmd a"}) == 1
    assert "m/scripts_api/b" in capsys.readouterr().out
