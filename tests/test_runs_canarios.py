"""Canários da revisão adversarial da F2 (achados #35, #36, #37, #38, #40 e #41).

Cada teste aqui existe para REPROVAR uma regressão que a suíte de tests/test_runs.py deixava
passar — os mutantes da revisão (M01, M02, …) vão nas docstrings. Validador que só foi visto
aprovando não está testado: por isso cada canário vem com o seu controle, o mesmo arranjo
sem a mexida, que precisa continuar passando (senão o 409 viria do arranjo, não do portão).

Os jobs continuam fingidos (fixture `lancados`), EXCETO onde o ponto é o caminho real de
execução: ali só o `queue.enqueue` é trocado e dispatch.lancar_script roda inteiro.
Nada aqui toca script selado: quando o canário precisa de um arquivo editado, ele edita uma
CÓPIA em tmp_path.
"""
from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

from omica import proveniencia, runs
from omica.config import REPO_ROOT
from omica.jobs import dispatch, queue
from omica.pipelines import Pipeline, validar_contrato
from omica.registry import MODULES_DIR, ScriptSpec, load_registry
from test_runs import (DE_OK, DEMO, REFERENCIA, _aprovado, _com_entradas, _concluir, _novo,  # noqa: F401
                       _png_bytes, api, lancados)

# OMICA_MAX_RUNS_ATIVOS/ABERTOS exportadas no shell: quem as tira do caminho é o conftest
# (ambiente_isolado). O fixture autouse local que fazia isso aqui saiu — ele escondia a falta
# do conftest. O canário dessa isolação é a classe TestShellComOmicaExportada, no fim.


def _executar(api, rid: str, passo: str = "de", **kw):
    return api.post(f"/api/v1/runs/{rid}/passos/{passo}/executar", **kw)


def _erro(r) -> str:
    return r.json()["detail"]["erro"]


def _estado(api, rid: str) -> dict:
    return api.get(f"/api/v1/runs/{rid}").json()


def _sha_lf(p: Path) -> str:
    return hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


# ------------------------------------------------------------- cópias descartáveis
def _copia_do_modulo(tmp_path: Path) -> Path:
    """modules/transcriptomica copiado para tmp (sem demo_data, que o registry não lê).
    Devolve a pasta `modules/` da cópia: o manifest dela pode ser mexido à vontade."""
    destino = tmp_path / "copia" / "modules"
    shutil.copytree(MODULES_DIR / "transcriptomica", destino / "transcriptomica",
                    ignore=shutil.ignore_patterns("demo_data", "__pycache__"))
    return destino


