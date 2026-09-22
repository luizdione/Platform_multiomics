"""Testes das correções da auditoria de 2026-09-14.

Regra que rege este arquivo (memória do autor: "validador que aprova tudo é
suspeito"): cada teste aqui REPROVA a versão anterior do código. Não há teste de
fumaça que só confira returncode — todos olham o conteúdo do artefato, e cada
gate tem uma canária que ele precisa pegar mais um controle negativo que ele
precisa deixar passar.
"""
from __future__ import annotations

import gzip
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from omica import __version__
from omica.jobs import queue, script_job
from omica.publish.gate import auditar
from omica.registry import load_registry
from omica.results import carregar_tabela

REPO = Path(__file__).resolve().parents[1]


def _carregar_script(rel: str):
    """Importa um script de módulo pelo caminho (eles usam `from _comum import`,
    então a pasta scripts/ entra no sys.path como aconteceria na execução real)."""
    caminho = REPO / rel
    sys.path.insert(0, str(caminho.parent))
    try:
        spec = importlib.util.spec_from_file_location(caminho.stem, caminho)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.pop(0)


# ---------------------------------------------------------------- Fst e NaN
# Linhas REAIS do contraste Angus×Nelore (E:\data\omica\results\genomica\fst),
# na ordem em que o código antigo as escreveu no "top 20".
CABECALHO_FST = ["#CHROM", "POS", "ID", "OBS_CT", "HUDSON_FST"]
CORPO_FST = [
    "14\t3893\t.\t0\tnan",        # sem nenhum genótipo observado
    "14\t6702\t.\t0\tnan",
    "14\t7675\t.\t241\tnan",
    "14\t8078\t.\t392\t0.529412",  # o MAIOR Fst real
    "14\t101983\t.\t494\t0.289593",
    "14\t8082\t.\t393\t0.0345927",
]


def test_top_fst_descarta_nan_e_variante_sem_genotipo():
    fst = _carregar_script("modules/genomica/scripts/fst_racas.py")
    top, comparaveis, n_nan, n_sem_obs, n_baixa = fst.top_por_fst(
        CABECALHO_FST, CORPO_FST, 20)

    # o defeito: float("nan") não levanta ValueError, então o topo vinha com NaN
    assert not any("nan" in l for l in top), "variante com Fst NaN chegou ao topo"
    assert top[0].startswith("14\t8078\t"), "o maior Fst real não ficou em 1º"
    valores = [float(l.split("\t")[4]) for l in top]
    assert valores == sorted(valores, reverse=True), "topo fora de ordem decrescente"
    assert (comparaveis, n_nan, n_sem_obs, n_baixa) == (3, 1, 2, 0)


def test_top_fst_nao_esconde_o_descarte():
    """O descarte precisa ser CONTADO (o usuário tem de saber quanto sumiu)."""
    fst = _carregar_script("modules/genomica/scripts/fst_racas.py")
    _, comparaveis, n_nan, n_sem_obs, n_baixa = fst.top_por_fst(
        CABECALHO_FST, CORPO_FST, 20)
    assert comparaveis + n_nan + n_sem_obs + n_baixa == len(CORPO_FST)


# A REVISÃO do primeiro patch pegou isto: descartar só OBS_CT==0 removia 28 de
# 122.372 variantes (0,02%) e deixava passar a cauda de cobertura ínfima — no
# arquivo real, esta linha (4 genótipos observados de 497, Fst=1,0) passou a
# encabeçar o top publicado. Trocar "NaN no topo" por "1,0 espúrio no topo" é
# pior: o número convence, e num manuscrito se lê como fixação entre raças.
LINHA_COBERTURA_INFIMA = "14\t123085\t.\t4\t1"


