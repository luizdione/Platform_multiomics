"""motifs com as referências do CONTEXTO (adendo v2 §A9.1, crítica M2) e o argv do demo dos
8 scripts_api da epigenômica (adendo §A11: "o cmd do demo … é igual ao argv da ESPEC; só o
motifs ganha as duas flags").

O plano certifica `contexto.valores.ref_fasta`/`ref_homer_preparsed`; antes, o HOMER rodava
com os literais do script (motifs_homer.py:31-32) e o valor do plano não era o que o script
recebia. No estilo dos testes do B5 (tests/test_linha_de_base_v2.py): com contexto.demo() o cmd
traz os mesmos caminhos de antes; com outro FASTA no contexto, o cmd muda; sem contexto,
montar_cmd recusa (nunca monta com um literal).
"""
from __future__ import annotations

import pytest

from omica import contexto
from omica.config import REPO_ROOT, get_config, reset_config_cache
from omica.jobs import dispatch
from omica.registry import load_registry

RUN = "epigenomica_dmc-20261029-120000-abcdef"
FASTA = "/data/snpeff_data/ARS-UCD1.3.113/sequences.fa"
PREPARSED = "/data/omica/refs/homer_preparsed"
# ESPEC §3.2.1, com os valores de um run (<R> = raw/runs/<RUN>, <S> = a pasta do passo)
ARGV_DEMO = {
    "montar_coverages": "python3 modules/epigenomica/scripts/montar_coverages.py --coverages <R>/coverages "
                        "--amostras <R>/amostras.tsv --saida <S>",
    "painel_janelas": "python3 modules/epigenomica/scripts/painel_janelas.py --painel <R>/painel.json --pad 15000 "
                      "--saida <S>",
    "dmc": "Rscript modules/epigenomica/scripts/metilacao_dmc.R --dir <R>/coverages --grupo-a Nelore --grupo-b HF "
           "--min-cov 5 --diff 15 --qvalue 0.05 --saida <S>",
    "intersecao": "python3 modules/epigenomica/scripts/intersecao_dmc_painel.py --dmc <R>/dmc.tsv --painel "
                  "<R>/janelas.bed --saida <S>",
    "tss": "python3 modules/epigenomica/scripts/perfil_tss.py --dir <R>/coverages --painel <R>/painel.json --grupo-a "
           "Nelore --grupo-b HF --janela 2000 --bin 100 --saida <S>",
    "motifs": "python3 modules/epigenomica/scripts/motifs_homer.py --cpgs <R>/todas_cpgs.tsv --q-max 0.25 --top-n 200 "
              f"--flanco 100 --genoma {FASTA} --preparsed {PREPARSED} --saida <S>",
    "mofa_preparar": "python3 modules/epigenomica/scripts/mofa_preparar.py --dir <R>/coverages --painel <R>/painel.json "
                     "--promotor 2000 --min-amostras 4 --saida <S>",
    "mofa_treinar_api": "python3 modules/epigenomica/scripts/mofa_treinar_api.py --promotor <R>/view_promotor.tsv "
                        "--corpo <R>/view_corpo.tsv --amostras <R>/amostras.tsv --fatores 3 --saida <S>",
}
VALORES = {
    "coverages": "coverages", "amostras": "amostras.tsv", "painel": "painel.json", "dmc": "dmc.tsv",
    "cpgs": "todas_cpgs.tsv", "promotor": "view_promotor.tsv", "corpo": "view_corpo.tsv",
}


@pytest.fixture()
def dp(monkeypatch):
    """A receita do golden: OMICA_DATA=/data/omica_golden (o mesmo texto no host e no Linux)."""
    monkeypatch.setenv("OMICA_DATA", "/data/omica_golden")
    reset_config_cache()
    yield get_config().data_dir.as_posix()
    reset_config_cache()


def _spec(chave: str):
    return next(s for s in load_registry().por_id("epigenomica").scripts_api if s.key == chave)


