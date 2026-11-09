"""Web v2 multiespécie (M3, ESPEC §7): a 1ª página escolhe a espécie, o contexto vive na URL e,
fora de bos_taurus/demo, só PIPELINES executam. Mais os itens da crítica que caem na web:
médio 5 (relatório de run corrompido não vira 500), 8 (chaves i18n completas), 15 (erro de
carga visível no /saude, no doctor e na home) e 16 (subprojeto privado só com a autorização
do autor).

Cada regra nova tem canário que REPROVA sem ela e controle que passa; a prova por mutação
(cada regra desligada numa cópia do repositório) está em scratchpad\\v2impl_W\\mutacoes_w.py.
"""
from __future__ import annotations

import html
import json
import re
import urllib.parse
from typing import get_args, get_type_hints

import pytest
import yaml
from fastapi.testclient import TestClient
from fixtures_v2 import apontar_catalogo, escrever_subprojeto

from omica.config import REPO_ROOT, reset_config_cache

TOKEN = "tok-do-autor-para-teste-123"


def _c(**kw) -> TestClient:
    from omica.web.app import app

    return TestClient(app, **kw)


def _aceita_como_plugin(r) -> bool:
    """O MESMO predicado de bioinfo_omica.py:644-652 (cmd_submeter) do plugin Bioinfo."""
    destino = r.headers.get("location") or ""
    partes = urllib.parse.urlsplit(destino)
    consulta = {k: v for k, _, v in (p.partition("=") for p in partes.query.split("&") if p)}
    return (r.status_code == 303 and "aviso" not in consulta
            and partes.path.startswith(("/jobs/", "/workflows/")))


def _depois_do_topo(html: str) -> str:
    return html.split("</header>", 1)[1]


@pytest.fixture()
def humano(cfg):
    """Rascunho humano em <dados>/projetos — onde um rascunho criado pela UI mora."""
    return escrever_subprojeto(cfg.projetos_dir, "homo_sapiens", "matriz_teste",
                               titulo="Matriz de teste", pergunta="DE em matriz pronta de contagens humanas?",
                               pipelines=("transcriptomica_de",))


@pytest.fixture()
def bovino_invalido(cfg):
    return escrever_subprojeto(cfg.projetos_dir, "bos_taurus", "gen_aims_girolando", status="invalido",
                               assembly="ARS-UCD1.3", modulos=("genomica",),
                               validade="AIMs sem validação externa")


@pytest.fixture()
def privado(cfg):
    return escrever_subprojeto(cfg.projetos_dir, "homo_sapiens", "segredo_lab", privado=True,
                               titulo="Projeto sigiloso", pergunta="Pergunta inédita do laboratório X?")