def test_top_fst_barra_cobertura_infima_com_fst_maximo():
    fst = _carregar_script("modules/genomica/scripts/fst_racas.py")
    corpo = CORPO_FST + [LINHA_COBERTURA_INFIMA]
    min_obs = 249  # metade da coorte real (112 Angus + 385 Nelore)

    top, comparaveis, n_nan, n_sem_obs, n_baixa = fst.top_por_fst(
        CABECALHO_FST, corpo, 20, min_obs)
    assert LINHA_COBERTURA_INFIMA not in top, (
        "variante com 4 de 497 genótipos observados encabeçou o top publicado")
    # 2 de baixa cobertura: a canária (OBS_CT=4) e a linha real de OBS_CT=241 —
    # a cobertura é avaliada ANTES da finitude, então esta última é contada aqui
    # e não entre as NaN. Os baldes são de diagnóstico; a soma é que tem de fechar.
    assert (n_baixa, n_sem_obs, n_nan) == (2, 2, 0)
    assert comparaveis + n_nan + n_sem_obs + n_baixa == len(corpo)
    # controle negativo: cobertura ALTA com Fst alto continua entrando — o filtro
    # não pode virar uma peneira que descarta o sinal junto com o ruído.
    assert top[0].startswith("14\t8078\t") and comparaveis == 3


def test_canaria_de_cobertura_reprova_o_limiar_permissivo():
    """A canária precisa REPROVAR com min_obs=1: se passasse dos dois jeitos,
    não estaria testando nada."""
    fst = _carregar_script("modules/genomica/scripts/fst_racas.py")
    corpo = CORPO_FST + [LINHA_COBERTURA_INFIMA]
    top_permissivo, _, _, _, n_baixa = fst.top_por_fst(CABECALHO_FST, corpo, 20, 1)
    assert top_permissivo[0] == LINHA_COBERTURA_INFIMA and n_baixa == 0


# ------------------------------------------------------------ MAF multialélica
@pytest.mark.parametrize("afs,esperado", [
    ([0.10], 0.10),            # bialélico comum
    ([0.90], 0.10),            # bialélico com ALT majoritário
    ([0.50, 0.30], 0.30),      # multialélico: REF=0,2 → MAF é o 2º (0,3), não 0,5
    ([0.40, 0.40], 0.40),      # REF=0,2; dois ALT empatados
    ([0.98, 0.01], 0.01),      # REF=0,01 → MAF = 0,01
])
def test_maf_de_afs(afs, esperado):
    freq = _carregar_script("modules/genomica/scripts/frequencias.py")
    assert freq.maf_de_afs(afs) == pytest.approx(esperado)


def test_maf_multialelica_difere_da_formula_antiga():
    """Canária explícita: no caso multialélico a fórmula antiga dava outro número."""
    freq = _carregar_script("modules/genomica/scripts/frequencias.py")
    afs = [0.50, 0.30]
    antiga = min(afs[0], 1.0 - afs[0])  # 0,5 — e a tabela é ORDENADA por isso
    assert freq.maf_de_afs(afs) != pytest.approx(antiga)


# --------------------------------------------------------- Perfil TSS e fita
def test_perfil_tss_inverte_a_fita_negativa(tmp_path):
    tss_mod = _carregar_script("modules/epigenomica/scripts/perfil_tss.py")
    cov = tmp_path / "amostra.cov.gz"
    # CpG 100 pb à DIREITA do TSS. Num gene da fita −, isso é UPSTREAM.
    with gzip.open(cov, "wt", encoding="utf-8") as f:
        f.write("1\t1100\t1100\t80.0\t8\t2\n")

    mapa_negativa = {"1": [(1000, -1, "GENE_NEG")]}
    _, n = tss_mod._acumular([cov], mapa_negativa, janela=200, n_bins=4)
    assert n == 1
    media, _ = tss_mod._acumular([cov], mapa_negativa, janela=200, n_bins=4)
    ocupado_neg = [i for i, v in enumerate(media) if v == v]  # não-NaN
    assert ocupado_neg == [1], "fita − deveria cair na metade UPSTREAM (bins 0-1)"

    # controle: o mesmo CpG num gene da fita + tem de cair na metade downstream
    mapa_positiva = {"1": [(1000, 1, "GENE_POS")]}
    media_pos, _ = tss_mod._acumular([cov], mapa_positiva, janela=200, n_bins=4)
    ocupado_pos = [i for i, v in enumerate(media_pos) if v == v]
    assert ocupado_pos == [3], "fita + deveria cair na metade DOWNSTREAM (bins 2-3)"