def _valores(spec, dp: str) -> dict[str, str]:
    """Parâmetro sem default = um arquivo do run; grupo_a/grupo_b = as escolhas do demo."""
    v = {}
    for p in spec.params:
        if p.default:
            continue
        v[p.name] = {"grupo_a": "Nelore", "grupo_b": "HF"}.get(p.name) or f"{dp}/raw/runs/{RUN}/" + (
            "janelas.bed" if (spec.key, p.name) == ("intersecao", "painel") else VALORES[p.name])
    return v


def _cmd(spec, dp: str, ctx) -> str:
    saida = f"{dp}/results/epigenomica/runs/{RUN}/p1"
    return dispatch.montar_cmd(spec, _valores(spec, dp), saida=saida, ctx=ctx, mod_id="epigenomica")


def test_registry_carrega_e_so_o_motifs_pede_referencia():
    reg = load_registry()
    assert reg.erros == {}
    m = reg.por_id("epigenomica")
    assert [s.key for s in m.scripts_api] == list(ARGV_DEMO)
    assert {s.key: s.requer_refs for s in m.scripts_api if s.requer_refs} == {
        "motifs": ["ref_fasta", "ref_homer_preparsed"]}
    assert all(s.especies == ["bos_taurus"] for s in m.scripts_api)


@pytest.mark.parametrize("chave", list(ARGV_DEMO))
def test_argv_do_demo_igual_ao_da_espec(dp, chave):
    esperado = ARGV_DEMO[chave].replace("<R>", f"{dp}/raw/runs/{RUN}").replace(
        "<S>", f"{dp}/results/epigenomica/runs/{RUN}/p1")
    assert _cmd(_spec(chave), dp, contexto.demo()) == esperado


def test_motifs_le_o_genoma_e_o_preparse_do_contexto(dp):
    spec = _spec("motifs")
    assert "{ref_fasta}" in spec.cmd and "{ref_homer_preparsed}" in spec.cmd
    assert "/data/snpeff_data" not in spec.cmd and "homer_preparsed " not in spec.cmd   # nenhum literal no cmd
    demo = contexto.demo()
    cmd = _cmd(spec, dp, demo)
    assert f"--genoma {FASTA} --preparsed {PREPARSED}" in cmd
    # canário: outro FASTA no contexto muda o cmd (não é um literal que está passando)
    outro = contexto.Contexto(demo.especie, demo.slug, demo.assembly,
                              {**demo.valores, "ref_fasta": "/data/outro/genoma.fa",
                               "ref_homer_preparsed": "/data/outro/preparsed"}, {}, {}, "x")
    cmd_outro = _cmd(spec, dp, outro)
    assert cmd_outro != cmd
    assert "--genoma /data/outro/genoma.fa --preparsed /data/outro/preparsed" in cmd_outro


def test_motifs_sem_contexto_recusa(dp):
    spec = _spec("motifs")
    with pytest.raises(ValueError, match="nenhum contexto foi dado"):
        dispatch.montar_cmd(spec, _valores(spec, dp), saida=f"{dp}/results/epigenomica/runs/{RUN}/p1")
    # controle: os outros 7 montam também sem contexto (não pedem referência)
    for chave in ARGV_DEMO:
        if chave != "motifs":
            s = _spec(chave)
            assert dispatch.montar_cmd(s, _valores(s, dp), saida=f"{dp}/results/epigenomica/runs/{RUN}/p1")


def test_os_valores_do_demo_sao_os_literais_do_script():
    """O argv do demo só GANHA as duas flags, com os mesmos caminhos que o script usava por
    padrão (o mesmo amarrado por tests/test_m0_dados.py::LITERAIS_DOS_SCRIPTS)."""
    texto = (REPO_ROOT / "modules" / "epigenomica" / "scripts" / "motifs_homer.py").read_text(encoding="utf-8")
    assert f'p.add_argument("--genoma", default="{FASTA}")' in texto
    assert f'p.add_argument("--preparsed", default="{PREPARSED}")' in texto
    assert contexto.demo().valores["ref_fasta"] == FASTA
    assert contexto.demo().valores["ref_homer_preparsed"] == PREPARSED