def _mexer_deseq2_api(modulos: Path, mexida: Callable[[dict], None]) -> None:
    """Aplica `mexida` à entrada `deseq2` de `scripts_api` da cópia. Mexe na ESTRUTURA do
    YAML, não no texto: o manifest é editado por outras frentes, e um replace textual
    quebrava a cada reformatação. A mexida nula também regrava (o controle passa pelo mesmo
    caminho de ida e volta do YAML que os canários)."""
    arq = modulos / "transcriptomica" / "manifest.yaml"
    dados = yaml.safe_load(arq.read_text(encoding="utf-8"))
    spec = next(s for s in dados["scripts_api"] if s["key"] == "deseq2")
    antes = repr(spec)
    mexida(spec)
    assert (repr(spec) == antes) == (mexida is _nada), "a mexida não mudou nada no manifest"
    arq.write_text(yaml.safe_dump(dados, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _nada(spec: dict) -> None:
    pass


def _troca_no_cmd(velho: str, novo: str) -> Callable[[dict], None]:
    def mexida(spec: dict) -> None:
        assert velho in spec["cmd"], f"o cmd do deseq2 mudou: {velho!r} não está nele"
        spec["cmd"] = spec["cmd"].replace(velho, novo)
    return mexida


def _param(nome: str, **campos: str) -> Callable[[dict], None]:
    def mexida(spec: dict) -> None:
        next(p for p in spec["params"] if p["name"] == nome).update(campos)
    return mexida


def _campo(chave: str, valor: Any) -> Callable[[dict], None]:
    def mexida(spec: dict) -> None:
        spec[chave] = valor
    return mexida


def _consome_fantasma(spec: dict) -> None:
    spec["consome"]["fantasma"] = "tabela_de"


def _pipeline_real() -> Pipeline:
    bruto = yaml.safe_load((REPO_ROOT / "pipelines" / "transcriptomica_de.yaml").read_text(encoding="utf-8"))
    return Pipeline.model_validate(bruto)


# ============================================================ #35 manifest e script
@pytest.mark.parametrize("mexida,trecho", [
    pytest.param(_nada, None, id="controle_copia_intacta"),
    # M04: sem a comparação de cmd, o manifest com as flags trocadas rodava fora do plano
    pytest.param(_troca_no_cmd("--padj {padj} --lfc {lfc}", "--padj {lfc} --lfc {padj}"),
                 "difere do aprovado", id="flags_trocadas"),
    # o valor aprovado ("0.05") deixa de passar no sanitizador do manifest novo
    pytest.param(_param("padj", tipo="inteiro"), "inteiro", id="tipo_endurecido"),
    pytest.param(_campo("key", "deseq2_v2"), "saiu de scripts_api", id="script_removido"),
    # mesmo cmd, outra IMAGEM (outra versão do R/DESeq2) ou reserva de RAM menor que a aprovada
    pytest.param(_campo("runtime", "image:omica-bioinfo"), "runtime", id="runtime_trocado"),
    pytest.param(_campo("mem_gb", 0.5), "recursos", id="mem_gb_reduzida"),
])
def test_manifest_alterado_depois_da_aprovacao_bloqueia(api, monkeypatch, lancados, tmp_path, mexida, trecho):
    """Canário REAL do manifest_mudou (achado #35): o registry vem de uma cópia do manifest
    mexida DEPOIS da aprovação — nenhuma função do portão é trocada por mock."""
    rid, _ = _aprovado(api)
    modulos = _copia_do_modulo(tmp_path)
    _mexer_deseq2_api(modulos, mexida)
    reg = load_registry(modulos)
    assert "transcriptomica" not in reg.erros, reg.erros
    monkeypatch.setattr(runs, "load_registry", lambda *a, **k: reg)

    r = _executar(api, rid)
    if trecho is None:
        assert r.status_code == 202, r.text
        assert len(lancados) == 1
        return
    assert r.status_code == 409 and _erro(r) == "manifest_mudou", r.text
    assert trecho in r.json()["detail"]["detalhe"]
    assert not lancados


@pytest.fixture()
def codigo_copiado(tmp_path, monkeypatch) -> Path:
    """Os scripts do módulo numa raiz temporária, com a receita de proveniência apontada para
    ela ANTES do plano: o arquivo pode ser editado depois da aprovação sem tocar no script
    selado do repositório."""
    raiz = tmp_path / "repo_copia"
    shutil.copytree(REPO_ROOT / "modules" / "transcriptomica" / "scripts",
                    raiz / "modules" / "transcriptomica" / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(proveniencia, "REPO_ROOT", raiz)
    return raiz / "modules" / "transcriptomica" / "scripts"


def _inverte_fim_de_linha(dados: bytes) -> bytes:
    lf = dados.replace(b"\r\n", b"\n")
    return lf if lf != dados else lf.replace(b"\n", b"\r\n")


@pytest.mark.parametrize("mexida,codigo", [
    ("linha_no_script", 409),
    ("linha_no_comum", 409),       # _comum.py entra na proveniência de todo passo do módulo
    ("script_apagado", 409),
    ("so_fim_de_linha", 202),      # controle: CRLF↔LF é o MESMO código (host × container)
    ("nada", 202),                 # controle
])
def test_script_editado_em_disco_depois_da_aprovacao_bloqueia(api, lancados, codigo_copiado, mexida, codigo):
    """Canário REAL do script_mudou (achado #35). O teste antigo trocava runs._sha_script por
    um mock e só exercitava o `!=`; aqui o arquivo muda de verdade. Mata M06 (_h constante) e
    M44 (_h do CAMINHO); o controle de fim de linha mata quem tirar a normalização CRLF."""
    script, comum = codigo_copiado / "expressao_deseq2.R", codigo_copiado / "_comum.py"
    rid, plano = _aprovado(api)
    prov = plano["passos"][0]["proveniencia"]
    # o plano leu a CÓPIA — senão o canário editaria um arquivo que ninguém confere
    assert prov["sha_script"] == _sha_lf(script) and prov["sha_comum"] == _sha_lf(comum)

    if mexida == "linha_no_script":
        with script.open("ab") as f:
            f.write(b"\n# editado depois da aprovacao\n")
    elif mexida == "linha_no_comum":
        with comum.open("ab") as f:
            f.write(b"\n# editado depois da aprovacao\n")
    elif mexida == "script_apagado":
        script.unlink()
    elif mexida == "so_fim_de_linha":
        script.write_bytes(_inverte_fim_de_linha(script.read_bytes()))

    r = _executar(api, rid)
    assert r.status_code == codigo, r.text
    if codigo == 409:
        assert _erro(r) == "script_mudou" and not lancados
    else:
        assert len(lancados) == 1


# ============================================== #36 executar não aceita parâmetro
@pytest.mark.parametrize("corpo", [
    {"json": {"valores": {"padj": "0.9", "lfc": "0"}, "saida": "/etc", "cmd": "rm -rf /"}},
    {"content": b"padj=0.9&lfc=0", "headers": {"Content-Type": "application/x-www-form-urlencoded"}},
    # nem o objeto vazio: o cliente que manda `{}` acha que está mandando "nada a mudar", e
    # a rota não pode depender de interpretar o corpo para decidir que ele é inócuo
    {"json": {}},
], ids=["json_hostil", "form_hostil", "json_vazio"])
def test_executar_recusa_qualquer_corpo(api, lancados, corpo):
    """M39: a rota passar a ler o corpo e mesclar `valores` sobre o plano. Um token vazado
    rodaria o DESeq2 com padj=0.9 sem aprovação. Contrato (frente api): corpo não vazio = 422
    `corpo_nao_aceito`, nada lançado, run intocado. O controle vem no MESMO run, logo depois:
    sem corpo (e com corpo de zero bytes), o que é lançado é exatamente o plano."""
    rid, plano = _aprovado(api)
    de = plano["passos"][0]
    r = _executar(api, rid, **corpo)
    assert r.status_code == 422 and _erro(r) == "corpo_nao_aceito", r.text
    assert not lancados
    assert _estado(api, rid)["estado"] == "aprovado"

    r = _executar(api, rid, content=b"")  # controle: zero bytes não é corpo
    assert r.status_code == 202, r.text
    (ch,) = lancados
    assert (ch["valores"], ch["saida"], ch["api"]) == (de["valores"], de["saida"], True)
    assert (ch["run_id"], ch["passo"]) == (rid, "de")


@pytest.mark.parametrize("query", [
    "?padj=0.9&valores=x&saida=/etc",
    "?padj",          # só o nome, sem valor: ainda é parâmetro
    "?segundos=0",    # nem o parâmetro legítimo de OUTRA rota (aguardar)
], ids=["query_hostil", "nome_sem_valor", "param_de_outra_rota"])
def test_executar_recusa_query_string(api, lancados, query):
    """Resíduo do #36: o invariante só valia para o CORPO — `executar?padj=0.9&saida=/etc`
    dava 202 com a query ignorada em silêncio, e um Query param acrescentado à rota mesclaria
    valor fora do plano sem teste nenhum reprovar. Contrato: query não vazia = 422
    `parametro_nao_aceito`, nada lançado, run intocado; o controle, no mesmo run, lança
    exatamente o plano."""
    rid, plano = _aprovado(api)
    de = plano["passos"][0]
    r = api.post(f"/api/v1/runs/{rid}/passos/de/executar{query}")
    assert r.status_code == 422 and _erro(r) == "parametro_nao_aceito", r.text
    assert not lancados
    assert _estado(api, rid)["estado"] == "aprovado"

    r = api.post(f"/api/v1/runs/{rid}/passos/de/executar?")  # controle: '?' sozinho é query vazia
    assert r.status_code == 202, r.text
    (ch,) = lancados
    assert (ch["valores"], ch["saida"]) == (de["valores"], de["saida"])


@pytest.fixture()
def enfileirados(monkeypatch) -> list[dict]:
    """Só o enqueue é trocado: dispatch.lancar_script, _resolver(api=True) e montar_cmd(saida)
    rodam de verdade. O fixture `lancados` troca a função INTEIRA — com ele, um _resolver que
    ignorasse `api` (M40, todo executar virando KeyError/500) passava na suíte."""
    chamadas: list[dict] = []

    def falso(func_path, prefixo, mem_gb=0.5, timeout_s=3600, fila=queue.FILA_DEFAULT, **meta):
        job_id = queue.novo_job_id(prefixo)
        queue.write_status(job_id, "queued", mem_gb=mem_gb, func=func_path, fila=fila, **meta)
        chamadas.append({"func": func_path, "prefixo": prefixo, "mem_gb": mem_gb, "fila": fila,
                         "job_id": job_id, **meta})
        return job_id, "rq"

    monkeypatch.setattr(dispatch.queue, "enqueue", falso)
    return chamadas


def test_executar_pelo_caminho_real_enfileira_exatamente_o_plano(api, enfileirados):
    rid, plano = _aprovado(api)
    de, volcano = plano["passos"]
    r = _executar(api, rid)
    assert r.status_code == 202, r.text
    (job,) = enfileirados
    assert r.json()["job_id"] == job["job_id"]
    assert job["cmd"] == de["cmd"] and job["cmd"].endswith(f"--saida {de['saida']}")
    assert job["func"] == "omica.jobs.script_job.executar" and job["prefixo"] == "transcriptomica-deseq2"
    assert (job["modulo"], job["script"], job["runtime"], job["fila"]) == (
        "transcriptomica", "deseq2", de["runtime"], de["fila"])
    assert job["mem_gb"] == de["mem_gb"] and (job["run_id"], job["passo"]) == (rid, "de")

    # o passo seguinte, pela mesma via: o volcano lê a porta do DE congelada no plano
    _concluir(job["job_id"], de["saida"], {"de_table.tsv": DE_OK, "de_resumo.json": '{"n": 1}'})
    r = _executar(api, rid, "volcano")
    assert r.status_code == 202, r.text
    assert enfileirados[1]["cmd"] == volcano["cmd"]
    assert f"--de {de['saida']}/de_table.tsv" in enfileirados[1]["cmd"]


def test_secoes_scripts_e_scripts_api_nunca_se_misturam(cfg, enfileirados):
    """_resolver: `api=True` só enxerga scripts_api, e o padrão só enxerga `scripts` — um
    _resolver que juntasse as duas listas faria a UI rodar entrada de run e vice-versa."""
    with pytest.raises(KeyError, match="script desconhecido"):
        dispatch.lancar_script("transcriptomica", "deseq2_demo", api=True, saida=_saida_de_run(cfg))
    with pytest.raises(KeyError, match="script desconhecido"):
        dispatch.lancar_script("transcriptomica", "deseq2", valores=_valores_run(cfg))
    assert not enfileirados


# ===================================================== #37 travas do demo selado
def _saida_de_run(cfg, passo: str = "de") -> str:
    return (f"{cfg.data_dir.as_posix()}/results/transcriptomica/runs/"
            f"transcriptomica_de-20261019-000000-abc123/{passo}")


def _valores_run(cfg) -> dict[str, str]:
    raiz = cfg.data_dir.as_posix()
    return {"counts": f"{raiz}/raw/runs/r1/counts.tsv", "coldata": f"{raiz}/raw/runs/r1/coldata.tsv",
            "referencia": REFERENCIA}


_DEMO_SELADO = "{dados}/results/transcriptomica/demo/de"


def test_template_sem_saida_recebendo_pasta_de_run_e_erro(cfg):
    """montar_cmd descartava a `saida` em silêncio quando o cmd não tinha {saida}: um cmd
    copiado de `scripts:` gravaria no demo SELADO e a pasta do run ficaria vazia."""
    spec = ScriptSpec(key="copiado_de_scripts", titulo="t", runtime="image:omica-methyl",
                      cmd=f"Rscript modules/transcriptomica/scripts/expressao_deseq2.R --saida {_DEMO_SELADO}")
    with pytest.raises(ValueError, match=r"não grava em \{saida\}"):
        dispatch.montar_cmd(spec, saida=_saida_de_run(cfg))
    # controle: o mesmo template no caminho da UI (sem pasta de run) monta como sempre
    assert dispatch.montar_cmd(spec).endswith("/results/transcriptomica/demo/de")


def test_cmd_de_run_que_cita_dados_e_erro(cfg):
    """Meia-edição: {saida} presente, mas outro caminho fixo sob {dados} (o demo) ainda no cmd."""
    base = "python3 modules/transcriptomica/scripts/volcano_plot.py --saida {saida}"
    meia = ScriptSpec(key="meia_edicao", titulo="t", runtime="image:omica-methyl",
                      cmd=f"{base} --log {_DEMO_SELADO}/log.txt")
    with pytest.raises(ValueError, match=r"cita \{dados\}"):
        dispatch.montar_cmd(meia, saida=_saida_de_run(cfg))
    limpo = ScriptSpec(key="limpo", titulo="t", runtime="image:omica-methyl", cmd=base)  # controle
    assert dispatch.montar_cmd(limpo, saida=_saida_de_run(cfg)).endswith(f"--saida {_saida_de_run(cfg)}")


_VOLCANO = "python3 modules/transcriptomica/scripts/volcano_plot.py"


@pytest.mark.parametrize("trecho,erro", [
    # Resíduo do #37: as travas comparavam o TEXTO "{dados}". Com conversão ou especificador o
    # str.format ainda substitui `dados`, e o log ia parar dentro do demo selado.
    ("--saida {saida} --log {dados!s}/results/transcriptomica/demo/de/x.log", "fora do formato"),
    ("--saida {saida} --log {dados!r}/results/transcriptomica/demo/de/x.log", "fora do formato"),
    ("--saida {saida} --log {dados:>1}/results/transcriptomica/demo/de/x.log", "fora do formato"),
    # especificador VAZIO é o próprio {dados} para o parser: cai na trava do nome
    ("--saida {saida} --log {dados:}/results/transcriptomica/demo/de/x.log", r"cita \{dados\}"),
    # índice: `{dados[0]}` é o 1º caractere da pasta ('/' no container) → /results/... absoluto
    ("--saida {saida} --log {dados[0]}results/transcriptomica/demo/de/x.log", "fora do formato"),
    # chave escapada vira o TEXTO "{saida}": a pasta do run seria ignorada e o script gravaria
    # numa pasta chamada '{saida}' na raiz do repositório (a busca literal achava "{saida}")
    ("--saida {{saida}}", r"não grava em \{saida\}"),
    ("--saida {saida!s}", "fora do formato"),
], ids=["dados_conv_s", "dados_conv_r", "dados_spec", "dados_spec_vazio", "dados_indice",
        "saida_escapada", "saida_conv"])
def test_campo_disfarcado_no_cmd_de_run_e_erro(cfg, trecho, erro):
    spec = ScriptSpec(key="disfarce", titulo="t", runtime="image:omica-methyl", cmd=f"{_VOLCANO} {trecho}")
    with pytest.raises(ValueError, match=erro):
        dispatch.montar_cmd(spec, saida=_saida_de_run(cfg))


def test_campo_disfarcado_no_cmd_da_ui_e_erro(cfg):
    """A mesma regra no caminho da UI (sem pasta de run): conversão/especificador nem formata.
    Controle: o mesmo cmd com `{dados}` cru monta como sempre (é o que os manifests usam)."""
    cru = f"{_VOLCANO} --saida {{dados}}/results/transcriptomica/demo/volcano"
    disfarcado = ScriptSpec(key="d", titulo="t", runtime="image:omica-methyl", cmd=cru.replace("{dados}", "{dados!s}"))
    with pytest.raises(ValueError, match="fora do formato"):
        dispatch.montar_cmd(disfarcado)
    limpo = ScriptSpec(key="l", titulo="t", runtime="image:omica-methyl", cmd=cru)
    assert dispatch.montar_cmd(limpo).endswith("/results/transcriptomica/demo/volcano")
    # chave escapada fora de campo continua sendo texto (não é campo, não é problema)
    escapado = ScriptSpec(key="e", titulo="t", runtime="image:omica-methyl", cmd=cru + " --rotulo {{x}}")
    assert dispatch.montar_cmd(escapado).endswith("--rotulo {x}")


@pytest.mark.parametrize("modulo_key,api_,saida,erro", [
    # a entrada da UI recebendo pasta de run: gravaria no demo e ignoraria a pasta
    ("deseq2_demo", False, "run", r"não grava em \{saida\}"),
    # a entrada de run sem pasta de run
    ("deseq2", True, None, "nenhuma pasta de run"),
    # a entrada de run apontada para o demo selado
    ("deseq2", True, "demo", "saida precisa ser"),
    ("deseq2", True, "run", None),  # controle
])
def test_lancar_script_real_protege_o_demo(cfg, enfileirados, modulo_key, api_, saida, erro):
    pasta = {"run": _saida_de_run(cfg), "demo": f"{cfg.data_dir.as_posix()}/results/transcriptomica/demo/de",
             None: None}[saida]
    valores = _valores_run(cfg) if api_ else None
    if erro is None:
        dispatch.lancar_script("transcriptomica", modulo_key, valores=valores, api=api_, saida=pasta)
        assert len(enfileirados) == 1 and enfileirados[0]["cmd"].endswith(f"--saida {pasta}")
        return
    with pytest.raises(ValueError, match=erro):
        dispatch.lancar_script("transcriptomica", modulo_key, valores=valores, api=api_, saida=pasta)
    assert not enfileirados


@pytest.mark.parametrize("mexida,trecho", [
    pytest.param(_nada, None, id="controle_copia_intacta"),
    # M41: o cmd copiado da seção `scripts:`, gravando no demo
    pytest.param(_troca_no_cmd("--saida {saida}", f"--saida {_DEMO_SELADO}"), "cmd sem {saida}",
                 id="sem_saida"),
    # meia-edição: {saida} ainda aparece, mas o demo também
    pytest.param(_troca_no_cmd("--saida {saida}", f"--saida {_DEMO_SELADO} --log {{saida}}"), "cita {dados}",
                 id="saida_e_demo"),
    # M42: chave igual a uma de `scripts:` (UI e API rodariam coisas diferentes com o mesmo nome)
    pytest.param(_campo("key", "deseq2_demo"), "chave de script repetida", id="chave_repetida"),
    # M43: `consome` de um parâmetro que o script não tem
    pytest.param(_consome_fantasma, "consome 'fantasma' que não é parâmetro", id="consome_fantasma"),
    # resíduo do #37: {dados} disfarçado com conversão/especificador escapava da busca literal
    pytest.param(_troca_no_cmd("--saida {saida}", "--saida {saida} --log {dados!s}/results/transcriptomica/demo/de/x.log"),
                 "cita {dados}", id="dados_com_conversao"),
    pytest.param(_troca_no_cmd("--saida {saida}", "--saida {saida} --log {dados:}/results/transcriptomica/demo/de/x.log"),
                 "cita {dados}", id="dados_com_especificador_vazio"),
    pytest.param(_troca_no_cmd("--saida {saida}", "--saida {saida!r}"), "conversão/especificador",
                 id="saida_com_conversao"),
    pytest.param(_troca_no_cmd("--saida {saida}", "--saida {{saida}}"), "cmd sem {saida}", id="saida_escapada"),
    pytest.param(_troca_no_cmd("--saida {saida}", "--saida {saida} --log {dados[0]}x.log"), "nome simples",
                 id="campo_com_indice"),
])
def test_registry_recusa_scripts_api_que_ameaca_o_demo(tmp_path, mexida, trecho):
    modulos = _copia_do_modulo(tmp_path)
    _mexer_deseq2_api(modulos, mexida)
    erros = load_registry(modulos).erros
    if trecho is None:
        assert erros == {}
    else:
        assert trecho in erros.get("transcriptomica", ""), erros


def test_registry_recusa_campo_com_conversao_tambem_em_scripts(tmp_path):
    """A regra de campo vale para as duas seções: na `scripts:` (UI) um `{dados!s}` não muda o
    destino, mas é a mesma forma de um nome passar por outro numa comparação de texto. Mexe
    numa CÓPIA do manifest; o selado do repositório não é tocado."""
    modulos = _copia_do_modulo(tmp_path)
    arq = modulos / "transcriptomica" / "manifest.yaml"
    dados = yaml.safe_load(arq.read_text(encoding="utf-8"))
    spec = next(s for s in dados["scripts"] if s["key"] == "deseq2_demo")
    assert "{dados}" in spec["cmd"]
    spec["cmd"] = spec["cmd"].replace("{dados}", "{dados!s}", 1)
    arq.write_text(yaml.safe_dump(dados, allow_unicode=True, sort_keys=False), encoding="utf-8")
    assert "deseq2_demo: campo 'dados' com conversão/especificador" in load_registry(modulos).erros["transcriptomica"]


def test_contrato_recusa_passo_cujo_cmd_nao_grava_em_saida():
    """2ª barreira (omica/pipelines.py), independente do registry: um Manifest montado à mão
    com o cmd do DESeq2 apontando para o demo não fecha o contrato do pipeline."""
    m = load_registry().por_id("transcriptomica")
    p = _pipeline_real()
    assert validar_contrato(p, m) == []  # controle
    mexido = m.model_copy(deep=True)
    spec = next(s for s in mexido.scripts_api if s.key == "deseq2")
    spec.cmd = spec.cmd.replace("--saida {saida}", f"--saida {_DEMO_SELADO}")
    assert any("não grava em {saida}" in e for e in validar_contrato(p, mexido))


# ========================================================= #38 transições de estado
def test_plano_nao_e_reproposto_depois_da_aprovacao(api, lancados):
    """M03: sem `plano_congelado`, um POST /plano num run aprovado reescrevia o plano e voltava
    o estado para 'planejado'; reaprovado com outra referência, o passo 'de' já entregue (feito
    com a antiga) passava no portão e o relatório atribuía à referência B números da A."""
    rid, plano = _aprovado(api)
    outra = {"escolhas": {"referencia": "summer_heat"}}
    r = api.post(f"/api/v1/runs/{rid}/plano", json=outra)
    assert r.status_code == 409 and _erro(r) == "plano_congelado", r.text
    assert _estado(api, rid)["estado"] == "aprovado"

    assert _executar(api, rid).status_code == 202
    r = api.post(f"/api/v1/runs/{rid}/plano", json=outra)
    assert r.status_code == 409 and _erro(r) == "plano_congelado"
    corpo = _estado(api, rid)
    assert corpo["estado"] == "executando" and corpo["plano_sha256"] == plano["plano_sha256"]


def test_plano_pode_ser_reproposto_antes_da_aprovacao(api):
    """Controle do canário acima: o congelamento é por estado — em 'planejado' o autor ainda
    pode trocar a escolha, e o sha muda."""
    rid = _com_entradas(api)
    p1 = api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {"referencia": REFERENCIA}}).json()
    r = api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {"referencia": "summer_heat"}})
    assert r.status_code == 200 and r.json()["plano_sha256"] != p1["plano_sha256"]
    assert _estado(api, rid)["estado"] == "planejado"