def test_tss_mapeia_fita_do_painel_real():
    tss_mod = _carregar_script("modules/epigenomica/scripts/perfil_tss.py")
    mapa = tss_mod._tss_por_cromossomo(
        REPO / "modules/epigenomica/data/paineis/painel_108.json")
    fitas = [fita for v in mapa.values() for _, fita, _ in v]
    assert set(fitas) <= {1, -1}
    assert -1 in fitas, "painel 108 tem genes na fita −; eles precisam ser distinguidos"


# ------------------------------------------ Interseção DMC × painel (dupla contagem)
def test_intersecao_conta_cada_dmc_uma_vez(tmp_path):
    """O bug publicou 320 DMCs 'no painel' de um total de 314: janelas de genes
    vizinhos (MX1/MX2) se sobrepõem e a mesma DMC somava 1 por janela."""
    dmc = tmp_path / "dmc.tsv"
    dmc.write_text(
        "chr\tstart\tend\tstrand\tpvalue\tqvalue\tmeth.diff\n"
        "1\t1600\t1600\t*\t0.001\t0.01\t20.5\n",
        encoding="utf-8")
    painel = tmp_path / "painel.bed"
    painel.write_text("1\t1000\t2000\tGENE_A\tENS1\n"
                      "1\t1500\t2500\tGENE_B\tENS2\n", encoding="utf-8")
    saida = tmp_path / "out"
    r = subprocess.run(
        [sys.executable, str(REPO / "modules/epigenomica/scripts/intersecao_dmc_painel.py"),
         "--dmc", str(dmc), "--painel", str(painel), "--saida", str(saida)],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    resumo = json.loads((saida / "intersecao_resumo.json").read_text(encoding="utf-8"))

    assert resumo["n_dmc_total"] == 1
    assert resumo["n_dmc_no_painel"] == 1, "DMC contada uma vez por janela sobreposta"
    assert resumo["n_dmc_no_painel"] <= resumo["n_dmc_total"], (
        "impossível haver mais DMCs no painel do que no total")
    assert resumo["n_atribuicoes_gene"] == 2  # atribuição por gene continua dupla…
    assert resumo["n_genes_com_dmc"] == 2     # …e isso fica declarado no resumo


# --------------------------------------------------------- MOFA: views disjuntas
def _intervalos(regioes, gene):
    return {view: (ini, fim) for _, ini, fim, g, view in regioes if g == gene}


def test_mofa_views_sao_disjuntas_nas_duas_fitas():
    """Teste de COMPORTAMENTO (a 1ª versão deste teste só procurava strings no
    fonte e passaria com os ramos das fitas trocados)."""
    prep = _carregar_script("modules/epigenomica/scripts/mofa_preparar.py")
    genes = {
        "MAIS":  {"cromossomo": "1", "inicio": 10_000, "fim": 40_000, "fita": 1},
        "MENOS": {"cromossomo": "1", "inicio": 10_000, "fim": 40_000, "fita": -1},
    }
    regioes, sem_corpo = prep.regioes_do_painel(genes, promotor=2000)
    assert sem_corpo == []

    mais = _intervalos(regioes, "MAIS")
    assert mais["promotor"] == (8_000, 12_000)      # TSS = início
    assert mais["corpo"] == (12_001, 40_000)        # corpo DEPOIS do promotor

    menos = _intervalos(regioes, "MENOS")
    assert menos["promotor"] == (38_000, 42_000)    # TSS = fim
    assert menos["corpo"] == (10_000, 37_999)       # corpo ANTES do promotor

    # a propriedade que dá sentido ao MOFA multi-view: as views não podem
    # compartilhar uma única base — senão parte da covariância é a mesma CpG.
    for gene in ("MAIS", "MENOS"):
        (pi, pf), (ci, cf) = _intervalos(regioes, gene)["promotor"], _intervalos(regioes, gene)["corpo"]
        assert cf < pi or ci > pf, f"views de {gene} se sobrepõem em [{pi},{pf}]×[{ci},{cf}]"


def test_mofa_gene_curto_demais_fica_sem_corpo_e_isso_e_declarado():
    prep = _carregar_script("modules/epigenomica/scripts/mofa_preparar.py")
    genes = {"CURTO": {"cromossomo": "1", "inicio": 1_000, "fim": 2_000, "fita": 1}}
    regioes, sem_corpo = prep.regioes_do_painel(genes, promotor=2000)
    assert sem_corpo == ["CURTO"]
    assert [v for *_, v in regioes] == ["promotor"]  # nenhuma view "corpo" fantasma


# ------------------------------------------------------------- Gate de segredos
CANARIAS = {
    "token_github.py": 'TOKEN = "ghp_' + "A" * 36 + '"\n',
    "aws.cfg": "aws_access_key_id = AKIAIOSFODNN7EXAMPLE\n",
    "anthropic.txt": "chave: sk-ant-api03-" + "B" * 24 + "\n",
    "chave.txt": "-----BEGIN OPENSSH PRIVATE KEY-----\nZmFrZQ==\n",
    "banco.ini": "password = trocaessasenha123\n",
    "notebook.ipynb": '{"cells":[{"source":["api_key = \\"' + "C" * 40 + '\\""]}]}\n',
}


@pytest.mark.parametrize("nome", sorted(CANARIAS))
def test_gate_reprova_cada_canaria_de_credencial(tmp_path, nome):
    (tmp_path / nome).write_text(CANARIAS[nome], encoding="utf-8")
    v = auditar(tmp_path)
    assert not v.aprovado, f"o gate APROVOU uma árvore com credencial em {nome}"
    assert any("[segredo]" in b for b in v.bloqueios)


@pytest.mark.parametrize("nome", [".env", "id_rsa", "servidor.pem", "app.key"])
def test_gate_reprova_arquivo_de_credencial_pelo_nome(tmp_path, nome):
    (tmp_path / nome).write_text("conteudo qualquer\n", encoding="utf-8")
    v = auditar(tmp_path)
    assert not v.aprovado and any("[credencial]" in b for b in v.bloqueios)


def test_gate_nao_bloqueia_env_example_nem_ciencia(tmp_path):
    """Controle negativo — um gate que reprova tudo é tão inútil quanto um que
    aprova tudo."""
    (tmp_path / ".env.example").write_text("OMICA_PORT=8710\nOMICA_DATA=\n", encoding="utf-8")
    (tmp_path / "fst.py").write_text("maf = min(af, 1 - af)\n", encoding="utf-8")
    (tmp_path / "tabela.tsv").write_text("GENE\tN\nMX1\t3\n", encoding="utf-8")
    v = auditar(tmp_path)
    assert v.aprovado, f"bloqueios indevidos: {v.bloqueios}"


def test_gate_avisa_sobre_arquivo_que_nao_leu(tmp_path):
    (tmp_path / "figura.pdf").write_bytes(b"%PDF-1.4 binario")
    v = auditar(tmp_path)
    assert any("[nao-lido]" in a for a in v.avisos), (
        "o gate precisa declarar o que NÃO auditou, em vez de aprovar em silêncio")


# ------------------------------------------------ Falso-done no executor de job
def _job_com_cmd(cfg, job_id: str, cmd: str) -> None:
    queue.write_status(job_id, "running", cmd=cmd)


def test_job_falha_quando_resultado_anunciado_nao_existe(cfg, tmp_path):
    script = tmp_path / "mentiroso.py"
    script.write_text("print('RESULTADO: /caminho/que/nao/existe.json')\n", encoding="utf-8")
    _job_com_cmd(cfg, "job-mentiroso",
                 f'"{Path(sys.executable).as_posix()}" "{script.as_posix()}"')
    with pytest.raises(RuntimeError, match="não existe"):
        script_job.executar("job-mentiroso")


def test_job_falha_quando_resultado_esta_vazio(cfg, tmp_path):
    alvo = tmp_path / "vazio.json"
    alvo.write_text("", encoding="utf-8")
    script = tmp_path / "escreve_vazio.py"
    script.write_text(f"print(r'RESULTADO: {alvo.as_posix()}')\n", encoding="utf-8")
    _job_com_cmd(cfg, "job-vazio",
                 f'"{Path(sys.executable).as_posix()}" "{script.as_posix()}"')
    with pytest.raises(RuntimeError, match="vazio"):
        script_job.executar("job-vazio")


def test_job_conclui_quando_o_artefato_existe(cfg, tmp_path):
    """Controle positivo: a guarda nova não pode quebrar o caminho feliz."""
    alvo = tmp_path / "resultado.json"
    script = tmp_path / "honesto.py"
    script.write_text(
        f"open(r'{alvo}', 'w', encoding='utf-8').write('{{\"n\": 1}}')\n"
        f"print(r'RESULTADO: {alvo.as_posix()}')\n", encoding="utf-8")
    _job_com_cmd(cfg, "job-honesto",
                 f'"{Path(sys.executable).as_posix()}" "{script.as_posix()}"')
    script_job.executar("job-honesto")
    status = queue.read_status("job-honesto")
    assert status["status"] == "done" and status["result_path"].endswith("resultado.json")


# ------------------------------------------- Registry: a checagem estava morta
def _manifest_tmp(tmp_path, cmd: str, criar_script: bool):
    modules = tmp_path / "modules"
    pasta = modules / "canario"
    (pasta / "scripts").mkdir(parents=True)
    if criar_script:
        (pasta / "scripts" / "existe.py").write_text("", encoding="utf-8")
    (pasta / "manifest.yaml").write_text(yaml.safe_dump({
        "id": "canario", "nome_pt": "C", "nome_en": "C",
        "scripts": [{"key": "a", "titulo": "a", "runtime": "image:omica-worker", "cmd": cmd}],
    }), encoding="utf-8")
    return modules


def test_registry_reprova_script_inexistente_no_formato_real(tmp_path):
    """A versão anterior olhava só cmd.split()[0] — sempre 'python3'/'Rscript' —
    e por isso NUNCA reprovava nada: manifest apontando para script apagado
    carregava limpo e o doctor dizia '4 módulos válidos'."""
    modules = _manifest_tmp(tmp_path, "python3 modules/canario/scripts/sumiu.py", False)
    reg = load_registry(modules)
    assert "canario" in reg.erros and "referências quebradas" in reg.erros["canario"]


def test_registry_aceita_script_existente(tmp_path):
    modules = _manifest_tmp(tmp_path, "python3 modules/canario/scripts/existe.py", True)
    reg = load_registry(modules)
    assert reg.erros == {} and reg.por_id("canario") is not None


def test_registry_reprova_cmd_sem_script(tmp_path):
    modules = _manifest_tmp(tmp_path, "python3 -c pass", True)
    reg = load_registry(modules)
    assert "canario" in reg.erros


def test_todos_os_scripts_dos_manifests_reais_existem():
    """Agora que a checagem funciona, ela vale para o repo de verdade."""
    reg = load_registry()
    assert reg.erros == {}, f"referências quebradas nos manifests: {reg.erros}"


# ------------------------------------------------- Tabela vazia ≠ 0 resultados
def test_tabela_de_arquivo_vazio_e_sem_dados(cfg):
    (cfg.results_dir / "vazia.tsv").parent.mkdir(parents=True, exist_ok=True)
    (cfg.results_dir / "vazia.tsv").write_text("", encoding="utf-8")
    assert carregar_tabela("vazia.tsv") is None


def test_tabela_so_com_cabecalho_declara_vazia(cfg):
    (cfg.results_dir / "so_cab.tsv").parent.mkdir(parents=True, exist_ok=True)
    (cfg.results_dir / "so_cab.tsv").write_text("GENE\tN\n", encoding="utf-8")
    t = carregar_tabela("so_cab.tsv")
    assert t is not None and t["vazia"] is True and t["total_linhas"] == 0


def test_tabela_com_dados_nao_e_vazia(cfg):
    (cfg.results_dir / "cheia.tsv").parent.mkdir(parents=True, exist_ok=True)
    (cfg.results_dir / "cheia.tsv").write_text("GENE\tN\nMX1\t3\n", encoding="utf-8")
    t = carregar_tabela("cheia.tsv")
    assert t["vazia"] is False and t["total_linhas"] == 1


# --------------------------------------------------------- Versão: fonte única
def test_versao_tem_fonte_unica():
    import tomllib

    dados = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    projeto = dados["project"]
    assert "version" not in projeto, (
        "pyproject não pode ter versão literal: ela fossilizou em 0.1.0 enquanto "
        "VERSION e a tag já estavam em 0.7.0")
    assert "version" in projeto.get("dynamic", [])
    assert dados["tool"]["setuptools"]["dynamic"]["version"]["file"] == "VERSION"
    assert __version__ == (REPO / "VERSION").read_text(encoding="utf-8").strip()


# ------------------------------------------------- Selo de workflow com lastro
def _modulo_com_workflow(tmp_path, corpo_script: str, hash_gravado: str):
    modules = tmp_path / "modules"
    pasta = modules / "selado"
    (pasta / "scripts").mkdir(parents=True)
    (pasta / "scripts" / "a.py").write_text(corpo_script, encoding="utf-8")
    (pasta / "scripts" / "b.py").write_text("# b\n", encoding="utf-8")
    (pasta / "manifest.yaml").write_text(yaml.safe_dump({
        "id": "selado", "nome_pt": "S", "nome_en": "S",
        "scripts": [
            {"key": "a", "titulo": "a", "runtime": "image:omica-worker",
             "cmd": "python3 modules/selado/scripts/a.py",
             "outputs": ["results/selado/a.json"]},
            {"key": "b", "titulo": "b", "runtime": "image:omica-worker",
             "cmd": "python3 modules/selado/scripts/b.py",
             "inputs": ["results/selado/a.json"]},
        ],
        "workflows": [{"id": "w", "titulo": "w", "passos": ["a", "b"],
                       "testado_em": "2026-09-01", "testado_por": "teste",
                       "hash_scripts": hash_gravado}],
    }), encoding="utf-8")
    return modules


def test_selo_vence_quando_o_script_muda(tmp_path):
    """O achado: `testado_em` era texto livre e sobrevivia à reescrita do próprio
    script que certifica — números publicados sob um selo sem lastro."""
    from omica.registry import hash_dos_passos, load_registry, selos_vencidos

    modules = _modulo_com_workflow(tmp_path, "# versao testada\n", "0" * 16)
    reg = load_registry(modules)
    m = reg.por_id("selado")
    correto = hash_dos_passos(m, m.workflows[0], modules)

    # com o hash errado gravado, o selo TEM de ser acusado
    vencidos = selos_vencidos(modules)
    assert [(v[0], v[1]) for v in vencidos] == [("selado", "w")]
    assert "re-executar" in vencidos[0][2]

    # com o hash certo, silêncio (controle negativo)
    modules_ok = _modulo_com_workflow(tmp_path / "ok", "# versao testada\n", correto)
    assert selos_vencidos(modules_ok) == []

    # e se o script mudar depois de selado, volta a acusar
    (modules_ok / "selado" / "scripts" / "a.py").write_text("# mexeram\n", encoding="utf-8")
    assert len(selos_vencidos(modules_ok)) == 1


def test_selo_sem_hash_nao_passa_por_valido(tmp_path):
    from omica.registry import selos_vencidos

    modules = _modulo_com_workflow(tmp_path, "# x\n", "")
    vencidos = selos_vencidos(modules)
    assert len(vencidos) == 1 and "sem hash_scripts" in vencidos[0][2]


def test_todo_workflow_selado_do_repo_tem_hash():
    """Invariante permanente: selo sem impressão digital não é selo."""
    for m in load_registry().modules:
        for wf in m.workflows:
            if wf.testado_em:
                assert wf.hash_scripts, f"{m.id}/{wf.id} selado sem hash_scripts"


# ------------------------- Guarda de RAM: o caminho RECUSA → UI segue coberto
def test_recusa_por_ram_vira_aviso_na_ui_e_nao_500(cfg, monkeypatch):
    """A revisão apontou: ao fixar a RAM em 12% no conftest (para a suíte parar
    de depender da máquina), o caminho RecusadoPorRAM → rota deixou de ser
    exercitado por ninguém. Este teste o exercita de propósito."""
    from fastapi.testclient import TestClient

    from omica.jobs import guarda_ram
    from omica.web.app import app

    class _Cheia:
        percent = 97.0

    monkeypatch.setattr(guarda_ram.psutil, "virtual_memory", lambda: _Cheia())
    cliente = TestClient(app)
    r = cliente.post("/modulos/genomica/scripts/extracao_demo",
                     data={}, follow_redirects=False)
    assert r.status_code == 303, "recusa por RAM não pode virar 500"
    destino = r.headers["location"]
    assert destino.startswith("/modulos/genomica?aviso=")
    assert "RAM" in destino and "97" in destino


def test_guarda_de_ram_continua_autorizando_quando_cabe(cfg, monkeypatch):
    """Controle negativo do teste acima."""
    from omica.jobs import guarda_ram

    class _Folgada:
        percent = 20.0

    monkeypatch.setattr(guarda_ram.psutil, "virtual_memory", lambda: _Folgada())
    guarda_ram.autorizar(1.0)  # não levanta


# --------------------------------- Diagnóstico: as checagens novas têm teste
def test_doctor_referencia_com_pasta_vazia_reprova(cfg, monkeypatch):
    """Antes bastava o diretório existir: pasta vazia (download interrompido,
    volume montado sem conteúdo) passava como OK."""
    from omica import doctor

    vazio = cfg.data_dir / "refs" / "salmon_bostau"
    vazio.mkdir(parents=True, exist_ok=True)
    checagens = {c.nome: c for c in doctor._referencias()}
    salmon = checagens["Índice Salmon bovino"]
    assert salmon.nivel == doctor.FALHA and "info.json" in salmon.detalhe

    (vazio / "info.json").write_text("{}", encoding="utf-8")
    salmon_ok = {c.nome: c for c in doctor._referencias()}["Índice Salmon bovino"]
    assert salmon_ok.nivel == doctor.OK  # controle negativo


def test_doctor_porta_livre_e_aviso_nao_falha(cfg, monkeypatch):
    from omica import doctor

    monkeypatch.setenv("OMICA_PORT", "59999")  # porta livre
    from omica.config import reset_config_cache

    reset_config_cache()
    c = doctor._porta()
    assert c.nivel == doctor.AVISO and "nada escutando" in c.detalhe


def test_doctor_acusa_resultado_mais_velho_que_o_script(cfg, monkeypatch):
    """Cobre o buraco que o selo de workflow não alcança: script avulso
    (perfil_tss, motifs_homer) que publica resultado e depois muda."""
    import os
    import time

    from omica import doctor

    reg = load_registry()
    gen = reg.por_id("genomica")
    spec = next(s for s in gen.scripts if s.outputs)
    alvo = next(t for t in spec.cmd.split() if t.endswith(".py"))
    script = REPO / alvo
    rel = spec.outputs[0]
    rel = rel[len("results/"):] if rel.startswith("results/") else rel
    artefato = cfg.results_dir / rel
    artefato.parent.mkdir(parents=True, exist_ok=True)
    artefato.write_text("{}", encoding="utf-8")

    # A referência é o mtime REAL do script (não "ontem": scripts intocados
    # desde setembro seriam mais velhos que qualquer data relativa, e o teste
    # passaria pelo motivo errado).
    ref = max(script.stat().st_mtime,
              (REPO / "modules/genomica/scripts/_comum.py").stat().st_mtime)

    os.utime(artefato, (ref - 3600, ref - 3600))  # resultado ANTES do código
    c = doctor._resultados_velhos()
    assert c.nivel == doctor.AVISO and "re-executar" in c.detalhe

    os.utime(artefato, (ref + 3600, ref + 3600))  # resultado DEPOIS (controle)
    assert doctor._resultados_velhos().nivel == doctor.OK
    assert time is not None


# ------------------------------- Vitrine do Professor sabe do selo vencido
def test_vitrine_marca_selo_vencido():
    """O selo com lastro precisa CHEGAR ao usuário: antes, a vitrine mostrava ✅
    só por existir testado_em, mesmo com o script já alterado."""
    from omica.professor import workflows_testados

    vitrine = workflows_testados()
    assert vitrine, "nenhum workflow selado"
    assert all("vencido" in item for item in vitrine)
    # hoje, depois das correções desta auditoria, há pelo menos um vencido —
    # e o ml_aims, cujos scripts não foram tocados, continua válido.
    por_id = {f"{i['modulo']}/{i['wf'].id}": i for i in vitrine}
    assert por_id["ml/wf_ml_aims"]["vencido"] is False


# ----------------- Cadeia: dependente de passo que falhou não fica fantasma
def test_cadeia_marca_dependente_de_passo_que_falhou(cfg):
    """No RQ o dependente fica DEFERIDO para sempre e o status.json congela em
    'queued' — a UI mostra a cadeia viva sem nada rodando e a guarda de RAM
    segue somando o mem_gb do fantasma."""
    import json as _json

    registro = cfg.work_dir / "workflows"
    registro.mkdir(parents=True, exist_ok=True)
    queue.write_status("wf-p1", "error", error="estourou", mem_gb=2.0)
    queue.write_status("wf-p2", "queued", mem_gb=2.0)
    (registro / "wf-run-x.json").write_text(
        _json.dumps({"wf_run_id": "wf-run-x", "backend": "rq",
                     "jobs": ["wf-p1", "wf-p2"]}), encoding="utf-8")

    run = queue.ler_workflow_run("wf-run-x")
    assert run["status"] == "error"
    assert run["passos"][1]["status"] == "error"
    assert "dependência cancelada" in run["passos"][1]["error"]
    # e o arquivo em disco também mudou — senão a guarda de RAM continuaria
    # contando o passo 2 como trabalho ativo.
    assert queue.read_status("wf-p2")["status"] == "error"


def test_cadeia_saudavel_nao_e_cancelada(cfg):
    """Controle negativo: sem falha, ninguém vira erro."""
    import json as _json

    registro = cfg.work_dir / "workflows"
    registro.mkdir(parents=True, exist_ok=True)
    queue.write_status("wf-ok1", "done", mem_gb=1.0)
    queue.write_status("wf-ok2", "queued", mem_gb=1.0)
    (registro / "wf-run-ok.json").write_text(
        _json.dumps({"wf_run_id": "wf-run-ok", "backend": "rq",
                     "jobs": ["wf-ok1", "wf-ok2"]}), encoding="utf-8")

    run = queue.ler_workflow_run("wf-run-ok")
    assert run["status"] == "running"
    assert run["passos"][1]["status"] == "queued"
