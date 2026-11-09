"""Relatório do pipeline ml_aims_vcf (F6: ESPEC §3.1.3 + adendo v2 §A8.3, críticas L5/M3).

A ficha é montada com envelopes que apontam para CÓPIAS dos arquivos selados do demo
(E:\\data\\omica\\results\\ml\\demo: dataset, classificacao, robustez; skip sem o E:\\data) mais os
resumos sintéticos do contraste (a saída do ml_contraste.py sobre a tabela de exemplo, Angus ×
Brahman) e do diagnóstico (os valores medidos no worker omica-ml, test_scripts_smoke_ml.py).
Com eles as 19 invariantes passam. Seis são eco de consistência (ECO_ML: 8-11, 16 e 19): os dois
lados saem da mesma conta do mesmo script e só divergiriam se o próprio script mudasse — ficam
marcadas no YAML com '# eco de consistência: não conta como canário' e sem canário. Cada uma das
outras 13 tem o seu canário (o resumo ou a tabela da porta alterados) que a reprova, e a mutação
que a tira do plano faz o canário passar — prova de que é ELA que morde.

Invariante → a falha real que ela pega ou, no eco, por que não pega nada (a invariante-eco do k,
diagnostico.k_painel = classificar.k_painel_minimo, mesmo parâmetro dos dois lados, saiu do
YAML — crítica L5):
   1. grupo_a.n + grupo_b.n = classificar.n_amostras — o classificador leu rótulos de outra
      extração (o .loc pegou outro conjunto de amostras) ou o dataset gravou rótulos a mais;
   2. dataset.n_variantes_matriz = classificar.n_variantes — o SimpleImputer do classificador
      descartou marcador todo NA (as colunas deixam de casar com os nomes) ou a matriz trocou;
   3. contraste.n_a = dataset.grupo_a.n — amostra rotulada que o dataset descartou em silêncio
      por não estar no VCF (ml_dataset_genotipos.py:60-63);
   4. contraste.n_b = dataset.grupo_b.n — idem para a classe B;
   5. diagnostico.n_marcadores = dataset.n_variantes_matriz — diagnóstico sobre outra matriz
      (os alertas falariam de outro dado);
   6. diagnostico.n_amostras = classificar.n_amostras — diagnóstico e classificador sobre
      conjuntos de amostras diferentes;
   7. robustez.k_painel = classificar.k_painel_minimo — matriz com menos de k marcadores (o
      robustez limita k, ml_robustez.py:65; o classificador não, ml_classificar.py:61) ou k
      divergente entre os passos;
   8-11. Σ de cada matriz de confusão = n_amostras — eco: o confusion_matrix(y, y_pred) do
      ml_classificar é sobre o mesmo y cujo tamanho é o n_amostras, e os 5 folds externos do
      StratifiedKFold são uma partição (cada amostra é predita uma vez, por construção);
  12-15. Σ da linha 0 / linha 1 da confusão (painel completo, RF e XGB) = n_y0 / n_y1 do
      contraste — classe positiva trocada (y = 1 não é a alfabeticamente maior) ou rótulos de
      outro contraste. Ponto cego: com n_y0 == n_y1 a troca não muda as somas das linhas e as
      quatro não veem nada; a orientação fica com a sincronia textual entre o ml_contraste.py
      (y0, y1 = min, max) e o ml_classificar.py:86-87 (y = 1 para sorted(...)[1]), em
      tests/test_ml_contraste.py;
  16. linhas de dataset.matriz = n_variantes_matriz — eco: n_mantidas é contado no mesmo laço
      que escreve cada linha do genotipos.tsv (ml_dataset_genotipos.py:82-92); uma troca depois
      da entrega reprova pelo sha256 do envelope (test_tabela_trocada_depois_do_envelope_reprova),
      não pela contagem;
  17. linhas de dataset.rotulos = classificar.n_amostras — tabela de rótulos com amostra a mais
      ou a menos do que o classificador usou;
  18. linhas de classificar.shap_top = k_painel_minimo — matriz com menos de k marcadores: a
      tabela SHAP sai mais curta que o painel que o resumo declara;
  19. linhas de robustez.nucleo_estavel = robustez.k_painel — eco: o nucleo_estavel.tsv é
      sorted(freq)[:k], freq tem um marcador por linha da matriz e k = min(k_painel, nº de
      marcadores) (ml_robustez.py:65,78,118).

Mais: a regra das marcas de eco conferida no texto do YAML (controle e canários), os alertas do
diagnóstico (canário e controle; o texto sem número solto, que o R1 reprovaria quando o redator
copia o alerta nas limitações), os selos por passo, o pedido ao redator e, pela API, o 422 da
`regiao` fora do demo (M3) e o 422 de classes iguais.
"""
from __future__ import annotations

