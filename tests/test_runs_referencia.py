"""Entrada POR REFERÊNCIA no run (G1 §1, genômica): um VCF/BCF numa raiz de referência entra
sem cópia, com sha256 completo e impressão; é conferido pelo PLANO no aprovar e no executar e
atestado pelo worker no início e no fim do job.

Cada canário tem o seu CONTROLE — o mesmo arranjo sem a mexida, que passa (senão o 409 viria
do arranjo, não do portão). Os números R1–R14 são os da especificação (§1.13); "crítica #n" é
o item da crítica da especificação. A raiz de referência dos testes é <GENOME_DATA>/1000bg_vcf
(o GENOME_DATA do conftest é o tmp_path), e o VCF é o sintético de test_formatos_genomica (os
3000 primeiros registros do demo, com .tbi). Jobs fingidos como em test_runs.py: `lancados`
grava o status como o enqueue e `_concluir` faz o papel do worker, inclusive o atestado das
entradas (script_job.atestar_entradas, a mesma função do worker).
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import types
from pathlib import Path

import pytest

from omica import runs
from omica.config import REPO_ROOT, reset_config_cache
from omica.contrato import bgzf, formatos
from omica.jobs import dispatch, queue, script_job
from test_formatos_genomica import _demo, escrever_bcf, escrever_csi, escrever_tbi, escrever_vcf, sintetico
from test_runs import _aprovado, _com_entradas, _concluir, api, lancados  # noqa: F401 (fixtures)

GEN = REPO_ROOT / "modules" / "genomica"
BED_DEMO = GEN / "demo_data" / "painel_demo.bed"
CRU = {"Content-Type": "application/octet-stream"}
RESUMO = '{"n": 1}'
BED_BCF = b"14\t50\t300\n"  # dentro do contig de 1 Mb do BCF sintético
DE_OK = ("gene_id\tgene_name\tbaseMean\tlog2FoldChange\tpvalue\tpadj\n"
         "G1\tGENE1\t10\t2.5\t0.001\t0.01\nG2\tGENE2\t5\t-1.2\t0.2\tNA\n")


@pytest.fixture(autouse=True)
def _cache_vcf_limpo():
    """O validador de VCF guarda leituras pela identidade do arquivo; no host Windows o ctime
    é a data de criação, e uma reescrita no lugar com o mtime devolvido não invalidaria."""
    formatos._ler_vcf_cache.cache_clear()
    yield
    formatos._ler_vcf_cache.cache_clear()


@pytest.fixture()
def raiz(cfg, monkeypatch) -> Path:
    """<GENOME_DATA>/1000bg_vcf, fixada como única raiz PELA CONFIG (a mesma variável que a
    produção lê), não por monkeypatch da função."""
    monkeypatch.setenv("OMICA_RAIZES_REFERENCIA", "1000bg_vcf")
    reset_config_cache()
    r = cfg.genome_data / "1000bg_vcf"
    r.mkdir(parents=True, exist_ok=True)
    return r


# ------------------------------------------------------------------------ helpers
def _erro(r) -> str:
    return r.json()["detail"]["erro"]


def _run(api, pipeline: str = "genomica_regioes") -> str:
    r = api.post("/api/v1/runs", json={"pipeline": pipeline})
    assert r.status_code == 201, r.text
    return r.json()["run_id"]


def _sobe_bed(api, rid: str, conteudo: bytes | None = None) -> None:
    r = api.put(f"/api/v1/runs/{rid}/entradas/regioes", content=conteudo or BED_DEMO.read_bytes(), headers=CRU)
    assert r.status_code == 200 and r.json()["entradas"]["regioes"]["veredito"]["ok"], r.text


def _por_caminho(api, rid: str, nome: str, caminho) -> object:
    texto = caminho if isinstance(caminho, str) else caminho.as_posix()
    return api.put(f"/api/v1/runs/{rid}/entradas/{nome}", json={"caminho": texto})


def _pronto(api, raiz: Path, nome: str = "x.vcf.gz") -> tuple[str, Path]:
    vcf = sintetico(raiz, nome)
    rid = _run(api)
    _sobe_bed(api, rid)
    r = _por_caminho(api, rid, "vcf", vcf)
    assert r.status_code == 200, r.text
    assert r.json()["entradas"]["vcf"]["veredito"]["ok"], r.json()["entradas"]["vcf"]["veredito"]
    return rid, vcf


def _plano(api, rid: str) -> dict:
    r = api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {}})
    assert r.status_code == 200, r.text
    return r.json()


def _aprovar(api, rid: str, plano: dict):
    return api.post(f"/api/v1/runs/{rid}/aprovar", json={"plano_sha256": plano["plano_sha256"]})


def _aprovado_ref(api, raiz: Path) -> tuple[str, dict, Path]:
    rid, vcf = _pronto(api, raiz)
    plano = _plano(api, rid)
    r = _aprovar(api, rid, plano)
    assert r.status_code == 200, r.text
    return rid, plano, vcf


def _executar(api, rid: str, passo: str = "extracao"):
    return api.post(f"/api/v1/runs/{rid}/passos/{passo}/executar")


def _envelope(api, rid: str, passo: str = "extracao") -> dict:
    return api.get(f"/api/v1/runs/{rid}/passos/{passo}/envelope").json()


def _saidas_extracao(tmp_path: Path) -> dict[str, bytes | str]:
    """Saídas VÁLIDAS do passo extracao (VCF.gz + .tbi + resumo), geradas fora da pasta do
    run; o _concluir as grava na ordem (o índice depois do dado: mtime ≥)."""
    c, r = _demo()
    p = tmp_path / "_saida_sintetica" / "extraido.vcf.gz"
    p.parent.mkdir(parents=True, exist_ok=True)
    regs = escrever_vcf(p, list(c) + list(r[:500]))
    escrever_tbi(Path(f"{p}.tbi"), regs)
    return {"extraido.vcf.gz": p.read_bytes(), "extraido.vcf.gz.tbi": Path(f"{p}.tbi").read_bytes(),
            "extracao_resumo.json": json.dumps({"n_regioes": 3, "n_variantes_extraidas": 500})}


def _mexer_entrada(monkeypatch, entrada: str, **campos) -> None:
    """O pipeline como o run o enxerga, com uma entrada mudada (contrato fora do YAML): é o
    jeito de exercitar regras de modo que nenhum pipeline real tem (ex.: só referência)."""
    original = runs._pipeline

    def falso(pipeline_id):
        p, m = original(pipeline_id)
        if entrada in p.entradas:
            novas = {**p.entradas, entrada: p.entradas[entrada].model_copy(update=campos)}
            p = p.model_copy(update={"entradas": novas})
        return p, m

    monkeypatch.setattr(runs, "_pipeline", falso)


def _regravar_run(rid: str, mexida) -> None:
    run = runs._carregar(rid)
    mexida(run)
    runs._gravar_run(rid, run)


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ===================================================================== controle
def test_referencia_registra_sem_copia_e_congela_o_caminho_resolvido(api, raiz, cfg):
    """Controle de tudo abaixo: a referência entra com modo, impressão, sha completo e
    índice; nada do VCF vai para raw/runs; o plano congela o caminho resolvido no argv."""
    rid, vcf = _pronto(api, raiz)
    reg = runs._carregar(rid)["entradas"]["vcf"]
    assert reg["modo"] == "referencia" and reg["caminho"] == vcf.resolve().as_posix()
    assert reg["origem"] == f"referencia:{vcf.as_posix()}" and reg["sha256"] == _sha(vcf)
    assert reg["impressao"]["bytes"] == vcf.stat().st_size == reg["bytes"]
    assert set(reg["irmaos"]) == {".tbi"} and reg["irmaos"][".tbi"]["sha256"] == _sha(Path(f"{vcf}.tbi"))
    assert reg["candidatos_irmaos"] == [".tbi", ".csi"]
    assert sorted(p.name for p in (cfg.raw_dir / "runs" / rid).iterdir()) == ["regioes.bed"]
    plano = _plano(api, rid)
    extracao = plano["passos"][0]
    assert extracao["valores"]["vcf"] == vcf.resolve().as_posix() and vcf.resolve().as_posix() in extracao["argv"]
    assert _aprovar(api, rid, plano).status_code == 200


def test_campos_que_o_resumo_do_n8n_le(api, raiz, cfg):
    """Contrato com o workflow do n8n (n8n/build_workflows.py, "Resumo do plano"): ele lê
    `plano["entradas"][n].modo` e as CHAVES de `.irmaos` (os sufixos), e, no GET do run,
    `entradas[n].veredito.perfil.contigs_com_dados` e `n_contigs_com_dados`. Nome mudado
    aqui some do formulário de aprovação sem erro nenhum — nos dois modos."""
    rid_ref, _ = _pronto(api, raiz)
    caixa = cfg.data_dir / "entradas"
    caixa.mkdir(parents=True, exist_ok=True)
    rid_copia = _run(api)
    _sobe_bed(api, rid_copia)
    assert _por_caminho(api, rid_copia, "vcf", sintetico(caixa, "c.vcf.gz")).status_code == 200
    for rid, modo in ((rid_ref, "referencia"), (rid_copia, "copia")):
        vcf = _plano(api, rid)["entradas"]["vcf"]
        assert vcf["modo"] == modo and sorted(vcf["irmaos"]) == [".tbi"], (modo, vcf)
        perfil = api.get(f"/api/v1/runs/{rid}").json()["entradas"]["vcf"]["veredito"]["perfil"]
        assert perfil["contigs_com_dados"] == ["14"] and perfil["n_contigs_com_dados"] == 1, perfil


def test_extracao_por_referencia_atestada_aprova(api, lancados, raiz, tmp_path):
    """Controle do worker (R7/R6c): o passo que lê as entradas leva o esperado no status, e
    o atestado de quem rodou, igual ao plano, aprova; o passo seguinte roda."""
    rid, plano, _ = _aprovado_ref(api, raiz)
    assert _executar(api, rid).status_code == 202
    job = lancados[-1]
    assert set(job["entradas"]) == {"regioes", "vcf"}
    assert job["entradas"]["vcf"]["modo"] == "referencia" and job["entradas"]["vcf"]["impressao"]
    _concluir(job["job_id"], plano["passos"][0]["saida"], _saidas_extracao(tmp_path))
    env = _envelope(api, rid)
    assert env["portao"]["ok"], env["portao"]
    assert _executar(api, rid, "frequencias").status_code == 202
    # o passo que NÃO lê entrada do run não leva `entradas` no status
    assert "entradas" not in lancados[-1]


# ========================================================== R1 + crítica #1/#3
@pytest.mark.parametrize("onde", ["work", "raw_outro_run", "genome_data_outro", "etc_passwd", "subida"])
def test_fora_das_raizes_responde_igual_ao_inexistente_na_raiz(api, raiz, cfg, onde):
    """R1: um VCF VÁLIDO fora das raízes e um inexistente dentro da raiz de referência são
    indistinguíveis, e nada entra no run.json."""
    alvos = {"work": cfg.work_dir / "x" / "x.vcf.gz",
             "raw_outro_run": cfg.raw_dir / "runs" / "genomica_regioes-20990101-000000-abcdef" / "x.vcf.gz",
             "genome_data_outro": cfg.genome_data / "outro_projeto" / "x.vcf.gz"}
    if onde in alvos:
        alvos[onde].parent.mkdir(parents=True, exist_ok=True)
        caminho = sintetico(alvos[onde].parent, "x.vcf.gz").as_posix()
    else:
        caminho = {"etc_passwd": "/etc/passwd", "subida": "../x.vcf.gz"}[onde]
    rid = _run(api)
    fora = _por_caminho(api, rid, "vcf", caminho)
    falta = _por_caminho(api, rid, "vcf", raiz / "nao_existe.vcf.gz")
    assert fora.status_code == falta.status_code == 422 and fora.json() == falta.json()
    assert _erro(fora) == "caminho_recusado" and caminho not in fora.text
    assert "vcf" not in runs._carregar(rid)["entradas"]


@pytest.mark.parametrize("nome", ["amostras_racas.csv", "biosamples_cache.json", "download.log",
                                  "gtonly_1000bg.sh", ".gtonly.lock", "Chr14-Run8-TAUIND-public.building.bcf",
                                  "Chr14.vcf.gz.tbi"])
def test_arquivo_da_raiz_que_nao_e_vcf_nao_revela_hash_nem_tamanho(api, raiz, nome):
    """Crítica #1 e #3: a raiz do Run 8 tem CSV, cache, log, script, trava e o BCF em
    construção do gtonly. Antes do sufixo, o modo referência fazia impressão, sha256 e
    validação de qualquer um, e o tamanho e o hash voltavam no GET do run. Agora o nome é
    conferido ANTES de qualquer stat ou sha: o mesmo 422 de um arquivo inexistente."""
    arq = raiz / nome
    arq.write_bytes(b"conteudo-qualquer-" * 50)
    if nome.endswith(".bcf"):  # o BCF em construção com o .csi ao lado, como o gtonly deixa
        Path(f"{arq}.csi").write_bytes(b"x" * 10)
    rid = _run(api)
    r = _por_caminho(api, rid, "vcf", arq)
    falta = _por_caminho(api, rid, "vcf", raiz / "nao_existe.vcf.gz")
    assert r.status_code == 422 and r.json() == falta.json(), r.text
    assert str(arq.stat().st_size) not in r.text and _sha(arq) not in r.text
    assert "vcf" not in runs._carregar(rid)["entradas"]


def test_vcf_da_raiz_com_nome_valido_passa_no_mesmo_portao(api, raiz):
    """Controle do canário acima: o MESMO portão aceita o VCF com o sufixo do formato."""
    rid, _ = _pronto(api, raiz, "Chr14-Run8-TAUIND-public.vcf.gz")
    assert runs._carregar(rid)["entradas"]["vcf"]["modo"] == "referencia"


def test_caminho_de_referencia_com_token_inseguro_e_recusado(api, raiz):
    """O caminho da referência vai CRU para o argv: um espaço só estouraria depois, como
    parametro_recusado no plano. Controle: o mesmo arquivo com '_' passa."""
    rid = _run(api)
    ruim = sintetico(raiz, "x y.vcf.gz")
    r = _por_caminho(api, rid, "vcf", ruim)
    assert r.status_code == 422 and _erro(r) == "caminho_recusado", r.text
    bom = sintetico(raiz, "x_y.vcf.gz")
    assert _por_caminho(api, rid, "vcf", bom).status_code == 200


# ================================================================ R3 symlink
def test_r3_symlink_para_fora_recusa_e_para_dentro_congela_o_resolvido(api, raiz, cfg):
    """Link para fora da raiz: 422 sem eco. Link para dentro: aceito, e o plano guarda o
    caminho RESOLVIDO — retargetar o link depois da aprovação não muda o argv."""
    fora = cfg.genome_data / "outro"
    fora.mkdir()
    longe = sintetico(fora, "longe.vcf.gz")
    real = sintetico(raiz, "real.vcf.gz")
    outro_real = sintetico(raiz, "outro_real.vcf.gz")
    try:
        (raiz / "fora.vcf.gz").symlink_to(longe)
        (raiz / "atalho.vcf.gz").symlink_to(real)
    except OSError:
        pytest.skip("sem permissão para criar symlink neste sistema")
    rid = _run(api)
    _sobe_bed(api, rid)
    r = _por_caminho(api, rid, "vcf", raiz / "fora.vcf.gz")
    assert r.status_code == 422 and _erro(r) == "caminho_recusado" and "longe" not in r.text
    reg = _por_caminho(api, rid, "vcf", raiz / "atalho.vcf.gz").json()["entradas"]["vcf"]
    assert reg["caminho"] == real.resolve().as_posix() and reg["veredito"]["ok"], reg
    plano = _plano(api, rid)
    assert _aprovar(api, rid, plano).status_code == 200
    (raiz / "atalho.vcf.gz").unlink()
    (raiz / "atalho.vcf.gz").symlink_to(outro_real)
    assert real.resolve().as_posix() in plano["passos"][0]["argv"]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="sem FIFO neste sistema")
def test_fifo_na_raiz_e_recusado_sem_ler(api, raiz):
    """Arquivo especial (FIFO) com o sufixo certo: ler travaria a request. S_ISREG antes."""
    os.mkfifo(raiz / "cano.vcf.gz")
    rid = _run(api)
    r = _por_caminho(api, rid, "vcf", raiz / "cano.vcf.gz")
    assert r.status_code == 422 and _erro(r) == "caminho_recusado"


# =================================================================== R2 raízes
@pytest.mark.parametrize("nome,motivo", [
    (".", "estritamente dentro"),
    ("dados", "pasta de dados"),             # = OMICA_DATA nos testes (/data/omica no container)
    ("nao_existe", "inexistente"),
    ("a/../dados", "fora do padrão"),
    ("/abs", "fora do padrão"),
    ("", "fora do padrão"),
])
def test_raiz_mal_configurada_e_descartada(api, cfg, monkeypatch, nome, motivo):
    if nome == "":  # vazio pela variável vira "ausente" (default): o caso entra pela função
        monkeypatch.setattr(dispatch, "_nomes_raizes_referencia", lambda: (nome,))
    else:
        monkeypatch.setenv("OMICA_RAIZES_REFERENCIA", nome)
    reset_config_cache()
    r, msg = dispatch._avaliar_raiz(cfg.genome_data.resolve(), nome)
    assert r is None and motivo in msg, msg
    assert dispatch.raizes_referencia() == []
    # um VCF válido dentro da pasta (quando ela existe) não entra por referência
    pasta = cfg.data_dir / "x" if nome == "dados" else cfg.genome_data / "x"
    pasta.mkdir(parents=True, exist_ok=True)
    rid = _run(api)
    r = _por_caminho(api, rid, "vcf", sintetico(pasta, "x.vcf.gz"))
    assert r.status_code == 422 and _erro(r) == "caminho_recusado", r.text


def test_raiz_que_contem_a_pasta_de_dados_e_descartada(cfg, monkeypatch, tmp_path):
    monkeypatch.setenv("OMICA_DATA", str(tmp_path / "pai" / "dados"))
    reset_config_cache()
    (tmp_path / "pai" / "dados").mkdir(parents=True)
    r, motivo = dispatch._avaliar_raiz(tmp_path.resolve(), "pai")
    assert r is None and "pasta de dados" in motivo


def test_raiz_que_cruza_o_repositorio_e_descartada(cfg, monkeypatch):
    """A regra é a do REPOSITÓRIO (o .env do token mora lá); uma raiz dentro de demo_data
    seria também caixa de entrada (crítica #15: o motivo específico é o que se afirma)."""
    monkeypatch.setenv("GENOME_DATA", str(REPO_ROOT.parent))
    reset_config_cache()
    nome = f"{REPO_ROOT.name}/modules/genomica/demo_data"
    monkeypatch.setenv("OMICA_RAIZES_REFERENCIA", nome)
    reset_config_cache()
    r, motivo = dispatch._avaliar_raiz(REPO_ROOT.parent.resolve(), nome)
    assert r is None and "repositório" in motivo
    assert dispatch.raizes_referencia() == []


def test_raiz_bem_configurada_e_aceita(raiz):
    """Controle de R2."""
    assert dispatch.raizes_referencia() == [raiz.resolve()]


def test_validar_caminho_aceita_so_as_raizes_de_referencia(raiz, cfg):
    """No host o GENOME_DATA é uma pasta temporária: o plano de um run por referência só
    monta o argv se o sanitizador aceitar a raiz configurada — e só ela."""
    dispatch._validar_caminho((raiz / "x.vcf.gz").resolve().as_posix(), "vcf")
    with pytest.raises(ValueError, match="fora das raízes"):
        dispatch._validar_caminho((cfg.genome_data / "outro" / "x.vcf.gz").resolve().as_posix(), "vcf")


# =================================================================== R4 modos
def test_modo_nao_aceito_pela_entrada_e_o_mesmo_422(api, raiz, cfg, monkeypatch):
    """R4: caminho de referência numa entrada só de cópia, e caminho da caixa numa entrada só
    de referência — o mesmo 422 de um inexistente. Controle: com os dois modos, os dois
    entram."""
    vcf = sintetico(raiz, "x.vcf.gz")
    caixa = cfg.data_dir / "entradas"
    caixa.mkdir(parents=True, exist_ok=True)
    na_caixa = sintetico(caixa, "c.vcf.gz")
    rid = _run(api)
    falta = _por_caminho(api, rid, "vcf", raiz / "nao_existe.vcf.gz").json()
    with monkeypatch.context() as mp:
        _mexer_entrada(mp, "vcf", modos=["copia"])
        r = _por_caminho(api, rid, "vcf", vcf)
        assert r.status_code == 422 and r.json() == falta
    with monkeypatch.context() as mp:
        _mexer_entrada(mp, "vcf", modos=["referencia"])
        r = _por_caminho(api, rid, "vcf", na_caixa)
        assert r.status_code == 422 and r.json() == falta
    assert _por_caminho(api, rid, "vcf", vcf).json()["entradas"]["vcf"]["modo"] == "referencia"
    assert _por_caminho(api, rid, "vcf", na_caixa).json()["entradas"]["vcf"]["modo"] == "copia"


def test_upload_em_entrada_so_de_referencia_e_recusado_antes_do_corpo(api, monkeypatch):
    """R4: o portão barato (`aceita_upload`, para a rota chamar antes de ler o corpo) e o
    registrar_entrada, sob a trava, recusam o upload numa entrada sem `copia`. Controle: a
    mesma entrada com `copia` aceita."""
    rid = _run(api)
    assert runs.aceita_upload(rid, "regioes") is None
    _mexer_entrada(monkeypatch, "regioes", modos=["referencia"])
    with pytest.raises(runs.ErroRun) as e:
        runs.aceita_upload(rid, "regioes")
    assert (e.value.status, e.value.erro) == (422, "entrada_so_por_referencia")
    r = api.put(f"/api/v1/runs/{rid}/entradas/regioes", content=BED_DEMO.read_bytes(), headers=CRU)
    assert r.status_code == 422 and _erro(r) == "entrada_so_por_referencia", r.text


def test_upload_de_vcf_exige_indice(api):
    """I7/A1: um upload não traz o .tbi/.csi; recusado no portão barato e no registro (que a
    rota não leia o corpo é o test_a1_upload_recusado_pela_rota_sem_ler_o_corpo)."""
    rid = _run(api)
    with pytest.raises(runs.ErroRun) as e:
        runs.aceita_upload(rid, "vcf")
    assert (e.value.status, e.value.erro) == (422, "entrada_exige_indice")
    r = api.put(f"/api/v1/runs/{rid}/entradas/vcf", content=b"\x1f\x8b" + b"0" * 100, headers=CRU)
    assert r.status_code == 422 and _erro(r) == "entrada_exige_indice", r.text
    with pytest.raises(runs.ErroRun) as e:
        runs.aceita_upload(rid, "nao_existe")
    assert (e.value.status, e.value.erro) == (404, "entrada_inexistente")


def _corpo_vigiado(lidos: list[str], blocos: list[bytes] | None = None):
    """Corpo em fluxo que registra cada bloco que o servidor pede. Sem `blocos`, é o upload
    de 300 MiB que NÃO pode ser lido: pedir o 1º bloco fica registrado e derruba a request
    (o teste nunca monta os 300 MiB na memória)."""
    if blocos is None:
        lidos.append("bloco")
        raise AssertionError("o servidor leu o corpo de um upload que ia recusar (300 MiB)")
    for b in blocos:
        lidos.append("bloco")
        yield b


def test_a1_upload_recusado_pela_rota_sem_ler_o_corpo(api, monkeypatch):
    """A1/R4/I7 (G1 §1.4): a ROTA chama o portão barato (`runs.aceita_upload`) antes de ler o
    corpo — VCF (índice obrigatório), entrada inexistente, entrada só por referência, run
    inexistente e run já aprovado são recusados sem ler um byte. Antes o servidor lia até
    256 MiB para só então
    recusar. Controles: o mesmo corpo vigiado num upload ACEITO é lido (o vigia funciona) e
    o VCF por caminho segue pelo ramo JSON, que não passa por esse portão."""
    rid = _run(api)
    for nome, status, erro in (("vcf", 422, "entrada_exige_indice"),
                               ("nao_existe", 404, "entrada_inexistente")):
        lidos: list[str] = []
        r = api.put(f"/api/v1/runs/{rid}/entradas/{nome}", content=_corpo_vigiado(lidos), headers=CRU)
        assert (r.status_code, _erro(r)) == (status, erro), r.text
        assert lidos == [], nome
    lidos = []
    r = api.put("/api/v1/runs/genomica_regioes-20260101-000000-abcdef/entradas/vcf",
                content=_corpo_vigiado(lidos), headers=CRU)
    assert (r.status_code, _erro(r), lidos) == (404, "run_inexistente", [])
    with monkeypatch.context() as mp:
        _mexer_entrada(mp, "regioes", modos=["referencia"])
        r = api.put(f"/api/v1/runs/{rid}/entradas/regioes", content=_corpo_vigiado(lidos), headers=CRU)
        assert (r.status_code, _erro(r), lidos) == (422, "entrada_so_por_referencia", [])
    assert runs._carregar(rid)["entradas"] == {}
    # run já aprovado: as entradas estão congeladas, o corpo seria lido para nada
    rid_aprovado, _ = _aprovado(api)
    r = api.put(f"/api/v1/runs/{rid_aprovado}/entradas/counts", content=_corpo_vigiado(lidos), headers=CRU)
    assert (r.status_code, _erro(r), lidos) == (409, "entradas_congeladas", [])
    # controle: o upload do BED (cópia, sem índice) passa pelo portão e o corpo É lido
    r = api.put(f"/api/v1/runs/{rid}/entradas/regioes", headers=CRU,
                content=_corpo_vigiado(lidos, [BED_DEMO.read_bytes()]))
    assert r.status_code == 200 and lidos == ["bloco"], r.text
    # controle: o VCF por caminho não passa pelo portão do upload (o ramo JSON segue igual)
    r = _por_caminho(api, rid, "vcf", "nao/existe.vcf.gz")
    assert (r.status_code, _erro(r)) == (422, "caminho_recusado"), r.text


def test_cliente_nao_escolhe_o_modo(api, raiz):
    """A2: o corpo JSON só tem `caminho`; o modo é do servidor (raiz × contrato)."""
    rid = _run(api)
    r = api.put(f"/api/v1/runs/{rid}/entradas/vcf",
                json={"caminho": sintetico(raiz, "x.vcf.gz").as_posix(), "modo": "referencia"})
    assert r.status_code == 422 and _erro(r) == "corpo_invalido"


# ================================================================ R5 teto
def test_referencia_nao_passa_pelo_teto_da_copia(api, raiz, cfg, monkeypatch):
    """R5: com _MAX_ENTRADA=100 a referência entra; o MESMO arquivo pela caixa (cópia) dá
    413, e a pasta do run fica sem resto (nem a pasta temporária do registro)."""
    monkeypatch.setattr(runs, "_MAX_ENTRADA", 100)
    vcf = sintetico(raiz, "x.vcf.gz")
    rid = _run(api)
    assert _por_caminho(api, rid, "vcf", vcf).status_code == 200
    caixa = cfg.data_dir / "entradas"
    caixa.mkdir(parents=True, exist_ok=True)
    for arq in (vcf, Path(f"{vcf}.tbi")):
        shutil.copy2(arq, caixa / arq.name)
    rid2 = _run(api)
    r = _por_caminho(api, rid2, "vcf", caixa / "x.vcf.gz")
    assert r.status_code == 413 and _erro(r) == "entrada_grande_demais", r.text
    assert list((cfg.raw_dir / "runs" / rid2).iterdir()) == []


def test_referencia_acima_do_proprio_teto_e_413_sem_registro(api, raiz, monkeypatch):
    """O teto da referência é o do sha completo (_MAX_REFERENCIA, 4 GiB): acima dele o sha
    seria None e a entrada não atestável. 413 ANTES do sha, com o limite, e nada no run.json.
    Controle: o teste acima (o mesmo arquivo com o teto padrão entra)."""
    monkeypatch.setattr(runs, "_MAX_REFERENCIA", 100)
    vcf = sintetico(raiz, "x.vcf.gz")
    rid = _run(api)
    r = _por_caminho(api, rid, "vcf", vcf)
    assert r.status_code == 413 and _erro(r) == "entrada_grande_demais", r.text
    assert r.json()["detail"]["limite_bytes"] == 100
    assert "vcf" not in runs._carregar(rid)["entradas"]


def test_tetos_do_worker_em_sincronia():
    """O worker não importa o contrato (imagem leve); os tetos dele são cópias."""
    assert script_job._MAX_SHA_IRMAO == bgzf.MAX_INDICE
    assert script_job._MAX_SHA_ENTRADA == runs._MAX_REFERENCIA


# ================================================================ R6 alterada
def test_r6a_outro_tamanho_depois_da_aprovacao_nao_lanca(api, lancados, raiz):
    rid, _, vcf = _aprovado_ref(api, raiz)
    with vcf.open("ab") as f:
        f.write(b"\0")
    r = _executar(api, rid)
    assert r.status_code == 409 and _erro(r) == "entrada_alterada", r.text
    assert lancados == []


def test_r6b_byte_trocado_no_trecho_amostrado_com_mtime_devolvido(api, lancados, raiz):
    rid, _, vcf = _aprovado_ref(api, raiz)
    st = vcf.stat()
    dados = bytearray(vcf.read_bytes())
    dados[100] ^= 0xFF
    vcf.write_bytes(bytes(dados))
    os.utime(vcf, ns=(st.st_atime_ns, st.st_mtime_ns))
    r = _executar(api, rid)
    assert r.status_code == 409 and _erro(r) == "entrada_alterada", r.text
    assert lancados == []


def _fora_da_impressao(n: int) -> int:
    trechos = [(0, min(script_job._PONTA, n)), (max(0, n - script_job._PONTA), min(script_job._PONTA, n))]
    trechos += [(i * n // 17, min(script_job._BLOCO, n - i * n // 17)) for i in range(1, 17)]
    return next(o for o in range(n // 3, n) if not any(a <= o < a + t for a, t in trechos))


def test_r6c_byte_fora_da_amostra_passa_no_executar_e_reprova_no_envelope(api, lancados, raiz, tmp_path,
                                                                         monkeypatch):
    """Resíduo R1 documentado: a impressão lê trechos fixos; um byte trocado fora deles, com o
    mtime devolvido, passa no executar. Quem pega é o sha256 COMPLETO que o worker tira ao
    terminar (`sha_entradas`): o envelope reprova e o passo seguinte não roda. (Trechos
    encolhidos para caber num arquivo de teste; a receita é a mesma.)
    Controle: test_extracao_por_referencia_atestada_aprova (o mesmo arranjo sem a troca)."""
    monkeypatch.setattr(script_job, "_PONTA", 1024)
    monkeypatch.setattr(script_job, "_BLOCO", 64)
    rid, plano, vcf = _aprovado_ref(api, raiz)
    st = vcf.stat()
    dados = bytearray(vcf.read_bytes())
    dados[_fora_da_impressao(len(dados))] ^= 0xFF
    vcf.write_bytes(bytes(dados))
    os.utime(vcf, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert _executar(api, rid).status_code == 202  # o resíduo: a impressão não vê
    _concluir(lancados[-1]["job_id"], plano["passos"][0]["saida"], _saidas_extracao(tmp_path))
    env = _envelope(api, rid)
    assert not env["portao"]["ok"]
    assert any("vcf: a entrada lida pelo job difere da aprovada (sha256 ao terminar)" in f
               for f in env["portao"]["falhas"]), env["portao"]
    assert not any("(início)" in f for f in env["portao"]["falhas"])
    r = _executar(api, rid, "frequencias")
    assert r.status_code == 409 and _erro(r) == "passo_anterior_pendente", r.text


# ============================================================ R7 worker calado
@pytest.mark.parametrize("como", ["sem_atestado", "sem_uma_entrada", "estragado"])
def test_r7_worker_sem_atestado_das_entradas_falha_fechado(api, lancados, como):
    """Sem o atestado do worker não há com o que comparar a entrada lida: falha, nunca "ok
    por omissão" (o worker antigo, de antes deste deploy, cai aqui). Controle: o teste
    seguinte, o mesmo arranjo com o atestado."""
    rid, plano = _aprovado(api)
    assert _executar(api, rid, "de").status_code == 202
    job = lancados[-1]
    saida = plano["passos"][0]["saida"]
    if como == "sem_atestado":
        _concluir(job["job_id"], saida, {"de_table.tsv": DE_OK, "de_resumo.json": RESUMO}, com_entradas=False)
        trecho = "o worker não atestou as entradas"
    elif como == "estragado":
        _concluir(job["job_id"], saida, {"de_table.tsv": DE_OK, "de_resumo.json": RESUMO}, com_entradas=False,
                  sha_entradas="lixo", entradas_execucao=["lixo"])
        trecho = "o worker não atestou as entradas"
    else:
        so_counts = {"counts": job["entradas"]["counts"]}
        _concluir(job["job_id"], saida, {"de_table.tsv": DE_OK, "de_resumo.json": RESUMO}, com_entradas=False,
                  sha_entradas=script_job.atestar_entradas(so_counts, completo=True),
                  entradas_execucao=script_job.atestar_entradas(so_counts, completo=False))
        trecho = "coldata: o worker não atestou esta entrada"
    env = _envelope(api, rid, "de")
    assert not env["portao"]["ok"] and any(trecho in f for f in env["portao"]["falhas"]), env["portao"]
    assert _executar(api, rid, "volcano").status_code == 409


def test_r7_controle_worker_com_atestado_aprova(api, lancados):
    rid, plano = _aprovado(api)
    assert _executar(api, rid, "de").status_code == 202
    assert set(lancados[-1]["entradas"]) == {"counts", "coldata"}
    _concluir(lancados[-1]["job_id"], plano["passos"][0]["saida"], {"de_table.tsv": DE_OK, "de_resumo.json": RESUMO})
    env = _envelope(api, rid, "de")
    assert env["portao"]["ok"], env["portao"]


def test_status_com_caminho_trocado_nao_atesta_outro_arquivo(api, lancados, tmp_path):
    """Crítica #9: o worker atesta caminhos lidos do status.json, que não é protegido por sha.
    Um status com o caminho trocado por um GÊMEO byte a byte (mesmo sha) atestava o gêmeo; o
    envelope agora exige o caminho e os candidatos do PLANO."""
    rid, plano = _aprovado(api)
    assert _executar(api, rid, "de").status_code == 202
    job = lancados[-1]
    gemeo = tmp_path / "gemeo" / "counts.tsv"
    gemeo.parent.mkdir()
    gemeo.write_bytes(Path(job["entradas"]["counts"]["caminho"]).read_bytes())
    forjado = {**job["entradas"], "counts": {**job["entradas"]["counts"], "caminho": gemeo.as_posix()}}
    _concluir(job["job_id"], plano["passos"][0]["saida"], {"de_table.tsv": DE_OK, "de_resumo.json": RESUMO},
              com_entradas=False, sha_entradas=script_job.atestar_entradas(forjado, completo=True),
              entradas_execucao=script_job.atestar_entradas(forjado, completo=False))
    falhas = _envelope(api, rid, "de")["portao"]["falhas"]
    assert any("counts: o worker atestou outro arquivo" in f for f in falhas), falhas
    assert not any("difere da aprovada" in f for f in falhas)  # o conteúdo é idêntico


# ============================================================== R8 largada
class _Parar(Exception):
    pass


def _espec_de(arq: Path, modo: str) -> dict:
    esp = {"caminho": arq.as_posix(), "modo": modo, "formato": "vcf_indexado", "bytes": arq.stat().st_size,
           "sha256": _sha(arq), "irmaos": {}, "candidatos_irmaos": []}
    if modo == "referencia":
        esp["impressao"] = script_job.impressao_arquivo(arq)
    return esp


@pytest.mark.parametrize("modo", ["copia", "referencia"])
@pytest.mark.parametrize("alterada", [True, False], ids=["alterada", "controle"])
def test_r8_worker_nao_roda_o_script_com_entrada_alterada(monkeypatch, cfg, tmp_path, modo, alterada):
    """R8: na largada o worker atesta as entradas; diferentes do aprovado, o job vai a
    `error` (pelo caminho real da fila, queue.executar_por_caminho) SEM chamar o Popen, e a
    mensagem leva só o nome. Controle: sem a troca, o Popen é chamado."""
    arq = tmp_path / "entrada.vcf.gz"
    arq.write_bytes(b"A" * 4096)
    job = queue.novo_job_id("genomica-extracao")
    queue.write_status(job, "queued", modulo="genomica", entradas={"vcf": _espec_de(arq, modo)},
                       cmd="python3 modules/genomica/scripts/extracao_regioes.py --saida x")
    if alterada:
        arq.write_bytes(b"B" * 4096)
    chamadas: list = []

    def popen(*a, **k):
        chamadas.append(a)
        raise _Parar

    monkeypatch.setattr(script_job, "subprocess", types.SimpleNamespace(Popen=popen, PIPE=-1, STDOUT=-2))
    with pytest.raises((RuntimeError, _Parar)):
        queue.executar_por_caminho("omica.jobs.script_job.executar", job)
    st = queue.read_status(job)
    assert st["status"] == "error" and isinstance(st["entradas_execucao"]["vcf"], dict)
    if alterada:
        assert chamadas == [] and "entrada alterada entre a aprovação e a execução: vcf" in st["error"]
        assert arq.as_posix() not in st["error"]
    else:
        assert len(chamadas) == 1


def test_worker_atesta_o_sha_completo_das_entradas_no_done(monkeypatch, cfg, tmp_path):
    """O worker REAL (não o _concluir dos testes): rc 0 → o 'done' traz `sha_entradas` com o
    sha completo e os índices, que conferem com o esperado (é o que fecha o resíduo R1)."""
    arq = tmp_path / "entrada.vcf.gz"
    arq.write_bytes(b"A" * 4096)
    tbi = Path(f"{arq}.tbi")
    tbi.write_bytes(b"T" * 100)
    esp = {**_espec_de(arq, "referencia"), "irmaos": {".tbi": _sha(tbi)}, "candidatos_irmaos": [".tbi", ".csi"]}
    job = queue.novo_job_id("genomica-extracao")
    queue.write_status(job, "queued", modulo="genomica", entradas={"vcf": esp},
                       cmd="python3 modules/genomica/scripts/extracao_regioes.py --saida x")

    class _Processo:
        stdout = iter(())

        def wait(self):
            return 0

    monkeypatch.setattr(script_job, "subprocess",
                        types.SimpleNamespace(Popen=lambda *a, **k: _Processo(), PIPE=-1, STDOUT=-2))
    queue.executar_por_caminho("omica.jobs.script_job.executar", job)
    st = queue.read_status(job)
    assert st["status"] == "done", st.get("error")
    assert st["sha_entradas"]["vcf"]["sha256"] == _sha(arq)
    assert st["sha_entradas"]["vcf"]["irmaos"] == {".tbi": _sha(tbi)}
    assert script_job.divergencias(esp, st["sha_entradas"]["vcf"], completo=True) == set()
    assert script_job.divergencias(esp, st["entradas_execucao"]["vcf"], completo=False) == set()


# ===================================================== R9 instável no registro
def test_r9_arquivo_que_cresce_durante_o_registro_e_instavel(api, raiz, monkeypatch):
    vcf = sintetico(raiz, "x.vcf.gz")
    rid = _run(api)
    original = runs.formatos.validar_com_nome

    def cresce(formato, caminho, **kw):
        with open(caminho, "ab") as f:
            f.write(b"\0" * 10)
        return original(formato, caminho, **kw)

    monkeypatch.setattr(runs.formatos, "validar_com_nome", cresce)
    r = _por_caminho(api, rid, "vcf", vcf)
    assert r.status_code == 409 and _erro(r) == "referencia_instavel", r.text
    assert "vcf" not in runs._carregar(rid)["entradas"]


def test_r9_indice_trocado_durante_o_registro_e_instavel(api, raiz, monkeypatch, tmp_path):
    """O índice registrado (sha) tem de ser o que o validador leu (perfil indices_sha256)."""
    vcf = sintetico(raiz, "x.vcf.gz")
    outro = sintetico(tmp_path, "outro.vcf.gz", reg=list(_demo()[1][:10]))
    rid = _run(api)
    original = runs.formatos.validar_com_nome

    def troca(formato, caminho, **kw):
        Path(f"{caminho}.tbi").write_bytes(Path(f"{outro}.tbi").read_bytes())
        return original(formato, caminho, **kw)

    monkeypatch.setattr(runs.formatos, "validar_com_nome", troca)
    r = _por_caminho(api, rid, "vcf", vcf)
    assert r.status_code == 409 and _erro(r) == "referencia_instavel", r.text
    assert "vcf" not in runs._carregar(rid)["entradas"]


def test_r9_controle_validacao_sem_mexida_registra(api, raiz, monkeypatch):
    original = runs.formatos.validar_com_nome
    monkeypatch.setattr(runs.formatos, "validar_com_nome", lambda f, c, **kw: original(f, c, **kw))
    rid = _run(api)
    assert _por_caminho(api, rid, "vcf", sintetico(raiz, "x.vcf.gz")).status_code == 200


# ================================================================ R10 L1
def test_r10_sha_none_da_referencia_nao_e_atestavel(api, raiz):
    """L1: o sha None injetado ANTES do plano (o aprovar lê o plano proposto, crítica #15) não
    "confere" com nada. Sem o portão, a referência passava: o aprovar nem relê o sha dela."""
    rid, _ = _pronto(api, raiz)
    _regravar_run(rid, lambda run: run["entradas"]["vcf"].update(sha256=None))
    r = _aprovar(api, rid, _plano(api, rid))
    assert r.status_code == 409 and _erro(r) == "entrada_nao_atestavel", r.text


def test_r10_sha_none_do_indice_nao_e_atestavel(api, raiz):
    """Crítica #14: o mesmo para o irmão (um .csi acima do teto tem sha None)."""
    rid, _ = _pronto(api, raiz)
    _regravar_run(rid, lambda run: run["entradas"]["vcf"]["irmaos"][".tbi"].update(sha256=None))
    r = _aprovar(api, rid, _plano(api, rid))
    assert r.status_code == 409 and _erro(r) == "entrada_nao_atestavel", r.text


def test_r10_sha_none_da_copia_acima_do_teto_nao_e_atestavel(api, monkeypatch):
    """L1 na cópia: registro sem sha e arquivo acima do teto do sha (os dois None). Antes,
    None != None é falso e o aprovar passava."""
    rid = _com_entradas(api)

    def sem_sha(run):
        for reg in run["entradas"].values():
            reg["sha256"] = None
    _regravar_run(rid, sem_sha)
    plano = api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {"referencia": "winter_control"}}).json()
    monkeypatch.setattr(runs, "_MAX_SHA", 10)
    r = _aprovar(api, rid, plano)
    assert r.status_code == 409 and _erro(r) == "entrada_nao_atestavel", r.text


def test_r10_controle_registro_com_sha_aprova(api, raiz):
    rid, _ = _pronto(api, raiz)
    assert _aprovar(api, rid, _plano(api, rid)).status_code == 200


def test_l1_adaptar_entrada_sem_sha_nao_e_atestavel(api):
    """L1 no adaptar: sem sha do original, `sha256_arquivo(original) != None` dava
    entrada_alterada por acaso; o certo é dizer que não é atestável."""
    rid = _com_entradas(api)
    _regravar_run(rid, lambda run: run["entradas"]["counts"].update(sha256=None))
    r = api.post(f"/api/v1/runs/{rid}/entradas/counts/adaptar")
    assert r.status_code == 409 and _erro(r) == "entrada_nao_atestavel", r.text


# ============================================================ R11 ciclo de vida
def test_r11_abortar_e_reregistrar_nao_apagam_a_referencia(api, raiz, cfg):
    rid, vcf = _pronto(api, raiz)
    antes = {p.name: _sha(p) for p in raiz.iterdir()}
    caixa = cfg.data_dir / "entradas"
    caixa.mkdir(parents=True, exist_ok=True)
    for arq in (vcf, Path(f"{vcf}.tbi")):
        shutil.copy2(arq, caixa / arq.name)
    # re-registro como cópia (o anterior era a referência) e de volta à referência
    assert _por_caminho(api, rid, "vcf", caixa / "x.vcf.gz").json()["entradas"]["vcf"]["modo"] == "copia"
    assert _por_caminho(api, rid, "vcf", vcf).json()["entradas"]["vcf"]["modo"] == "referencia"
    # a cópia intermediária saiu de raw/runs (re-registro limpa o que é do run)
    assert sorted(p.name for p in (cfg.raw_dir / "runs" / rid).iterdir()) == ["regioes.bed"]
    assert api.post(f"/api/v1/runs/{rid}/abortar").json()["estado"] == "abortado"
    assert {p.name: _sha(p) for p in raiz.iterdir()} == antes


# ======================================================== R12 plano e lista branca
def test_r12_impressao_mexida_no_plano_e_adulteracao(api, lancados, raiz, cfg):
    rid, _, _ = _aprovado_ref(api, raiz)
    arq = cfg.work_dir / "runs" / rid / "plano.json"
    plano = json.loads(arq.read_text(encoding="utf-8"))
    plano["entradas"]["vcf"]["impressao"]["amostra_sha256"] = "0" * 64
    arq.write_text(json.dumps(plano), encoding="utf-8")
    r = _executar(api, rid)
    assert r.status_code == 409 and _erro(r) == "plano_adulterado", r.text
    assert lancados == []


def test_r12_todo_campo_do_registro_entra_no_plano(api, raiz, cfg):
    """Canário estrutural da lista branca: para todo modo, o registro inteiro (menos o
    veredito) está no plano — um campo novo fora da lista ficaria fora do sha aprovado."""
    rid, _ = _pronto(api, raiz)
    caixa = cfg.data_dir / "entradas"
    caixa.mkdir(parents=True, exist_ok=True)
    rid_copia = _run(api)
    _sobe_bed(api, rid_copia)
    assert _por_caminho(api, rid_copia, "vcf", sintetico(caixa, "c.vcf.gz")).status_code == 200
    rid_tr = _com_entradas(api)
    planos = {rid: _plano(api, rid), rid_copia: _plano(api, rid_copia),
              rid_tr: api.post(f"/api/v1/runs/{rid_tr}/plano", json={"escolhas": {"referencia": "winter_control"}}).json()}
    modos = set()
    for r_id, plano in planos.items():
        for n, reg in runs._carregar(r_id)["entradas"].items():
            modos.add(reg.get("modo"))
            assert set(reg) - {"veredito"} <= set(plano["entradas"][n]), (n, set(reg) - set(plano["entradas"][n]))
    assert modos == {"copia", "referencia"}


# ================================================================ R13 conteúdo
def test_r13_bcf_com_sufixo_de_vcf_reprova_no_veredito(api, raiz):
    falso = raiz / "y.vcf.gz"
    regs = escrever_bcf(falso, ["14"], [(0, 100), (0, 200)])
    escrever_csi(Path(f"{falso}.csi"), regs, n_ref=1, mtime_de=falso)
    rid = _run(api)
    _sobe_bed(api, rid)
    r = _por_caminho(api, rid, "vcf", falso)
    v = r.json()["entradas"]["vcf"]["veredito"]
    assert r.status_code == 200 and not v["ok"] and any("conteúdo BCF com extensão de VCF" in f for f in v["falhas"])
    r = api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {}})
    assert r.status_code == 409 and _erro(r) == "entradas_reprovadas"


def test_r13_bcf_com_csi_por_referencia_aprova(api, raiz):
    """Controle do R13 e do I2: o mesmo BCF com o sufixo certo."""
    bcf = raiz / "y.bcf"
    regs = escrever_bcf(bcf, ["14"], [(0, 100), (0, 200)])
    escrever_csi(Path(f"{bcf}.csi"), regs, n_ref=1, mtime_de=bcf)
    rid = _run(api)
    _sobe_bed(api, rid, BED_BCF)
    reg = _por_caminho(api, rid, "vcf", bcf).json()["entradas"]["vcf"]
    assert reg["veredito"]["ok"], reg["veredito"]
    assert set(reg["irmaos"]) == {".csi"} and reg["candidatos_irmaos"] == [".csi"]
    assert _aprovar(api, rid, _plano(api, rid)).status_code == 200


def test_r13_bed_nao_entra_por_referencia(api, raiz):
    """bed_regioes não é referenciável: a entrada só aceita cópia, e o caminho da raiz de
    referência é o 422 de sempre."""
    bed = raiz / "regioes.bed"
    bed.write_bytes(BED_DEMO.read_bytes())
    rid = _run(api)
    r = _por_caminho(api, rid, "regioes", bed)
    assert r.status_code == 422 and _erro(r) == "caminho_recusado"


# ============================================================ R14 L3
def test_r14_run_json_mexido_nao_engana_o_executar(api, lancados, raiz):
    """L3: o executar confere as entradas pelo PLANO. Trocar o arquivo E o registro do
    run.json juntos (o run.json não é protegido por sha) não faz o passo rodar."""
    rid, _, vcf = _aprovado_ref(api, raiz)
    with vcf.open("ab") as f:
        f.write(b"\0" * 7)
    imp = script_job.impressao_arquivo(vcf)
    _regravar_run(rid, lambda run: run["entradas"]["vcf"].update(
        impressao=imp, bytes=imp["bytes"], sha256=_sha(vcf)))
    r = _executar(api, rid)
    assert r.status_code == 409 and _erro(r) == "entrada_alterada", r.text
    assert lancados == []


def test_r14_aprovar_confere_pelo_plano_proposto(api, raiz):
    """L3 no aprovar: arquivo e run.json mexidos juntos depois do plano proposto; o aprovar
    confere pelo plano PROPOSTO (é ele que o autor aprova pelo sha)."""
    rid, vcf = _pronto(api, raiz)
    plano = _plano(api, rid)
    with vcf.open("ab") as f:
        f.write(b"\0" * 7)
    imp = script_job.impressao_arquivo(vcf)
    _regravar_run(rid, lambda run: run["entradas"]["vcf"].update(
        impressao=imp, bytes=imp["bytes"], sha256=_sha(vcf)))
    r = _aprovar(api, rid, plano)
    assert r.status_code == 409 and _erro(r) == "entrada_alterada", r.text


def test_r14_controle_sem_mexida_executa(api, lancados, raiz):
    rid, _, _ = _aprovado_ref(api, raiz)
    assert _executar(api, rid).status_code == 202


# ===================================================== crítica #13 adaptar
def test_adaptar_referencia_e_recusado_em_tempo_de_execucao(api, raiz, monkeypatch):
    """O contrato recusa adaptavel + referencia; esta é a guarda em runtime (um pipeline
    que escapasse do contrato gravaria '.adaptando.*' dentro da raiz de referência)."""
    rid, _ = _pronto(api, raiz)
    antes = sorted(p.name for p in raiz.iterdir())
    _mexer_entrada(monkeypatch, "vcf", adaptavel=True)
    r = api.post(f"/api/v1/runs/{rid}/entradas/vcf/adaptar")
    assert r.status_code == 409 and _erro(r) == "entrada_nao_adaptavel", r.text
    assert "referência" in r.json()["detail"]["detalhe"]
    assert sorted(p.name for p in raiz.iterdir()) == antes


# ============================================================ receitas do worker
def test_impressao_e_divergencias():
    """A impressão muda com tamanho e com bytes dos trechos; None nunca casa com None (L1)."""
    esperado = {"caminho": "x", "modo": "copia", "sha256": None, "irmaos": {}, "candidatos_irmaos": []}
    assert "conteudo" in script_job.divergencias(esperado, {**esperado}, completo=False)
    esperado = {**esperado, "sha256": "a" * 64, "irmaos": {".tbi": None}}
    lido = {**esperado, "irmaos": {".tbi": None}}
    assert script_job.divergencias(esperado, lido, completo=False) == {"indice"}
    assert script_job.divergencias({**esperado, "irmaos": {}}, {**lido, "irmaos": {}}, completo=True) == set()
    assert script_job.divergencias(esperado, None, completo=True) == {"caminho", "conteudo", "indice"}


def test_impressao_muda_com_tamanho_e_cabecalho(tmp_path):
    a = tmp_path / "a"
    a.write_bytes(b"x" * 1000)
    i1 = script_job.impressao_arquivo(a)
    a.write_bytes(b"y" + b"x" * 999)
    i2 = script_job.impressao_arquivo(a)
    a.write_bytes(b"x" * 1001)
    i3 = script_job.impressao_arquivo(a)
    assert len({i1["amostra_sha256"], i2["amostra_sha256"], i3["amostra_sha256"]}) == 3
    assert i1["esquema"] == "omica.impressao/1" and i3["bytes"] == 1001


def test_raiz_de_referencia_montada_so_leitura_em_todo_container():
    """Crítica #3: a raiz do Run 8 tem um escritor ativo (o gtonly troca o .bcf e apaga o
    .vcf.gz). Nenhum container da omica escreve nela: todo serviço que monta /data monta a
    raiz por cima, `:ro`. (Que o default da raiz seja 1000bg_vcf é o que liga os dois.)"""
    import yaml

    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    com_data = {nome: s.get("volumes", []) for nome, s in compose["services"].items()
                if any(str(v).endswith(":/data") for v in s.get("volumes", []))}
    assert len(com_data) == 6, sorted(com_data)
    for nome, volumes in com_data.items():
        assert any(str(v).endswith(":/data/1000bg_vcf:ro") for v in volumes), nome
    from omica.config import _DEFAULTS, Config

    assert _DEFAULTS["OMICA_RAIZES_REFERENCIA"] == "1000bg_vcf"
    assert Config.__dataclass_fields__["raizes_referencia"].default == ("1000bg_vcf",)


def test_raizes_de_referencia_vem_da_config(cfg, monkeypatch):
    """Canário da regra 'UMA config': a raiz é lida da variável (antes, um literal em
    dispatch.py ignorava OMICA_RAIZES_REFERENCIA); '-' desliga; ausente = 1000bg_vcf."""
    from omica.config import get_config

    monkeypatch.delenv("OMICA_RAIZES_REFERENCIA", raising=False)
    reset_config_cache()
    assert dispatch._nomes_raizes_referencia() == ("1000bg_vcf",)
    monkeypatch.setenv("OMICA_RAIZES_REFERENCIA", "outra, mais/uma")
    reset_config_cache()
    assert get_config().raizes_referencia == ("outra", "mais/uma")
    assert dispatch._nomes_raizes_referencia() == ("outra", "mais/uma")
    monkeypatch.setenv("OMICA_RAIZES_REFERENCIA", "-")
    reset_config_cache()
    assert dispatch._nomes_raizes_referencia() == () and dispatch.raizes_referencia() == []


def test_referencia_desligada_pela_config_recusa_o_caminho(api, cfg, raiz, monkeypatch):
    """Com '-', um VCF válido na antiga raiz não entra mais por referência (mesmo 422)."""
    vcf = sintetico(raiz, "x.vcf.gz")
    monkeypatch.setenv("OMICA_RAIZES_REFERENCIA", "-")
    reset_config_cache()
    rid = _run(api)
    r = _por_caminho(api, rid, "vcf", vcf)
    assert r.status_code == 422 and _erro(r) == "caminho_recusado", r.text


def test_script_job_continua_python_310():
    """O omica-vep (jammy, Python 3.10) importa script_job: nada de sintaxe ou API 3.11+."""
    fonte = (REPO_ROOT / "omica" / "jobs" / "script_job.py").read_text(encoding="utf-8")
    ast.parse(fonte, feature_version=(3, 10))
    assert "file_digest" not in fonte and "tomllib" not in fonte and "ExceptionGroup" not in fonte
