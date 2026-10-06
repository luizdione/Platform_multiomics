"""Ponte com o plugin Bioinfo: busca PT/EN no servidor (gêmea de bioinfo_find.py),
cópia do glossário com proveniência, elo módulo→domínio dos manifests, cards de
domínio e o "fora do catálogo" do Professor.

Nada aqui lê o plugin real nem a biblioteca real: só os arquivos versionados em
data/ e fixtures — a suíte roda igual no host do autor, no container e no CI."""
import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from omica import doctor, professor
from omica.config import REPO_ROOT, reset_config_cache
from omica.registry import Manifest, Registry, load_registry
from omica.skills import build_index, busca

SKILLS = [
    {"id": "bio-methylation-calling", "dominio": "epigenomics", "origem": "bioskills",
     "descricao": "Call methylation from bisulfite reads with Bismark.",
     "stack": ["bismark", "samtools"], "pasta": "epigenomics/methylation-calling"},
    {"id": "proteomics-lfq", "dominio": "proteo-metabo", "origem": "sciagent",
     "descricao": "Label-free MS quantification of whole proteomics systems.",
     "stack": ["maxquant"], "pasta": "proteo-metabo/proteomics-lfq"},
    {"id": "deseq2-basics", "dominio": "rnaseq-bulk", "origem": "bioskills",
     "descricao": "Differential expression of bulk RNA-seq counts.",
     "stack": ["deseq2"], "pasta": "rnaseq-bulk/deseq2-basics"},
    {"id": "systems-modeling", "dominio": "multiomics", "origem": "sciagent",
     "descricao": "Constraint-based metabolic models.",
     "stack": ["cobrapy"], "pasta": "multiomics/systems-modeling"},
]
GLOSSARIO = {"metilacao": ["methylation"], "expressao": ["expression"],
             "diferencial": ["differential"], "dge": ["differential+expression"],
             "ms": ["mass+spectrometry", "proteom"]}
STOPWORDS = {"de", "do", "quero", "a"}


def _ids(frase, **kw):
    return [s["id"] for s in busca.buscar_em(SKILLS, frase, GLOSSARIO, STOPWORDS, **kw)["resultados"]]


def _client():
    from omica.web.app import app

    return TestClient(app)


# ---------------------------------------------------------------- busca (fixture)

def test_portugues_acha_o_mesmo_que_ingles():
    # o defeito de origem: o índice é em inglês e "metilação" devolvia zero
    assert _ids("metilação") == _ids("methylation") == ["bio-methylation-calling"]
    assert _ids("METILACAO") == ["bio-methylation-calling"]  # sem acento e sem caixa


def test_frase_humana_perde_as_palavras_de_ligacao():
    r = busca.buscar_em(SKILLS, "quero a expressão diferencial,", GLOSSARIO, STOPWORDS)
    assert [s["id"] for s in r["resultados"]] == ["deseq2-basics"]
    assert r["ignorados"] == ["quero", "a"]
    assert r["termos"] == ["expressão", "diferencial"]  # a vírgula colada saiu
    assert {x["termo"] for x in r["traducao"]} == {"expressão", "diferencial"}


def test_variante_composta_exige_os_dois_radicais():
    # "dge" → differential+expression: só a skill que tem os DOIS
    assert _ids("dge") == ["deseq2-basics"]


def test_sigla_curta_so_casa_como_palavra_inteira():
    # "ms" não pode casar dentro de "systems" (id systems-modeling)
    assert _ids("ms") == ["proteomics-lfq"]


def test_modo_parcial_quando_nada_casa_tudo():
    r = busca.buscar_em(SKILLS, "bismark xyzinexistente", GLOSSARIO, STOPWORDS)
    assert r["modo"] == "parcial" and r["sem_casar"] == ["xyzinexistente"]
    assert r["resultados"][0]["id"] == "bio-methylation-calling"
    assert r["resultados"][0]["casou"] == ["bismark"]
    # um termo só que não casa nada não vira "parcial": é vazio mesmo
    vazio = busca.buscar_em(SKILLS, "xyzinexistente", GLOSSARIO, STOPWORDS)
    assert vazio["modo"] == "todos" and vazio["total"] == 0


def test_modo_parcial_ordena_pelo_termo_mais_raro():
    # "systems" está em 2 skills, "cobrapy" em 1: quem tem o termo raro vem antes
    r = busca.buscar_em(SKILLS, "systems cobrapy inexistente", GLOSSARIO, STOPWORDS)
    assert [s["id"] for s in r["resultados"]] == ["systems-modeling", "proteomics-lfq"]