import copy
import functools
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Callable, Optional

import pytest

from fixtures_v2 import escrever_subprojeto
from omica import runs
from omica.config import REPO_ROOT
from omica.pipelines import carregar_pipelines
from omica.registry import load_registry
from omica.relatorio import fatos as fatos_mod
from omica.relatorio import servico
from omica.relatorio.portao_numeros import NUMERO_SOLTO, conferir, conferir_invariantes
from test_formatos_ml import _rotulos, _vcf_com_amostras
from test_runs import api  # noqa: F401 (fixture)
from test_scripts_smoke_ml import DIAGNOSTICO_DEMO

E_ML = Path(r"E:\data\omica\results\ml\demo")
RUN = "ml_aims_vcf-20261029-000000-abcdef"
PID = "ml_aims_vcf"
# porta → (arquivo selado relativo a E_ML, formato do manifest)
SELADOS = {
    "dataset": {"matriz": ("dataset/genotipos.tsv", "matriz_marcadores"),
                "rotulos": ("dataset/rotulos.tsv", "rotulos_grupos"),
                "dataset_resumo": ("dataset/dataset_resumo.json", "resumo_json")},
    "classificar": {"ml_resumo": ("classificacao/ml_resumo.json", "resumo_json"),
                    "shap_top": ("classificacao/shap_top.tsv", "tabela_shap"),
                    "shap_png": ("classificacao/shap_top.png", "figura_png")},
    "robustez": {"robustez_resumo": ("robustez/robustez_resumo.json", "resumo_json"),
                 "nucleo_estavel": ("robustez/nucleo_estavel.tsv", "tabela_nucleo_estavel"),
                 "robustez_png": ("robustez/robustez.png", "figura_png")},
}
# a saída do ml_contraste.py sobre modules/ml/data/rotulos_1000bg_racas.tsv (test_ml_contraste)
CONTRASTE = {"gerado_em": "2026-10-29T00:00:00", "classe_a": "Angus", "classe_b": "Brahman",
             "n_a": 102, "n_b": 55, "classe_y0": "Angus", "classe_y1": "Brahman", "n_y0": 102,
             "n_y1": 55, "n_niveis_tabela": 16, "minimo": 10}
DIAGNOSTICO = {"gerado_em": "2026-10-29T00:00:00", **DIAGNOSTICO_DEMO}
SEM_SELO = {"contraste", "diagnostico", "robustez"}
YAML = REPO_ROOT / "pipelines" / "ml_aims_vcf.yaml"
MARCA_ECO = "# eco de consistência: não conta como canário"
# os ecos de consistência (o porquê de cada um está no docstring e no comentário do YAML)
ECO_ML = {8, 9, 10, 11, 16, 19}

ALERTA_EMPATE = "empates em importância zero"
ALERTA_SEPARADOR = "separa as duas classes sozinho"


def _pipeline():
    validos, erros = carregar_pipelines()
    assert erros == {}, erros
    return validos[PID]


def _exige_selados() -> None:
    if not E_ML.exists():
        pytest.skip("saídas seladas do demo ML ausentes (dado real fora do repositório)")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@functools.lru_cache(maxsize=None)
def _selos_do_script(script: str) -> str:
    """O mesmo cálculo do plano real (runs._selos_relacionados): workflow selado do módulo que
    roda o MESMO arquivo de script. Em cache: o selos.conferir re-hasheia os scripts a cada
    chamada, e cada canário monta o run duas vezes."""
    m = load_registry().por_id("ml")
    spec = next(s for s in m.scripts_api if s.key == script)
    return json.dumps(runs._selos_relacionados(m, spec))