@pytest.mark.parametrize("status,codigo", [
    ("queued", 409), ("running", 409), ("done", 409),
    ("error", 202),  # controle: só job em erro é relançável
])
def test_relancar_passo_depende_do_status_do_job(api, lancados, status, codigo):
    """M08: com a tupla reduzida a ('queued',), relançar um passo running punha dois jobs na
    mesma pasta, e um done sobrescrevia o artefato já entregue."""
    rid, plano = _aprovado(api)
    assert _executar(api, rid).status_code == 202
    job = lancados[0]["job_id"]
    if status == "done":
        _concluir(job, plano["passos"][0]["saida"], {"de_table.tsv": DE_OK, "de_resumo.json": '{"n": 1}'})
        env = api.get(f"/api/v1/runs/{rid}/passos/de/envelope").json()
        assert env["portao"]["ok"], env["portao"]  # entregue: relançar sobrescreveria o artefato
    else:
        queue.write_status(job, status, error="R morreu" if status == "error" else None)
    r = _executar(api, rid)
    assert r.status_code == codigo, r.text
    if codigo == 409:
        assert _erro(r) == "passo_ja_lancado" and r.json()["detail"]["status"] == status
        assert len(lancados) == 1
    else:
        assert len(lancados) == 2


def test_relancar_run_em_erro_disputa_a_vaga_de_novo(api, lancados):
    """M09: um run em 'erro' sai da conta de ativos; relançá-lo sem disputar a vaga deixava
    dois runs ativos com limite 1."""
    rid_a, _ = _aprovado(api)
    assert _executar(api, rid_a).status_code == 202
    queue.write_status(lancados[0]["job_id"], "error", error="R morreu")
    assert _estado(api, rid_a)["estado"] == "erro"
    rid_b, _ = _aprovado(api)  # A em erro não ocupa a vaga: B é aprovado

    r = _executar(api, rid_a)
    assert r.status_code == 409 and _erro(r) == "limite_de_runs_ativos", r.text
    assert r.json()["detail"]["ativos"] == [rid_b] and len(lancados) == 1
    # controle: liberada a vaga, o mesmo relançamento passa
    assert api.post(f"/api/v1/runs/{rid_b}/abortar").status_code == 200
    assert _executar(api, rid_a).status_code == 202 and len(lancados) == 2
    assert _estado(api, rid_a)["estado"] == "executando"