def test_termo_no_nome_vem_antes():
    # as duas casam "systems"; pela ordem do índice proteomics-lfq viria primeiro
    assert _ids("systems") == ["systems-modeling", "proteomics-lfq"]


def test_limite_corta_sem_mentir_o_total():
    r = busca.buscar_em(SKILLS, "", GLOSSARIO, STOPWORDS, limite=1)
    assert r["total"] == 4 and r["mostrando"] == 1 and len(r["resultados"]) == 1
    assert busca.buscar_em(SKILLS, "", GLOSSARIO, STOPWORDS, limite=0)["mostrando"] == 4


def test_frase_longa_e_cortada_no_teto_sem_travar():
    """Achado da revisão da 0.9.0: um trecho de Métodos colado no Professor (centenas de
    siglas) levava 40–80 s — um regex recompilado por termo × skill — e, na rota do
    Professor, parava o servidor inteiro. O teto é o mesmo do plugin (busca gêmea)."""
    import itertools
    import string
    import time

    siglas = ["".join(p) for p in itertools.product(string.ascii_lowercase, repeat=2)][:600]
    r = busca.buscar_em(SKILLS, " ".join(siglas), GLOSSARIO, STOPWORDS)
    assert len(r["termos"]) == busca.MAX_TERMOS and r["truncado"] is True
    assert busca.buscar_em(SKILLS, "metilação bismark", GLOSSARIO, STOPWORDS)["truncado"] is False
    longa = "metilação " + "x" * (busca.MAX_CARACTERES + 100)
    assert busca.buscar_em(SKILLS, longa, GLOSSARIO, STOPWORDS)["truncado"] is True
    t0 = time.perf_counter()
    busca.buscar(" ".join(siglas), limite=0)  # índice real das 815 skills
    assert time.perf_counter() - t0 < 5


def test_rota_do_professor_nao_roda_no_event_loop():
    """`async def` com motor local e busca síncronos dentro parava o servidor inteiro
    enquanto o Professor pensava (até a 0.8.0): a rota tem de ser síncrona (threadpool)."""
    import inspect

    from omica.web import app as web

    assert not inspect.iscoroutinefunction(web.professor_perguntar)


# ---------------------------------------------------------------- dados versionados

def test_glossario_versionado_tem_proveniencia():
    copia = json.loads(build_index.GLOSSARIO_JSON.read_text(encoding="utf-8"))
    assert copia["_meta"]["n_termos"] == len(copia["termos"]) > 100
    assert len(copia["_meta"]["sha256"]) == 64
    assert copia["stopwords"]


def test_busca_real_portugues_igual_a_ingles():
    pt, en = busca.buscar("metilação", limite=0), busca.buscar("methylation", limite=0)
    assert pt["total"] == en["total"] > 0
    assert [s["id"] for s in pt["resultados"]] == [s["id"] for s in en["resultados"]]
    frase = busca.buscar("quero integrar os lotes do meu single-cell com scvi")
    assert frase["total"] > 0 and "quero" in frase["ignorados"]


def test_indice_versionado_tem_cards_dos_19_dominios():
    indice = json.loads(build_index.INDICE_JSON.read_text(encoding="utf-8"))
    assert set(indice["cards"]) == set(indice["dominios"])
    for cards in indice["cards"].values():
        for card in cards:
            assert card["titulo"] and card["quando"] and card["regras"] and card["stack"]


def test_api_skills_busca_no_servidor():
    r = _client().get("/api/skills", params={"q": "expressão diferencial", "dominio": "rnaseq-bulk"})
    assert r.status_code == 200
    dados = r.json()
    assert dados["total"] > 0 and dados["modo"] == "todos"
    assert {s["dominio"] for s in dados["resultados"]} == {"rnaseq-bulk"}
    assert dados["cards"] and dados["cards"][0]["quando"]
    assert {x["termo"] for x in dados["traducao"]} == {"expressão", "diferencial"}
    # sem domínio não vão cards; n limita o que volta
    geral = _client().get("/api/skills", params={"q": "deseq2", "n": 3}).json()
    assert geral["cards"] == [] and geral["mostrando"] == 3 < geral["total"]


def test_pagina_skills_aceita_busca_enderecavel():
    r = _client().get("/skills", params={"q": "bismark", "dominio": "epigenomics"})
    assert r.status_code == 200
    assert 'value="bismark"' in r.text
    assert '<option value="epigenomics" selected>' in r.text


# ---------------------------------------------------------------- elo módulo ↔ domínio