def _selos_do_passo(passo) -> list[dict]:
    return json.loads(_selos_do_script(passo.script))


def _montar(tmp_path: Path, *, resumos: Optional[Callable[[dict], None]] = None,
            tabelas: Optional[Callable[[dict], None]] = None,
            sem_invariante: Optional[int] = None) -> tuple[Path, dict, dict]:
    """Pasta do run (envelopes) + plano com o `relatorio` do YAML real. `resumos(r)` altera os
    resumos ({passo: dict}) antes de gravar; `tabelas(t)` altera as cópias das portas de tabela
    ({"passo.porta": Path}) antes do sha256 do envelope; `sem_invariante` (1-based) tira uma
    invariante do plano (a mutação que prova o canário)."""
    _exige_selados()
    p = _pipeline()
    saida = tmp_path / "saidas"
    dados = {"contraste": copy.deepcopy(CONTRASTE), "diagnostico": copy.deepcopy(DIAGNOSTICO)}
    portas: dict[str, dict[str, tuple[Path, str]]] = {}
    for passo, ps in SELADOS.items():
        for porta, (rel, formato) in ps.items():
            if formato == "resumo_json":
                dados[passo] = json.loads((E_ML / rel).read_text(encoding="utf-8"))
                continue
            destino = saida / passo / Path(rel).name
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(E_ML / rel, destino)
            portas.setdefault(passo, {})[porta] = (destino, formato)
    if resumos:
        resumos(dados)
    if tabelas:
        tabelas({f"{passo}.{porta}": arq for passo, ps in portas.items() for porta, (arq, _) in ps.items()})
    nome_resumo = {"contraste": "contraste_resumo", "dataset": "dataset_resumo", "diagnostico": "diagnostico_resumo",
                   "classificar": "ml_resumo", "robustez": "robustez_resumo"}
    for passo, d in dados.items():
        arq = saida / passo / f"{nome_resumo[passo]}.json"
        arq.parent.mkdir(parents=True, exist_ok=True)
        arq.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        portas.setdefault(passo, {})[nome_resumo[passo]] = (arq, "resumo_json")
    pasta = tmp_path / "run"
    (pasta / "envelopes").mkdir(parents=True)
    for passo, ps in portas.items():
        env = {"portas": {k: {"caminho": a.as_posix(), "sha256": _sha(a), "formato": f} for k, (a, f) in ps.items()}}
        (pasta / "envelopes" / f"{passo}.json").write_text(json.dumps(env), encoding="utf-8")
    rel = p.relatorio.model_dump()
    if sem_invariante is not None:
        del rel["invariantes"][sem_invariante - 1]
    plano = {"run_id": RUN, "plano_sha256": "f" * 64, "pipeline": p.id, "modulo": p.modulo,
             "escolhas": {"classe_a": "Angus", "classe_b": "Brahman"},
             "passos": [{"id": x.id, "titulo": f"passo {x.id}", "selos": _selos_do_passo(x)} for x in p.passos],
             "relatorio": rel}
    return pasta, {"run_id": RUN, "estado": "concluido"}, plano


def _ficha(tmp_path: Path, **kw) -> dict:
    pasta, _, plano = _montar(tmp_path, **kw)
    return fatos_mod.gerar(plano, servico._envelopes(pasta, plano))


def _reprovadas(falhas: list[str]) -> set[int]:
    return {int(re.match(r"invariante (\d+) ", f).group(1)) for f in falhas}


# ================================================================ o contrato
def test_pipeline_carrega_com_19_invariantes_e_sem_o_eco_do_k():
    """§A7/§A8.3: carregado sem erro, só bos_taurus, `regiao` fora de params (M3), k com fonte
    única no diagnóstico, e a invariante-eco do k (L5) fora."""
    p = _pipeline()
    assert p.especies == ["bos_taurus"] and p.modulo == "ml"
    assert [x.id for x in p.passos] == ["contraste", "dataset", "diagnostico", "classificar", "robustez"]
    params = {x.id: x.params for x in p.passos}
    assert params == {"contraste": {"minimo": "10"}, "dataset": {"maf": "0.2", "passo": "15"},
                      "diagnostico": {"k_painel": "50"},
                      "classificar": {"k_painel": "=diagnostico.k_painel"},
                      "robustez": {"k_painel": "=diagnostico.k_painel", "n_boot": "100"}}
    inv = p.relatorio.invariantes
    assert len(inv) == 19
    assert not [i for i in inv if i.tipo == "igual" and "diagnostico.diagnostico_resumo.k_painel" in (i.a, i.b)]
    assert p.escolhas_autor["classe_b"].diferente_de == "classe_a"
    assert [c.tipo for c in p.checagens_entrada] == ["vcf_e_rotulos"]
    assert {a.fato for a in p.relatorio.alertas} == {"diagnostico.diagnostico_resumo.alerta_empate_zero",
                                                    "diagnostico.diagnostico_resumo.alerta_separador_perfeito"}