def test_limite_de_runs_ativos_vem_do_ambiente(api, monkeypatch):
    """Por que o conftest tira as OMICA_* do shell: a variável muda o portão de verdade. É
    também o controle da TestShellComOmicaExportada (posta DENTRO do teste, ela vale)."""
    monkeypatch.setenv("OMICA_MAX_RUNS_ATIVOS", "2")
    _aprovado(api)
    _aprovado(api)  # com limite 2, o segundo passa (e test_um_run_ativo_por_vez cairia)


def test_aprovar_duas_vezes_e_409(api):
    rid, plano = _aprovado(api)
    r = api.post(f"/api/v1/runs/{rid}/aprovar", json={"plano_sha256": plano["plano_sha256"]})
    assert r.status_code == 409 and _erro(r) == "nada_a_aprovar" and r.json()["detail"]["estado"] == "aprovado"


def _concluido(api, lancados) -> str:
    rid, plano = _aprovado(api)
    de, volcano = plano["passos"]
    assert _executar(api, rid).status_code == 202
    _concluir(lancados[-1]["job_id"], de["saida"], {"de_table.tsv": DE_OK, "de_resumo.json": '{"n": 1}'})
    r = _executar(api, rid, "volcano")
    assert r.status_code == 202, r.text
    _concluir(lancados[-1]["job_id"], volcano["saida"],
              {"volcano.png": _png_bytes(), "volcano_resumo.json": '{"n": 2}'})
    assert _estado(api, rid)["estado"] == "concluido"
    return rid