def test_todo_modulo_declara_dominios_que_a_biblioteca_tem():
    dominios = set(json.loads(build_index.INDICE_JSON.read_text(encoding="utf-8"))["dominios"])
    for m in load_registry().modules:
        assert m.skills_dominios, f"{m.id} sem skills_dominios"
        assert set(m.skills_dominios) <= dominios, f"{m.id}: {m.skills_dominios}"


def test_pagina_do_modulo_aponta_para_as_skills_relacionadas():
    r = _client().get("/modulos/genomica")
    assert r.status_code == 200
    assert "/skills?dominio=variants-popgen" in r.text
    assert "Skills relacionadas na biblioteca" in r.text


def test_pagina_da_skill_avisa_quando_a_plataforma_ja_cobre(tmp_path, monkeypatch):
    indice = json.loads(build_index.INDICE_JSON.read_text(encoding="utf-8"))
    skill = next(s for s in indice["skills"] if s["dominio"] == "rnaseq-bulk")
    pasta = tmp_path / "biblioteca" / skill["pasta"]
    pasta.mkdir(parents=True)
    (pasta / "SKILL.md").write_text(
        "---\nname: nome-interno\ndescription: Descrição completa da receita.\n---\n"
        "# Receita de teste\n\ncorpo", encoding="utf-8")
    monkeypatch.setenv("SKILLS_DIR", str(tmp_path / "biblioteca"))
    reset_config_cache()
    r = _client().get(f"/skills/{skill['pasta']}")
    assert r.status_code == 200
    assert "Receita de teste" in r.text and skill["id"] in r.text
    # front-matter não vaza como texto cru; a descrição completa aparece
    assert "Descrição completa da receita." in r.text and "name: nome-interno" not in r.text
    assert "/skills?dominio=rnaseq-bulk" in r.text      # volta para o domínio, não para /ajuda
    assert "/modulos/transcriptomica" in r.text         # análise validada neste domínio


# ---------------------------------------------------------------- reindex: cópia do glossário

@pytest.fixture()
def plugin_falso(tmp_path, monkeypatch):
    """Plugin de mentira com um glossário, e data/skills_glossario.json desviado para tmp."""
    fonte = tmp_path / "plugin" / build_index.GLOSSARIO_NO_PLUGIN
    fonte.parent.mkdir(parents=True)
    fonte.write_text(json.dumps({"stopwords": ["de"], "termos": {"metilacao": ["methylation"]}}),
                     encoding="utf-8")
    copia = tmp_path / "skills_glossario.json"
    monkeypatch.setattr(build_index, "GLOSSARIO_JSON", copia)
    monkeypatch.setenv("BIOINFO_PLUGIN_DIR", str(tmp_path / "plugin"))
    reset_config_cache()
    return fonte, copia


def test_reindex_copia_o_glossario_com_sha_da_origem(plugin_falso):
    fonte, copia = plugin_falso
    assert "1 termos copiados" in build_index.copiar_glossario()
    dados = json.loads(copia.read_text(encoding="utf-8"))
    assert dados["termos"] == {"metilacao": ["methylation"]} and dados["stopwords"] == ["de"]
    assert dados["_meta"]["sha256"] == build_index.sha_do_glossario(
        {"metilacao": ["methylation"]}, ["de"])
    # segunda vez: nada muda em disco (sem ruído no git a cada reindex)
    antes = copia.read_bytes()
    assert "já em dia" in build_index.copiar_glossario()
    assert copia.read_bytes() == antes


def test_reformatar_o_glossario_nao_e_mudanca(plugin_falso):
    # o sha é do CONTEÚDO: reindentar o JSON ou trocar o fim de linha (CRLF×LF) do
    # arquivo do plugin não pode fazer o doctor acusar cópia atrasada
    fonte, copia = plugin_falso
    build_index.copiar_glossario()
    copia_antes, fonte_antes = copia.read_bytes(), fonte.read_bytes()
    conteudo = {"termos": {"metilacao": ["methylation"]}, "stopwords": ["de"]}
    fonte.write_bytes(json.dumps(conteudo, indent=4).replace("\n", "\r\n").encode("utf-8"))
    assert hashlib.sha256(fonte.read_bytes()).digest() != hashlib.sha256(fonte_antes).digest()
    assert "já em dia" in build_index.copiar_glossario()
    assert copia.read_bytes() == copia_antes
    assert doctor._ponte_bioinfo().nivel == doctor.OK


def test_reindex_sem_plugin_nao_apaga_a_copia(plugin_falso, monkeypatch, tmp_path):
    _, copia = plugin_falso
    build_index.copiar_glossario()
    monkeypatch.setenv("BIOINFO_PLUGIN_DIR", str(tmp_path / "sumiu"))
    reset_config_cache()
    assert "mantida" in build_index.copiar_glossario()
    assert copia.exists()  # reindex dentro do container não pode matar a busca em português