# ======================================================= controle com os selados
def test_ficha_dos_selados_aprova_as_19(tmp_path):
    ficha = _ficha(tmp_path)
    assert len(ficha["invariantes"]) == 19
    assert conferir_invariantes(ficha) == []
    por_id = {f["id"]: f["valor"] for f in ficha["fatos"]}
    # os números da tabela "Valores no demo" (ESPEC §3.1.3, adendo §A8.3)
    assert por_id["classificar.ml_resumo.n_amostras"] == 157 == 102 + 55
    assert por_id["dataset.dataset_resumo.n_variantes_matriz"] == por_id["classificar.ml_resumo.n_variantes"] == 515
    assert por_id["robustez.robustez_resumo.k_painel"] == por_id["classificar.ml_resumo.k_painel_minimo"] == 50
    rf = "classificar.ml_resumo.modelos.random_forest.painel_completo.confusion"
    assert [por_id[f"{rf}.{i}.{j}"] for i in (0, 1) for j in (0, 1)] == [102, 0, 1, 54]
    assert por_id["diagnostico.diagnostico_resumo.primeiro_separador"] == "14:378564"
    assert por_id["escolha.classe_a"] == "Angus" and por_id["escolha.classe_b"] == "Brahman"
    assert set(ficha["tabelas_invariantes"]) == {"dataset.matriz", "dataset.rotulos", "classificar.shap_top",
                                                 "robustez.nucleo_estavel"}
    assert [f["id"] for f in ficha["figuras"]] == ["classificar.shap_png", "robustez.robustez_png"]


def test_selos_por_passo(tmp_path):
    """dataset e classificar rodam os scripts do wf_ml_aims selado; os outros três, não."""
    ficha = _ficha(tmp_path)
    assert ficha["selos"] == {"contraste": "sem_selo", "dataset": "vigente", "diagnostico": "sem_selo",
                              "classificar": "vigente", "robustez": "sem_selo"}


# ===================================================== um canário por invariante
def _r(passo: str, **campos):
    def f(d):
        d[passo].update(campos)
    return f


def _confusao(modelo: str, painel: str, i: int, j: int, delta: int):
    def f(d):
        d["classificar"]["modelos"][modelo][painel]["confusion"][i][j] += delta
    return f


def _cortar(ref: str, n: int = 1):
    def f(t):
        linhas = t[ref].read_bytes().split(b"\n")
        t[ref].write_bytes(b"\n".join(linhas[:-1 - n]) + b"\n")
    return f


def _juntos(*fs):
    def f(d):
        for g in fs:
            g(d)
    return f