@pytest.fixture()
def com_token(monkeypatch):
    monkeypatch.setenv("OMICA_API_TOKEN", TOKEN)
    return {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture()
def sem_formulario(monkeypatch):
    """Ler o formulário reprova o teste: a recusa tem de vir ANTES de `request.form()`."""
    import starlette.requests

    async def explode(self, *a, **k):
        raise AssertionError("request.form() chamado antes da recusa")

    monkeypatch.setattr(starlette.requests.Request, "form", explode)


@pytest.fixture()
def sem_dispatch(monkeypatch):
    from omica.jobs import dispatch

    def explode(*a, **k):
        raise AssertionError("dispatch chamado numa execução recusada")

    monkeypatch.setattr(dispatch, "lancar_script", explode)
    monkeypatch.setattr(dispatch, "lancar_workflow", explode)


# ------------------------------------------------------------------ página inicial
def test_home_mostra_as_3_especies_na_ordem_mesmo_sem_referencia(cfg):
    r = _c().get("/")
    assert r.status_code == 200
    posicoes = [r.text.index(f'data-especie="{e}"') for e in ("homo_sapiens", "mus_musculus", "bos_taurus")]
    assert posicoes == sorted(posicoes)                       # humano, camundongo, bovinos
    for e in ("homo_sapiens", "mus_musculus", "bos_taurus"):
        assert f'href="/e/{e}?lang=pt"' in r.text
    # nos testes GENOME_DATA é tmp: nenhuma ref no disco, as 3 aparecem "sem referência"
    assert r.text.count("badge estado-sem_referencia") == 3
    assert "0/7 referências presentes" in r.text              # bovino: 7 refs de arquivo
    assert "2 subprojeto(s) (2 executáveis)" in r.text        # demo + aula
    assert 'href="/modulos/' not in r.text and "/e/bos_taurus/demo/modulos" not in r.text
    assert 'id="erros-de-carga"' not in r.text                # controle: catálogo real carrega


def test_home_em_ingles(cfg):
    r = _c().get("/?lang=en")
    assert "Cattle (Bos taurus / Bos indicus)" in r.text and "no reference" in r.text


# ------------------------------------------------------------------ redirects v1
def test_get_modulo_antigo_e_307_com_query_crua():
    r = _c().get("/modulos/genomica?lang=en&aviso=exige%20Redis%20+%20worker", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/e/bos_taurus/demo/modulos/genomica?lang=en&aviso=exige%20Redis%20+%20worker"


def test_get_modulo_antigo_sem_query_nao_anexa_interrogacao():
    r = _c().get("/modulos/genomica", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/e/bos_taurus/demo/modulos/genomica"


def test_get_modulo_antigo_inexistente_nao_vira_307():
    r = _c().get("/modulos/fantasma", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"


@pytest.mark.parametrize("rota,falso", [
    ("scripts/extracao_demo", ("lancar_script", ("genomica-x-1", "thread"))),
    ("workflows/wf_painel_demo", ("lancar_workflow", ("wf-genomica-1", [], "thread"))),
])
def test_post_antigo_continua_303_e_o_plugin_aceita(cfg, monkeypatch, rota, falso):
    from omica.jobs import dispatch

    nome, retorno = falso
    chamadas = []
    monkeypatch.setattr(dispatch, nome, lambda *a, **k: chamadas.append((a, k)) or retorno)
    r = _c().post(f"/modulos/genomica/{rota}", follow_redirects=False)
    assert _aceita_como_plugin(r), (r.status_code, r.headers.get("location"))
    assert "ctx" not in chamadas[0][1]          # o alias é o código da v1: sem contexto


def test_post_antigo_recusado_volta_com_aviso_e_o_plugin_recusa(cfg):
    r = _c().post("/modulos/genomica/scripts/extracao_demo", follow_redirects=False)  # sem Redis
    assert r.status_code == 303 and r.headers["location"].startswith("/modulos/genomica?aviso=")
    assert not _aceita_como_plugin(r)


# ------------------------------------------------------------------ demo
def test_pagina_do_demo_tem_botoes_na_rota_canonica(cfg):
    r = _c().get("/e/bos_taurus/demo/modulos/genomica")
    assert r.status_code == 200
    assert 'action="/e/bos_taurus/demo/modulos/genomica/scripts/extracao_demo"' in r.text
    assert 'action="/e/bos_taurus/demo/modulos/genomica/workflows/wf_painel_demo"' in r.text
    assert 'action="/modulos/' not in r.text
    assert "Resultados (lidos do disco agora)" in r.text      # results_spec só no demo
    assert "genomica_painel" in r.text                         # pipelines do demo no módulo


@pytest.mark.parametrize("rota,nome,retorno,destino", [
    ("scripts/extracao_demo", "lancar_script", ("genomica-x-1", "thread"), "/jobs/genomica-x-1"),
    ("workflows/wf_painel_demo", "lancar_workflow", ("wf-genomica-1", [], "thread"), "/workflows/wf-genomica-1"),
])
def test_post_canonico_no_demo_passa(cfg, monkeypatch, rota, nome, retorno, destino):
    from omica.jobs import dispatch

    chamadas = []
    monkeypatch.setattr(dispatch, nome, lambda *a, **k: chamadas.append((a, k)) or retorno)
    r = _c().post(f"/e/bos_taurus/demo/modulos/genomica/{rota}", follow_redirects=False,
                  headers={"Origin": "http://testserver"})       # mesma origem passa
    assert _aceita_como_plugin(r) and r.headers["location"].startswith(destino)
    assert chamadas and chamadas[0][1].get("ctx") is None      # demo: o cmd da v1 (ctx=None)


def test_post_canonico_recusado_pelo_dispatch_volta_ao_demo_com_aviso(cfg):
    r = _c().post("/e/bos_taurus/demo/modulos/genomica/scripts/extracao_demo", follow_redirects=False)
    assert r.status_code == 303     # sem Redis: o runtime bioinfo é recusado, como na v1
    assert r.headers["location"].startswith("/e/bos_taurus/demo/modulos/genomica?aviso=")


def test_epigenomica_do_demo_mostra_o_aviso_do_painel_108(cfg):
    """Crítica v2, médio 12 (pedido da frente D): o aviso do demo que começa por "epigenômica:"
    aparece na página do módulo; controle: não aparece na genômica."""
    epi = _c().get("/e/bos_taurus/demo/modulos/epigenomica").text
    gen = _c().get("/e/bos_taurus/demo/modulos/genomica").text
    assert "painel_108 v1" in epi and "painel_108 v1" not in gen


# ------------------------------------------------------------------ fora do demo
@pytest.mark.parametrize("url", ["/e/bos_taurus/aula_granulosa_calor/modulos/transcriptomica",
                                 "/e/homo_sapiens/matriz_teste/modulos/transcriptomica"])
def test_fora_do_demo_nao_ha_botao_e_o_pipeline_aparece(cfg, humano, url):
    r = _c().get(url)
    assert r.status_code == 200
    corpo = _depois_do_topo(r.text)
    assert "<form" not in corpo                                # nem script, nem workflow
    assert "só pipelines executam" in corpo
    assert 'data-pipeline="transcriptomica_de"' in corpo
    assert 'data-selado-so-demo="wf_de_granulosa"' in corpo
    assert 'href="/e/bos_taurus/demo/modulos/transcriptomica?lang=pt"' in corpo   # ver no demo
    # resultados: NUNCA os do demo (results_spec) fora dele; só os runs deste subprojeto
    assert "Resultados (lidos do disco agora)" not in corpo
    assert "Resultados (dos runs deste subprojeto)" in corpo


@pytest.mark.parametrize("rota", ["scripts/deseq2_demo", "workflows/wf_de_granulosa"])
@pytest.mark.parametrize("ctx", ["bos_taurus/aula_granulosa_calor", "homo_sapiens/matriz_teste"])
def test_post_fora_do_demo_e_409_antes_de_ler_o_formulario(cfg, humano, sem_formulario, sem_dispatch,
                                                           rota, ctx):
    r = _c().post(f"/e/{ctx}/modulos/transcriptomica/{rota}", follow_redirects=False)
    assert r.status_code == 409
    assert r.headers["x-omica-recusa"] == "so_pipelines_fora_do_demo"
    assert 'data-motivo="so_pipelines_fora_do_demo"' in r.text


def test_subprojeto_invalido_bovino_e_409_nao_executavel(cfg, bovino_invalido, sem_formulario, sem_dispatch):
    url = "/e/bos_taurus/gen_aims_girolando"
    r = _c().post(f"{url}/modulos/genomica/scripts/extracao_demo", follow_redirects=False)
    assert r.status_code == 409 and r.headers["x-omica-recusa"] == "subprojeto_nao_executavel"
    pagina = _c().get(url)
    assert pagina.status_code == 200 and 'data-motivo="subprojeto_nao_executavel"' in pagina.text
    modulo = _c().get(f"{url}/modulos/genomica")
    assert "<form" not in _depois_do_topo(modulo.text)


@pytest.mark.parametrize("url,status,motivo", [
    ("/e/zz_inexistente/demo/modulos/genomica/scripts/extracao_demo", 404, "especie_inexistente"),
    ("/e/XX/demo/modulos/genomica/scripts/extracao_demo", 404, "especie_inexistente"),
    ("/e/bos_taurus/nao_existe/modulos/genomica/scripts/extracao_demo", 404, "subprojeto_inexistente"),
    ("/e/bos_taurus/sub03/modulos/genomica/workflows/wf_painel_demo", 404, "subprojeto_inexistente"),
    ("/e/bos_taurus/demo/modulos/fantasma/scripts/x", 422, "modulo_fora_do_subprojeto"),
])
def test_post_com_contexto_inexistente_e_recusa_e_nunca_500(cfg, sem_formulario, sem_dispatch, url, status, motivo):
    r = _c(raise_server_exceptions=False).post(url, follow_redirects=False)
    assert r.status_code == status and r.headers["x-omica-recusa"] == motivo, r.text[:300]


@pytest.mark.parametrize("url,destino", [
    ("/e/zz", "/"), ("/e/ZZ_maiusculo", "/"), ("/e/xx_inexistente", "/"),
    ("/e/bos_taurus/sub03", "/e/bos_taurus"), ("/e/bos_taurus/nao_existe", "/e/bos_taurus"),
    ("/e/bos_taurus/Demo", "/e/bos_taurus"),
    ("/e/homo_sapiens/matriz_teste/modulos/ml", "/e/homo_sapiens/matriz_teste"),       # fora da espécie
    ("/e/bos_taurus/aula_granulosa_calor/modulos/genomica", "/e/bos_taurus/aula_granulosa_calor"),  # fora do sub
    ("/e/bos_taurus/demo/modulos/fantasma", "/e/bos_taurus/demo"),
])
def test_contexto_invalido_redireciona_sem_500(cfg, humano, url, destino):
    r = _c(raise_server_exceptions=False).get(url, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == destino


def test_travessia_nunca_e_500(cfg):
    for url in ("/e/bos_taurus/..%2F..", "/e/..%2F..%2Fetc", "/e/bos_taurus/demo/modulos/..%2F..%2Fx"):
        assert _c(raise_server_exceptions=False).get(url, follow_redirects=False).status_code in (303, 404), url


# ------------------------------------------------------------------ espécie e subprojeto
def test_pagina_especie_e_subprojeto_fora_do_demo(cfg, humano):
    r = _c().get("/e/homo_sapiens")
    assert r.status_code == 200
    assert "Matriz de teste" in r.text and "badge sub-draft" in r.text and "rascunho" in r.text
    assert "GRCh38" in r.text and "badge estado-sem_referencia" in r.text
    r = _c().get("/e/homo_sapiens/matriz_teste")
    assert r.status_code == 200
    assert "DE em matriz pronta de contagens humanas?" in r.text
    assert "Amostras (2)" in r.text and "<td>A1</td>" in r.text
    texto = html.unescape(r.text)                               # o JSON vai escapado no <pre>
    assert '"especie": "homo_sapiens"' in texto and '"subprojeto": "matriz_teste"' in texto  # como executar
    assert 'href="/e/homo_sapiens/matriz_teste/modulos/transcriptomica?lang=pt"' in r.text


def test_especie_lista_o_demo_primeiro(cfg):
    r = _c().get("/e/bos_taurus")
    assert r.text.index('data-subprojeto="demo"') < r.text.index('data-subprojeto="aula_granulosa_calor"')


def test_pagina_do_demo(cfg):
    r = _c().get("/e/bos_taurus/demo")
    assert r.status_code == 200
    assert "reproduzem, de ponta a ponta" in r.text            # a pergunta do demo
    assert "Amostras (14)" in r.text and "epigenômica: painel_108 v1" in r.text
    for mod in ("genomica", "epigenomica", "transcriptomica", "ml"):
        assert f'href="/e/bos_taurus/demo/modulos/{mod}?lang=pt"' in r.text
    for p in ("transcriptomica_de", "genomica_painel", "genomica_regioes"):
        assert f"<code>{p}</code>" in r.text
    # datasets/dados legados: só exists(); modules/... existe, /data/... não existe nos testes
    assert r.text.count("✅") >= 3 and "❌" in r.text


def test_pagina_do_subprojeto_corta_amostras_em_15_e_arquivos_em_50(cfg):
    linhas = "".join(f"A{i}\tc\n" for i in range(20))
    escrever_subprojeto(cfg.projetos_dir, "homo_sapiens", "muitas_coisas", amostras_tsv="amostra\tcondicao\n" + linhas,
                        dados_legados=[f"/data/legado_{i}.tsv" for i in range(60)])
    r = _c().get("/e/homo_sapiens/muitas_coisas")
    assert r.status_code == 200 and "Amostras (20)" in r.text
    assert r.text.count("<td>A") == 15 and "<td>A15</td>" not in r.text
    assert r.text.count("<code>/data/legado_") == 50 and "lista cortada nos primeiros 50" in r.text


def test_arquivado_so_aparece_com_arquivados_1(cfg, humano):
    escrever_subprojeto(cfg.projetos_dir, "homo_sapiens", "velho_arquivado", status="arquivado",
                        titulo="Projeto arquivado", validade="refeito noutro subprojeto")
    assert "Projeto arquivado" not in _c().get("/e/homo_sapiens").text
    com = _c().get("/e/homo_sapiens?arquivados=1").text
    assert "Projeto arquivado" in com and "Matriz de teste" in com


def test_runs_do_subprojeto_aparecem_com_link_do_relatorio(cfg):
    rid = "transcriptomica_de-20261029-120000-abcdef"
    pasta = cfg.work_dir / "runs" / rid
    pasta.mkdir(parents=True)
    (pasta / "run.json").write_text(json.dumps({
        "run_id": rid, "pipeline": "transcriptomica_de", "modulo": "transcriptomica", "estado": "concluido",
        "criado_em": 1761739200.0, "especie": "bos_taurus", "subprojeto": "aula_granulosa_calor"}),
        encoding="utf-8")
    rel = cfg.results_dir / "projetos/bos_taurus/aula_granulosa_calor/transcriptomica/runs" / rid / "relatorio"
    rel.mkdir(parents=True)
    (rel / "relatorio.html").write_text("<p>relatório da aula</p>", encoding="utf-8")
    pagina = _c().get("/e/bos_taurus/aula_granulosa_calor").text
    assert rid in pagina and f'href="/relatorios/{rid}/"' in pagina
    assert rid in _c().get("/e/bos_taurus/aula_granulosa_calor/modulos/transcriptomica").text
    assert rid not in _c().get("/e/bos_taurus/demo").text           # controle: outro subprojeto
    assert "relatório da aula" in _c().get(f"/relatorios/{rid}/").text  # layout novo (projetos/)


# ------------------------------------------------------------------ /skills
def test_skills_sem_indice_e_503_e_nao_redireciona(monkeypatch):
    from omica.skills import busca

    monkeypatch.setattr(busca, "carregar_indice", lambda: None)
    r = _c().get("/skills", follow_redirects=False)
    assert r.status_code == 503 and "reindex" in r.text and 'id="skills-sem-indice"' in r.text


def test_skills_com_indice_continua_200():
    r = _c().get("/skills", follow_redirects=False)
    assert r.status_code == 200 and 'id="skills-sem-indice"' not in r.text


# ------------------------------------------------------------------ rascunho pela UI
def _ligar_rascunho(monkeypatch) -> dict:
    """Liga a flag e configura o token; devolve o cabeçalho de quem está autorizado (criar
    rascunho exige a autorização do autor — verificação v2, interfaces)."""
    monkeypatch.setenv("OMICA_PERMITIR_RASCUNHO", "1")
    monkeypatch.setenv("OMICA_API_TOKEN", TOKEN)
    reset_config_cache()
    return {"Authorization": f"Bearer {TOKEN}"}


_RASCUNHO = {"slug": "meu_rascunho", "titulo": "Meu rascunho", "pergunta": "Há DE entre os grupos A e B?",
             "assembly": "GRCh38"}


def test_rascunho_desligado_recusa_sem_ler_nem_gravar(cfg, monkeypatch, sem_formulario):
    from omica import subprojetos

    monkeypatch.setattr(subprojetos, "criar_rascunho", lambda *a, **k: pytest.fail("criar_rascunho chamado"))
    r = _c().post("/e/homo_sapiens/novo-subprojeto", data=_RASCUNHO, follow_redirects=False)
    assert r.status_code == 403 and r.headers["x-omica-recusa"] == "rascunho_desligado"
    assert not cfg.projetos_dir.exists()
    # a página e o botão dizem por quê
    assert 'data-motivo="rascunho_desligado"' in _c().get("/e/homo_sapiens").text
    form = _c().get("/e/homo_sapiens/novo-subprojeto")
    assert form.status_code == 200 and "<fieldset disabled" in form.text


def test_rascunho_ligado_cria_privado_e_redireciona(cfg, monkeypatch, com_token):
    aut = _ligar_rascunho(monkeypatch)
    amostras = b"amostra\tcondicao\nA1\tc\nA2\tt\n"
    r = _c(headers=aut).post("/e/homo_sapiens/novo-subprojeto", data=_RASCUNHO, follow_redirects=False,
                  files={"amostras": ("amostras.tsv", amostras, "text/tab-separated-values")})
    assert r.status_code == 303 and r.headers["location"] == "/e/homo_sapiens/meu_rascunho"
    pasta = cfg.projetos_dir / "homo_sapiens" / "meu_rascunho"
    dados = yaml.safe_load((pasta / "subprojeto.yaml").read_text(encoding="utf-8"))
    assert (dados["status"], dados["privado"], dados["pergunta"]) == ("draft", True, _RASCUNHO["pergunta"])
    assert (pasta / "amostras.tsv").read_bytes() == amostras
    # nasce privado: sem autorização não aparece; com o token, sim
    assert _c().get("/e/homo_sapiens/meu_rascunho", follow_redirects=False).status_code == 303
    assert "Amostras (2)" in _c().get("/e/homo_sapiens/meu_rascunho", headers=com_token).text


def _multipart_do_navegador(campos: dict) -> tuple[bytes, dict]:
    """O corpo que o NAVEGADOR manda com o <input type=file> vazio: a parte do arquivo vai com
    `filename=""` (o Starlette a entrega como UploadFile vazio). O httpx omite o filename vazio
    e a parte chegaria como texto — não exercitaria o caso."""
    partes = [f'--XX\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
              for k, v in campos.items()]
    partes.append(b'--XX\r\nContent-Disposition: form-data; name="amostras"; filename=""\r\n'
                  b"Content-Type: application/octet-stream\r\n\r\n\r\n--XX--\r\n")
    return b"".join(partes), {"content-type": "multipart/form-data; boundary=XX"}


def test_rascunho_sem_arquivo_escolhido_nao_grava_amostras(cfg, monkeypatch):
    aut = _ligar_rascunho(monkeypatch)
    corpo, cabecalhos = _multipart_do_navegador(_RASCUNHO)
    r = _c(headers=aut).post("/e/homo_sapiens/novo-subprojeto", content=corpo, headers=cabecalhos,
                  follow_redirects=False)
    assert r.status_code == 303, r.text[-300:]
    assert (cfg.projetos_dir / "homo_sapiens" / "meu_rascunho" / "subprojeto.yaml").is_file()
    assert not (cfg.projetos_dir / "homo_sapiens" / "meu_rascunho" / "amostras.tsv").exists()


@pytest.mark.parametrize("mudanca,erro", [
    ({"pergunta": "curta"}, "pergunta_curta"),
    ({"slug": "sub03"}, "slug_invalido"),
    ({"titulo": ""}, "titulo_invalido"),
    ({"assembly": "GRCm39"}, "assembly_invalida"),
    ({"slug": "matriz_teste"}, "id_duplicado"),
])
def test_rascunho_recusado_volta_ao_formulario_com_422(cfg, humano, monkeypatch, mudanca, erro):
    aut = _ligar_rascunho(monkeypatch)
    dados = {**_RASCUNHO, **mudanca}
    r = _c(headers=aut).post("/e/homo_sapiens/novo-subprojeto", data=dados, follow_redirects=False)
    assert r.status_code == 422 and f'data-erro="{erro}"' in r.text
    assert f'value="{dados["slug"]}"' in r.text                 # o que o autor digitou volta
    assert "[rascunho_erro_" not in r.text                      # mensagem traduzida


def test_rascunho_amostras_grandes_demais_e_422(cfg, monkeypatch):
    from omica.subprojetos import AMOSTRAS_MAX_BYTES

    aut = _ligar_rascunho(monkeypatch)
    grande = b"amostra\tc\n" + b"A\tc\n" * (AMOSTRAS_MAX_BYTES // 4 + 10)
    r = _c(headers=aut).post("/e/homo_sapiens/novo-subprojeto", data=_RASCUNHO, follow_redirects=False,
                  files={"amostras": ("amostras.tsv", grande, "text/plain")})
    assert r.status_code == 422 and 'data-erro="amostras_invalidas"' in r.text
    assert not (cfg.projetos_dir / "homo_sapiens" / "meu_rascunho").exists()


def test_rascunho_le_no_maximo_1_mib_mais_1_byte(cfg, monkeypatch):
    """O upload é lido até o teto + 1 byte (o núcleo recusa o que passar): nunca o arquivo inteiro
    para a memória do servidor."""
    from omica import subprojetos

    recebido = []

    def falso(*a, amostras=None, **k):
        recebido.append(len(amostras))
        raise subprojetos.RascunhoRecusado("amostras_invalidas")

    aut = _ligar_rascunho(monkeypatch)
    monkeypatch.setattr(subprojetos, "criar_rascunho", falso)
    teto = subprojetos.AMOSTRAS_MAX_BYTES
    r = _c(headers=aut).post("/e/homo_sapiens/novo-subprojeto", data=_RASCUNHO, follow_redirects=False,
                  files={"amostras": ("amostras.tsv", b"x" * (teto + 4096), "text/plain")})
    assert r.status_code == 422 and recebido == [teto + 1]


def test_rascunho_de_outra_origem_e_403(cfg, monkeypatch, sem_formulario):
    aut = _ligar_rascunho(monkeypatch)
    r = _c(headers=aut).post("/e/homo_sapiens/novo-subprojeto", data=_RASCUNHO, follow_redirects=False,
                  headers={"Origin": "http://evil"})
    assert r.status_code == 403 and r.headers["x-omica-recusa"] == "origem_recusada"
    assert not cfg.projetos_dir.exists()


def test_rascunho_em_especie_inexistente_e_404(cfg, monkeypatch):
    aut = _ligar_rascunho(monkeypatch)
    r = _c(headers=aut).post("/e/xx_inexistente/novo-subprojeto", data=_RASCUNHO, follow_redirects=False)
    assert r.status_code == 404 and r.headers["x-omica-recusa"] == "especie_inexistente"


def test_rascunho_sem_autorizacao_e_403_sem_ler_nem_gravar(cfg, monkeypatch, sem_formulario):
    """Canário (verificação v2, interfaces): com a flag ligada e sem a autorização do autor,
    criar rascunho é 403 ANTES de ler o formulário. O slug de um PRIVADO e um slug livre dão a
    mesma resposta (nada de oráculo 422 id_duplicado × 303) e nada é gravado. O controle com o
    token está em test_rascunho_ligado_cria_privado_e_redireciona."""
    from omica import subprojetos

    escrever_subprojeto(cfg.projetos_dir, "homo_sapiens", "segredo_lab", privado=True)
    _ligar_rascunho(monkeypatch)
    monkeypatch.setattr(subprojetos, "criar_rascunho", lambda *a, **k: pytest.fail("criar_rascunho chamado"))
    for slug in ("segredo_lab", "slug_livre"):
        r = _c().post("/e/homo_sapiens/novo-subprojeto", data={**_RASCUNHO, "slug": slug},
                      follow_redirects=False)
        assert r.status_code == 403 and r.headers["x-omica-recusa"] == "rascunho_sem_autorizacao"
    assert not (cfg.projetos_dir / "homo_sapiens" / "slug_livre").exists()


def test_rascunho_botao_e_formulario_so_com_autorizacao(cfg, monkeypatch):
    aut = _ligar_rascunho(monkeypatch)
    sem = _c().get("/e/homo_sapiens").text
    assert 'data-motivo="rascunho_sem_autorizacao"' in sem and 'href="/e/homo_sapiens/novo-subprojeto' not in sem
    form = _c().get("/e/homo_sapiens/novo-subprojeto")
    assert "<fieldset disabled" in form.text and 'data-motivo="rascunho_sem_autorizacao"' in form.text
    com = _c(headers=aut).get("/e/homo_sapiens").text
    assert 'href="/e/homo_sapiens/novo-subprojeto' in com
    assert "<fieldset disabled" not in _c(headers=aut).get("/e/homo_sapiens/novo-subprojeto").text


def test_rota_de_rascunho_nao_e_engolida_pela_do_subprojeto(cfg):
    # declarada ANTES de /e/{esp}/{sub}: senão "novo-subprojeto" viraria slug inválido (303)
    assert _c().get("/e/homo_sapiens/novo-subprojeto", follow_redirects=False).status_code == 200


# ------------------------------------------------------------------ origem
def test_post_canonico_de_outra_origem_e_403(cfg, sem_formulario, sem_dispatch):
    r = _c().post("/e/bos_taurus/demo/modulos/genomica/scripts/extracao_demo",
                  headers={"Origin": "http://evil"}, follow_redirects=False)
    assert r.status_code == 403 and r.headers["x-omica-recusa"] == "origem_recusada"
    r = _c().post("/e/bos_taurus/demo/modulos/genomica/workflows/wf_painel_demo",
                  headers={"Origin": "null"}, follow_redirects=False)
    assert r.status_code == 403


# ------------------------------------------------------------------ /jobs
def test_jobs_filtra_por_contexto_e_job_sem_campo_e_do_demo(cfg):
    from omica.jobs import queue

    queue.write_status("demo-20261029-000000-aaaaaa", "done")                      # job da v1
    queue.write_status("transcriptomica-20261029-000001-bbbbbb", "done",
                       especie="homo_sapiens", subprojeto="matriz_teste")
    humano = _c().get("/jobs?especie=homo_sapiens").text
    assert "transcriptomica-20261029-000001-bbbbbb" in humano and "demo-20261029-000000-aaaaaa" not in humano
    demo = _c().get("/jobs?especie=bos_taurus&sub=demo").text
    assert "demo-20261029-000000-aaaaaa" in demo and "transcriptomica-20261029-000001-bbbbbb" not in demo
    todos = _c().get("/jobs").text
    assert "<code>bos_taurus/demo</code>" in todos and "<code>homo_sapiens/matriz_teste</code>" in todos


def test_jobs_filtro_olha_alem_dos_50_mais_recentes(cfg):
    """Crítica v2 (baixo): o filtro vem depois do corte de listar_jobs; com filtro, o corte é mais
    largo. Um job humano mais antigo que 50 jobs do demo continua achável pelo filtro."""
    import os

    from omica.jobs import queue

    antigo = "transcriptomica-20261028-000000-ffffff"
    queue.write_status(antigo, "done", especie="homo_sapiens", subprojeto="matriz_teste")
    os.utime(cfg.jobs_dir / antigo, (1_000_000_000, 1_000_000_000))
    for i in range(55):
        queue.write_status(f"demo-20261029-0000{i:02d}-aaaaaa", "done")
    assert antigo not in _c().get("/jobs").text                       # controle: fora dos 50 recentes
    assert antigo in _c().get("/jobs?especie=homo_sapiens").text


# ------------------------------------------------------- médio 5: relatório de run corrompido
_RID = "transcriptomica_de-20261029-130000-abcdef"


def _run_json(cfg, **campos):
    pasta = cfg.work_dir / "runs" / _RID
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "run.json").write_text(json.dumps(campos), encoding="utf-8")


@pytest.mark.parametrize("campos", [
    {"run_id": _RID, "modulo": "transcriptomica", "resultados": "../../x"},
    {"run_id": _RID, "modulo": "transcriptomica", "especie": "../x", "subprojeto": "demo"},
    {"run_id": _RID, "modulo": "transcriptomica", "especie": "bos_taurus"},   # só um dos dois
    {"run_id": "outro", "modulo": "transcriptomica"},
    {"run_id": _RID, "modulo": "../genomica"},
    ["nem", "dicionario"],
])
def test_relatorio_de_run_corrompido_e_303_nunca_500(cfg, campos):
    if isinstance(campos, list):
        pasta = cfg.work_dir / "runs" / _RID
        pasta.mkdir(parents=True, exist_ok=True)
        (pasta / "run.json").write_text(json.dumps(campos), encoding="utf-8")
    else:
        _run_json(cfg, **campos)
    c = _c(raise_server_exceptions=False)
    for url in (f"/relatorios/{_RID}", f"/relatorios/{_RID}/", f"/relatorios/{_RID}/figuras/volcano.png"):
        r = c.get(url, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/", (url, r.status_code)


def test_relatorio_do_demo_legado_continua(cfg):
    """Controle: run de antes da v2 (sem espécie/subprojeto) é do demo, layout results/<mod>/runs."""
    _run_json(cfg, run_id=_RID, modulo="transcriptomica")
    rel = cfg.results_dir / "transcriptomica" / "runs" / _RID / "relatorio"
    rel.mkdir(parents=True)
    (rel / "relatorio.html").write_text("<p>legado</p>", encoding="utf-8")
    assert "legado" in _c().get(f"/relatorios/{_RID}/").text


# ------------------------------------------------- médio 15: erro de carga visível
def test_saude_tem_os_erros_de_carga_vazios_no_repositorio_real(cfg):
    corpo = _c().get("/saude").json()
    assert corpo["erros_especies"] == {} and corpo["erros_subprojetos"] == {}
    assert {"versao", "fila_backend", "modulos_validos", "erros_manifests"} <= set(corpo)   # chaves v1


@pytest.fixture()
def especie_quebrada(tmp_path, monkeypatch):
    pasta = tmp_path / "especies_teste"
    pasta.mkdir()
    for arq in (REPO_ROOT / "especies").glob("*.yaml"):
        (pasta / arq.name).write_bytes(arq.read_bytes())
    (pasta / "zz_quebrada.yaml").write_text("id: outra_coisa\nnome_cientifico: X\n", encoding="utf-8")
    apontar_catalogo(monkeypatch, pasta, REPO_ROOT / "projetos")
    return pasta


def test_especie_quebrada_aparece_na_saude_na_home_e_no_doctor(cfg, especie_quebrada):
    from omica import doctor

    erros = _c().get("/saude").json()["erros_especies"]
    assert any("zz_quebrada" in k for k in erros), erros
    home = _c().get("/").text
    assert 'id="erros-de-carga"' in home and "zz_quebrada" in home
    assert home.count("data-especie=") == 3                      # as boas continuam
    checagens = {c.nome: c for c in doctor._especies_e_subprojetos()}
    assert checagens["Espécies (especies/*.yaml)"].nivel == doctor.FALHA
    assert "zz_quebrada" in checagens["Espécies (especies/*.yaml)"].detalhe


def test_doctor_controle_catalogo_real_ok(cfg):
    from omica import doctor

    niveis = {c.nome: c.nivel for c in doctor._especies_e_subprojetos()}
    assert set(niveis.values()) == {doctor.OK} and len(niveis) == 2
    assert "Espécies (especies/*.yaml)" in _c().get("/diagnostico").text


def test_subprojeto_quebrado_em_dados_e_aviso_e_so_contado_sem_autorizacao(cfg, com_token):
    from omica import doctor

    pasta = cfg.projetos_dir / "homo_sapiens" / "quebrado"
    pasta.mkdir(parents=True)
    (pasta / "subprojeto.yaml").write_text("id: homo_sapiens/quebrado\ntitulo: Segredo no erro\n",
                                           encoding="utf-8")
    sem = _c().get("/saude").json()["erros_subprojetos"]
    assert list(sem) == ["dados:*"] and sem["dados:*"].startswith("1 subprojeto(s)")
    com = _c().get("/saude", headers=com_token).json()["erros_subprojetos"]
    assert "dados:homo_sapiens/quebrado" in com
    sub = {c.nome: c for c in doctor._especies_e_subprojetos()}["Subprojetos (projetos/ e <dados>/projetos)"]
    assert sub.nivel == doctor.AVISO and "dados:homo_sapiens/quebrado" in sub.detalhe   # CLI: tudo
    oculto = {c.nome: c for c in doctor._especies_e_subprojetos(detalhar_privados=False)}
    assert "quebrado" not in oculto["Subprojetos (projetos/ e <dados>/projetos)"].detalhe
    assert "dados:homo_sapiens/quebrado" not in _c().get("/diagnostico").text
    assert "dados:homo_sapiens/quebrado" in _c().get("/diagnostico", headers=com_token).text
    assert "dados:homo_sapiens/quebrado" not in _c().get("/e/homo_sapiens").text


def test_catalogo_que_quebra_inteiro_aparece_sem_a_mensagem(cfg, monkeypatch):
    """/saude da UI não pede credencial: a falha do catálogo inteiro sai com a classe da exceção,
    não com a mensagem (que pode trazer caminho do host); o doctor reprova."""
    from omica import doctor, especies

    def explode(*a, **k):
        raise RuntimeError("C:\\segredo\\do\\host")

    monkeypatch.setattr(especies, "carregar_especies", explode)
    erros = _c().get("/saude").json()["erros_especies"]
    assert erros == {"_catalogo": "falha ao carregar o catálogo: RuntimeError"}
    assert doctor._especies_e_subprojetos()[0].nivel == doctor.FALHA


def test_subprojeto_quebrado_no_repositorio_e_falha(cfg, tmp_path, monkeypatch):
    from omica import doctor

    repo = tmp_path / "projetos_teste"
    escrever_subprojeto(repo, "bos_taurus", "x_quebrado", assembly="ARS-UCD1.3", tipo="inventado")
    apontar_catalogo(monkeypatch, REPO_ROOT / "especies", repo)
    sub = {c.nome: c for c in doctor._especies_e_subprojetos()}["Subprojetos (projetos/ e <dados>/projetos)"]
    assert sub.nivel == doctor.FALHA and "repo:bos_taurus/x_quebrado" in sub.detalhe
    assert "repo:bos_taurus/x_quebrado" in _c().get("/saude").json()["erros_subprojetos"]


# ------------------------------------------------- médio 16: privado só com autorização
def test_privado_some_sem_autorizacao(cfg, privado, sem_dispatch):
    c = _c(raise_server_exceptions=False)
    especie = c.get("/e/homo_sapiens").text
    assert "segredo_lab" not in especie and "Projeto sigiloso" not in especie
    for url in ("/e/homo_sapiens/segredo_lab", "/e/homo_sapiens/segredo_lab/modulos/transcriptomica"):
        r = c.get(url, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/e/homo_sapiens", url
    # POST: indistinguível de inexistente (nada de oráculo de existência)
    r = c.post("/e/homo_sapiens/segredo_lab/modulos/transcriptomica/scripts/deseq2_demo", follow_redirects=False)
    assert r.status_code == 404 and r.headers["x-omica-recusa"] == "subprojeto_inexistente"
    card = c.get("/").text.split('data-especie="homo_sapiens"', 1)[1].split("</a>", 1)[0]
    assert "0 subprojeto(s) (0 executáveis)" in card              # nem conta na home


def test_privado_conta_na_home_com_autorizacao(cfg, privado, com_token):
    """Controle do anterior: o mesmo card, com o token, conta o privado."""
    card = _c().get("/", headers=com_token).text.split('data-especie="homo_sapiens"', 1)[1].split("</a>", 1)[0]
    assert "1 subprojeto(s) (1 executáveis)" in card


def test_privado_controle_publico_aparece(cfg, humano):
    assert "Matriz de teste" in _c().get("/e/homo_sapiens").text


def test_privado_aparece_com_o_token_e_some_ao_sair(cfg, privado, com_token):
    c = _c()
    r = c.post("/autorizacao", data={"token": TOKEN, "volta": "/e/homo_sapiens"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/e/homo_sapiens"
    cookie = c.cookies.get("omica_autor")
    assert cookie and TOKEN not in cookie                      # o cookie leva o HMAC, não o token
    assert "Projeto sigiloso" in c.get("/e/homo_sapiens").text
    assert "Pergunta inédita do laboratório X?" in c.get("/e/homo_sapiens/segredo_lab").text
    c.post("/autorizacao", data={"token": "", "volta": "/e/homo_sapiens"}, follow_redirects=False)
    assert "Projeto sigiloso" not in c.get("/e/homo_sapiens").text


def test_autorizacao_com_token_errado_e_403(cfg, privado, com_token):
    c = _c()
    r = c.post("/autorizacao", data={"token": "chute", "volta": "/"}, follow_redirects=False)
    assert r.status_code == 403 and r.headers["x-omica-recusa"] == "autorizacao_recusada"
    assert "Projeto sigiloso" not in c.get("/e/homo_sapiens").text


def test_autorizacao_sem_token_configurado_nunca_abre(cfg, privado):
    c = _c()
    r = c.post("/autorizacao", data={"token": "qualquer"}, follow_redirects=False)
    assert r.status_code == 403
    c.cookies.set("omica_autor", "forjado")
    assert "Projeto sigiloso" not in c.get("/e/homo_sapiens", headers={"Authorization": "Bearer "}).text


def test_trocar_o_token_revoga_o_cookie(cfg, privado, monkeypatch, com_token):
    c = _c()
    c.post("/autorizacao", data={"token": TOKEN}, follow_redirects=False)
    assert "Projeto sigiloso" in c.get("/e/homo_sapiens").text
    monkeypatch.setenv("OMICA_API_TOKEN", "token-novo-depois-do-vazamento")
    assert "Projeto sigiloso" not in c.get("/e/homo_sapiens").text


def test_autorizacao_nao_redireciona_para_fora(cfg, com_token):
    for volta in ("https://evil.example/", "//evil.example", "/e/../../x", "/jobs"):
        r = _c().post("/autorizacao", data={"token": TOKEN, "volta": volta}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/", volta


def test_autorizacao_de_outra_origem_e_403(cfg, com_token):
    r = _c().post("/autorizacao", data={"token": TOKEN}, headers={"Origin": "http://evil"},
                  follow_redirects=False)
    assert r.status_code == 403 and "omica_autor" not in r.headers.get("set-cookie", "")


def test_jobs_mascaram_o_subprojeto_privado(cfg, privado, com_token):
    from omica.jobs import queue

    queue.write_status("transcriptomica-20261029-000002-cccccc", "done",
                       especie="homo_sapiens", subprojeto="segredo_lab")
    sem = _c().get("/jobs").text
    assert "segredo_lab" not in sem and "🔒 privado" in sem
    assert "transcriptomica-20261029-000002-cccccc" not in _c().get("/jobs?sub=segredo_lab").text
    assert "homo_sapiens/segredo_lab" in _c().get("/jobs", headers=com_token).text


@pytest.mark.parametrize("sub,oculto", [("segredo_lab", True), ("matriz_teste", False)])
def test_job_de_privado_detalhe_e_console_so_com_autorizacao(cfg, privado, humano, com_token, sub, oculto):
    """O console do job traz o cmd com a pasta do subprojeto: o detalhe e o SSE de um job de
    subprojeto privado também somem sem a autorização. Controle: o job do público aparece."""
    from omica.jobs import queue

    jid = "transcriptomica-20261029-000003-dddddd"
    queue.write_status(jid, "done", especie="homo_sapiens", subprojeto=sub)
    queue.append_log(jid, f"--saida /dados/results/projetos/homo_sapiens/{sub}/x")
    detalhe = _c().get(f"/jobs/{jid}", follow_redirects=False)
    console = _c().get(f"/jobs/{jid}/events")
    if oculto:
        assert detalhe.status_code == 303 and detalhe.headers["location"] == "/jobs"
        assert console.status_code == 404 and sub not in console.text
    else:
        assert detalhe.status_code == 200 and sub in console.text
    assert _c().get(f"/jobs/{jid}", headers=com_token).status_code == 200
    assert sub in _c().get(f"/jobs/{jid}/events", headers=com_token).text


@pytest.mark.parametrize("sub,oculto", [("segredo_lab", True), ("matriz_teste", False)])
def test_relatorio_e_resultado_de_privado_so_com_autorizacao(cfg, privado, humano, com_token, sub, oculto):
    rid = "transcriptomica_de-20261029-140000-abcdef"
    pasta = cfg.work_dir / "runs" / rid
    pasta.mkdir(parents=True)
    (pasta / "run.json").write_text(json.dumps({
        "run_id": rid, "pipeline": "transcriptomica_de", "modulo": "transcriptomica",
        "especie": "homo_sapiens", "subprojeto": sub}), encoding="utf-8")
    base = cfg.results_dir / "projetos" / "homo_sapiens" / sub / "transcriptomica" / "runs" / rid
    (base / "relatorio" / "figuras").mkdir(parents=True)
    (base / "relatorio" / "relatorio.html").write_text("<p>conteúdo do relatório</p>", encoding="utf-8")
    (base / "relatorio" / "figuras" / "volcano.png").write_bytes(b"\x89PNG")
    (base / "de").mkdir()
    (base / "de" / "de_table.tsv").write_text("gene\tlfc\n", encoding="utf-8")
    rel = f"projetos/homo_sapiens/{sub}/transcriptomica/runs/{rid}/de/de_table.tsv"
    urls = (f"/relatorios/{rid}", f"/relatorios/{rid}/", f"/relatorios/{rid}/figuras/volcano.png",
            f"/resultados/{rel}")
    for url in urls:
        r = _c().get(url, follow_redirects=False)
        assert (r.status_code == 303 and r.headers["location"] == "/") is oculto, (url, r.status_code)
        assert _c().get(url, headers=com_token, follow_redirects=False).status_code in (200, 307), url
    if oculto:   # no Windows o disco ignora a caixa: a variação não fura a conferência
        r = _c().get(f"/resultados/{rel.replace('projetos/', 'Projetos/')}", follow_redirects=False)
        assert r.status_code in (303, 404)


# ------------------------------------------------------------------ i18n (médio 8)
_CHAVE_ESTATICA = re.compile(r"""\bt\(\s*'([a-z0-9_]+)'\s*,""")


def _sem_traducao(chaves) -> list[str]:
    from omica.web.i18n import TRANSLATIONS

    return sorted(k for k in chaves
                  if not (TRANSLATIONS.get(k, {}).get("pt") and TRANSLATIONS.get(k, {}).get("en")))


def _motivos_de_recusa() -> set[str]:
    """Todo motivo que chega ao recusa.html: os do núcleo e os que a própria UI levanta."""
    from omica import contexto

    fonte = (REPO_ROOT / "omica" / "web" / "app.py").read_text(encoding="utf-8")
    da_ui = set(re.findall(r'_recusa\(request, "([a-z_]+)"', fonte))
    assert {"origem_recusada", "rascunho_desligado", "autorizacao_recusada"} <= da_ui
    return set(contexto.STATUS_DA_RECUSA) | da_ui


def test_i18n_controle_o_verificador_morde():
    assert _sem_traducao({"chave_que_nao_existe", "nav_inicio"}) == ["chave_que_nao_existe"]


def test_i18n_todo_motivo_de_recusa_tem_traducao():
    faltando = _sem_traducao(f"recusa_{m}" for m in _motivos_de_recusa())
    assert faltando == [], faltando


def test_i18n_estados_status_e_tipos():
    from omica.especies import EstadoEspecie
    from omica.subprojetos import Subprojeto

    chaves = [f"estado_{e}" for e in get_args(get_type_hints(EstadoEspecie)["estado"])]
    chaves += [f"estado_explica_{e}" for e in get_args(get_type_hints(EstadoEspecie)["estado"])]
    chaves += [f"status_sub_{s}" for s in get_args(Subprojeto.model_fields["status"].annotation)]
    chaves += [f"tipo_{s}" for s in get_args(Subprojeto.model_fields["tipo"].annotation)]
    assert len(chaves) == 3 + 3 + 5 + 3
    assert _sem_traducao(chaves) == []


def test_i18n_todo_erro_de_rascunho_tem_traducao():
    fonte = (REPO_ROOT / "omica" / "subprojetos.py").read_text(encoding="utf-8")
    codigos = set(re.findall(r'RascunhoRecusado\("([a-z_]+)"', fonte))
    assert {"rascunho_desligado", "titulo_invalido", "pergunta_invalida", "amostras_invalidas"} <= codigos
    assert _sem_traducao(f"rascunho_erro_{c}" for c in codigos) == []


def test_i18n_toda_chave_fixa_dos_templates_existe():
    pasta = REPO_ROOT / "omica" / "web" / "templates"
    chaves = {k for arq in pasta.glob("*.html") for k in _CHAVE_ESTATICA.findall(arq.read_text(encoding="utf-8"))}
    assert len(chaves) > 100
    assert _sem_traducao(chaves) == []


_CHAVE_CRUA = re.compile(r"\[[a-z][a-z0-9_]+\]")    # o que i18n.t devolve sem tradução


def _paginas_v2(c, lang: str):
    """As páginas e recusas da v2, renderizadas (pega também as chaves DINÂMICAS — t(a.tipo),
    t('estado_' + …) —, que a varredura estática dos templates não vê)."""
    urls = ["/", "/e/bos_taurus", "/e/bos_taurus?arquivados=1", "/e/homo_sapiens", "/e/mus_musculus",
            "/e/bos_taurus/demo", "/e/bos_taurus/aula_granulosa_calor", "/e/homo_sapiens/matriz_teste",
            "/e/bos_taurus/gen_aims_girolando", "/e/homo_sapiens/novo-subprojeto", "/jobs", "/professor",
            "/e/bos_taurus/aula_granulosa_calor/modulos/transcriptomica",
            "/e/bos_taurus/gen_aims_girolando/modulos/genomica",
            *(f"/e/bos_taurus/demo/modulos/{m}" for m in ("genomica", "epigenomica", "transcriptomica", "ml"))]
    for url in urls:
        r = c.get(f"{url}{'&' if '?' in url else '?'}lang={lang}")
        assert r.status_code == 200, url
        yield url, r.text
    recusas = ["/e/bos_taurus/aula_granulosa_calor/modulos/transcriptomica/scripts/deseq2_demo",
               "/e/bos_taurus/gen_aims_girolando/modulos/genomica/scripts/extracao_demo",
               "/e/xx_inexistente/demo/modulos/genomica/scripts/extracao_demo",
               "/e/bos_taurus/nao_existe/modulos/genomica/scripts/extracao_demo",
               "/e/bos_taurus/demo/modulos/fantasma/scripts/x", "/e/homo_sapiens/novo-subprojeto"]
    for url in recusas:
        r = c.post(f"{url}?lang={lang}", follow_redirects=False)
        assert r.headers.get("x-omica-recusa"), url
        yield url, r.text
    r = c.post(f"/e/bos_taurus/demo/modulos/genomica/scripts/extracao_demo?lang={lang}",
               headers={"Origin": "http://evil"}, follow_redirects=False)
    yield "origem", r.text


@pytest.mark.parametrize("lang", ["pt", "en"])
def test_i18n_nenhuma_pagina_mostra_chave_sem_traducao(cfg, humano, bovino_invalido, monkeypatch, lang):
    from omica import professor
    from omica.jobs import queue

    monkeypatch.setattr(professor, "motor_disponivel", lambda: (False, ""))   # sem rede nos testes
    queue.write_status("transcriptomica-20261029-000004-eeeeee", "done",
                       especie="homo_sapiens", subprojeto="matriz_teste")
    vistas = 0
    for url, texto in _paginas_v2(_c(), lang):
        assert _CHAVE_CRUA.findall(_depois_do_topo(texto)) == [], url
        vistas += 1
    assert vistas == 25


def test_i18n_canario_chave_dinamica_sem_traducao_aparece(cfg, monkeypatch):
    """Controle do anterior: sem a tradução de `datasets` (chave dinâmica, t(a.tipo)), a página do
    demo mostra a chave crua e o verificador morde."""
    from omica.web import i18n

    monkeypatch.delitem(i18n.TRANSLATIONS, "datasets")
    assert "[datasets]" in _CHAVE_CRUA.findall(_c().get("/e/bos_taurus/demo").text)


def test_i18n_chaves_da_v1_mantidas_e_as_do_prototipo_que_sairam():
    from omica.web.i18n import TRANSLATIONS

    assert TRANSLATIONS["titulo_plataforma"] and TRANSLATIONS["nav_inicio"]["en"] == "Home"
    assert "professor_contexto" not in TRANSLATIONS and "professor_pipeline" not in TRANSLATIONS
    assert "professor_so_demo" in TRANSLATIONS


def test_professor_avisa_que_fica_no_demo(monkeypatch):
    from omica import professor

    monkeypatch.setattr(professor, "motor_disponivel", lambda: (False, ""))   # sem rede nos testes
    r = _c().get("/professor")
    assert r.status_code == 200 and 'id="professor-so-demo"' in r.text
    assert 'action="/modulos/' not in r.text


@pytest.mark.parametrize("caso", ["privado_carrega", "privado_quebrado", "publico"])
def test_privado_que_nao_carrega_continua_oculto(cfg, caso):
    """Canário (verificação v2, interfaces): um privado de <dados> cujo YAML deixa de carregar
    (typo, `forbid`, parâmetro que sumiu do manifest) sai do catálogo e não pode virar público:
    detalhe e console do job, relatório do run e a linha de /jobs continuam ocultos sem a
    autorização. Controle: o subprojeto público aparece."""
    from omica.jobs import queue

    privado = caso != "publico"
    pasta = escrever_subprojeto(cfg.projetos_dir, "homo_sapiens", "segredo_lab", privado=privado,
                                titulo="Projeto sigiloso", pergunta="Pergunta inedita do laboratorio X?")
    jid = "transcriptomica-20261029-000003-dddddd"
    queue.write_status(jid, "done", especie="homo_sapiens", subprojeto="segredo_lab")
    queue.append_log(jid, "--counts /dados/projetos/homo_sapiens/segredo_lab/amostras.tsv SEGREDO")
    rid = "transcriptomica_de-20261029-140000-abcdef"
    (cfg.work_dir / "runs" / rid).mkdir(parents=True)
    (cfg.work_dir / "runs" / rid / "run.json").write_text(json.dumps({
        "run_id": rid, "pipeline": "transcriptomica_de", "modulo": "transcriptomica",
        "especie": "homo_sapiens", "subprojeto": "segredo_lab"}), encoding="utf-8")
    base = cfg.results_dir / "projetos" / "homo_sapiens" / "segredo_lab" / "transcriptomica" / "runs" / rid
    (base / "relatorio").mkdir(parents=True)
    (base / "relatorio" / "relatorio.html").write_text("<p>RELATORIO SIGILOSO</p>", encoding="utf-8")
    if caso == "privado_quebrado":  # um campo a mais (extra=forbid): o que um typo do autor faz
        y = pasta / "subprojeto.yaml"
        y.write_text(y.read_text(encoding="utf-8") + "campo_novo: 1\n", encoding="utf-8")
    c = _c()
    det = c.get(f"/jobs/{jid}", follow_redirects=False)
    lista = c.get("/jobs").text
    if not privado:
        assert det.status_code == 200 and "segredo_lab" in lista
        return
    sse = c.get(f"/jobs/{jid}/events")
    rel = c.get(f"/relatorios/{rid}/", follow_redirects=False)
    assert det.status_code == 303 and sse.status_code == 404 and rel.status_code == 303
    assert "SEGREDO" not in sse.text and "SIGILOSO" not in rel.text and "segredo_lab" not in lista


# ---------------------------------------- X9 (F6, crítica M13): /resultados sem a cópia bruta
_RUN_EPI = "epigenomica_dmc-20261029-150000-abcdef"


def _arvore_epi(cfg, base_rel: str):
    base = cfg.results_dir / base_rel / "runs" / _RUN_EPI
    (base / "montar" / "coverages").mkdir(parents=True)
    (base / "montar" / "coverages" / "HF__FILTERED_H82_HF_ERS4410317_CD4.bismark.cov.gz").write_bytes(b"\x1f\x8bdados")
    (base / "montar" / "coverages" / "notas.txt").write_text("fora da regra, mas dentro da porta de pasta")
    (base / "montar" / "montar_resumo.json").write_text("{}", encoding="utf-8")
    (base / "tss").mkdir()
    (base / "tss" / "tss_perfil.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (base / "dmc").mkdir()
    (base / "dmc" / "solto.cov").write_text("1\t1\t1\t100\t1\t0\n", encoding="utf-8")
    (base / "dmc" / "solto.COV.GZ").write_bytes(b"\x1f\x8b")
    return base


@pytest.mark.parametrize("base_rel", ["epigenomica", "projetos/bos_taurus/x_epi/epigenomica"])
def test_x9_resultados_nao_serve_copia_bruta(cfg, base_rel):
    escrever_subprojeto(cfg.projetos_dir, "bos_taurus", "x_epi", assembly="ARS-UCD1.3", modulos=["epigenomica"])
    _arvore_epi(cfg, base_rel)
    pre = f"/resultados/{base_rel}/runs/{_RUN_EPI}"
    casos = [("montar/coverages/HF__FILTERED_H82_HF_ERS4410317_CD4.bismark.cov.gz", 303),  # canário (E2E-4 m)
             ("montar/coverages/notas.txt", 303),     # dentro da porta de pasta, qualquer nome
             ("dmc/solto.cov", 303), ("dmc/solto.COV.GZ", 303),  # coverage solto, qualquer caixa
             ("tss/tss_perfil.png", 200), ("montar/montar_resumo.json", 200)]  # controles
    for rel, codigo in casos:
        r = _c().get(f"{pre}/{rel}", follow_redirects=False)
        assert r.status_code == codigo, (rel, r.status_code)
        if codigo == 303:
            assert r.headers["location"] == "/"


def test_x9_figuras_do_relatorio_continuam_servidas(cfg):
    """Controle: toda figura que o relatorio.html referencia (figuras/<nome>, relativa à página
    /relatorios/<run>/) é servida."""
    base = _arvore_epi(cfg, "epigenomica")
    pasta = cfg.work_dir / "runs" / _RUN_EPI
    pasta.mkdir(parents=True)
    (pasta / "run.json").write_text(json.dumps({"run_id": _RUN_EPI, "pipeline": "epigenomica_dmc",
                                                "modulo": "epigenomica"}), encoding="utf-8")
    (base / "relatorio" / "figuras").mkdir(parents=True)
    (base / "relatorio" / "figuras" / "tss_perfil.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (base / "relatorio" / "relatorio.html").write_text(
        '<figure><img src="figuras/tss_perfil.png" alt="tss.tss_png"></figure>', encoding="utf-8")
    pagina = _c().get(f"/relatorios/{_RUN_EPI}/")
    assert pagina.status_code == 200
    figuras = re.findall(r'src="(figuras/[^"]+)"', pagina.text)
    assert figuras
    for f in figuras:
        assert _c().get(f"/relatorios/{_RUN_EPI}/{f}", follow_redirects=False).status_code == 200, f
