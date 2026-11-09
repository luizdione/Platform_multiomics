"""Relatório dos dois pipelines da epigenômica (F6, ESPEC §3.2.4/§3.2.5 + adendo v2 §A9.4/§A9.5
e §A11): a ficha montada do YAML REAL com as saídas reais, e um canário por invariante.

epigenomica_dmc — os resumos e as tabelas dos passos selados (dmc, intersecao, tss, motifs)
são CÓPIAS dos arquivos do demo selado (E:\\data\\omica\\results\\epigenomica\\demo; skip quando
ausente); `montar` e `janelas` rodam de verdade no host sobre o demo. As 13 invariantes passam,
com a recontagem em 275 pelo desempate; com `abs_maior_ou_igual` e sem desempate, 276 reprova.

epigenomica_mofa — `montar` e `views` (o mofa_preparar selado, stdlib) rodam de verdade; o
`mofa_treinar_api` roda com o treino trocado por um Z fixo (no host não há mofapy2). No worker
methyl, o smoke (tests/test_scripts_smoke_epi.py) monta a MESMA ficha com o treino real.

Eco de consistência (marcada no YAML com "# eco de consistência: não conta como canário"): o
número e a tabela (ou os dois números) saem do MESMO script e da MESMA variável, e a invariante
nunca reprova. Uma tabela mexida DEPOIS do envelope não é ela que pega: é o sha256 (R3), que o
portão confere antes de ler a porta. As ecos ficam fora da conta "um canário por invariante".

Invariante → falha real que ela pega (epigenomica_dmc, na ordem do YAML):
   1. hiper + hipo = n_dmc ............................. eco (o mesmo `tab` do R conta os três)
   2. interseção.n_dmc_total = dmc.n_dmc ............... a interseção leu um dmc.tsv que não é o deste run
   3. grupo_a.n + grupo_b.n = montar.n_amostras ........ o R achou por substring amostras a mais/a menos
   4. tss.grupo_a.n_amostras = dmc.grupo_a.n ........... o glob do TSS e o list.files do R viram grupos diferentes
   5. tss.grupo_b.n_amostras = dmc.grupo_b.n ........... idem, grupo B
   6. interseção.n_genes_painel = janelas.n_janelas .... a interseção leu um BED que não é o do passo janelas
   7. tss.n_tss = janelas.n_genes ....................... eco (o mesmo painel JSON)
   8. tss.n_tss_fita_negativa = janelas.n_fita_negativa . eco (o mesmo painel JSON)
   9. recontagem (q < corte, |diff| > corte, desempate) . n_dmc que não é o que a tabela inteira diz
  10. linhas de todas_cpgs = n_cpg_testados ............. o calculateDiffMeth não devolveu uma linha por CpG
                                                          do unite (nrow(unido) × getData(dif): objetos diferentes)
  11. linhas de dmc = n_dmc ............................. eco (nrow do mesmo `tab` que vira o dmc.tsv)
  12. Σ N_DMC da interseção = n_atribuicoes_gene ........ eco (o mesmo dicionário `contagem`)
  13. linhas da interseção = n_genes_com_dmc ............ eco (len do mesmo `contagem`)

Invariante → falha real que ela pega (epigenomica_mofa):
   1. mofa.n_amostras = views.n_amostras ................ o MOFA treinou sem alguma coluna
   2-3. genes_por_view do mofa = das views .............. o MOFA treinou sem alguma linha
   4. views.n_amostras = montar.n_amostras .............. o mofa_preparar não leu todos os coverages
   5-6. mofa.n_y0/n_y1 = montar.n_y0/n_y1 ............... rótulo errado que muda as contagens
   7. mofa.n_rotulo_igual_prefixo = montar.n_amostras ... rótulo errado que PRESERVA as contagens (o grupos.tsv
                                                          do selado com Lote1 × Lote2 misturando HF e Nelore, M8;
                                                          a ordem invertida): a 5 e a 6 aprovam, a 7 reprova
   8. linhas da variância = n_fatores ................... eco (o mesmo args.fatores)
   9. linhas dos fatores = n_amostras ................... eco (a mesma lista de rótulos)
   10-11. linhas das views = genes_por_view .............. eco (a mesma lista linhas_out do mofa_preparar)
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

import pytest

from omica import contexto
from omica.config import REPO_ROOT
from omica.pipelines import carregar_pipelines
from omica.registry import load_registry
from omica.relatorio import fatos as fatos_mod
from omica.relatorio import servico
from omica.relatorio.portao_numeros import conferir_invariantes

SCRIPTS = REPO_ROOT / "modules" / "epigenomica" / "scripts"
DEMO = REPO_ROOT / "modules" / "epigenomica" / "demo_data"
CD4 = REPO_ROOT / "modules" / "epigenomica" / "data" / "amostras_cd4_demo.tsv"
PAINEL = REPO_ROOT / "modules" / "epigenomica" / "data" / "paineis" / "painel_108.json"
# o demo selado: no host (E:) ou no container (/data)
SELADOS = [Path(r"E:\data\omica\results\epigenomica\demo"), Path("/data/omica/results/epigenomica/demo")]
RUN_DMC = "epigenomica_dmc-20261029-150000-abcdef"
RUN_MOFA = "epigenomica_mofa-20261029-150000-abcdef"
ECO_DMC = {1, 7, 8, 11, 12, 13}
ECO_MOFA = {8, 9, 10, 11}
MARCA_ECO = "# eco de consistência: não conta como canário"


def selado() -> Path:
    for p in SELADOS:
        if (p / "dmc" / "todas_cpgs.tsv").is_file():
            return p
    pytest.skip("demo selado da epigenômica ausente (dado real fora do repositório)")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _py(script: str, *args: str) -> None:
    r = subprocess.run([sys.executable, str(SCRIPTS / script), *args], capture_output=True, text=True,
                       encoding="utf-8", cwd=REPO_ROOT,
                       env={**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    assert r.returncode == 0, f"{script}:\n{r.stdout}\n{r.stderr}"


def formatos_das_portas() -> dict[str, dict[str, str]]:
    m = load_registry().por_id("epigenomica")
    return {s.key: {n: p.formato for n, p in s.produz.items()} for s in m.scripts_api}


def montar_run(base: Path, pipeline: str, run_id: str, portas: dict[str, dict[str, Path]],
               escolhas: Optional[dict] = None) -> tuple[Path, dict, dict]:
    """Pasta do run (envelopes com caminho, sha256 e formato de cada porta) + plano com o
    `relatorio` do YAML real e o contexto do demo."""
    p = carregar_pipelines()[0][pipeline]
    script_do_passo = {x.id: x.script for x in p.passos}
    formatos = formatos_das_portas()
    pasta = base / "run"
    (pasta / "envelopes").mkdir(parents=True, exist_ok=True)
    for passo, ps in portas.items():
        env = {"portas": {n: {"caminho": a.as_posix(), "sha256": _sha(a),
                              "formato": formatos[script_do_passo[passo]][n]} for n, a in ps.items()}}
        (pasta / "envelopes" / f"{passo}.json").write_text(json.dumps(env), encoding="utf-8")
    plano = {"run_id": run_id, "plano_sha256": "f" * 64, "pipeline": p.id, "modulo": p.modulo,
             "escolhas": escolhas or {}, "contexto": contexto.demo().para_plano(),
             "passos": [{"id": x.id, "titulo": f"passo {x.id}", "selos": []} for x in p.passos],
             "relatorio": p.relatorio.model_dump()}
    return pasta, {"run_id": run_id, "estado": "concluido"}, plano


def ficha_de(pasta: Path, plano: dict) -> dict:
    return fatos_mod.gerar(plano, servico._envelopes(pasta, plano))


def _json(arq: Path, mudar: Callable[[dict], None]) -> None:
    d = json.loads(arq.read_text(encoding="utf-8"))
    mudar(d)
    arq.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


def _sem_ultima_linha(arq: Path) -> None:
    linhas = arq.read_text(encoding="utf-8").splitlines(keepends=True)
    arq.write_text("".join(linhas[:-1]), encoding="utf-8", newline="")


def ecos_do_yaml(pipeline: str) -> set[int]:
    """Os números (1-based) das invariantes do YAML cujo bloco de comentário logo acima traz a
    MARCA_ECO. Lê o texto (o YAML carregado perde os comentários): um item é a linha '    - ' no
    nível de `invariantes:`; os filtros da recontagem ('        - ') não contam."""
    linhas = (REPO_ROOT / "pipelines" / f"{pipeline}.yaml").read_text(encoding="utf-8").splitlines()
    i = linhas.index("  invariantes:") + 1
    ecos, n, marcada = set(), 0, False
    for linha in linhas[i:]:
        if linha.strip() and not linha.startswith("    "):
            break
        if linha.startswith("    #"):
            marcada = marcada or MARCA_ECO in linha
        elif linha.startswith("    - "):
            n += 1
            if marcada:
                ecos.add(n)
            marcada = False
    assert n == len(carregar_pipelines()[0][pipeline].relatorio.invariantes), "itens do YAML × invariantes carregadas"
    assert "\n".join(linhas).count(MARCA_ECO) == len(ecos), "marca de eco fora do bloco de uma invariante"
    return ecos


# ============================================================ epigenomica_dmc
def saidas_dmc(base: Path) -> dict[str, dict[str, Path]]:
    """montar e janelas de verdade (host); dmc, intersecao, tss e motifs = cópias do selado."""
    sel = selado()
    _py("montar_coverages.py", "--coverages", str(DEMO), "--amostras", str(CD4), "--saida", str(base / "montar"))
    _py("painel_janelas.py", "--painel", str(PAINEL), "--pad", "15000", "--saida", str(base / "janelas"))
    for sub in ("dmc", "intersecao", "tss", "motifs"):
        (base / sub).mkdir()
    copias = {"dmc": ["dmc.tsv", "todas_cpgs.tsv", "dmc_resumo.json"], "intersecao": ["intersecao.tsv",
              "intersecao_resumo.json"], "tss": ["tss_perfil.png", "tss_resumo.json"],
              "motifs": ["motifs_top.tsv", "motifs_resumo.json"]}
    for sub, nomes in copias.items():
        for n in nomes:
            shutil.copyfile(sel / sub / n, base / sub / n)
    return {
        # a porta de pasta montar.coverages não entra na ficha: o envelope do teste só leva o resumo
        "montar": {"montar_resumo": base / "montar" / "montar_resumo.json"},
        "janelas": {"bed": base / "janelas" / "janelas.bed", "janelas_resumo": base / "janelas" / "janelas_resumo.json"},
        "dmc": {"dmc": base / "dmc" / "dmc.tsv", "todas_cpgs": base / "dmc" / "todas_cpgs.tsv",
                "dmc_resumo": base / "dmc" / "dmc_resumo.json"},
        "intersecao": {"intersecao": base / "intersecao" / "intersecao.tsv",
                       "intersecao_resumo": base / "intersecao" / "intersecao_resumo.json"},
        "tss": {"tss_png": base / "tss" / "tss_perfil.png", "tss_resumo": base / "tss" / "tss_resumo.json"},
        "motifs": {"motifs_top": base / "motifs" / "motifs_top.tsv",
                   "motifs_resumo": base / "motifs" / "motifs_resumo.json"},
    }


def _run_dmc(base: Path, mexer: Optional[Callable[[dict], None]] = None,
             invariantes: Optional[Callable[[list], None]] = None) -> tuple[Path, dict, dict]:
    portas = saidas_dmc(base)
    if mexer:
        mexer(portas)   # antes do envelope: o sha é o do arquivo mexido (a invariante, não o R3, reprova)
    pasta, run, plano = montar_run(base, "epigenomica_dmc", RUN_DMC, portas, {"grupo_a": "Nelore", "grupo_b": "HF"})
    if invariantes:
        invariantes(plano["relatorio"]["invariantes"])
    return pasta, run, plano


def test_dmc_controle_13_invariantes_com_desempate(tmp_path):
    pasta, _, plano = _run_dmc(tmp_path)
    assert len(plano["relatorio"]["invariantes"]) == 13
    ficha = ficha_de(pasta, plano)
    assert conferir_invariantes(ficha) == []
    assert set(ficha["tabelas_invariantes"]) == {"dmc.todas_cpgs", "dmc.dmc", "intersecao.intersecao"}
    por_id = {f["id"]: f["valor"] for f in ficha["fatos"]}
    assert por_id["dmc.dmc_resumo.n_dmc"] == 275 and por_id["intersecao.intersecao.top1.GENE"] == "HOXA10"
    assert (por_id["montar.montar_resumo.condicao_y0"], por_id["montar.montar_resumo.n_y0"],
            por_id["montar.montar_resumo.condicao_y1"], por_id["montar.montar_resumo.n_y1"]) == ("HF", 3, "Nelore", 3)
    assert por_id["janelas.janelas_resumo.n_janelas"] == 108
    # o alerta fixo do HOMER vai na ficha
    assert len(ficha["alertas"]) == 1 and "HOMER é exploratória" in ficha["alertas"][0]
    assert [f["id"] for f in ficha["figuras"]] == ["tss.tss_png"]
    # crítica L9: nenhum fato com nome de coverage (o resumo do montar só tem contagens)
    assert not [f for f in ficha["fatos"] if isinstance(f["valor"], str) and f["valor"].endswith((".cov", ".cov.gz"))]


def test_dmc_canario_abs_maior_ou_igual_sem_desempate_reconta_276(tmp_path):
    """Adendo §A5 X3′ (2): o filtro antigo, sem desempate, reconta a CpG de |diff| = 15 exatos
    (4:68856356, ausente do dmc.tsv) e dá 276."""
    def trocar(invs):
        rec = next(i for i in invs if i["tipo"] == "recontagem")
        rec["desempate"] = None
        rec["filtros"][1] = {"coluna": "meth.diff", "abs_maior_ou_igual": rec["filtros"][1]["abs_maior_que"],
                             "abs_maior_que": None, "menor_que": None}
    pasta, _, plano = _run_dmc(tmp_path, invariantes=trocar)
    falhas = conferir_invariantes(ficha_de(pasta, plano))
    assert falhas == ["invariante 9 (recontagem): recontagem em dmc.todas_cpgs = 276 ≠ dmc.dmc_resumo.n_dmc = 275"]


@pytest.mark.parametrize("n_dmc", [274, 277])
def test_dmc_canario_desempate_com_total_errado(tmp_path, n_dmc):
    """Adendo §A5 X3′ (4): com desempate, o total 274 ou 277 reprova a recontagem (as outras
    invariantes que usam n_dmc também reprovam: não é o ponto aqui)."""
    pasta, _, plano = _run_dmc(tmp_path, lambda p: _json(p["dmc"]["dmc_resumo"], lambda d: d.update(n_dmc=n_dmc)))
    falhas = conferir_invariantes(ficha_de(pasta, plano))
    assert any(f.startswith("invariante 9 (recontagem): recontagem em dmc.todas_cpgs = 275 (certas") for f in falhas), falhas


def test_dmc_canario_desempate_com_chave_ausente(tmp_path):
    """Adendo §A5 X3′ (5): uma linha do dmc.tsv sem par em todas_cpgs reprova "ausente"."""
    def mexer(p):
        with open(p["dmc"]["dmc"], "a", encoding="utf-8", newline="") as f:
            f.write("29\t1\t1\t*\t0.001\t0.01\t20\n")
    pasta, _, plano = _run_dmc(tmp_path, mexer)
    falhas = conferir_invariantes(ficha_de(pasta, plano))
    assert "invariante 9 (recontagem): 1 linha(s) de dmc.dmc ausente(s) em dmc.todas_cpgs" in falhas, falhas


def test_dmc_borda_decidida_pelo_methylkit_pede_o_desempate(tmp_path):
    """Por que o YAML declara `desempate: dmc.dmc`: o texto '15' não diz de que lado do limite o
    double do methylKit estava. Se a ferramenta tivesse posto a CpG de borda no dmc.tsv (n_dmc
    276), a recontagem exata ('>') daria 275 e reprovaria um resultado certo; com o desempate, a
    borda conta porque está no dmc.tsv. Controle: o YAML real aprova; canário: o mesmo dado sem
    o desempate reprova."""
    def borda_no_dmc(p):
        with open(p["dmc"]["dmc"], "a", encoding="utf-8", newline="") as f:
            f.write("4\t68856356\t68856356\t*\t0.00300441202929259\t0.0370322328876295\t15\n")
        _json(p["dmc"]["dmc_resumo"], lambda d: d.update(n_dmc=276, n_hiper_no_grupo_a=186))
        _json(p["intersecao"]["intersecao_resumo"], lambda d: d.update(n_dmc_total=276))
    pasta, _, plano = _run_dmc(tmp_path, borda_no_dmc)
    assert conferir_invariantes(ficha_de(pasta, plano)) == []
    rec = next(i for i in plano["relatorio"]["invariantes"] if i["tipo"] == "recontagem")
    rec["desempate"] = None
    falhas = conferir_invariantes(ficha_de(pasta, plano))
    assert falhas == ["invariante 9 (recontagem): recontagem em dmc.todas_cpgs = 275 ≠ dmc.dmc_resumo.n_dmc = 276"]


def _qvalue_de_uma_dmc(p: dict) -> None:
    """Uma DMC certa (q 6,7e-07) passa a ter q = 0,9 em todas_cpgs: a recontagem dá 274."""
    arq = p["dmc"]["todas_cpgs"]
    linhas = arq.read_text(encoding="utf-8").splitlines(keepends=True)
    i = next(i for i, x in enumerate(linhas) if x.startswith("1\t95029131\t"))
    c = linhas[i].split("\t")
    c[5] = "0.9"
    linhas[i] = "\t".join(c)
    arq.write_text("".join(linhas), encoding="utf-8", newline="")


CANARIOS_DMC = {
    2: lambda p: _json(p["intersecao"]["intersecao_resumo"], lambda d: d.update(n_dmc_total=274)),
    3: lambda p: _json(p["montar"]["montar_resumo"], lambda d: d.update(n_amostras=7)),
    4: lambda p: _json(p["tss"]["tss_resumo"], lambda d: d["grupo_a"].update(n_amostras=2)),
    5: lambda p: _json(p["tss"]["tss_resumo"], lambda d: d["grupo_b"].update(n_amostras=2)),
    6: lambda p: _json(p["intersecao"]["intersecao_resumo"], lambda d: d.update(n_genes_painel=107)),
    9: _qvalue_de_uma_dmc,
    # o calculateDiffMeth devolvendo uma CpG a menos que o unite (n_cpg_testados = nrow(unido))
    10: lambda p: _json(p["dmc"]["dmc_resumo"], lambda d: d.update(n_cpg_testados=3713)),
}


def test_dmc_canarios_cobrem_toda_invariante_que_nao_e_eco():
    """As ecos (1, 7, 8, 11, 12, 13) não têm canário: nenhum script real as faz reprovar. A marca
    no YAML fica exatamente sobre elas (lida pela posição, não só contada)."""
    p = carregar_pipelines()[0]["epigenomica_dmc"]
    assert ecos_do_yaml("epigenomica_dmc") == ECO_DMC
    assert set(CANARIOS_DMC) == set(range(1, len(p.relatorio.invariantes) + 1)) - ECO_DMC


@pytest.mark.parametrize("i", sorted(CANARIOS_DMC))
def test_dmc_canario_por_invariante(tmp_path, i):
    pasta, _, plano = _run_dmc(tmp_path, CANARIOS_DMC[i])
    falhas = conferir_invariantes(ficha_de(pasta, plano))
    assert len(falhas) == 1 and falhas[0].startswith(f"invariante {i} ("), falhas


def test_dmc_pedido_traz_o_alerta_do_homer_e_o_aviso_do_painel(tmp_path, monkeypatch, cfg):
    """Adendo §A5 X5′: a mensagem ao redator de um epigenomica_dmc no demo traz o aviso do
    painel_108 (ALERTAS DO SUBPROJETO) e o alerta fixo do HOMER (ALERTAS)."""
    pasta, run, plano = _run_dmc(tmp_path)
    monkeypatch.setattr(servico, "_pastas", lambda run_id: (pasta, run, plano))
    msg = servico.pedido(RUN_DMC)["mensagem"]
    assert "ALERTAS DO SUBPROJETO" in msg and "painel_108 v1" in msg
    assert "A busca de motifs do HOMER é exploratória" in msg
    assert "Contexto: bos_taurus/demo" in msg


# ============================================================ epigenomica_mofa
def saidas_mofa(montar: Path, views: Path, mofa: Path) -> dict[str, dict[str, Path]]:
    return {
        "montar": {"montar_resumo": montar / "montar_resumo.json"},
        "views": {"view_promotor": views / "view_promotor.tsv", "view_corpo": views / "view_corpo.tsv",
                  "views_resumo": views / "views_resumo.json"},
        "mofa": {"variancia": mofa / "variancia_explicada.tsv", "fatores": mofa / "fatores_por_amostra.tsv",
                 "mofa_png": mofa / "mofa_fatores.png", "mofa_resumo": mofa / "mofa_resumo.json"},
    }


def ficha_mofa(base: Path, portas: dict[str, dict[str, Path]]) -> tuple[Path, dict, dict]:
    """O run do epigenomica_mofa sobre saídas já gravadas (usado também pelo smoke no worker)."""
    return montar_run(base, "epigenomica_mofa", RUN_MOFA, portas)


# Um coldata que mistura as raças nas duas condições: Lote1 = HF 317, HF 318, Nelore 323;
# Lote2 = HF 319, Nelore 324, Nelore 325. Os nomes continuam com 'HF'/'Nelore', e o grupos.tsv
# do mofa_preparar selado (que rotula pelo nome) dá 3 HF / 3 Nelore — as MESMAS contagens 3/3
# do montar (Lote1/Lote2): a 5 e a 6 não veem a troca.
LOTES = {"FILTERED_H82_HF_ERS4410317_CD4": "Lote1", "FILTERED_H82_HF_ERS4410318_CD4": "Lote1",
         "FILTERED_H82_Nelore_ERS4410323_CD4": "Lote1", "FILTERED_H82_HF_ERS4410319_CD4": "Lote2",
         "FILTERED_H82_Nelore_ERS4410324_CD4": "Lote2", "FILTERED_H82_Nelore_ERS4410325_CD4": "Lote2"}


def _coldata_lotes(base: Path) -> Path:
    arq = base / "amostras_lotes.tsv"
    arq.write_text("amostra\tcondicao\n" + "".join(f"{a}\t{c}\n" for a, c in LOTES.items()), encoding="utf-8")
    return arq


def rotulo_do_grupos_tsv(views: Path, _certo: Callable) -> Callable:
    """O defeito M8: o mofa_treinar_api religado ao grupos.tsv do mofa_preparar selado ('Nelore'
    no nome → Nelore, o resto → HF) em vez do coldata."""
    def condicoes_das_colunas(colunas, pares):
        grupo = dict(x.split("\t") for x in (views / "grupos.tsv").read_text(encoding="utf-8").splitlines()[1:])
        return [(c, grupo[c]) for c in colunas]
    return condicoes_das_colunas


def rotulo_invertido(_views: Path, certo: Callable) -> Callable:
    """A coluna i recebe a condição da coluna n−1−i (o mapeamento certo, na ordem errada)."""
    def condicoes_das_colunas(colunas, pares):
        r = certo(colunas, pares)
        return [(a, c) for (a, _), (_, c) in zip(r, reversed(r))]
    return condicoes_das_colunas


def _saidas_mofa_host(base: Path, monkeypatch, pasta: Path = DEMO, coldata: Path = CD4,
                      rotular: Optional[Callable] = None) -> dict[str, dict[str, Path]]:
    """montar e views de verdade; mofa_treinar_api com o treino trocado por um Z fixo. `rotular`
    (views, mapeamento certo) → mapeamento mutante, para os canários do rótulo."""
    np = pytest.importorskip("numpy")
    pytest.importorskip("matplotlib")
    from test_montar_coverages import carregar

    _py("montar_coverages.py", "--coverages", str(pasta), "--amostras", str(coldata), "--saida", str(base / "montar"))
    _py("mofa_preparar.py", "--dir", str(base / "montar" / "coverages"), "--painel", str(PAINEL), "--promotor",
        "2000", "--min-amostras", "4", "--saida", str(base / "views"))
    mapi = carregar(SCRIPTS / "mofa_treinar_api.py")
    Z = np.array([[0.5, 0.1, 0.0]] * 3 + [[-0.7, -0.1, 0.0]] * 3)
    monkeypatch.setattr(mapi, "treinar", lambda matrizes, amostras, fp, fc, args: (
        Z, {"promotor": [0.08, 8.35, 0.0], "corpo": [7.3, 0.14, 0.0]}))
    if rotular:
        monkeypatch.setattr(mapi, "condicoes_das_colunas", rotular(base / "views", mapi.condicoes_das_colunas))
    monkeypatch.setattr(sys, "argv", ["mofa_treinar_api.py", "--promotor", str(base / "views" / "view_promotor.tsv"),
                                      "--corpo", str(base / "views" / "view_corpo.tsv"), "--amostras", str(coldata),
                                      "--fatores", "3", "--saida", str(base / "mofa")])
    mapi.main()
    return saidas_mofa(base / "montar", base / "views", base / "mofa")


def test_mofa_controle_11_invariantes(tmp_path, monkeypatch):
    portas = _saidas_mofa_host(tmp_path, monkeypatch)
    pasta, _, plano = ficha_mofa(tmp_path, portas)
    assert len(plano["relatorio"]["invariantes"]) == 11
    ficha = ficha_de(pasta, plano)
    assert conferir_invariantes(ficha) == []
    por_id = {f["id"]: f["valor"] for f in ficha["fatos"]}
    assert (por_id["views.views_resumo.genes_por_view.promotor"], por_id["views.views_resumo.genes_por_view.corpo"]) \
        == (58, 55)
    assert (por_id["mofa.mofa_resumo.condicao_y0"], por_id["mofa.mofa_resumo.n_y0"], por_id["mofa.mofa_resumo.n_y1"]) \
        == ("HF", 3, 3)
    assert por_id["mofa.mofa_resumo.n_rotulo_igual_prefixo"] == por_id["montar.montar_resumo.n_amostras"] == 6
    assert por_id["mofa.variancia.top2.promotor"] == 8.35
    assert ficha["alertas"] == [] and [f["id"] for f in ficha["figuras"]] == ["mofa.mofa_png"]


CANARIOS_MOFA = {
    # o MOFA sem uma coluna: o resumo e a tabela dos fatores (a mesma lista) dizem 5; só a 1 reprova
    1: lambda p: (_json(p["mofa"]["mofa_resumo"], lambda d: d.update(n_amostras=5)),
                  _sem_ultima_linha(p["mofa"]["fatores"])),
    2: lambda p: _json(p["mofa"]["mofa_resumo"], lambda d: d["genes_por_view"].update(promotor=57)),
    3: lambda p: _json(p["mofa"]["mofa_resumo"], lambda d: d["genes_por_view"].update(corpo=54)),
    4: lambda p: _json(p["montar"]["montar_resumo"], lambda d: d.update(n_amostras=7)),
    5: lambda p: _json(p["montar"]["montar_resumo"], lambda d: d.update(n_y0=2)),
    6: lambda p: _json(p["montar"]["montar_resumo"], lambda d: d.update(n_y1=4)),
    7: lambda p: _json(p["mofa"]["mofa_resumo"], lambda d: d.update(n_rotulo_igual_prefixo=5)),
}
# o canário que também reprova outra invariante real pelo MESMO número: a 7 compara com o mesmo
# montar.n_amostras da 4
TAMBEM_MOFA = {4: {7}}


def _numeros(falhas: list[str]) -> list[int]:
    return [int(f.split(" (")[0].removeprefix("invariante ")) for f in falhas]


@pytest.mark.parametrize("i", sorted(CANARIOS_MOFA))
def test_mofa_canario_por_invariante(tmp_path, monkeypatch, i):
    portas = _saidas_mofa_host(tmp_path, monkeypatch)
    CANARIOS_MOFA[i](portas)
    pasta, _, plano = ficha_mofa(tmp_path, portas)
    falhas = conferir_invariantes(ficha_de(pasta, plano))
    assert _numeros(falhas) == sorted({i} | TAMBEM_MOFA.get(i, set())), falhas


@pytest.mark.parametrize("rotular", [rotulo_do_grupos_tsv, rotulo_invertido], ids=["grupos_tsv_M8", "invertido"])
def test_mofa_canario_rotulo_trocado_que_preserva_as_contagens(tmp_path, monkeypatch, rotular):
    """O defeito que a 5 e a 6 NÃO pegam (verificador da F6): o rótulo trocado com as mesmas
    contagens. M8 = o mofa_treinar_api religado ao grupos.tsv do selado, com o coldata Lote1 ×
    Lote2 que mistura HF e Nelore (o grupos.tsv dá HF/Nelore 3/3, o montar Lote1/Lote2 3/3);
    invertido = o mapeamento certo na ordem errada, com o coldata do demo. Só a 7 reprova:
    nenhuma coluna tem a condição do prefixo do nome. Controle: o MESMO coldata com o mofa
    certo aprova as 11 (test_mofa_controle_lotes_e_cd4_renomeado)."""
    coldata = _coldata_lotes(tmp_path) if rotular is rotulo_do_grupos_tsv else CD4
    portas = _saidas_mofa_host(tmp_path, monkeypatch, coldata=coldata, rotular=rotular)
    m = json.loads(portas["mofa"]["mofa_resumo"].read_text(encoding="utf-8"))
    mo = json.loads(portas["montar"]["montar_resumo"].read_text(encoding="utf-8"))
    assert (m["n_y0"], m["n_y1"]) == (mo["n_y0"], mo["n_y1"]) == (3, 3)   # a 5 e a 6 não veem
    pasta, _, plano = ficha_mofa(tmp_path, portas)
    falhas = conferir_invariantes(ficha_de(pasta, plano))
    assert falhas == ["invariante 7 (igual): mofa.mofa_resumo.n_rotulo_igual_prefixo = 0 ≠ "
                      "montar.montar_resumo.n_amostras = 6"], falhas


@pytest.mark.parametrize("caso", ["lotes", "cd4_renomeado"])
def test_mofa_controle_lotes_e_cd4_renomeado(tmp_path, monkeypatch, caso):
    """Controle do canário acima: com o rótulo do coldata (o mofa_treinar_api como está), o
    coldata Lote1 × Lote2 e o "CD4 renomeado" (S1–S6, Holandes × Zebu; o grupos.tsv diria 6 × HF)
    fecham as 11 invariantes, com n_rotulo_igual_prefixo = 6."""
    if caso == "lotes":
        pasta, coldata, y = DEMO, _coldata_lotes(tmp_path), ("Lote1", "Lote2")
    else:
        from test_mofa_treinar_api import cd4_renomeado

        (tmp_path / "entrada").mkdir()
        pasta, coldata = cd4_renomeado(tmp_path / "entrada")
        y = ("Holandes", "Zebu")
    portas = _saidas_mofa_host(tmp_path, monkeypatch, pasta=pasta, coldata=coldata)
    m = json.loads(portas["mofa"]["mofa_resumo"].read_text(encoding="utf-8"))
    assert (m["condicao_y0"], m["n_y0"], m["condicao_y1"], m["n_y1"], m["n_rotulo_igual_prefixo"]) == (
        y[0], 3, y[1], 3, 6)
    pasta_run, _, plano = ficha_mofa(tmp_path, portas)
    assert conferir_invariantes(ficha_de(pasta_run, plano)) == []


def test_mofa_canarios_cobrem_toda_invariante_que_nao_e_eco():
    """As ecos (8-11: as tabelas que o MESMO script escreveu da MESMA variável) não têm canário."""
    p = carregar_pipelines()[0]["epigenomica_mofa"]
    assert ecos_do_yaml("epigenomica_mofa") == ECO_MOFA
    assert set(CANARIOS_MOFA) == set(range(1, len(p.relatorio.invariantes) + 1)) - ECO_MOFA


# ============================================================ §A7: carga dos YAMLs
def test_carga_dos_dois_pipelines_e_do_registry():
    """Adendo §A7: registry sem erro, pipelines sem erro, só bos_taurus (o default), e o
    contrato fechado dos dois."""
    reg = load_registry()
    assert reg.erros == {}
    pls, erros = carregar_pipelines()
    assert erros == {}
    for pid in ("epigenomica_dmc", "epigenomica_mofa"):
        assert pls[pid].especies == ["bos_taurus"] and pls[pid].modulo == "epigenomica"
        assert pls[pid].entradas["coverages"].modos == ["copia"] and not pls[pid].entradas["coverages"].adaptavel
    dmc = pls["epigenomica_dmc"]
    assert [c.tipo for c in dmc.checagens_entrada] == ["coverages_e_amostras", "coverages_e_painel",
                                                       "coverages_contigs_ars_ucd"]
    assert "coverages_contigs_ars_ucd" not in [c.tipo for c in pls["epigenomica_mofa"].checagens_entrada]
    assert dmc.escolhas_autor["grupo_b"].diferente_de == "grupo_a"
    assert [x.id for x in dmc.passos][-1] == "motifs"   # o HOMER é o último (relatório exige todos)


@pytest.mark.parametrize("pid", ["epigenomica_dmc", "epigenomica_mofa"])
def test_checagens_aprovam_os_exemplos(pid):
    """Controle das checagens com os `exemplo` do YAML (o que o formulário mostra): demo ×
    amostras_cd4_demo.tsv × painel_108.json — 27 cromossomos em comum, 0 arquivos sem amostra.
    Os canários de cada checagem estão em tests/test_formatos_epigenomica.py (F1a)."""
    from omica.contrato import formatos

    p = carregar_pipelines()[0][pid]
    for c in p.checagens_entrada:
        func, campos = formatos.CHECAGENS[c.tipo]
        args = [REPO_ROOT / p.entradas[getattr(c, campo)].exemplo for campo in campos]
        v = func(*args)
        assert v.ok, (c.tipo, v.falhas)
        if c.tipo == "coverages_e_amostras":
            assert v.perfil["n_arquivos_sem_amostra"] == 0 and v.perfil["por_condicao"] == {"HF": 3, "Nelore": 3}
        if c.tipo == "coverages_e_painel":
            assert v.perfil["n_cromossomos_comuns"] == 27


# ============================================================ plano do pipeline REAL
SUB_EPI = ("bos_taurus", "epi_f3")


@pytest.fixture()
def api(monkeypatch, cfg):
    """O cliente da API de tests/test_runs.py, com o import DENTRO da fixture: o smoke do worker
    methyl (sem fastapi) importa este arquivo pelos helpers da ficha."""
    from fastapi.testclient import TestClient

    from omica.config import reset_config_cache
    from test_runs import TOKEN

    monkeypatch.setenv("OMICA_API_TOKEN", TOKEN)
    reset_config_cache()
    from omica.web.app import app

    c = TestClient(app)
    c.headers.update({"Authorization": f"Bearer {TOKEN}"})
    return c


def _run_epi(api, cfg, pipeline: str, coverages: str, amostras: str, painel: str) -> tuple[str, list]:
    from fixtures_v2 import escrever_subprojeto

    escrever_subprojeto(cfg.projetos_dir, *SUB_EPI, assembly="ARS-UCD1.3", modulos=["epigenomica"],
                        pipelines=["epigenomica_dmc", "epigenomica_mofa"])
    r = api.post("/api/v1/runs", json={"pipeline": pipeline, "especie": SUB_EPI[0], "subprojeto": SUB_EPI[1]})
    assert r.status_code == 201, r.text
    rid = r.json()["run_id"]
    checagens: list = []
    for nome, caminho in (("coverages", coverages), ("amostras", amostras), ("painel", painel)):
        r = api.put(f"/api/v1/runs/{rid}/entradas/{nome}", json={"caminho": caminho})
        assert r.status_code == 200, r.text
        assert r.json()["entradas"][nome]["veredito"]["ok"], r.json()["entradas"][nome]["veredito"]
        checagens = r.json()["checagens"]
    return rid, checagens


def test_plano_do_epigenomica_dmc_com_o_demo(api, cfg):
    """O pipeline real no núcleo do run (F1b): a pasta do demo por caminho, o coldata e o painel
    do repositório; as 3 checagens aprovam; o plano encadeia a PORTA de pasta do montar no DMC e
    no TSS, e o argv do motifs leva o genoma e o preparse do CONTEXTO (adendo §A11, EPI-A).
    Canário: grupo_a == grupo_b → 422 escolhas_iguais."""
    rid, checagens = _run_epi(api, cfg, "epigenomica_dmc", "modules/epigenomica/demo_data",
                              "modules/epigenomica/data/amostras_cd4_demo.tsv",
                              "modules/epigenomica/data/paineis/painel_108.json")
    assert [(c["formato"], c["ok"]) for c in checagens] == [
        ("coverages_e_amostras", True), ("coverages_e_painel", True), ("coverages_contigs_ars_ucd", True)]
    r = api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {"grupo_a": "Nelore", "grupo_b": "Nelore"}})
    assert r.status_code == 422 and r.json()["detail"]["erro"] == "escolhas_iguais", r.text
    r = api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {"grupo_a": "Nelore", "grupo_b": "HF"}})
    assert r.status_code == 200, r.text
    passos = {p["id"]: p for p in r.json()["passos"]}
    assert list(passos) == ["montar", "janelas", "dmc", "intersecao", "tss", "motifs"]
    pasta_montar = passos["montar"]["saida"] + "/coverages"
    assert passos["dmc"]["valores"]["coverages"] == pasta_montar == passos["tss"]["valores"]["coverages"]
    assert passos["intersecao"]["valores"]["painel"] == passos["janelas"]["saida"] + "/janelas.bed"
    assert (passos["dmc"]["valores"]["grupo_a"], passos["dmc"]["valores"]["grupo_b"]) == ("Nelore", "HF")
    argv = passos["motifs"]["argv"]
    assert argv[argv.index("--genoma") + 1] == "/data/snpeff_data/ARS-UCD1.3.113/sequences.fa"
    assert argv[argv.index("--preparsed") + 1] == "/data/omica/refs/homer_preparsed"
    assert passos["montar"]["portas"]["coverages"]["tipo"] == "diretorio"


def _pasta_chr(cfg) -> tuple[str, str, str]:
    """Coverages com 'chr' (como o W15, GSE161113) na caixa de entrada, coldata A × B e um
    painel também em 'chr' (para a checagem de painel aprovar e sobrar só a de contig)."""
    base = cfg.data_dir / "entradas" / "w15_chr"
    base.mkdir(parents=True)
    import gzip as _gzip

    for i, a in enumerate(("A01", "A02", "N01", "N02")):
        linhas = "".join(f"chr1\t{100 + 10 * k + i}\t{100 + 10 * k + i}\t75.0\t3\t1\n" for k in range(5))
        (base / f"{a}.cov.gz").write_bytes(_gzip.compress(linhas.encode("ascii"), mtime=0))
    coldata = cfg.data_dir / "entradas" / "w15_amostras.tsv"
    coldata.write_text("amostra\tcondicao\nA01\tA\nA02\tA\nN01\tN\nN02\tN\n", encoding="utf-8")
    painel = cfg.data_dir / "entradas" / "painel_chr.json"
    painel.write_text(json.dumps({"genes": {"G1": {"cromossomo": "chr1", "inicio": 50, "fim": 400, "fita": 1}}}),
                      encoding="utf-8")
    return base.as_posix(), coldata.as_posix(), painel.as_posix()


def test_coverages_chr_reprovam_no_dmc_e_rodam_no_mofa(api, cfg):
    """Decisão 25 / R14 (crítica M7): o portão de contig do HOMER existe só no epigenomica_dmc.
    Canário: coverages 'chr*' reprovam ali antes do plano ("prefixo 'chr'", plano 409);
    controle: o mesmo dado passa nas checagens do epigenomica_mofa e o plano sai."""
    cov, amostras, painel = _pasta_chr(cfg)
    rid, checagens = _run_epi(api, cfg, "epigenomica_dmc", cov, amostras, painel)
    falhas = {c["formato"]: c for c in checagens if not c["ok"]}
    assert list(falhas) == ["coverages_contigs_ars_ucd"], checagens
    assert any("prefixo 'chr'" in f for f in falhas["coverages_contigs_ars_ucd"]["falhas"])
    r = api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {"grupo_a": "A", "grupo_b": "N"}})
    assert r.status_code == 409, r.text
    rid, checagens = _run_epi(api, cfg, "epigenomica_mofa", cov, amostras, painel)
    assert all(c["ok"] for c in checagens), checagens
    r = api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {}})
    assert r.status_code == 200, r.text


def test_selos_continuam_cinco_vigentes():
    """Adendo §A7: o scripts_api da epigenômica não entra no hash nem tem linha de selo —
    `python -m omica selos` sai com 0 e os 5 selos continuam válidos."""
    r = subprocess.run([sys.executable, "-m", "omica", "selos"], capture_output=True, text=True, encoding="utf-8",
                       cwd=REPO_ROOT, env={**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    assert r.returncode == 0, r.stdout + r.stderr
    validos = [x for x in r.stdout.splitlines() if "válido —" in x]
    assert len(validos) == 5 and all(s in r.stdout for s in (
        "46124beee606ccc6", "89df0a07439b5f96", "ef8e5355bc92acc7", "ef51927233d11997", "9563a9c0e7dbc8de"))