# (invariante, resumos, tabelas, invariantes que reprovam, trecho da falha da invariante) — só
# as que não são eco. O eco aparece em `esperadas` quando o canário de outra o arrasta junto.
CANARIOS = [
    (1, _juntos(_r("contraste", n_a=103), lambda d: d["dataset"]["grupo_a"].update(n=103)), None, {1},
     "= 158 ≠ classificar.ml_resumo.n_amostras = 157"),
    (2, _r("classificar", n_variantes=514), None, {2}, "n_variantes_matriz = 515 ≠ classificar.ml_resumo.n_variantes = 514"),
    (3, _r("contraste", n_a=101), None, {3}, "contraste.contraste_resumo.n_a = 101 ≠"),
    (4, _r("contraste", n_b=54), None, {4}, "contraste.contraste_resumo.n_b = 54 ≠"),
    (5, _r("diagnostico", n_marcadores=514), None, {5}, "diagnostico.diagnostico_resumo.n_marcadores = 514 ≠"),
    (6, _r("diagnostico", n_amostras=156), None, {6}, "diagnostico.diagnostico_resumo.n_amostras = 156 ≠"),
    # o núcleo com k = 49 tem 49 linhas (é o que o robustez grava): a 19, eco, segue de acordo
    (7, _r("robustez", k_painel=49), _cortar("robustez.nucleo_estavel"), {7},
     "robustez.robustez_resumo.k_painel = 49 ≠ classificar.ml_resumo.k_painel_minimo = 50"),
    (12, _r("contraste", n_y0=101), None, {12, 14}, "= 102 ≠ contraste.contraste_resumo.n_y0 = 101"),
    (13, _r("contraste", n_y1=56), None, {13, 15}, "= 55 ≠ contraste.contraste_resumo.n_y1 = 56"),
    (14, _confusao("xgboost", "painel_completo", 0, 1, +1), None, {10, 14}, "= 103 ≠ contraste.contraste_resumo.n_y0 = 102"),
    (15, _confusao("xgboost", "painel_completo", 1, 0, +1), None, {10, 15}, "= 56 ≠ contraste.contraste_resumo.n_y1 = 55"),
    (17, None, _cortar("dataset.rotulos"), {17}, "linhas de dado em dataset.rotulos = 156 ≠"),
    (18, None, _cortar("classificar.shap_top"), {18}, "linhas de dado em classificar.shap_top = 49 ≠"),
]


def _falhas_ecos(texto: str, n_invariantes: int, canarios: set[int]) -> list[str]:
    """A regra "um canário por invariante que não é eco", conferida no TEXTO do YAML: cada
    MARCA_ECO vale para o item de `invariantes:` logo depois dela (as linhas de comentário que a
    continuam não contam); as marcadas têm de ser exatamente ECO_ML, nenhuma marca fica fora de
    uma invariante, e os canários são exatamente as outras."""
    falhas: list[str] = []
    marcadas: set[int] = set()
    dentro = pendente = False
    n = 0
    for linha in texto.splitlines():
        if linha.startswith("  invariantes:"):
            dentro = True
        elif dentro and linha.strip() and not linha.startswith("    "):
            break
        elif dentro and linha.strip().startswith(MARCA_ECO):
            pendente = True
        elif dentro and linha.strip().startswith("- "):
            n += 1
            if pendente:
                marcadas.add(n)
            pendente = False
    if texto.count(MARCA_ECO) != len(marcadas) or pendente:
        falhas.append(f"{texto.count(MARCA_ECO)} marca(s) de eco no YAML, {len(marcadas)} antes de uma invariante")
    if n != n_invariantes:
        falhas.append(f"{n} itens em `invariantes:` no texto ≠ {n_invariantes} carregadas")
    if marcadas != ECO_ML:
        falhas.append(f"marcadas como eco no YAML: {sorted(marcadas)} ≠ ECO_ML {sorted(ECO_ML)}")
    reais = set(range(1, n_invariantes + 1)) - ECO_ML
    if canarios != reais:
        falhas.append(f"canários {sorted(canarios)} ≠ invariantes que não são eco {sorted(reais)}")
    return falhas


def _ids_canarios() -> set[int]:
    return {c[0] for c in CANARIOS}


def test_canarios_cobrem_toda_invariante_que_nao_e_eco():
    """Controle: no YAML real as marcas estão em 8-11, 16 e 19 e os canários cobrem as outras 13."""
    assert _falhas_ecos(YAML.read_text(encoding="utf-8"), len(_pipeline().relatorio.invariantes), _ids_canarios()) == []
    assert len(_ids_canarios()) == 13


_MARCA_16 = MARCA_ECO + " (n_mantidas"
_ITEM_17 = "    - {tipo: contagem_linhas, porta: dataset.rotulos"


