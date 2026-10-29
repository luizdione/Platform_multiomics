"""M1 da v2 multiespécie — contexto no servidor (especificação §5 B1/B2, frente B).

`omica/contexto.py` resolve (espécie, subprojeto) nos valores que o SERVIDOR põe no cmd, e
`omica/jobs/dispatch.py` os aplica. Cada regra tem canário (o caso que TEM de reprovar) e
controle (o vizinho válido que tem de passar — sem ele, um canário verde poderia ser só um
dispatch que recusa tudo).

Nada aqui grava em especies/ nem em projetos/ do repositório: o que muda o catálogo trabalha
numa cópia em tmp (fixture `copia`), e os rascunhos vão para <OMICA_DATA>/projetos, que o
conftest já põe em tmp.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

import pytest
import yaml

from fixtures_v2 import apontar_catalogo, escrever_especie, escrever_subprojeto
from omica import contexto
from omica.chaves_contexto import CHAVES_CONTEXTO
from omica.config import REPO_ROOT, get_config
from omica.jobs import dispatch
from omica.pipelines import carregar_pipelines
from omica.registry import ScriptSpec, load_registry

RUN = "transcriptomica_de-20261028-120000-abcdef"
GOLDEN = Path(__file__).resolve().parent / "golden" / "cmd_demo_v1.json"
# o literal de dispatch._SAIDA_RUN em c483d9e (antes da v2), copiado à mão
SAIDA_RUN_V1 = r"/results/[a-z][a-z0-9_]*/runs/[A-Za-z0-9_\-]{1,80}/[a-z][a-z0-9_]*"


# ------------------------------------------------------------------------- ajudantes

@pytest.fixture()
def copia(tmp_path, monkeypatch):
    """especies/ e projetos/ do repositório copiados para tmp e apontados pelos loaders."""
    esp, proj = tmp_path / "repo_especies", tmp_path / "repo_projetos"
    shutil.copytree(REPO_ROOT / "especies", esp)
    shutil.copytree(REPO_ROOT / "projetos", proj)
    apontar_catalogo(monkeypatch, esp, proj)
    return esp, proj


def _rascunho(especie: str = "homo_sapiens", slug: str = "teste_rna", **kw) -> Path:
    """Subprojeto em <OMICA_DATA>/projetos (tmp pelo conftest), como o rascunho da UI."""
    kw.setdefault("assembly", "GRCh38" if especie == "homo_sapiens" else "ARS-UCD1.3")
    return escrever_subprojeto(get_config().projetos_dir, especie, slug, **kw)


def _spec(mod: str, key: str, api: bool = True) -> ScriptSpec:
    m = load_registry().por_id(mod)
    return next(s for s in (m.scripts_api if api else m.scripts) if s.key == key)


def _dp() -> str:
    return get_config().data_dir.as_posix()


def _valores(spec: ScriptSpec) -> dict[str, str]:
    """A receita do golden: todo parâmetro SEM default recebe um caminho de raw/runs/<RUN>."""
    return {p.name: f"{_dp()}/raw/runs/{RUN}/arq.tsv" for p in spec.params if not p.default}


def _saida(mod: str, ctx=None) -> str:
    if ctx is None or ctx.eh_demo:
        return f"{_dp()}/results/{mod}/runs/{RUN}/p1"
    return f"{_dp()}/results/projetos/{ctx.especie}/{ctx.slug}/{mod}/runs/{RUN}/p1"


def _golden(chave: str) -> str:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))[chave]


def _spec_ref(**kw) -> ScriptSpec:
    """Spec em memória que pede uma referência (o registry não é consultado)."""
    base = {"key": "salmon_api", "titulo": "t", "runtime": "image:omica-bioinfo",
            "cmd": "python3 modules/transcriptomica/scripts/salmon_quant.py --indice {ref_salmon_indice} "
                   "--saida {saida}",
            "especies": ["bos_taurus", "homo_sapiens"], "requer_refs": ["ref_salmon_indice"]}
    base.update(kw)
    return ScriptSpec.model_validate(base)


# ============================================================ contexto: resolver (B1)

def test_demo_resolve_refs_assembly_e_amostras():
    ctx = contexto.demo()
    assert (ctx.id, ctx.eh_demo, ctx.eh_especie_legada) == ("bos_taurus/demo", True, True)
    assert ctx.assembly == "ARS-UCD1.3" and ctx.valores["esp_assembly"] == "ARS-UCD1.3"
    assert ctx.valores["ref_gtf"] == "/data/snpeff_data/ARS-UCD1.3.113/genes.gtf.gz"
    assert ctx.valores["ref_snpeff_db"] == "ARS-UCD1.3.113"
    # repositório: relativo à raiz (cwd dos workers), montado dos ids
    assert ctx.valores["sub_amostras"] == "projetos/bos_taurus/demo/amostras.tsv"
    assert set(ctx.valores) <= set(CHAVES_CONTEXTO)
    assert re.fullmatch(r"[0-9a-f]{64}", ctx.sha256)
    assert contexto.demo().sha256 == ctx.sha256  # determinístico
    plano = ctx.para_plano()
    assert json.loads(json.dumps(plano)) == plano and plano["sha256"] == ctx.sha256
    assert set(plano["arquivos"]) == {"especie", "subprojeto", "amostras", "refs_manifest"}
    with pytest.raises(TypeError):
        ctx.valores["ref_gtf"] = "/data/x"  # imutável depois de resolvido


def test_rascunho_em_dados_aponta_para_a_pasta_de_dados():
    _rascunho()
    ctx = contexto.resolver("homo_sapiens", "teste_rna")
    assert not ctx.eh_demo and not ctx.eh_especie_legada and ctx.assembly == "GRCh38"
    assert ctx.valores["sub_amostras"] == f"{_dp()}/projetos/homo_sapiens/teste_rna/amostras.tsv"
    assert not any(k.startswith("ref_") for k in ctx.valores)  # humano: nenhuma referência
    # o valor passa no MESMO sanitizador que o dispatch aplica
    from omica.registry import ParamSpec

    dispatch._validar_valor(ParamSpec(name="sub_amostras", rotulo="x", tipo="caminho"), ctx.valores["sub_amostras"])


def test_resolver_canarios():
    _rascunho("bos_taurus", "velho", status="invalido", validade="rótulos errados", modulos=["transcriptomica"])
    _rascunho("bos_taurus", "guardado", status="arquivado", validade="substituído", modulos=["transcriptomica"])
    casos = [(("xx_nada", "demo"), 404, "especie_inexistente"),
             (("bos_taurus", "nao_existe"), 404, "subprojeto_inexistente"),
             (("bos_taurus", "sub03"), 404, "subprojeto_inexistente"),
             (("bos_taurus", "velho"), 409, "subprojeto_nao_executavel"),
             (("bos_taurus", "guardado"), 409, "subprojeto_nao_executavel"),
             ((None, "demo"), 404, "especie_inexistente"),
             (("bos_taurus", None), 404, "subprojeto_inexistente")]
    for args, status, erro in casos:
        with pytest.raises(contexto.ContextoRecusado) as e:
            contexto.resolver(*args)
        assert (e.value.status, e.value.erro) == (status, erro), args
    with pytest.raises(contexto.ContextoRecusado) as e:
        contexto.resolver("bos_taurus", "velho")
    assert e.value.detalhe["status_subprojeto"] == "invalido" and e.value.detalhe["validade"] == "rótulos errados"
    assert "status" not in e.value.detalhe  # não confunde com o código HTTP


def test_painel_entra_so_se_existir(tmp_path):
    pasta = _rascunho("bos_taurus", "com_painel", modulos=["genomica"], painel="painel.bed")
    ctx = contexto.resolver("bos_taurus", "com_painel")
    # declarado e ausente: sem valor (o cmd que pedir recebe "referência ausente") e sha None
    assert "sub_painel_bed" not in ctx.valores and ctx.arquivos["painel"] is None
    (pasta / "painel.bed").write_text("14\t1\t100\n", encoding="utf-8")
    ctx2 = contexto.resolver("bos_taurus", "com_painel")
    assert ctx2.valores["sub_painel_bed"] == f"{_dp()}/projetos/bos_taurus/com_painel/painel.bed"
    assert ctx2.arquivos["painel"] and ctx2.sha256 != ctx.sha256


# ------------------------------------------------------------------ sha do contexto

def test_sha_texto_igual_ao_replace_mesmo_com_crlf_partido_entre_blocos(tmp_path):
    """O '\\r\\n' que cai exatamente na fronteira de 1 MiB (o '\\r' no fim de um bloco)."""
    bloco = 1 << 20
    conteudo = b"a" * (bloco - 1) + b"\r\n" + b"b\r\nc\rd\r"
    arq = tmp_path / "x.tsv"
    arq.write_bytes(conteudo)
    assert contexto._sha_texto(arq) == hashlib.sha256(conteudo.replace(b"\r\n", b"\n")).hexdigest()
    assert contexto._sha_texto(tmp_path / "nao_existe") is None


def test_sha_crlf_lf_e_restauro(copia):
    _, proj = copia
    amostras = proj / "bos_taurus" / "aula_granulosa_calor" / "amostras.tsv"
    original = amostras.read_bytes()
    lf = original.replace(b"\r\n", b"\n")
    amostras.write_bytes(lf)
    sha_lf = contexto.resolver("bos_taurus", "aula_granulosa_calor").sha256
    amostras.write_bytes(lf.replace(b"\n", b"\r\n"))  # controle: checkout com autocrlf
    assert contexto.resolver("bos_taurus", "aula_granulosa_calor").sha256 == sha_lf
    linhas = lf.split(b"\n")
    linhas[1] = linhas[1].replace(b"summer_heat", b"winter_control", 1)
    amostras.write_bytes(b"\n".join(linhas))  # canário: UMA linha muda
    assert contexto.resolver("bos_taurus", "aula_granulosa_calor").sha256 != sha_lf
    amostras.write_bytes(lf)  # restaurado
    assert contexto.resolver("bos_taurus", "aula_granulosa_calor").sha256 == sha_lf


def _editar_yaml(arq: Path, mudar) -> None:
    dados = yaml.safe_load(arq.read_text(encoding="utf-8"))
    mudar(dados)
    arq.write_text(yaml.safe_dump(dados, allow_unicode=True, sort_keys=False), encoding="utf-8")


def test_sha_especie_cobre_so_a_assembly_usada(copia):
    """Crítica v2, médio 13: comentário, nome de exibição, recurso ou OUTRA assembly não
    derrubam os runs aprovados; ref, equivalência, módulos e parâmetros da assembly usada sim."""
    esp_dir, _ = copia
    arq = esp_dir / "bos_taurus.yaml"
    base = contexto.demo().sha256
    arq.write_text(arq.read_text(encoding="utf-8") + "\n# comentário novo\n", encoding="utf-8")
    assert contexto.demo().sha256 == base

    def irrelevante(d):
        d["nome_exibido"] = "Bovinos (renomeado)"
        d["recursos"]["outro"] = "/data/outro"
        d["assemblies"]["ARS-UCD1.2"] = {"contigs": "sem_chr", "prefixo_gene": "ENSBTAG",
                                         "refs": {"gtf": "/data/outra/genes.gtf.gz"}}
    _editar_yaml(arq, irrelevante)
    assert contexto.demo().sha256 == base

    for nome, mudar in [
        ("ref", lambda d: d["assemblies"]["ARS-UCD1.3"]["refs"].update(gtf="/data/outro/genes.gtf.gz")),
        ("equivalencia", lambda d: d["assemblies"]["ARS-UCD1.3"]["equivalencias"]["vep_cache"].update(
            relacao="identica")),
        # (tirar um módulo faria o demo, que o usa, deixar de carregar: acrescenta)
        ("modulos", lambda d: d.update(modulos=[*d["modulos"], "proteomica"])),
        ("parametros", lambda d: d.update(parametros={"transcriptomica.deseq2.padj": "0.01"})),
    ]:
        guardado = arq.read_text(encoding="utf-8")
        _editar_yaml(arq, mudar)
        cat = contexto.catalogo()
        assert cat.erros_especies == {}, nome
        assert contexto.demo(cat=cat).sha256 != base, nome
        arq.write_text(guardado, encoding="utf-8")
    assert contexto.demo().sha256 == base


def test_sha_subprojeto_cobre_o_conteudo_e_nao_o_comentario(copia):
    _, proj = copia
    arq = proj / "bos_taurus" / "aula_granulosa_calor" / "subprojeto.yaml"
    base = contexto.resolver("bos_taurus", "aula_granulosa_calor").sha256
    arq.write_text("# comentário\n" + arq.read_text(encoding="utf-8"), encoding="utf-8")
    assert contexto.resolver("bos_taurus", "aula_granulosa_calor").sha256 == base
    _editar_yaml(arq, lambda d: d.update(contrastes=[]))
    assert contexto.resolver("bos_taurus", "aula_granulosa_calor").sha256 != base


def test_manifest_das_refs_entra_no_sha(cfg):
    base = contexto.demo().sha256
    manifesto = cfg.refs_dir / "bos_taurus" / "ARS-UCD1.3" / "MANIFEST.json"
    manifesto.parent.mkdir(parents=True)
    manifesto.write_text('{"gtf": "9d012e09"}\n', encoding="utf-8")
    ctx = contexto.demo()
    assert ctx.arquivos["refs_manifest"] and ctx.sha256 != base


# ------------------------------------------------------------------------ do_plano

def test_do_plano_legado_confere_e_recusa():
    demo = contexto.demo()
    assert contexto.do_plano({"passos": []}).sha256 == demo.sha256  # plano de antes da v2
    assert contexto.do_plano({"contexto": demo.para_plano()}).sha256 == demo.sha256
    velho = {**demo.para_plano(), "sha256": "0" * 64}
    with pytest.raises(contexto.ContextoRecusado) as e:
        contexto.do_plano({"contexto": velho})
    assert (e.value.status, e.value.erro) == (409, "contexto_mudou")
    assert e.value.detalhe == {"esperado": "0" * 64, "atual": demo.sha256}
    for forjado in ([], {"especie": "bos_taurus"}, {"especie": 1, "subprojeto": "demo", "sha256": "x"}):
        with pytest.raises(contexto.ContextoRecusado) as e:
            contexto.do_plano({"contexto": forjado})
        assert (e.value.status, e.value.erro) == (409, "plano_adulterado")


def test_rel_resultados():
    _rascunho()
    assert contexto.rel_resultados(contexto.demo(), "transcriptomica", RUN) == f"transcriptomica/runs/{RUN}"
    assert (contexto.rel_resultados(contexto.resolver("homo_sapiens", "teste_rna"), "transcriptomica", RUN)
            == f"projetos/homo_sapiens/teste_rna/transcriptomica/runs/{RUN}")


# ------------------------------------------------------------- execucao_permitida

def test_execucao_permitida_precedencia_e_motivos():
    _rascunho()  # humano, draft, pipelines []
    _rascunho("bos_taurus", "velho", status="invalido", validade="x", modulos=["transcriptomica"])
    _rascunho("bos_taurus", "so_rna", modulos=["transcriptomica"])  # pipelines [] e só transcriptômica
    cat = contexto.catalogo()
    validos, _ = carregar_pipelines()
    de, painel = validos["transcriptomica_de"], validos["genomica_painel"]
    wf = next(w for w in load_registry().por_id("genomica").workflows if w.id == "wf_painel_demo")
    wf_fora = wf.model_copy(update={"validado_em": ["bos_taurus/outro"]})
    casos = [
        (("xx_nada", "demo", "script"), {}, "especie_inexistente"),
        (("bos_taurus", "nao_existe", "pipeline"), {"pipeline": de}, "subprojeto_inexistente"),
        # não executável vem ANTES de "só pipelines"
        (("bos_taurus", "velho", "script"), {}, "subprojeto_nao_executavel"),
        (("bos_taurus", "aula_granulosa_calor", "script"), {}, "so_pipelines_fora_do_demo"),
        (("bos_taurus", "aula_granulosa_calor", "workflow"), {"workflow": wf}, "so_pipelines_fora_do_demo"),
        (("homo_sapiens", "teste_rna", "workflow"), {"workflow": wf}, "so_pipelines_fora_do_demo"),
        (("bos_taurus", "demo", "workflow"), {"workflow": wf_fora}, "workflow_nao_validado_neste_subprojeto"),
        (("homo_sapiens", "teste_rna", "pipeline"), {"pipeline": painel}, "pipeline_fora_da_especie"),
        (("bos_taurus", "aula_granulosa_calor", "pipeline"), {"pipeline": painel}, "pipeline_fora_do_subprojeto"),
        (("bos_taurus", "so_rna", "pipeline"), {"pipeline": painel}, "modulo_fora_do_subprojeto"),
        # controles
        (("bos_taurus", "demo", "script"), {}, ""),
        (("bos_taurus", "demo", "workflow"), {"workflow": wf}, ""),
        (("bos_taurus", "demo", "pipeline"), {"pipeline": painel}, ""),
        (("bos_taurus", "aula_granulosa_calor", "pipeline"), {"pipeline": de}, ""),
        (("homo_sapiens", "teste_rna", "pipeline"), {"pipeline": de}, ""),
        (("bos_taurus", "so_rna", "pipeline"), {"pipeline": de}, ""),
    ]
    for args, kw, motivo in casos:
        ok, recebido = contexto.execucao_permitida(*args, cat=cat, **kw)
        assert (ok, recebido) == (motivo == "", motivo), args
        assert motivo == "" or motivo in contexto.STATUS_DA_RECUSA
    with pytest.raises(ValueError):
        contexto.execucao_permitida("bos_taurus", "demo", "pipeline", cat=cat)
    with pytest.raises(ValueError):
        contexto.execucao_permitida("bos_taurus", "demo", "job", cat=cat)


# ----------------------------------------------------------------------- consultas

def test_listar_especies_na_ordem():
    assert [e.id for e in contexto.listar_especies()] == ["homo_sapiens", "mus_musculus", "bos_taurus"]


def test_listar_subprojetos_demo_primeiro_e_arquivados():
    _rascunho("bos_taurus", "aaa_primeiro_titulo", titulo="AAA", modulos=["transcriptomica"])
    _rascunho("bos_taurus", "guardado", status="arquivado", validade="x", modulos=["transcriptomica"])
    ids = [r.sub.id for r in contexto.listar_subprojetos("bos_taurus")]
    assert ids == ["bos_taurus/demo", "bos_taurus/aaa_primeiro_titulo", "bos_taurus/aula_granulosa_calor"]
    com = [r.sub.id for r in contexto.listar_subprojetos("bos_taurus", incluir_arquivados=True)]
    assert "bos_taurus/guardado" in com and len(com) == 4
    assert contexto.listar_subprojetos("homo_sapiens") == []


def test_obter_subprojeto():
    assert contexto.obter_subprojeto("bos_taurus", "demo").sub.id == "bos_taurus/demo"
    for esp, slug in [("bos_taurus", "sub03"), ("bos_taurus", "Demo"), ("..", "demo"),
                      ("bos_taurus", "../demo"), ("bos_taurus", None), ("homo_sapiens", "demo")]:
        assert contexto.obter_subprojeto(esp, slug) is None, (esp, slug)


def test_modulos_habilitados():
    _rascunho()
    cat = contexto.catalogo()
    ob = lambda e, s: contexto.obter_subprojeto(e, s, cat)  # noqa: E731
    assert contexto.modulos_habilitados(ob("bos_taurus", "demo"), cat) == [
        "genomica", "epigenomica", "transcriptomica", "ml"]
    assert contexto.modulos_habilitados(ob("bos_taurus", "aula_granulosa_calor"), cat) == ["transcriptomica"]
    assert contexto.modulos_habilitados(ob("homo_sapiens", "teste_rna"), cat) == ["transcriptomica"]
    # módulo que o registry recusou nunca aparece
    sem_ml = contexto.Catalogo(cat.especies, {}, cat.subprojetos, {}, ("genomica", "epigenomica", "transcriptomica"))
    assert "ml" not in contexto.modulos_habilitados(ob("bos_taurus", "demo"), sem_ml)


def test_pipelines_do_subprojeto():
    _rascunho()
    ob = contexto.obter_subprojeto
    ids = lambda regs: [p.id for p in regs]  # noqa: E731
    assert ids(contexto.pipelines_do_subprojeto(ob("bos_taurus", "demo"))) == [
        "transcriptomica_de", "genomica_painel", "genomica_regioes"]
    assert ids(contexto.pipelines_do_subprojeto(ob("bos_taurus", "demo"), "genomica")) == [
        "genomica_painel", "genomica_regioes"]
    assert ids(contexto.pipelines_do_subprojeto(ob("bos_taurus", "aula_granulosa_calor"))) == ["transcriptomica_de"]
    assert ids(contexto.pipelines_do_subprojeto(ob("homo_sapiens", "teste_rna"))) == ["transcriptomica_de"]
    # o subprojeto que lista só um pipeline do módulo não vê o outro
    _rascunho("bos_taurus", "so_painel", modulos=["genomica"], pipelines=["genomica_painel"])
    assert ids(contexto.pipelines_do_subprojeto(ob("bos_taurus", "so_painel"))) == ["genomica_painel"]


def test_pipelines_do_subprojeto_filtra_pela_especie(tmp_path, monkeypatch):
    """Uma espécie com o módulo de genômica não herda os pipelines bovinos dele."""
    esp, proj = tmp_path / "e", tmp_path / "p"
    escrever_especie(esp, "mus_musculus", assembly="GRCm39", prefixo="ENSMUSG",
                     modulos=("transcriptomica", "genomica"))
    escrever_subprojeto(proj, "mus_musculus", "camundongo", assembly="GRCm39",
                        modulos=["transcriptomica", "genomica"])
    apontar_catalogo(monkeypatch, esp, proj)
    cat = contexto.catalogo()
    reg = contexto.obter_subprojeto("mus_musculus", "camundongo", cat)
    assert contexto.modulos_habilitados(reg, cat) == ["transcriptomica", "genomica"]
    assert [p.id for p in contexto.pipelines_do_subprojeto(reg, cat=cat)] == ["transcriptomica_de"]
    painel = carregar_pipelines()[0]["genomica_painel"]
    assert contexto.execucao_permitida("mus_musculus", "camundongo", "pipeline", pipeline=painel,
                                       cat=cat) == (False, "pipeline_fora_da_especie")


def test_catalogo_reporta_chaves_de_parametros_inexistentes(tmp_path, monkeypatch):
    """Crítica v2, médio 14: chave digitada errado vai para erros_*, nunca vira default bovino."""
    esp, proj = tmp_path / "e", tmp_path / "p"
    escrever_especie(esp, parametros={"transcriptomica.deseq2.padjj": "0.01"})
    escrever_especie(esp, "mus_musculus", ordem=2, assembly="GRCm39", prefixo="ENSMUSG",
                     parametros={"transcriptomica.deseq2.padj": "0.01"})  # controle
    escrever_subprojeto(proj, "mus_musculus", "erro_param", assembly="GRCm39",
                        parametros={"transcriptomica.deseq3.padj": "0.01"})
    escrever_subprojeto(proj, "mus_musculus", "ok_param", assembly="GRCm39",
                        parametros={"transcriptomica.deseq2.lfc": "2"})
    apontar_catalogo(monkeypatch, esp, proj)
    cat = contexto.catalogo()
    assert set(cat.especies) == {"mus_musculus"} and "homo_sapiens.yaml" in cat.erros_especies
    assert "padjj" in cat.erros_especies["homo_sapiens.yaml"]
    assert set(cat.subprojetos) == {"mus_musculus/ok_param"}
    assert any("deseq3" in v for v in cat.erros_subprojetos.values())


# ======================================================================= dispatch (B2)

def test_saida_run_literal_e_padrao_por_contexto():
    assert dispatch._SAIDA_RUN == SAIDA_RUN_V1
    demo = contexto.demo()
    assert dispatch.padrao_saida(None) == dispatch.padrao_saida(demo) == SAIDA_RUN_V1
    _rascunho()
    humano = contexto.resolver("homo_sapiens", "teste_rna")
    assert dispatch.padrao_saida(humano) == "/results/projetos/homo_sapiens/teste_rna" + SAIDA_RUN_V1[len("/results"):]


@pytest.mark.parametrize("caminho, ok", [
    ("projetos/bos_taurus/demo/amostras.tsv", True),
    ("projetos/homo_sapiens/teste_rna", True),
    ("modules/genomica/data/paineis/x.txt", True),
    ("projetos/Bos/demo/x", False),
    ("projetos/bo/demo/x", False),
    ("projetos/bos_taurus", False),
    ("projetos/bos_taurus/de/x", False),
    ("projetos/bos_taurus/demo/../../.env", False),
    ("projetos2/bos_taurus/demo/x", False),
])
def test_raiz_do_repositorio_inclui_subprojeto(caminho, ok):
    if ok:
        dispatch._validar_caminho(caminho, "x")
    else:
        with pytest.raises(ValueError):
            dispatch._validar_caminho(caminho, "x")


def test_controle_humano_deseq2_no_layout_projetos():
    """§9.2 fora do demo: o mesmo argv do golden, só a saída no layout do subprojeto."""
    _rascunho()
    ctx = contexto.resolver("homo_sapiens", "teste_rna")
    spec = _spec("transcriptomica", "deseq2")
    dp = _dp()
    esperado = _golden("transcriptomica/scripts_api/deseq2").replace("<DP>", dp).replace(
        f"{dp}/results/transcriptomica/runs/{RUN}/p1",
        f"{dp}/results/projetos/homo_sapiens/teste_rna/transcriptomica/runs/{RUN}/p1")
    cmd = dispatch.montar_cmd(spec, _valores(spec), saida=_saida("transcriptomica", ctx), ctx=ctx,
                              mod_id="transcriptomica")
    assert cmd == esperado


def test_canarios_de_layout_de_saida():
    _rascunho()
    humano = contexto.resolver("homo_sapiens", "teste_rna")
    aula = contexto.resolver("bos_taurus", "aula_granulosa_calor")
    demo = contexto.demo()
    spec = _spec("transcriptomica", "deseq2")
    v = _valores(spec)
    for ctx, saida in [(humano, _saida("transcriptomica")),          # humano no layout legado
                       (demo, _saida("transcriptomica", humano)),     # demo no layout de projetos
                       (aula, _saida("transcriptomica", humano)),     # um subprojeto na árvore de outro
                       (aula, _saida("transcriptomica"))]:
        with pytest.raises(ValueError, match="saida precisa ser"):
            dispatch.montar_cmd(spec, v, saida=saida, ctx=ctx, mod_id="transcriptomica")
    # controles: cada um no seu
    for ctx in (humano, aula, demo):
        dispatch.montar_cmd(spec, v, saida=_saida("transcriptomica", ctx), ctx=ctx, mod_id="transcriptomica")


def test_canario_referencia_ausente_e_controle_com_referencia():
    _rascunho()
    spec = _spec_ref()
    humano = contexto.resolver("homo_sapiens", "teste_rna")
    with pytest.raises(ValueError, match="referência ausente: ref_salmon_indice"):
        dispatch.montar_cmd(spec, saida=_saida("transcriptomica", humano), ctx=humano, mod_id="transcriptomica")
    demo = contexto.demo()
    cmd = dispatch.montar_cmd(spec, saida=_saida("transcriptomica"), ctx=demo, mod_id="transcriptomica")
    assert "--indice /data/omica/refs/salmon_bostau --saida" in cmd


def test_canario_requer_refs_conta_mesmo_fora_do_cmd():
    _rascunho()
    spec = _spec_ref(cmd="python3 modules/transcriptomica/scripts/salmon_quant.py --saida {saida}")
    humano = contexto.resolver("homo_sapiens", "teste_rna")
    with pytest.raises(ValueError, match="referência ausente: ref_salmon_indice"):
        dispatch.montar_cmd(spec, saida=_saida("transcriptomica", humano), ctx=humano, mod_id="transcriptomica")
    # controle: com a referência no contexto, o cmd não muda (a chave não está nele)
    cmd = dispatch.montar_cmd(spec, saida=_saida("transcriptomica"), ctx=contexto.demo(), mod_id="transcriptomica")
    assert cmd.endswith(f"--saida {_saida('transcriptomica')}")


def test_canario_sem_contexto_e_chave_fora_da_lista():
    spec = _spec_ref()
    with pytest.raises(ValueError, match="nenhum contexto foi dado"):
        dispatch.montar_cmd(spec, saida=_saida("transcriptomica"))
    demo = contexto.demo()
    fora = _spec_ref(cmd="python3 a.py --x {ref_bowtie} --saida {saida}", requer_refs=["ref_bowtie"])
    with pytest.raises(ValueError, match="fora da lista fechada"):
        dispatch.montar_cmd(fora, saida=_saida("transcriptomica"), ctx=demo, mod_id="transcriptomica")
    so_req = _spec_ref(cmd="python3 a.py --saida {saida}", requer_refs=["sub_inexistente"])
    with pytest.raises(ValueError, match="fora da lista fechada"):
        dispatch.montar_cmd(so_req, saida=_saida("transcriptomica"), ctx=demo, mod_id="transcriptomica")


def test_canario_contexto_sem_mod_id():
    spec = _spec("transcriptomica", "deseq2")
    with pytest.raises(ValueError, match="exige mod_id"):
        dispatch.montar_cmd(spec, _valores(spec), saida=_saida("transcriptomica"), ctx=contexto.demo())


@pytest.mark.parametrize("ruim", ["/etc/passwd", "{dados}/x", "/data/x y", "../x"])
def test_canario_valor_de_contexto_passa_pelo_sanitizador(ruim):
    demo = contexto.demo()
    forjado = contexto.Contexto(demo.especie, demo.slug, demo.assembly,
                                {**demo.valores, "ref_salmon_indice": ruim}, {}, {}, "x")
    with pytest.raises(ValueError):
        dispatch.montar_cmd(_spec_ref(), saida=_saida("transcriptomica"), ctx=forjado, mod_id="transcriptomica")


def test_canario_scripts_fora_do_demo_so_pipelines():
    _rascunho()
    aula = contexto.resolver("bos_taurus", "aula_granulosa_calor")
    humano = contexto.resolver("homo_sapiens", "teste_rna")
    spec = _spec("transcriptomica", "deseq2_demo", api=False)
    with pytest.raises(ValueError, match="só pipelines executam"):
        dispatch.montar_cmd(spec, ctx=aula, mod_id="transcriptomica")
    tri = spec.model_copy(update={"especies": ["bos_taurus", "homo_sapiens", "mus_musculus"]})
    with pytest.raises(ValueError, match="só pipelines executam"):
        dispatch.montar_cmd(tri, ctx=humano, mod_id="transcriptomica")
    # controle: no demo, o golden
    esperado = _golden("transcriptomica/scripts/deseq2_demo").replace("<DADOS>", str(get_config().data_dir))
    assert dispatch.montar_cmd(spec, ctx=contexto.demo(), mod_id="transcriptomica") == esperado


def test_canario_especie_nao_declarada_no_script():
    _rascunho()
    humano = contexto.resolver("homo_sapiens", "teste_rna")
    spec = _spec("transcriptomica", "deseq2")
    so_bov = spec.model_copy(update={"especies": ["bos_taurus"]})
    with pytest.raises(ValueError, match="não declara a espécie homo_sapiens"):
        dispatch.montar_cmd(so_bov, _valores(spec), saida=_saida("transcriptomica", humano), ctx=humano,
                            mod_id="transcriptomica")
    dispatch.montar_cmd(spec, _valores(spec), saida=_saida("transcriptomica", humano), ctx=humano,
                        mod_id="transcriptomica")  # controle: o real declara as 3


def test_canario_valor_de_formulario_com_chave():
    spec = _spec("transcriptomica", "deseq2")
    v = {**_valores(spec), "counts": f"{_dp()}/raw/{{ref_gtf}}"}
    with pytest.raises(ValueError, match="valor inválido"):
        dispatch.montar_cmd(spec, v, saida=_saida("transcriptomica"), ctx=contexto.demo(), mod_id="transcriptomica")


def _com_escopo(spec: ScriptSpec, nome: str, escopo: str) -> ScriptSpec:
    novo = spec.model_copy(deep=True)
    next(p for p in novo.params if p.name == nome).escopo = escopo
    return novo


def test_escopo_especie_e_subprojeto():
    _rascunho()
    humano = contexto.resolver("homo_sapiens", "teste_rna")
    aula = contexto.resolver("bos_taurus", "aula_granulosa_calor")
    demo = contexto.demo()
    base = _spec("transcriptomica", "deseq2")
    v = _valores(base)
    de_especie = _com_escopo(base, "padj", "especie")
    de_sub = _com_escopo(base, "lfc", "subprojeto")
    # canários: o default bovino/do demo não vale fora dele sem valor
    with pytest.raises(ValueError, match="é bovino; informe o valor para homo_sapiens"):
        dispatch.montar_cmd(de_especie, v, saida=_saida("transcriptomica", humano), ctx=humano, mod_id="transcriptomica")
    with pytest.raises(ValueError, match="é do demo; informe o valor para bos_taurus/aula_granulosa_calor"):
        dispatch.montar_cmd(de_sub, v, saida=_saida("transcriptomica", aula), ctx=aula, mod_id="transcriptomica")
    # controles: escopo especie vale na espécie legada (fora do demo também); com envio vale em qualquer uma
    golden = dispatch.montar_cmd(base, v, saida=_saida("transcriptomica"), ctx=demo, mod_id="transcriptomica")
    assert dispatch.montar_cmd(de_especie, v, saida=_saida("transcriptomica"), ctx=demo,
                               mod_id="transcriptomica") == golden
    assert dispatch.montar_cmd(de_sub, v, saida=_saida("transcriptomica"), ctx=demo, mod_id="transcriptomica") == golden
    dispatch.montar_cmd(de_especie, v, saida=_saida("transcriptomica", aula), ctx=aula, mod_id="transcriptomica")
    cmd = dispatch.montar_cmd(de_especie, {**v, "padj": "0.01"}, saida=_saida("transcriptomica", humano), ctx=humano,
                              mod_id="transcriptomica")
    assert "--padj 0.01" in cmd
    # sem contexto (UI antiga do demo) o escopo não muda nada
    assert dispatch.montar_cmd(de_sub, v, saida=_saida("transcriptomica")) == golden


def test_controle_precedencia_envio_subprojeto_especie_default(tmp_path, monkeypatch):
    esp, proj = tmp_path / "e", tmp_path / "p"
    escrever_especie(esp, parametros={"transcriptomica.deseq2.padj": "0.01", "transcriptomica.deseq2.lfc": "2"})
    escrever_subprojeto(proj, "homo_sapiens", "com_param", parametros={"transcriptomica.deseq2.padj": "0.001"})
    apontar_catalogo(monkeypatch, esp, proj)
    ctx = contexto.resolver("homo_sapiens", "com_param")
    spec = _spec("transcriptomica", "deseq2")
    de_especie = _com_escopo(spec, "lfc", "especie")  # o contexto dá o valor: não recusa
    v = _valores(spec)
    saida = _saida("transcriptomica", ctx)
    cmd = dispatch.montar_cmd(de_especie, v, saida=saida, ctx=ctx, mod_id="transcriptomica")
    assert "--padj 0.001 --lfc 2 " in cmd  # subprojeto > espécie; espécie > default
    cmd = dispatch.montar_cmd(de_especie, {**v, "padj": "0.5"}, saida=saida, ctx=ctx, mod_id="transcriptomica")
    assert "--padj 0.5 --lfc 2 " in cmd    # envio > subprojeto
    par = {p.name: p for p in spec.params}
    assert dispatch.valor_efetivo(par["padj"], {}, None, "transcriptomica", "deseq2") == "0.05"  # default
    assert dispatch.valor_efetivo(par["padj"], {}, ctx, "transcriptomica", "deseq2") == "0.001"
    assert dispatch.valor_efetivo(par["padj"], {}, ctx, "transcriptomica", "volcano") == "0.05"  # outro script
    assert dispatch.valor_efetivo(par["padj"], {"padj": " 0.2 "}, ctx, "transcriptomica", "deseq2") == " 0.2 "  # cru


def test_canario_envio_so_com_espaco_e_obrigatorio_vazio():
    spec = _spec("transcriptomica", "deseq2")
    v = {**_valores(spec), "padj": "   "}
    for ctx, mod in ((None, ""), (contexto.demo(), "transcriptomica")):
        with pytest.raises(ValueError, match="parâmetro obrigatório vazio"):
            dispatch.montar_cmd(spec, v, saida=_saida("transcriptomica"), ctx=ctx, mod_id=mod)


# --------------------------------------------------------------- lançar com contexto

def test_meta_do_job_com_especie_e_subprojeto(monkeypatch, cfg):
    chamadas = []

    def falso(func_path, prefixo, mem_gb=0.5, timeout_s=3600, fila="omica", **meta):
        chamadas.append(meta)
        return "job-x", "rq"

    monkeypatch.setattr(dispatch.queue, "enqueue", falso)
    aula = contexto.resolver("bos_taurus", "aula_granulosa_calor")
    spec = _spec("transcriptomica", "deseq2")
    dispatch.lancar_script("transcriptomica", "deseq2", valores=_valores(spec), api=True,
                           saida=_saida("transcriptomica", aula), ctx=aula, extra_meta={"run_id": "r"})
    dispatch.lancar_script("transcriptomica", "deseq2_demo")
    (com, sem) = chamadas
    assert (com["especie"], com["subprojeto"], com["run_id"]) == ("bos_taurus", "aula_granulosa_calor", "r")
    assert "/results/projetos/bos_taurus/aula_granulosa_calor/" in com["cmd"]
    assert (sem["especie"], sem["subprojeto"]) == ("bos_taurus", "demo")


def test_lancar_workflow_so_onde_foi_validado(monkeypatch, cfg):
    cadeias = []
    monkeypatch.setattr(dispatch.queue, "enqueue_cadeia",
                        lambda itens, wf_meta: cadeias.append((itens, wf_meta)) or ("wf-x", [], "rq"))
    _rascunho()
    for ctx in (contexto.resolver("homo_sapiens", "teste_rna"), contexto.resolver("bos_taurus", "aula_granulosa_calor")):
        with pytest.raises(ValueError, match="não é validado em"):
            dispatch.lancar_workflow("transcriptomica", "wf_de_granulosa", ctx=ctx)
    assert cadeias == []  # nada enfileirado antes da recusa
    dispatch.lancar_workflow("transcriptomica", "wf_de_granulosa", ctx=contexto.demo())
    dispatch.lancar_workflow("transcriptomica", "wf_de_granulosa")
    (itens_ctx, meta_ctx), (itens_sem, meta_sem) = cadeias
    assert [i["meta"]["cmd"] for i in itens_ctx] == [i["meta"]["cmd"] for i in itens_sem]
    assert all((i["meta"]["especie"], i["meta"]["subprojeto"]) == ("bos_taurus", "demo") for i in itens_ctx + itens_sem)
    assert (meta_ctx["especie"], meta_ctx["subprojeto"]) == ("bos_taurus", "demo") == (
        meta_sem["especie"], meta_sem["subprojeto"])