@pytest.mark.parametrize("fim", ["abortado", "concluido"])
def test_run_encerrado_nao_aborta_nem_executa(api, lancados, fim):
    """M26: sem `run_encerrado`, abortar de novo (ou abortar um run concluído) era aceito e
    reescrevia o estado final."""
    if fim == "abortado":
        rid, _ = _aprovado(api)
        r = api.post(f"/api/v1/runs/{rid}/abortar")
        assert r.status_code == 200 and r.json()["estado"] == "abortado"  # controle: 1ª vez passa
    else:
        rid = _concluido(api, lancados)
    n = len(lancados)
    r = api.post(f"/api/v1/runs/{rid}/abortar")
    assert r.status_code == 409 and _erro(r) == "run_encerrado" and r.json()["detail"]["estado"] == fim
    r = _executar(api, rid)
    assert r.status_code == 409 and _erro(r) == "run_nao_executavel" and len(lancados) == n
    assert _estado(api, rid)["estado"] == fim


@pytest.mark.parametrize("metodo,rota", [
    ("post", "passos/nao_existe/executar"),
    ("get", "passos/nao_existe/envelope"),
    ("get", "passos/nao_existe/aguardar?segundos=0"),
])
def test_passo_inexistente_e_404(api, lancados, metodo, rota):
    """M27: sem a checagem, `ids.index` levantava ValueError e a API devolvia 500."""
    rid, _ = _aprovado(api)
    r = getattr(api, metodo)(f"/api/v1/runs/{rid}/{rota}")
    assert r.status_code == 404 and _erro(r) == "passo_inexistente", r.text
    assert not lancados
    # controle: o passo que existe responde (envelope de passo ainda não lançado)
    assert api.get(f"/api/v1/runs/{rid}/passos/de/envelope").json()["estado"] == "nao_lancado"


