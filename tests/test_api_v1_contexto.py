"""API v1 com contexto (v2 multiespécie, M4; especificação §6, frente P).

O que esta API promete, cada promessa com canário (o que ela PRECISA recusar) e controle (o
que ela precisa deixar passar):
  - `/catalogo` e `/pipelines` SEM query respondem como na v1, chave por chave (crítica v2,
    médio 4: o `escopo` novo do ParamSpec vazava pelo model_dump);
  - com `?especie=` (e `&subprojeto=`) filtram pelo contexto, com as recusas do resolver;
  - `/especies` e `/subprojetos` mostram estado e presença por stat, sem virar oráculo de
    existência de arquivo fora dos caminhos declarados;
  - `POST /runs` aceita o contexto; sem ele, o demo com aviso (o formulário do n8n).

Os dados reais do repositório (especies/, projetos/) são só LIDOS; o que precisa de um
subprojeto diferente grava um rascunho em <OMICA_DATA>/projetos (tmp pelo conftest).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from fixtures_v2 import apontar_catalogo, escrever_especie, escrever_subprojeto
from omica import contexto, runs
from omica.api import v1
from omica.chaves_contexto import ID_ESPECIE, SLUG
from omica.config import get_config, reset_config_cache
from omica.pipelines import carregar_pipelines

TOKEN = "token-de-teste-nao-e-segredo"
AULA = ("bos_taurus", "aula_granulosa_calor")

# As chaves da v1 (c483d9e), escritas à mão de propósito: se o código ganhar uma chave, é
# aqui que o teste reprova (a lista não pode sair do próprio código que ela confere).
CHAVES_CATALOGO_V1 = {"versao", "modulos"}
CHAVES_MODULO_V1 = {"id", "nome", "scripts", "workflows"}
CHAVES_SCRIPT_V1 = {"key", "titulo", "descricao", "runtime", "fila", "mem_gb", "cpus", "tempo_estimado",
                    "params", "inputs", "outputs"}
CHAVES_PARAM_V1 = {"name", "rotulo", "default", "tipo"}
CHAVES_WORKFLOW_V1 = {"id", "titulo", "passos", "testado_em", "selo_vigente"}
CHAVES_PIPELINES_V1 = {"pipelines", "erros"}
CHAVES_PIPELINE_V1 = {"id", "modulo", "titulo", "descricao", "entradas", "escolhas", "passos", "selo"}
CHAVES_ESCOLHA_V1 = {"rotulo", "valores_de"}
CHAVES_ESPECIE = {"id", "nome_cientifico", "nome_exibido", "nome_exibido_en", "taxon_ids", "ordem",
                  "assembly_padrao", "assemblies", "modulos", "estado", "refs_presentes", "refs_total",
                  "n_subprojetos", "n_executaveis"}
CHAVES_SUBPROJETO = {"id", "especie", "slug", "titulo", "tipo", "status", "executavel", "assembly",
                     "modulos", "pipelines", "origem", "privado"}


@pytest.fixture()
def api(monkeypatch, cfg):
    monkeypatch.setenv("OMICA_API_TOKEN", TOKEN)
    reset_config_cache()
    from omica.web.app import app

    c = TestClient(app)
    c.headers.update({"Authorization": f"Bearer {TOKEN}"})
    return c


def _detalhe(r) -> dict:
    return r.json()["detail"]


def _rascunho(especie: str = "homo_sapiens", slug: str = "teste_rna", **kw) -> Path:
    kw.setdefault("assembly", "GRCh38" if especie == "homo_sapiens" else "ARS-UCD1.3")
    return escrever_subprojeto(get_config().projetos_dir, especie, slug, **kw)


def _pipelines_do_yaml_do_demo() -> list[str]:
    import yaml

    from omica.config import REPO_ROOT

    dados = yaml.safe_load((REPO_ROOT / "projetos" / "bos_taurus" / "demo" / "subprojeto.yaml").read_text(encoding="utf-8"))
    return list(dados["pipelines"])


def _refs_bovinas_no_disco(quais=None) -> None:
    """Cria (vazios) os arquivos/pastas das refs bovinas sob o GENOME_DATA do teste (tmp)."""
    esp = contexto.catalogo().especies["bos_taurus"]
    for chave, valor in esp.assemblies["ARS-UCD1.3"].refs.items():
        if not valor.startswith("/data/") or (quais is not None and chave not in quais):
            continue
        alvo = get_config().genome_data / valor[len("/data/"):]
        if chave in ("salmon_indice", "homer_preparsed", "snpeff_datadir", "vep_cache"):
            alvo.mkdir(parents=True, exist_ok=True)
        else:
            alvo.parent.mkdir(parents=True, exist_ok=True)
            alvo.write_bytes(b"")


# ====================================================== sem query = a v1 (médio 4)
def test_catalogo_sem_query_tem_as_chaves_da_v1(api):
    """Canário do médio 4: com p.model_dump(), cada param saía com `escopo`."""
    corpo = api.get("/api/v1/catalogo").json()
    assert set(corpo) == CHAVES_CATALOGO_V1 and "contexto" not in corpo
    assert len(corpo["modulos"]) == 4
    n_params = 0
    for m in corpo["modulos"]:
        assert set(m) == CHAVES_MODULO_V1, m["id"]
        for s in m["scripts"]:
            assert set(s) == CHAVES_SCRIPT_V1, s["key"]
            for p in s["params"]:
                assert set(p) == CHAVES_PARAM_V1, (s["key"], p)
                n_params += 1
        for w in m["workflows"]:
            assert set(w) == CHAVES_WORKFLOW_V1, w["id"]
    assert n_params > 20  # o teste não passa no vazio


def test_pipelines_sem_query_tem_as_chaves_da_v1(api):
    corpo = api.get("/api/v1/pipelines").json()
    assert set(corpo) == CHAVES_PIPELINES_V1 and corpo["erros"] == {}
    # B1 (F6): a lista é a do catálogo, não fixa — os pipelines novos entram sem editar o teste
    assert {p["id"] for p in corpo["pipelines"]} == set(carregar_pipelines()[0])
    assert {"transcriptomica_de", "genomica_painel", "genomica_regioes"} <= {p["id"] for p in corpo["pipelines"]}
    for p in corpo["pipelines"]:
        assert set(p) == CHAVES_PIPELINE_V1, p["id"]
        # X1′ (F6): as escolhas sem query saem com as chaves da v1 (sem `diferente_de`)
        for n, e in p["escolhas"].items():
            assert set(e) == CHAVES_ESCOLHA_V1, (p["id"], n, e)
    assert any(p["escolhas"] for p in corpo["pipelines"])  # o teste não passa no vazio


def test_x1_escolhas_com_query_trazem_diferente_de(api):
    corpo = api.get("/api/v1/pipelines", params={"especie": "bos_taurus", "subprojeto": "demo"}).json()
    escolhas = [e for p in corpo["pipelines"] for e in p["escolhas"].values()]
    assert escolhas and all(set(e) == CHAVES_ESCOLHA_V1 | {"diferente_de"} for e in escolhas)


def test_sem_query_nem_carrega_o_catalogo_de_especies(api, monkeypatch):
    """Sem query nada da v2 entra: um especies/ quebrado não muda a resposta da v1. Controle:
    com query, o catálogo é carregado (a troca abaixo é exercida)."""
    chamadas = []

    def explode():
        chamadas.append(1)
        raise RuntimeError("o catálogo de espécies não devia ser lido sem query")

    monkeypatch.setattr(contexto, "catalogo", explode)
    assert api.get("/api/v1/catalogo").status_code == 200
    assert api.get("/api/v1/pipelines").status_code == 200
    assert chamadas == []
    c = TestClient(api.app, raise_server_exceptions=False)
    c.headers.update(api.headers)
    assert c.get("/api/v1/pipelines", params={"especie": "bos_taurus"}).status_code == 500
    assert chamadas == [1]


# ================================================================= /catalogo com contexto
def test_catalogo_aula_so_pipelines(api):
    corpo = api.get("/api/v1/catalogo", params={"especie": AULA[0], "subprojeto": AULA[1]}).json()
    assert corpo["contexto"] == {"especie": "bos_taurus", "subprojeto": "aula_granulosa_calor",
                                 "execucao": "so_pipelines"}
    assert [m["id"] for m in corpo["modulos"]] == ["transcriptomica"]
    m = corpo["modulos"][0]
    assert m["scripts"] == []
    assert m["workflows"] and all(w["executavel"] is False for w in m["workflows"])
    assert all(w["validado_em"] == ["bos_taurus/demo"] for w in m["workflows"])


def test_catalogo_demo_executa_e_params_trazem_escopo(api):
    corpo = api.get("/api/v1/catalogo", params={"especie": "bos_taurus", "subprojeto": "demo"}).json()
    assert corpo["contexto"]["execucao"] == "demo"
    assert [m["id"] for m in corpo["modulos"]] == ["genomica", "epigenomica", "transcriptomica", "ml"]
    scripts = [s for m in corpo["modulos"] for s in m["scripts"]]
    workflows = [w for m in corpo["modulos"] for w in m["workflows"]]
    assert len(scripts) == 21 and all(s["executavel"] is True for s in scripts)
    assert len(workflows) == 5 and all(w["executavel"] is True for w in workflows)
    params = [p for s in scripts for p in s["params"]]
    assert params and all(set(p) == CHAVES_PARAM_V1 | {"escopo"} for p in params)


@pytest.mark.parametrize("especie,modulos,no_demo", [
    ("bos_taurus", ["genomica", "epigenomica", "transcriptomica", "ml"], True),
    ("homo_sapiens", ["transcriptomica"], False),
])
def test_catalogo_so_especie(api, especie, modulos, no_demo):
    """Sem subprojeto, `executavel_no_demo` (crítica v2: `executavel` seria ambíguo)."""
    corpo = api.get("/api/v1/catalogo", params={"especie": especie}).json()
    assert corpo["contexto"] == {"especie": especie, "subprojeto": None, "execucao": None}
    assert [m["id"] for m in corpo["modulos"]] == modulos
    itens = [x for m in corpo["modulos"] for x in [*m["scripts"], *m["workflows"]]]
    assert itens and all(x["executavel_no_demo"] is no_demo for x in itens)
    assert all("executavel" not in x for x in itens)


def _registro_trocado(validado_em: Optional[dict[str, list[str]]] = None,
                      especies: Optional[dict[str, list[str]]] = None):
    """Uma cópia do registry real com o `validado_em` de alguns workflows e as `especies` de
    alguns scripts trocados. No real, tudo é bos_taurus/demo: sem a troca, as regras não se
    distinguem."""
    import copy

    from omica.registry import load_registry

    reg = copy.deepcopy(load_registry())
    for m in reg.modules:
        for w in m.workflows:
            if w.id in (validado_em or {}):
                w.validado_em = list(validado_em[w.id])
        for s in m.scripts:
            if s.key in (especies or {}):
                s.especies = list(especies[s.key])
    return reg


def test_catalogo_executavel_e_o_que_o_post_aceita(api, monkeypatch):
    """Canário: um workflow que declare a aula em `validado_em` continua NÃO executável lá —
    fora do demo só pipelines, e o POST da web recusa com so_pipelines_fora_do_demo. Com
    `ctx.id in validado_em`, o catálogo prometia o que o POST recusa. Controle: no demo, o
    workflow fora de `validado_em` é False e os outros são True. A paridade com
    contexto.execucao_permitida (a regra da UI e do dispatch) é conferida item a item."""
    reg = _registro_trocado(validado_em={"wf_de_granulosa": ["bos_taurus/demo", "bos_taurus/aula_granulosa_calor"],
                                         "wf_ml_aims": ["bos_taurus/aula_granulosa_calor"]})
    monkeypatch.setattr(v1, "load_registry", lambda: reg)
    vistos = set()
    for esp, slug in [("bos_taurus", "demo"), AULA]:
        corpo = api.get("/api/v1/catalogo", params={"especie": esp, "subprojeto": slug}).json()
        for m in corpo["modulos"]:
            por_id = {w.id: w for w in reg.por_id(m["id"]).workflows}
            for w in m["workflows"]:
                esperado = contexto.execucao_permitida(esp, slug, "workflow", workflow=por_id[w["id"]])[0]
                assert w["executavel"] is esperado, (esp, slug, w["id"])
                vistos.add((slug, w["id"], esperado))
    assert ("aula_granulosa_calor", "wf_de_granulosa", False) in vistos            # o canário
    assert {("demo", "wf_ml_aims", False), ("demo", "wf_painel_demo", True),
            ("demo", "wf_de_granulosa", True)} <= vistos                           # os controles


def test_catalogo_script_sem_a_especie_nao_executa(api, monkeypatch):
    """Canário: um script que não declara bos_taurus em `especies` não roda nem no demo,
    porque o dispatch.montar_cmd recusa a espécie não declarada (conferido aqui, na fonte).
    Controle: os outros 20 scripts do demo, que o dispatch monta."""
    from omica.jobs import dispatch

    reg = _registro_trocado(especies={"deseq2_demo": ["homo_sapiens"]})
    monkeypatch.setattr(v1, "load_registry", lambda: reg)
    trn = reg.por_id("transcriptomica")
    demo = contexto.demo()
    with pytest.raises(ValueError):
        dispatch.montar_cmd(next(s for s in trn.scripts if s.key == "deseq2_demo"), ctx=demo, mod_id=trn.id)
    dispatch.montar_cmd(next(s for s in trn.scripts if s.key == "volcano_demo"), ctx=demo, mod_id=trn.id)
    for params, chave in [({"especie": "bos_taurus", "subprojeto": "demo"}, "executavel"),
                          ({"especie": "bos_taurus"}, "executavel_no_demo")]:
        corpo = api.get("/api/v1/catalogo", params=params).json()
        veredito = {s["key"]: s[chave] for m in corpo["modulos"] for s in m["scripts"]}
        assert veredito.pop("deseq2_demo") is False, params
        assert len(veredito) == 20 and all(veredito.values()), params


@pytest.mark.parametrize("status,esperado", [("invalido", False), ("valido", True)])
def test_catalogo_executavel_no_demo_segue_o_demo(api, monkeypatch, tmp_path, status, esperado):
    """Canário: `executavel_no_demo` responde "isto roda no demo?". Com o demo invalidado,
    nada roda lá, mesmo na espécie bovina (o POST recusa com subprojeto_nao_executavel).
    Controle: o mesmo demo, válido."""
    pasta, repo = tmp_path / "especies_teste", tmp_path / "projetos_teste"
    escrever_especie(pasta, "bos_taurus", assembly="ARS-UCD1.3", prefixo="ENSBTAG")
    extra = {"validade": "rótulos errados"} if status == "invalido" else {}
    escrever_subprojeto(repo, "bos_taurus", "demo", status=status, assembly="ARS-UCD1.3", tipo="demo", **extra)
    apontar_catalogo(monkeypatch, pasta, repo)
    assert contexto.execucao_permitida("bos_taurus", "demo", "script")[0] is esperado
    corpo = api.get("/api/v1/catalogo", params={"especie": "bos_taurus"}).json()
    assert [m["id"] for m in corpo["modulos"]] == ["transcriptomica"]
    itens = [x for m in corpo["modulos"] for x in [*m["scripts"], *m["workflows"]]]
    assert len(itens) > 3 and all(x["executavel_no_demo"] is esperado for x in itens)


# ================================================================= /pipelines com contexto
def test_pipelines_humano_so_transcriptomica(api):
    corpo = api.get("/api/v1/pipelines", params={"especie": "homo_sapiens"}).json()
    assert [p["id"] for p in corpo["pipelines"]] == ["transcriptomica_de"]
    assert corpo["pipelines"][0]["especies"] == ["bos_taurus", "homo_sapiens", "mus_musculus"]
    assert corpo["contexto"] == {"especie": "homo_sapiens", "subprojeto": None, "execucao": None}
    assert set(corpo["pipelines"][0]) == CHAVES_PIPELINE_V1 | {"especies"}


def test_pipelines_so_especie_exige_modulo_da_especie(api, monkeypatch, tmp_path):
    """Canário: o transcriptomica_de declara homo_sapiens, mas numa espécie sem o módulo de
    transcriptômica ele não rodaria em subprojeto nenhum — não é listado. Controle: com o
    módulo, é."""
    pasta = tmp_path / "especies_teste"
    escrever_especie(pasta, "homo_sapiens", modulos=["genomica"])
    apontar_catalogo(monkeypatch, pasta, tmp_path / "projetos_vazio")
    corpo = api.get("/api/v1/pipelines", params={"especie": "homo_sapiens"}).json()
    assert corpo["pipelines"] == []
    escrever_especie(pasta, "homo_sapiens", modulos=["genomica", "transcriptomica"])
    corpo = api.get("/api/v1/pipelines", params={"especie": "homo_sapiens"}).json()
    assert [p["id"] for p in corpo["pipelines"]] == ["transcriptomica_de"]


def test_pipelines_aula_e_demo(api):
    aula = api.get("/api/v1/pipelines", params={"especie": AULA[0], "subprojeto": AULA[1]}).json()
    assert [p["id"] for p in aula["pipelines"]] == ["transcriptomica_de"]
    assert aula["contexto"]["execucao"] == "so_pipelines"
    demo = api.get("/api/v1/pipelines", params={"especie": "bos_taurus", "subprojeto": "demo"}).json()
    # B1 (F6): a lista do demo é o `pipelines:` do próprio subprojeto.yaml (a F5 o edita uma vez)
    assert [p["id"] for p in demo["pipelines"]] == _pipelines_do_yaml_do_demo()


def test_pipelines_listados_sao_os_que_o_run_aceita(api):
    """Paridade com o POST /runs: em cada contexto, listado ⇔ execucao_permitida aceita."""
    _rascunho()                                                          # humano, sem `pipelines`
    _rascunho("bos_taurus", "so_rna", modulos=["transcriptomica"])        # bovino sem genômica
    validos, _ = carregar_pipelines()
    for esp, slug in [("bos_taurus", "demo"), AULA, ("homo_sapiens", "teste_rna"), ("bos_taurus", "so_rna")]:
        listados = {p["id"] for p in api.get("/api/v1/pipelines",
                                             params={"especie": esp, "subprojeto": slug}).json()["pipelines"]}
        aceitos = {pid for pid, p in validos.items()
                   if contexto.execucao_permitida(esp, slug, "pipeline", pipeline=p)[0]}
        assert listados == aceitos, (esp, slug)
        assert listados  # cada contexto aceita ao menos o transcriptomica_de


# ======================================================= recusas de contexto na query
@pytest.mark.parametrize("rota", ["/api/v1/catalogo", "/api/v1/pipelines"])
@pytest.mark.parametrize("params,status,erro", [
    ({"subprojeto": "aula_granulosa_calor"}, 422, "contexto_incompleto"),
    ({"subprojeto": "x"}, 422, "parametro_invalido"),               # fora do padrão de slug
    ({"especie": "zz_nada"}, 404, "especie_inexistente"),
    ({"especie": "zz_nada", "subprojeto": "demo"}, 404, "especie_inexistente"),
    ({"especie": "bos_taurus", "subprojeto": "nao_existe"}, 404, "subprojeto_inexistente"),
    ({"especie": "bos_taurus", "subprojeto": "sub03"}, 404, "subprojeto_inexistente"),
    ({"especie": "bos_taurus", "subprojeto": "velho"}, 409, "subprojeto_nao_executavel"),
    ({"especie": "Bos_taurus"}, 422, "parametro_invalido"),
    ({"especie": "bos_taurus\n"}, 422, "parametro_invalido"),
    ({"especie": ""}, 422, "parametro_invalido"),
    ({"especie": "bos_taurus", "subprojeto": "../demo"}, 422, "parametro_invalido"),
])
def test_query_de_contexto_recusada(api, rota, params, status, erro):
    _rascunho("bos_taurus", "velho", status="invalido", validade="rótulos errados")
    r = api.get(rota, params=params)
    assert r.status_code == status, r.text
    assert _detalhe(r)["erro"] == erro


def test_recusa_traz_o_detalhe_do_resolver(api):
    _rascunho("bos_taurus", "velho", status="invalido", validade="rótulos errados")
    det = _detalhe(api.get("/api/v1/catalogo", params={"especie": "bos_taurus", "subprojeto": "velho"}))
    assert det == {"erro": "subprojeto_nao_executavel", "subprojeto": "bos_taurus/velho",
                   "status_subprojeto": "invalido", "validade": "rótulos errados"}
    det = _detalhe(api.get("/api/v1/pipelines", params={"subprojeto": "demo"}))
    assert det == {"erro": "contexto_incompleto", "especie": None, "subprojeto": "demo"}


# ======================================================================== /especies
def test_especies_as_tres_na_ordem_com_estado(api):
    corpo = api.get("/api/v1/especies").json()
    assert corpo["erros"] == {}
    assert [e["id"] for e in corpo["especies"]] == ["homo_sapiens", "mus_musculus", "bos_taurus"]
    por_id = {e["id"]: e for e in corpo["especies"]}
    for e in corpo["especies"]:
        assert set(e) == CHAVES_ESPECIE, e["id"]
    for sem_ref in ("homo_sapiens", "mus_musculus"):
        e = por_id[sem_ref]
        assert (e["estado"], e["refs_presentes"], e["refs_total"]) == ("sem_referencia", 0, 0)
        assert (e["n_subprojetos"], e["n_executaveis"]) == (0, 0)
    bos = por_id["bos_taurus"]
    # GENOME_DATA é tmp: nenhuma das 7 refs de arquivo existe
    assert (bos["estado"], bos["refs_presentes"], bos["refs_total"]) == ("sem_referencia", 0, 7)
    assert (bos["n_subprojetos"], bos["n_executaveis"]) == (2, 2)
    assert bos["assemblies"] == ["ARS-UCD1.3"] and bos["taxon_ids"] == [9913, 9915]


def test_especies_conta_subprojetos_e_executaveis(api):
    """Arquivado fora da contagem (como na 1ª página); não executável conta só em n_subprojetos."""
    _rascunho("bos_taurus", "velho", status="invalido", validade="rótulos errados")
    _rascunho("bos_taurus", "antigo", status="arquivado", validade="substituído pela aula")
    _rascunho()
    por_id = {e["id"]: e for e in api.get("/api/v1/especies").json()["especies"]}
    assert (por_id["bos_taurus"]["n_subprojetos"], por_id["bos_taurus"]["n_executaveis"]) == (3, 2)
    assert (por_id["homo_sapiens"]["n_subprojetos"], por_id["homo_sapiens"]["n_executaveis"]) == (1, 1)


def test_especies_estado_sai_do_disco(api):
    """O estado é calculado por stat: as mesmas refs viram parcial e pronta no disco."""
    _refs_bovinas_no_disco(["cdna", "vep_cache"])  # nenhuma é pasta-mãe de outra ref
    bos = api.get("/api/v1/especies/bos_taurus").json()
    assert (bos["estado"], bos["refs_presentes"]) == ("parcial", 2)
    assert set(bos["faltando"]) == {"fasta", "gtf", "salmon_indice", "homer_preparsed", "snpeff_datadir"}
    _refs_bovinas_no_disco()
    bos = api.get("/api/v1/especies/bos_taurus").json()
    assert (bos["estado"], bos["refs_presentes"], bos["faltando"]) == ("pronta", 7, [])


def test_ver_especie_refs_recursos_e_erros(api):
    _refs_bovinas_no_disco(["gtf"])
    _rascunho("bos_taurus", "quebrado", assembly="nao_existe")   # erro de carga desta espécie
    _rascunho("homo_sapiens", "quebrado_h", assembly="nao_existe")  # de outra espécie
    corpo = api.get("/api/v1/especies/bos_taurus").json()
    assert set(corpo) == CHAVES_ESPECIE | {"refs", "recursos", "faltando", "erros_subprojetos"}
    refs = corpo["refs"]["ARS-UCD1.3"]
    assert refs["gtf"] == {"valor": "/data/snpeff_data/ARS-UCD1.3.113/genes.gtf.gz", "presente": True}
    assert refs["fasta"]["presente"] is False
    assert refs["snpeff_db"] == {"valor": "ARS-UCD1.3.113", "presente": None}   # texto não é arquivo
    assert refs["plink_chr_set"]["presente"] is None                             # inteiro também não
    assert corpo["recursos"]["vcf_populacional"] == "/data/1000bg_vcf"
    assert list(corpo["erros_subprojetos"]) == ["dados:bos_taurus/quebrado"]
    humano = api.get("/api/v1/especies/homo_sapiens").json()
    assert humano["refs"] == {"GRCh38": {}} and humano["faltando"] == []
    assert list(humano["erros_subprojetos"]) == ["dados:homo_sapiens/quebrado_h"]


def test_presente_so_para_ref_de_caminho_ou_dir(api, monkeypatch, tmp_path):
    """Canário: uma ref de TEXTO cujo valor tem forma de caminho existente (aceita pelo
    sanitizador) não é arquivo — `presente` é None. Controle: a ref de caminho ao lado é
    conferida."""
    pasta = tmp_path / "especies_teste"
    escrever_especie(pasta, "homo_sapiens", refs={"snpeff_db": "modules/transcriptomica/demo_data",
                                                  "gtf": "modules/transcriptomica/demo_data/counts_granulosa.tsv"})
    apontar_catalogo(monkeypatch, pasta, tmp_path / "projetos_vazio")
    corpo = api.get("/api/v1/especies/homo_sapiens").json()
    assert corpo["refs"]["GRCh38"]["snpeff_db"]["presente"] is None
    assert corpo["refs"]["GRCh38"]["gtf"]["presente"] is True


def test_especie_inexistente_e_404_do_contrato(api):
    r = api.get("/api/v1/especies/xx_inexistente")
    assert r.status_code == 404
    assert r.json() == {"detail": {"erro": "especie_inexistente", "especie": "xx_inexistente"}}


def test_especie_que_nao_carregou_diz_por_que(api, monkeypatch, tmp_path):
    pasta = tmp_path / "especies_teste"
    escrever_especie(pasta, "homo_sapiens", modulos=["transcriptomica"], chave_desconhecida=1)
    apontar_catalogo(monkeypatch, pasta, tmp_path / "projetos_vazio")
    r = api.get("/api/v1/especies/homo_sapiens")
    assert r.status_code == 404 and "chave_desconhecida" in _detalhe(r)["erro_de_carga"]
    assert "homo_sapiens.yaml" in api.get("/api/v1/especies").json()["erros"]


@pytest.mark.parametrize("url", ["/api/v1/especies/X", "/api/v1/especies/ab", "/api/v1/especies/bos_taurus%0A",
                                 "/api/v1/especies/Bos_taurus", "/api/v1/especies/" + "a" * 33])
def test_especie_fora_do_padrao_e_422(api, url):
    r = api.get(url)
    assert r.status_code == 422 and _detalhe(r)["erro"] == "parametro_invalido", r.text


# ===================================================================== /subprojetos
def test_subprojetos_bovinos_demo_primeiro(api):
    corpo = api.get("/api/v1/subprojetos", params={"especie": "bos_taurus"}).json()
    assert [s["id"] for s in corpo["subprojetos"]] == ["bos_taurus/demo", "bos_taurus/aula_granulosa_calor"]
    assert corpo["erros"] == {}
    for s in corpo["subprojetos"]:
        assert set(s) == CHAVES_SUBPROJETO
        assert s["origem"] == "repo" and s["executavel"] is True and s["privado"] is False
    assert corpo["subprojetos"][1]["pipelines"] == ["transcriptomica_de"]
    assert api.get("/api/v1/subprojetos", params={"especie": "homo_sapiens"}).json() == {"subprojetos": [], "erros": {}}


def test_subprojetos_todas_as_especies_e_arquivados(api):
    _rascunho()
    _rascunho("bos_taurus", "antigo", status="arquivado", validade="substituído pela aula")
    _rascunho("mus_musculus", "quebrado", assembly="nao_existe")
    corpo = api.get("/api/v1/subprojetos").json()
    # na ordem das espécies (humano, camundongo, bovinos); arquivado fora por padrão
    assert [s["id"] for s in corpo["subprojetos"]] == [
        "homo_sapiens/teste_rna", "bos_taurus/demo", "bos_taurus/aula_granulosa_calor"]
    assert corpo["subprojetos"][0]["origem"] == "dados" and corpo["subprojetos"][0]["status"] == "draft"
    assert list(corpo["erros"]) == ["dados:mus_musculus/quebrado"]
    com = api.get("/api/v1/subprojetos", params={"especie": "bos_taurus", "arquivados": "true"}).json()
    assert "bos_taurus/antigo" in [s["id"] for s in com["subprojetos"]]
    assert com["erros"] == {}  # o erro do camundongo não é desta espécie


def test_subprojetos_especie_inexistente_e_404(api):
    r = api.get("/api/v1/subprojetos", params={"especie": "zz_nada"})
    assert r.status_code == 404 and _detalhe(r)["erro"] == "especie_inexistente"
    r = api.get("/api/v1/subprojetos", params={"especie": "Bos"})
    assert r.status_code == 422 and _detalhe(r)["erro"] == "parametro_invalido"


def test_ver_subprojeto_demo(api):
    legado = get_config().genome_data / "omica" / "results" / "genomica" / "demo"
    legado.mkdir(parents=True)
    corpo = api.get("/api/v1/subprojetos/bos_taurus/demo").json()
    assert set(corpo) >= CHAVES_SUBPROJETO | {"pergunta", "validade", "substituido_por", "avisos", "contrastes",
                                              "n_amostras", "datasets", "dados_legados", "contexto_sha256", "motivo"}
    assert corpo["n_amostras"] == 14 and corpo["tipo"] == "demo"
    assert corpo["pergunta"].startswith("Os workflows da plataforma") and corpo["avisos"]
    assert corpo["contexto_sha256"] == contexto.demo().sha256 and corpo["motivo"] is None
    ds = corpo["datasets"]
    assert ds["GSE235171"] == {"valor": "modules/transcriptomica/demo_data/counts_granulosa.tsv", "presente": True}
    assert ds["run8_1000bg"] == {"valor": "/data/1000bg_vcf", "presente": False}
    presentes = {d["valor"]: d["presente"] for d in corpo["dados_legados"]}
    assert presentes.pop("/data/omica/results/genomica/demo") is True
    assert presentes and set(presentes.values()) == {False}


def test_ver_subprojeto_aula_com_contrastes(api):
    corpo = api.get("/api/v1/subprojetos/bos_taurus/aula_granulosa_calor").json()
    assert corpo["n_amostras"] == 8 and corpo["tipo"] == "aula"
    assert corpo["contrastes"] == [{"id": "verao_vs_inverno", "coluna": "condicao",
                                    "a": "summer_heat", "b": "winter_control"}]
    assert corpo["contexto_sha256"] == contexto.resolver(*AULA).sha256


def test_ver_subprojeto_nao_executavel_tem_motivo(api):
    _rascunho("bos_taurus", "velho", status="invalido", validade="rótulos errados")
    corpo = api.get("/api/v1/subprojetos/bos_taurus/velho").json()
    assert corpo["executavel"] is False and corpo["validade"] == "rótulos errados"
    assert corpo["contexto_sha256"] is None and corpo["motivo"] == "subprojeto_nao_executavel"


@pytest.mark.parametrize("url,status,erro", [
    ("/api/v1/subprojetos/bos_taurus/sub03", 404, "subprojeto_inexistente"),
    ("/api/v1/subprojetos/bos_taurus/nao_existe", 404, "subprojeto_inexistente"),
    ("/api/v1/subprojetos/zz_nada/demo", 404, "especie_inexistente"),
    ("/api/v1/subprojetos/bos_taurus/De", 422, "parametro_invalido"),
    ("/api/v1/subprojetos/Bos/demo", 422, "parametro_invalido"),
])
def test_ver_subprojeto_recusas(api, url, status, erro):
    r = api.get(url)
    assert r.status_code == status and _detalhe(r)["erro"] == erro, r.text


def test_slug_nu_e_404_mesmo_que_alguem_o_resolva(api, monkeypatch):
    """Canário da trava própria da rota: se um dia `sub03` passar a resolver (por alias,
    p. ex.), a API continua recusando — as numerações antigas colidem entre plataformas.
    Controle: o mesmo stub com um slug comum devolve o subprojeto."""
    demo = contexto.obter_subprojeto("bos_taurus", "demo")
    monkeypatch.setattr(contexto, "obter_subprojeto", lambda especie, slug, cat=None: demo)
    r = api.get("/api/v1/subprojetos/bos_taurus/sub03")
    assert r.status_code == 404 and _detalhe(r)["erro"] == "subprojeto_inexistente"
    assert api.get("/api/v1/subprojetos/bos_taurus/qualquer_um").status_code == 200


@pytest.mark.parametrize("valor", ["/etc/passwd", "C:/Windows/win.ini", "/data/../segredo", "modules/../.env",
                                   "../x", "~/x", "/data/.oculto", "projetos/a/../../x", ""])
def test_presenca_nunca_vira_oraculo_fora_dos_caminhos_declarados(valor):
    """Canário (médio 10c): fora da forma declarada, `presente` é None — nem stat é feito."""
    assert v1._presente(valor) is None


def test_presenca_controle(cfg):
    assert v1._presente("modules/transcriptomica/demo_data/counts_granulosa.tsv") is True
    assert v1._presente("modules/transcriptomica/nao_existe.tsv") is False
    (cfg.genome_data / "x").mkdir()
    assert v1._presente("/data/x") is True and v1._presente("/data/y") is False


def test_presenca_tem_teto_de_stat(monkeypatch):
    feitos = []
    monkeypatch.setattr(v1, "_presente", lambda c: feitos.append(c) or True)
    r = v1._presencas(f"/data/x{i}" for i in range(v1._MAX_PRESENCA + 3))
    assert r == [True] * v1._MAX_PRESENCA + [None] * 3 and len(feitos) == v1._MAX_PRESENCA


# ============================================================================ POST /runs
def test_novo_run_com_contexto_sem_aviso(api):
    r = api.post("/api/v1/runs", json={"pipeline": "transcriptomica_de", "especie": AULA[0], "subprojeto": AULA[1]})
    assert r.status_code == 201, r.text
    run = r.json()
    assert "avisos" not in run and (run["especie"], run["subprojeto"]) == AULA
    assert run["resultados"] == f"projetos/bos_taurus/aula_granulosa_calor/transcriptomica/runs/{run['run_id']}"
    assert run["contexto_sha256"] == contexto.resolver(*AULA).sha256


def test_novo_run_sem_contexto_e_o_demo_com_aviso(api):
    r = api.post("/api/v1/runs", json={"pipeline": "transcriptomica_de"})
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["avisos"] == ["contexto_padrao_bos_taurus_demo"]
    assert (run["especie"], run["subprojeto"]) == ("bos_taurus", "demo")


def test_novo_run_repassa_o_contexto_ao_nucleo(api, monkeypatch):
    """Sem contexto, a chamada da v1 literal (um só argumento); com, os três."""
    chamadas = []
    monkeypatch.setattr(runs, "criar", lambda *a, **k: chamadas.append((a, k)) or {"run_id": "x"})
    assert api.post("/api/v1/runs", json={"pipeline": "p"}).status_code == 201
    assert api.post("/api/v1/runs", json={"pipeline": "p", "especie": "homo_sapiens",
                                          "subprojeto": "teste_rna"}).status_code == 201
    assert api.post("/api/v1/runs", json={"pipeline": "p", "especie": "homo_sapiens"}).status_code == 201
    assert chamadas == [(("p",), {}), (("p", "homo_sapiens", "teste_rna"), {}),
                        (("p", "homo_sapiens", None), {})]


@pytest.mark.parametrize("corpo,status,erro", [
    ({"pipeline": "transcriptomica_de", "especie": "homo_sapiens"}, 422, "contexto_incompleto"),
    ({"pipeline": "transcriptomica_de", "subprojeto": "teste_rna"}, 422, "contexto_incompleto"),
    ({"pipeline": "transcriptomica_de", "especie": "homo_sapiens", "subprojeto": "nao_existe"},
     404, "subprojeto_inexistente"),
    ({"pipeline": "genomica_painel", "especie": AULA[0], "subprojeto": AULA[1]}, 422, "pipeline_fora_do_subprojeto"),
    ({"pipeline": "transcriptomica_de", "especie": "bos_taurus", "subprojeto": "sub03"}, 404, "subprojeto_inexistente"),
    ({"pipeline": "transcriptomica_de", "especie": "bos_taurus", "subprojeto": "demo", "assembly": "x"},
     422, "corpo_invalido"),                                                           # campo extra
    ({"pipeline": "transcriptomica_de", "especie": "Homo_sapiens", "subprojeto": "x_y_z"}, 422, "corpo_invalido"),
    ({"pipeline": "transcriptomica_de", "especie": "homo_sapiens\n", "subprojeto": "x_y_z"}, 422, "corpo_invalido"),
    ({"pipeline": "transcriptomica_de", "especie": "bos_taurus", "subprojeto": "../demo"}, 422, "corpo_invalido"),
    ({"pipeline": "transcriptomica_de", "especie": 3, "subprojeto": "demo"}, 422, "corpo_invalido"),
])
def test_novo_run_recusas(api, corpo, status, erro):
    antes = list(runs._runs_no_disco())
    r = api.post("/api/v1/runs", json=corpo)
    assert r.status_code == status and _detalhe(r)["erro"] == erro, r.text
    assert list(runs._runs_no_disco()) == antes  # nenhum run.json órfão


@pytest.mark.parametrize("valor", ["abc", "bos_taurus", "a" * 32, "abc_", "Abc", "ab", "a" * 33, "abc\n", "1abc",
                                   "ab-c", "abç", "abc/def", " abc", "abc\u0661"])
def test_padroes_da_api_sao_os_de_chaves_contexto(valor):
    """O `pattern` do pydantic é busca: as âncoras e o motor têm de dar o MESMO veredito que o
    fullmatch de chaves_contexto (inclusive o '\\n' final)."""
    def aceita(**kw) -> bool:
        try:
            v1._NovoRun(pipeline="p", **kw)
            return True
        except ValidationError:
            return False

    assert aceita(especie=valor) is bool(ID_ESPECIE.fullmatch(valor))
    assert aceita(subprojeto=valor) is bool(SLUG.fullmatch(valor))


# ===================================================================== token antes de tudo
@pytest.mark.parametrize("metodo,url,kw", [
    ("get", "/api/v1/especies", {}),
    ("get", "/api/v1/especies/X", {}),
    ("get", "/api/v1/subprojetos/bos_taurus/sub03", {}),
    ("get", "/api/v1/subprojetos?especie=X", {}),
    ("get", "/api/v1/catalogo?subprojeto=demo", {}),
    ("get", "/api/v1/pipelines?especie=Bos", {}),
    ("post", "/api/v1/runs", {"json": {"pipeline": "p", "especie": "X", "extra": 1}}),
])
def test_sem_token_e_401_antes_da_validacao(monkeypatch, cfg, metodo, url, kw):
    monkeypatch.setenv("OMICA_API_TOKEN", TOKEN)
    reset_config_cache()
    from omica.web.app import app

    for cabecalho in ({}, {"Authorization": "Bearer errado"}):
        r = getattr(TestClient(app), metodo)(url, headers=cabecalho, **kw)
        assert r.status_code == 401 and _detalhe(r)["erro"] == "nao_autorizado", (url, r.text)


# ========================================================================= /saude (médio 15)
def test_saude_mostra_erros_de_carga_do_catalogo(api, monkeypatch, tmp_path):
    corpo = api.get("/api/v1/saude").json()
    assert corpo["erros_especies"] == {} and corpo["erros_subprojetos"] == {}
    pasta = tmp_path / "especies_teste"
    escrever_especie(pasta, "homo_sapiens")
    escrever_especie(pasta, "mus_musculus", modulos=["projetos"])            # módulo reservado
    repo = tmp_path / "projetos_teste"
    escrever_subprojeto(repo, "homo_sapiens", "sub07")                        # slug nu
    apontar_catalogo(monkeypatch, pasta, repo)
    corpo = api.get("/api/v1/saude").json()
    assert list(corpo["erros_especies"]) == ["mus_musculus.yaml"]
    assert list(corpo["erros_subprojetos"]) == ["repo:homo_sapiens/sub07"]


def test_saude_nao_cai_se_o_catalogo_falhar(api, monkeypatch):
    """O /saude é o que diz que algo está errado: uma falha no carregamento inteiro vira a
    entrada `_catalogo`, e não um 500. Os erros vêm de saude.dados_saude (o MESMO dado do
    /saude da página). As duas portas de carga quebram, então vale para qualquer uma."""
    from omica import especies

    def explode(*a, **k):
        raise RuntimeError("catálogo ilegível")

    monkeypatch.setattr(contexto, "catalogo", explode)
    monkeypatch.setattr(especies, "carregar_especies", explode)
    r = api.get("/api/v1/saude")
    assert r.status_code == 200
    assert list(r.json()["erros_especies"]) == ["_catalogo"] and r.json()["erros_subprojetos"] == {}


# ====================================================== /subprojetos?pipeline= (F6, X8, D-10)
def _ids(r) -> list[str]:
    assert r.status_code == 200, r.text
    return [s["id"] for s in r.json()["subprojetos"]]


def test_x8_listados_para_o_pipeline_sao_os_que_o_post_aceita(api, monkeypatch):
    """Paridade com o POST /runs, a MESMA função (contexto.execucao_permitida): para todo
    pipeline P e cada subprojeto S de {demo, aula, um rascunho bovino, um humano},
    S ∈ GET /subprojetos?pipeline=P ⇔ POST /runs {P, S} dá 201."""
    monkeypatch.setenv("OMICA_MAX_RUNS_ABERTOS", "1000")  # um run por par (P, S) aceito
    _rascunho()
    _rascunho("bos_taurus", "so_rna", modulos=["transcriptomica"])
    alvos = [("bos_taurus", "demo"), AULA, ("bos_taurus", "so_rna"), ("homo_sapiens", "teste_rna")]
    recusados = 0
    for pid in carregar_pipelines()[0]:
        listados = set(_ids(api.get("/api/v1/subprojetos", params={"pipeline": pid})))
        for esp, slug in alvos:
            r = api.post("/api/v1/runs", json={"pipeline": pid, "especie": esp, "subprojeto": slug})
            assert (f"{esp}/{slug}" in listados) == (r.status_code == 201), (pid, esp, slug, r.text)
            recusados += r.status_code != 201
    assert recusados  # o filtro não passa no vazio: a aula e o humano recusam a genômica
    # a ordem é a de /subprojetos sem filtro (espécies na ordem; em cada uma, o demo primeiro)
    todos = _ids(api.get("/api/v1/subprojetos"))
    do_rna = _ids(api.get("/api/v1/subprojetos", params={"pipeline": "transcriptomica_de"}))
    assert do_rna == [s for s in todos if s in do_rna]
    assert _ids(api.get("/api/v1/subprojetos", params={"pipeline": "transcriptomica_de",
                                                       "especie": "bos_taurus"}))[0] == "bos_taurus/demo"


def test_x8_genomica_so_no_demo(api):
    _rascunho()
    assert _ids(api.get("/api/v1/subprojetos", params={"pipeline": "genomica_regioes"})) == ["bos_taurus/demo"]


@pytest.mark.parametrize("pipeline", ["nao_existe", "../x", "Transcriptomica_de", "transcriptomica_de\n"])
def test_x8_pipeline_inexistente_e_404(api, pipeline):
    r = api.get("/api/v1/subprojetos", params={"pipeline": pipeline})
    assert r.status_code == 404 and _detalhe(r)["erro"] == "pipeline_inexistente", r.text


def test_x8_privados_false_tira_os_privados(api):
    _rascunho("bos_taurus", "segredo", modulos=["transcriptomica"], privado=True)
    com = _ids(api.get("/api/v1/subprojetos", params={"pipeline": "transcriptomica_de"}))
    sem = _ids(api.get("/api/v1/subprojetos", params={"pipeline": "transcriptomica_de", "privados": "false"}))
    assert "bos_taurus/segredo" in com and "bos_taurus/segredo" not in sem
    assert set(com) - set(sem) == {"bos_taurus/segredo"}
    # sem pipeline, o filtro de privados vale igual; o padrão (true) é a resposta de hoje
    assert "bos_taurus/segredo" in _ids(api.get("/api/v1/subprojetos"))
    assert "bos_taurus/segredo" not in _ids(api.get("/api/v1/subprojetos", params={"privados": "false"}))


def test_x8_privados_false_nao_devolve_erro_de_carga_de_dados(api, monkeypatch, tmp_path):
    """Verificação F6: o GET com privados=false é o que o n8n chama e grava no registro de
    execução. O erro de carga de um subprojeto de <dados> traz o slug na chave e, na mensagem do
    pydantic, o input_value (título, campo a mais) — e um subprojeto que não carrega pode ser
    privado. Canário: dois privados quebrados em <dados> (campo extra; título > TITULO_MAX), com
    e sem pipeline e por espécie; nada deles sai em privados=false. Controles: com privados=true
    o detalhe fica, e o erro do repositório (público) sobrevive ao filtro."""
    from omica import especies
    from omica.subprojetos import TITULO_MAX

    repo = tmp_path / "projetos_teste"
    escrever_subprojeto(repo, "homo_sapiens", "sub07")  # erro do repositório (slug nu)
    apontar_catalogo(monkeypatch, especies.ESPECIES_DIR, repo)
    _rascunho("bos_taurus", "segredo_extra", modulos=["transcriptomica"], privado=True,
              campo_a_mais="VALOR SIGILOSO DO CAMPO")
    _rascunho("bos_taurus", "segredo_titulo", modulos=["transcriptomica"], privado=True,
              titulo="TITULO SIGILOSO " + "x" * TITULO_MAX)
    sigilos = ("segredo_extra", "segredo_titulo", "VALOR SIGILOSO", "TITULO SIGILOSO")
    for params in ({"pipeline": "transcriptomica_de"}, {}, {"especie": "bos_taurus"},
                   {"pipeline": "transcriptomica_de", "especie": "bos_taurus"}):
        com = api.get("/api/v1/subprojetos", params=params)
        sem = api.get("/api/v1/subprojetos", params={**params, "privados": "false"})
        assert com.status_code == 200 and sem.status_code == 200, (params, com.text, sem.text)
        for s in sigilos:  # controle: o token é do autor, o detalhe fica
            assert s in com.text, (params, s, com.json()["erros"])
        vazou = [s for s in sigilos if s in sem.text]
        assert not vazou, f"privados=false vazou {vazou} em {params}: {sem.json()['erros']}"
        esperado = set() if "especie" in params else {"repo:homo_sapiens/sub07"}
        assert set(sem.json()["erros"]) == esperado, (params, sem.json()["erros"])


def test_x8_privados_false_falha_fechada_em_origem_desconhecida(api, monkeypatch):
    """O filtro é "só repo:*", não "tudo menos dados:*": um rótulo de origem que o loader venha
    a ganhar não sai em privados=false até alguém decidir que ele é público."""
    import dataclasses

    original = contexto.catalogo

    def com_erro_novo():
        cat = original()
        return dataclasses.replace(cat, erros_subprojetos={**cat.erros_subprojetos,
                                                           "outra:bos_taurus/desconhecido": "ORIGEM NOVA"})

    monkeypatch.setattr(contexto, "catalogo", com_erro_novo)
    com = api.get("/api/v1/subprojetos", params={"pipeline": "transcriptomica_de"})
    sem = api.get("/api/v1/subprojetos", params={"pipeline": "transcriptomica_de", "privados": "false"})
    assert "outra:bos_taurus/desconhecido" in com.json()["erros"], com.text  # controle
    assert "outra:bos_taurus/desconhecido" not in sem.json()["erros"], \
        f"privados=false deixou passar origem desconhecida: {sem.json()['erros']}"