@pytest.mark.parametrize("mexe_texto, mexe_canarios, trecho", [
    # a marca da 16 some: a invariante passaria a cobrar canário sem ter (ou o eco viraria "real")
    (lambda t: t.replace(_MARCA_16, "# (n_mantidas"), None,
     "marcadas como eco no YAML: [8, 9, 10, 11, 19] ≠ ECO_ML"),
    # a marca desce para a 17 (comentário fora do lugar)
    (lambda t: t.replace(_MARCA_16, "# (n_mantidas").replace(_ITEM_17, f"    {MARCA_ECO}\n{_ITEM_17}"), None,
     "marcadas como eco no YAML: [8, 9, 10, 11, 17, 19] ≠ ECO_ML"),
    # marca fora de `invariantes:` (o texto conta, o item não)
    (lambda t: t.replace("  invariantes:\n", f"  {MARCA_ECO}\n  invariantes:\n"), None,
     "7 marca(s) de eco no YAML, 6 antes de uma invariante"),
    # um canário para um eco, e um canário real que falta
    (None, lambda c: c | {16}, "≠ invariantes que não são eco"),
    (None, lambda c: c - {18}, "≠ invariantes que não são eco"),
])
def test_regra_dos_ecos_reprova(mexe_texto, mexe_canarios, trecho):
    texto = YAML.read_text(encoding="utf-8")
    mexido = mexe_texto(texto) if mexe_texto else texto
    assert mexe_texto is None or mexido != texto
    canarios = mexe_canarios(_ids_canarios()) if mexe_canarios else _ids_canarios()
    falhas = _falhas_ecos(mexido, len(_pipeline().relatorio.invariantes), canarios)
    assert len(falhas) == 1 and trecho in falhas[0], falhas


@pytest.mark.parametrize("n, resumos, tabelas, esperadas, trecho", CANARIOS, ids=[f"inv{c[0]}" for c in CANARIOS])
def test_canario_reprova_a_invariante_e_a_mutacao_o_solta(tmp_path, n, resumos, tabelas, esperadas, trecho):
    falhas = conferir_invariantes(_ficha(tmp_path / "c", resumos=resumos, tabelas=tabelas))
    assert _reprovadas(falhas) == esperadas, falhas
    da_n = next(f for f in falhas if f.startswith(f"invariante {n} "))
    assert trecho in da_n, da_n
    # mutação: sem a invariante n no plano, o mesmo canário não reprova mais por ela (as outras
    # renumeram — conferido pelo texto da referência, que é único por invariante)
    p = _pipeline()
    ref = json.dumps(p.relatorio.invariantes[n - 1].model_dump(), sort_keys=True)
    sem = conferir_invariantes(_ficha(tmp_path / "m", resumos=resumos, tabelas=tabelas, sem_invariante=n))
    assert len(sem) == len(falhas) - 1, (falhas, sem)
    restantes = [json.dumps(i.model_dump(), sort_keys=True) for k, i in enumerate(p.relatorio.invariantes, 1)
                 if k != n]
    assert ref not in restantes


def test_matriz_com_menos_de_k_marcadores_reprova_as_duas_de_k(tmp_path):
    """O caso da nota "Quando a invariante de k reprova": 40 marcadores → o robustez limita k a
    40 (núcleo com 40 linhas), o classificador declara 50 e o SHAP só tem 40 linhas."""
    def tabelas(t):
        _cortar("robustez.nucleo_estavel", 10)(t)
        _cortar("classificar.shap_top", 10)(t)
    falhas = conferir_invariantes(_ficha(tmp_path, resumos=_r("robustez", k_painel=40), tabelas=tabelas))
    assert _reprovadas(falhas) == {7, 18}, falhas


def test_tabela_trocada_depois_do_envelope_reprova(tmp_path):
    """A porta é reaberta pelo sha256 do envelope: uma linha a mais depois da entrega não é
    contada como se fosse a entregue."""
    pasta, _, plano = _montar(tmp_path)
    ficha = fatos_mod.gerar(plano, servico._envelopes(pasta, plano))
    arq = Path(ficha["tabelas_invariantes"]["dataset.matriz"]["arquivo"])
    arq.write_bytes(arq.read_bytes() + arq.read_bytes().split(b"\n")[1] + b"\n")
    falhas = conferir_invariantes(ficha)
    assert _reprovadas(falhas) == {16} and "mudou depois do envelope" in falhas[0], falhas