# ============================================================ #40 portões do plano
def test_entrada_fora_do_formato_reprova_nos_dois_portoes(api):
    """As CHAVES das falhas separam o veredito individual (counts) da checagem cruzada
    (contagens_e_coldata). Só o status 409 não distinguia: M02 (sem a cruzada) e M38 (sem o
    veredito) sobreviviam cada um, porque o counts inválido reprova nos dois."""
    rid = _novo(api)
    r = api.put(f"/api/v1/runs/{rid}/entradas/counts", content=b"gene\ta\nG1\t1\n",
                headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 200 and not r.json()["entradas"]["counts"]["veredito"]["ok"], r.text
    api.put(f"/api/v1/runs/{rid}/entradas/coldata", json={"caminho": f"{DEMO}/coldata_granulosa.tsv"})
    r = api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {"referencia": REFERENCIA}})
    assert r.status_code == 409 and _erro(r) == "entradas_reprovadas", r.text
    assert set(r.json()["detail"]["falhas"]) == {"counts", "contagens_e_coldata"}


def _coldata_demo() -> bytes:
    return (REPO_ROOT / DEMO / "coldata_granulosa.tsv").read_bytes().replace(b"\r\n", b"\n")


def _sobe_coldata(api, rid: str, cru: bytes) -> dict:
    r = api.put(f"/api/v1/runs/{rid}/entradas/coldata", content=cru,
                headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 200, r.text
    return r.json()


def test_amostra_do_coldata_ausente_da_matriz_bloqueia_o_plano(api):
    """Entradas válidas ISOLADAMENTE e cruzamento ruim — o único arranjo em que só a checagem
    cruzada segura o plano (M02). Sem ela, o R quebraria no meio do job."""
    rid = _novo(api)
    api.put(f"/api/v1/runs/{rid}/entradas/counts", json={"caminho": f"{DEMO}/counts_granulosa.tsv"})
    cru = _coldata_demo().rstrip(b"\n") + b"\nfantasma1\twinter_control\nfantasma2\tsummer_heat\n"
    corpo = _sobe_coldata(api, rid, cru)
    assert all(e["veredito"]["ok"] for e in corpo["entradas"].values())
    assert [c["ok"] for c in corpo["checagens"]] == [False]
    r = api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {"referencia": REFERENCIA}})
    assert r.status_code == 409 and _erro(r) == "entradas_reprovadas"
    assert set(r.json()["detail"]["falhas"]) == {"contagens_e_coldata"}