def test_reindex_nao_copia_glossario_ilegivel(plugin_falso):
    fonte, copia = plugin_falso
    fonte.write_text("{ isto não é json", encoding="utf-8")
    assert build_index.copiar_glossario().startswith("AVISO")
    assert not copia.exists()


# ---------------------------------------------------------------- doctor

def test_doctor_ponte_ok_sem_plugin_a_vista():
    c = doctor._ponte_bioinfo()
    assert c.nivel == doctor.OK and "não visível" in c.detalhe


def test_doctor_acusa_copia_do_glossario_atrasada(plugin_falso):
    fonte, _ = plugin_falso
    build_index.copiar_glossario()
    assert doctor._ponte_bioinfo().nivel == doctor.OK
    fonte.write_text(json.dumps({"stopwords": [], "termos": {"novo": ["new"]}}), encoding="utf-8")
    c = doctor._ponte_bioinfo()
    assert c.nivel == doctor.AVISO and "ficou para trás" in c.detalhe


def test_doctor_reprova_dominio_que_a_biblioteca_nao_tem(monkeypatch):
    fantasma = Manifest(id="x", nome_pt="X", nome_en="X", skills_dominios=["dominio-que-nao-existe"])
    monkeypatch.setattr(doctor, "load_registry", lambda: Registry(modules=[fantasma]))
    c = doctor._ponte_bioinfo()
    assert c.nivel == doctor.FALHA and "dominio-que-nao-existe" in c.detalhe


# ---------------------------------------------------------------- Professor: fora do catálogo

class _RespostaFalsa:
    def __init__(self, conteudo):
        self._conteudo = conteudo

    def raise_for_status(self):
        pass

    def json(self):
        return {"message": {"content": self._conteudo}}


def _motor(monkeypatch, plano):
    monkeypatch.setattr(professor, "motor_disponivel", lambda: (True, "modelo-de-teste"))
    monkeypatch.setattr(professor.httpx, "post",
                        lambda *a, **kw: _RespostaFalsa(json.dumps(plano)))


FRASE = "quero integrar os lotes do meu single-cell com scvi"


def test_fora_do_catalogo_e_deterministico_e_nao_inventa():
    achado = professor.fora_do_catalogo(FRASE)
    assert achado and achado["skills"] and achado["total"] >= len(achado["skills"])
    assert len(achado["skills"]) <= 5
    assert professor.fora_do_catalogo("") is None            # sem termo não devolve as 815
    assert professor.fora_do_catalogo("de do para") is None  # só palavra de ligação
    assert professor.fora_do_catalogo("xyzinexistente") is None


def test_professor_oferece_a_biblioteca_quando_o_catalogo_nao_cobre(monkeypatch):
    _motor(monkeypatch, {"acao": "explicar", "explicacao": "a plataforma não cobre single-cell"})
    r = professor.perguntar(FRASE)
    assert r["status"] == "ok" and r["biblioteca"]["skills"]


def test_professor_nao_empurra_biblioteca_quando_o_catalogo_resolve(monkeypatch):
    _motor(monkeypatch, {"acao": "rodar_workflow", "modulo": "genomica",
                         "alvo": "wf_painel_demo", "explicacao": "x"})
    r = professor.perguntar("extrair o painel demo")
    assert r["status"] == "ok" and r["biblioteca"] is None


def test_professor_com_plano_invalido_ou_motor_fora_ainda_ajuda(monkeypatch):
    _motor(monkeypatch, {"acao": "rodar_script", "modulo": "genomica",
                         "alvo": "script_inventado", "explicacao": "x"})
    assert professor.perguntar(FRASE)["biblioteca"]["skills"]
    monkeypatch.setattr(professor, "motor_disponivel", lambda: (False, ""))
    r = professor.perguntar(FRASE)
    assert r["status"] == "motor_indisponivel" and r["biblioteca"]["skills"]


def test_pagina_do_professor_mostra_as_receitas_sem_botao_de_executar(monkeypatch):
    _motor(monkeypatch, {"acao": "explicar", "explicacao": "fora do catálogo"})
    r = _client().post("/professor/perguntar", data={"frase": FRASE})
    assert r.status_code == 200
    assert "Fora do catálogo" in r.text and "/skills?q=" in r.text
    receitas = r.text.split("Fora do catálogo")[1].split("</section>")[0]
    assert "/skills/" in receitas and "<form" not in receitas  # receita é para LER