# ================================================================== alertas
def test_alertas_do_diagnostico_valem_no_demo(tmp_path):
    ficha = _ficha(tmp_path)
    assert len(ficha["alertas"]) == 2
    assert ALERTA_EMPATE in ficha["alertas"][0] and ALERTA_SEPARADOR in ficha["alertas"][1]


@pytest.mark.parametrize("campos, ficam", [
    ({"alerta_empate_zero": 0}, [ALERTA_SEPARADOR]),
    ({"alerta_separador_perfeito": 0}, [ALERTA_EMPATE]),
    ({"alerta_empate_zero": 0, "alerta_separador_perfeito": 0}, []),
])
def test_alerta_so_quando_o_fato_vale_1(tmp_path, campos, ficam):
    """Controle de cada alerta: com o fato em 0 ele some, e só ele."""
    alertas = _ficha(tmp_path, resumos=_r("diagnostico", **campos))["alertas"]
    assert len(alertas) == len(ficam) and all(t in a for t, a in zip(ficam, alertas)), alertas


_SEM_DIGITO = "importância maior que zero"


def test_alertas_sem_numero_solto():
    """O redator declara cada alerta nas limitações COM AS PALAVRAS dele (servico._INSTRUCAO_ALERTAS)
    e esse texto passa pelo R1: um dígito no alerta ("importância > 0") reprovaria todo relatório
    em que o alerta vale. Controle: os textos do YAML; canário: o do empate com "> 0". O alerta do
    empate diz que é o XGBoost (o RF deu importância maior que zero a 263 marcadores no demo)."""
    textos = [a.texto for a in _pipeline().relatorio.alertas]
    assert [NUMERO_SOLTO.findall(t) for t in textos] == [[], []]
    assert textos[0].startswith(f"O XGBoost deu {_SEM_DIGITO}") and "(que usam só o XGBoost)" in textos[0]
    canario = textos[0].replace(_SEM_DIGITO, "importância > 0")
    assert canario != textos[0] and NUMERO_SOLTO.findall(canario) == ["0"]


def test_alertas_copiados_passam_no_portao(tmp_path):
    """O mesmo pelo portão inteiro, sobre a ficha dos selados: limitações com as palavras dos dois
    alertas passam; com "> 0" no do empate, reprovam pelo R1 (e só por ele)."""
    pasta, _, plano = _montar(tmp_path)
    ficha = fatos_mod.gerar(plano, servico._envelopes(pasta, plano))

    def portao(paragrafos: list[str]) -> tuple[bool, list[tuple[str, str]]]:
        r = conferir({"secoes": [{"id": "limitacoes", "titulo": "Limitações", "paragrafos": paragrafos}]},
                     ficha, plano)
        return r["ok"], [(f["regra"], f["detalhe"]) for f in r["falhas"]]

    assert portao(ficha["alertas"]) == (True, [])
    assert portao([ficha["alertas"][0].replace(_SEM_DIGITO, "importância > 0")]) == (
        False, [("R1", "número fora de placeholder: '0'")])


def test_alerta_com_fato_ausente_impede_a_ficha(tmp_path):
    """Canário: um diagnóstico sem o campo do alerta não vira "sem alerta" em silêncio."""
    def tira(d):
        d["diagnostico"].pop("alerta_empate_zero")
    with pytest.raises(ValueError, match="alerta_empate_zero"):
        _ficha(tmp_path, resumos=tira)


# ============================================================ pedido ao redator
@pytest.fixture()
def run_ml(tmp_path, monkeypatch, cfg):
    pasta, run, plano = _montar(tmp_path)
    monkeypatch.setattr(servico, "_pastas", lambda run_id: (pasta, run, plano))
    return pasta, plano


def test_pedido_do_ml_leva_as_figuras_e_os_alertas(run_ml):
    p = servico.pedido(RUN)
    assert p["n_figuras"] == 2
    m = p["mensagem"]
    assert "ALERTAS (obrigatórios" in m and ALERTA_EMPATE in m and ALERTA_SEPARADOR in m
    assert "classificar.ml_resumo.n_amostras = 157" in m
    assert "diagnostico: passo diagnostico (selo do código: sem_selo)" in m