_NIVEIS_HOSTIS = ["--help", "-rf", "Angus=.env", "a b"]


def _coldata_com_nivel(api, nivel: str) -> tuple[str, dict]:
    rid = _novo(api)
    api.put(f"/api/v1/runs/{rid}/entradas/counts", json={"caminho": f"{DEMO}/counts_granulosa.tsv"})
    # só a coluna condicao muda (os nomes das amostras também contêm 'summer_heat')
    return rid, _sobe_coldata(api, rid, _coldata_demo().replace(b"	summer_heat", b"	" + nivel.encode()))


@pytest.mark.parametrize("nivel", _NIVEIS_HOSTIS)
def test_nivel_hostil_do_coldata_reprova_a_entrada(api, nivel):
    """1ª barreira do único caminho em que o CONTEÚDO de um arquivo do usuário vira argv
    (coldata.condicao → opções da escolha → --referencia): o formato reprova o nível que não
    iria para a linha de comando, e o dropdown nunca o oferece."""
    rid, corpo = _coldata_com_nivel(api, nivel)
    assert not corpo["entradas"]["coldata"]["veredito"]["ok"]
    assert corpo["escolhas_opcoes"]["referencia"]["valores"] is None
    r = api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {"referencia": nivel}})
    assert r.status_code == 409 and _erro(r) == "entradas_reprovadas", r.text
    assert "coldata" in r.json()["detail"]["falhas"]


@pytest.mark.parametrize("nivel", _NIVEIS_HOSTIS)
def test_nivel_hostil_so_vira_argv_pelo_sanitizador(api, monkeypatch, nivel):
    """2ª barreira, independente da 1ª: com a regra de nível do formato DESLIGADA (o que uma
    regressão nela faria), o plano ainda recusa '--referencia --help'. Mata M45 (referencia sem
    _validar_valor) e M18 (plano sem montar_cmd), que a 1ª barreira passou a esconder."""
    from omica.contrato import formatos

    monkeypatch.setattr(formatos, "_problema_nivel", lambda v: None)
    rid, corpo = _coldata_com_nivel(api, nivel)
    assert corpo["checagens"][0]["ok"] and nivel in corpo["escolhas_opcoes"]["referencia"]["valores"]

    r = api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {"referencia": nivel}})
    assert r.status_code == 422 and _erro(r) == "parametro_recusado", r.text
    assert r.json()["detail"]["passo"] == "de"
    # controle: o MESMO arquivo, com a outra condição como referência, gera plano normal
    r = api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {"referencia": REFERENCIA}})
    assert r.status_code == 200, r.text
    argv = r.json()["passos"][0]["argv"]
    assert argv[argv.index("--referencia") + 1] == REFERENCIA


# ================================================================== #41 run_id
_ROTAS_COM_RUN_ID = [
    ("get", "", {}),
    ("put", "/entradas/counts", {"json": {"caminho": f"{DEMO}/counts_granulosa.tsv"}}),
    ("post", "/entradas/counts/adaptar", {}),
    ("post", "/plano", {"json": {"escolhas": {}}}),
    ("post", "/aprovar", {"json": {"plano_sha256": "0" * 64}}),
    ("post", "/abortar", {}),
    ("post", "/passos/de/executar", {}),
    ("get", "/passos/de/envelope", {}),
    ("get", "/passos/de/aguardar?segundos=0", {}),
    ("post", "/relatorio/pedido", {}),
    # resíduo do #41: a 11ª rota com run_id (servico.receber → _pastas → runs.estado)
    ("post", "/relatorio/rascunho", {"json": {}}),
]
_IDS_ROTAS = [f"{m}{r or '/'}".replace("?segundos=0", "") for m, r, _ in _ROTAS_COM_RUN_ID]


@pytest.mark.parametrize("metodo,sufixo,kw", _ROTAS_COM_RUN_ID, ids=_IDS_ROTAS)
@pytest.mark.parametrize("rid", ["%2e%2e", "..%5C..%5Cx", "transcriptomica_de-x", "a" * 200],
                         ids=["ponto_ponto", "barra_invertida", "fora_do_formato", "longo"])
def test_run_id_fora_do_formato_e_422_em_toda_rota(api, lancados, metodo, sufixo, kw, rid):
    """Exatamente 422 `run_id_invalido` — o teste antigo aceitava 404 OU 422, e sem a regex
    (M01) estes ids davam 404 run_inexistente e ele continuava verde. '%2e%2e' chega ao handler
    como '..' (work/runs/.. = a pasta de controle); '%5C' é separador no host Windows."""
    r = getattr(api, metodo)(f"/api/v1/runs/{rid}{sufixo}", **kw)
    assert r.status_code == 422 and _erro(r) == "run_id_invalido", r.text
    assert not lancados


@pytest.mark.parametrize("metodo,sufixo,kw", _ROTAS_COM_RUN_ID, ids=_IDS_ROTAS)
def test_run_id_bem_formado_mas_falso_e_404_sem_pasta_orfa(api, cfg, lancados, metodo, sufixo, kw):
    """Controle do canário acima: a regex APROVA um id bem-formado, e o que responde é o
    run_inexistente — sem criar pasta nenhuma (trava, entradas) para o id falso."""
    falso = "transcriptomica_de-20990101-000000-abcdef"
    r = getattr(api, metodo)(f"/api/v1/runs/{falso}{sufixo}", **kw)
    assert r.status_code == 404 and _erro(r) == "run_inexistente", r.text
    assert not (cfg.work_dir / "runs" / falso).exists() and not (cfg.raw_dir / "runs" / falso).exists()
    assert not lancados


def test_barra_codificada_no_run_id_nem_casa_a_rota(api):
    """'..%2F..%2Fetc' vira '../../etc' no Starlette e não casa rota nenhuma: 404 de ROTA, que
    não diz nada sobre a regex — por isso saiu do canário acima e ficou separado aqui."""
    r = api.get("/api/v1/runs/..%2F..%2Fetc")
    # desde o resíduo do #33 o 404 de rota também vem no formato do contrato — com o código
    # da ROTA, que não se confunde com run_id_invalido nem com run_inexistente
    assert r.status_code == 404 and _erro(r) == "rota_inexistente", r.text


def test_lista_de_rotas_com_run_id_esta_completa():
    """Resíduo do #41: o canário de run_id se dizia 'em toda rota' e cobria 10 de 11. Agora a
    lista é conferida contra as rotas REAIS da API: rota nova com {run_id} sem entrada acima
    reprova aqui, em vez de ficar de fora em silêncio."""
    import re

    from fastapi.routing import APIRoute

    from omica.web.app import app

    listadas = [(m.upper(), suf.split("?")[0]) for m, suf, _ in _ROTAS_COM_RUN_ID]
    vistas, faltando = [], []
    for rota in app.routes:
        if not (isinstance(rota, APIRoute) and rota.path.startswith("/api/v1/runs/{run_id}")):
            continue
        resto = rota.path[len("/api/v1/runs/{run_id}"):]
        # '/passos/{passo}/executar' → '/passos/[^/]+/executar', o resto escapado
        padrao = re.compile("[^/]+".join(re.escape(p) for p in re.split(r"\{[a-z_]+\}", resto)))
        for metodo in rota.methods:
            vistas.append(f"{metodo} {rota.path}")
            if not any(m == metodo and padrao.fullmatch(suf) for m, suf in listadas):
                faltando.append(f"{metodo} {rota.path}")
    assert not faltando, f"rotas com run_id fora de _ROTAS_COM_RUN_ID: {faltando}"
    # controle: a conferência enxergou as rotas reais (não passou por varrer uma lista vazia)
    assert len(vistas) >= 11, vistas


# ========================================================= #38 ambiente do shell
class TestShellComOmicaExportada:
    """Resíduo do #38: o isolamento das OMICA_* era só de fixture autouse LOCAL (aqui e no
    test_runs_estado); o conftest não as tirava, e com OMICA_MAX_RUNS_ATIVOS=2 exportada o
    test_um_run_ativo_por_vez reprovava (200 em vez de 409). O fixture desta classe exporta as
    variáveis ANTES do conftest rodar — escopo de classe vem antes do de função —, que é
    exatamente a situação do shell do autor. Controle: test_limite_de_runs_ativos_vem_do_ambiente
    (posta dentro do teste, a variável continua valendo)."""

    _EXPORTADAS = {"OMICA_MAX_RUNS_ATIVOS": "2", "OMICA_MAX_RUNS_ABERTOS": "1",
                   "OMICA_VARIAVEL_DO_FUTURO": "x"}

    @pytest.fixture(scope="class", autouse=True)
    def _shell_do_autor(self):
        with pytest.MonkeyPatch.context() as mp:
            for nome, valor in self._EXPORTADAS.items():
                mp.setenv(nome, valor)
            mp.setenv("OMICA_SMOKE_REDE", "1")  # opção do harness: essa o conftest deixa
            yield

    def test_conftest_tira_as_omica_do_shell(self):
        assert not [n for n in self._EXPORTADAS if n in os.environ]
        assert os.environ.get("OMICA_SMOKE_REDE") == "1"  # controle: a opção do harness fica
        assert os.environ.get("OMICA_DATA")  # controle: as que a suíte precisa são repostas

    def test_limite_de_um_run_ativo_vale_com_o_shell_exportando_2(self, api):
        _aprovado(api)  # com OMICA_MAX_RUNS_ABERTOS=1 vazando, nem o 2º run seria criado
        rid2 = _com_entradas(api)
        plano = api.post(f"/api/v1/runs/{rid2}/plano", json={"escolhas": {"referencia": REFERENCIA}}).json()
        r = api.post(f"/api/v1/runs/{rid2}/aprovar", json={"plano_sha256": plano["plano_sha256"]})
        assert r.status_code == 409 and _erro(r) == "limite_de_runs_ativos", r.text