def test_invariante_reprovada_bloqueia_o_pedido(tmp_path, monkeypatch, cfg):
    pasta, run, plano = _montar(tmp_path, resumos=_r("contraste", n_b=54))
    monkeypatch.setattr(servico, "_pastas", lambda run_id: (pasta, run, plano))
    with pytest.raises(servico.ErroRelatorio) as e:
        servico.pedido(RUN)
    assert e.value.erro == "invariantes_reprovados"
    assert e.value.detalhe["falhas"][0].startswith("invariante 4 (igual)")


# ===================================================== M3 e escolhas, pela API
def _entradas_sinteticas(cfg) -> tuple[str, str]:
    """VCF.gz com GT (+ .tbi) e tabela de rótulos A × B na caixa de entrada <dados>/entradas."""
    caixa = cfg.data_dir / "entradas"
    caixa.mkdir(parents=True, exist_ok=True)
    vcf = _vcf_com_amostras(caixa, [f"S{k:04d}" for k in range(1, 26)])
    rot = _rotulos(caixa, {"A": 10, "B": 10}, nome="rotulos.tsv")
    return vcf.as_posix(), rot.as_posix()


def _run_com_entradas(api, cfg, slug: str) -> str:  # noqa: F811
    r = api.post("/api/v1/runs", json={"pipeline": PID, "especie": "bos_taurus", "subprojeto": slug})
    assert r.status_code == 201, r.text
    rid = r.json()["run_id"]
    vcf, rot = _entradas_sinteticas(cfg)
    for nome, caminho in (("vcf", vcf), ("rotulos", rot)):
        r = api.put(f"/api/v1/runs/{rid}/entradas/{nome}", json={"caminho": caminho})
        assert r.status_code == 200, r.text
    return rid


def test_regiao_fora_do_demo_sem_sobrescrita_e_422(api, cfg):  # noqa: F811
    """M3 (adendo §A8.1): num rascunho bovino a região do demo não vale em silêncio — o plano
    dá 422 parametro_recusado. Controle: o mesmo rascunho com `parametros` da região planeja, e
    o argv do dataset leva a região do subprojeto."""
    escrever_subprojeto(cfg.projetos_dir, "bos_taurus", "ml_sem", assembly="ARS-UCD1.3",
                        modulos=["ml"], pipelines=[PID])
    rid = _run_com_entradas(api, cfg, "ml_sem")
    r = api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {"classe_a": "A", "classe_b": "B"}})
    assert r.status_code == 422 and r.json()["detail"]["erro"] == "parametro_recusado", r.text
    assert "o padrão é do demo" in r.json()["detail"]["detalhe"]

    escrever_subprojeto(cfg.projetos_dir, "bos_taurus", "ml_com", assembly="ARS-UCD1.3", modulos=["ml"],
                        pipelines=[PID], parametros={"ml.ml_dataset.regiao": "14:1-1000"})
    rid = _run_com_entradas(api, cfg, "ml_com")
    r = api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {"classe_a": "A", "classe_b": "B"}})
    assert r.status_code == 200, r.text
    dataset = next(x for x in r.json()["passos"] if x["id"] == "dataset")
    assert dataset["valores"]["regiao"] == "14:1-1000" and " --regiao 14:1-1000 " in dataset["cmd"]
    k = {x["id"]: x["valores"].get("k_painel") for x in r.json()["passos"]}
    assert k == {"contraste": None, "dataset": None, "diagnostico": "50", "classificar": "50", "robustez": "50"}


def test_classes_iguais_sao_recusadas_no_plano(api, cfg):  # noqa: F811
    """E2E-4(b) em unidade: Angus × Angus → 422 escolhas_iguais (o ml_classificar daria
    IndexError). Controle: A × B no mesmo run planeja."""
    escrever_subprojeto(cfg.projetos_dir, "bos_taurus", "ml_com", assembly="ARS-UCD1.3", modulos=["ml"],
                        pipelines=[PID], parametros={"ml.ml_dataset.regiao": "14:1-1000"})
    rid = _run_com_entradas(api, cfg, "ml_com")
    r = api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {"classe_a": "A", "classe_b": "A"}})
    assert r.status_code == 422 and r.json()["detail"]["erro"] == "escolhas_iguais", r.text
    r = api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {"classe_a": "B", "classe_b": "A"}})
    assert r.status_code == 200, r.text
