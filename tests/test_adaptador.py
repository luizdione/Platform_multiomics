"""Agente adaptador (F3): motor local com as travas da GPU, aplicador determinístico e as
cinco salvaguardas. O LLM é sempre FALSO aqui (nenhum teste depende de Ollama no ar): o que
se testa é o que o código aceita, recusa e grava quando o modelo propõe isto ou aquilo.
"""
from __future__ import annotations

import csv
import json
import threading
import time
from pathlib import Path

import pytest

from omica.agentes import adaptador, motor_local
from omica.agentes.adaptador import adaptar
from omica.config import REPO_ROOT, get_config
from omica.contrato import formatos
from omica.contrato.aplicar_spec import SpecInvalida, aplicar, nada_criado, perfil_tabela

DEMO = REPO_ROOT / "modules" / "transcriptomica" / "demo_data"
COUNTS = DEMO / "counts_granulosa.tsv"
COLDATA = DEMO / "coldata_granulosa.tsv"


def _ler(caminho: Path) -> list[list[str]]:
    with caminho.open(encoding="utf-8-sig", newline="") as f:
        return [l for l in csv.reader(f, delimiter="\t")]


def _gravar(caminho: Path, linhas: list[list[str]], delim: str = "\t", bom: bool = False) -> Path:
    texto = "\n".join(delim.join(l) for l in linhas) + "\n"
    caminho.write_bytes((b"\xef\xbb\xbf" if bom else b"") + texto.encode("utf-8"))
    return caminho


def _gpu(estado: str | None) -> None:
    sw = get_config().gpu_switch
    sw.parent.mkdir(parents=True, exist_ok=True)
    if estado is None:
        sw.unlink(missing_ok=True)
    else:
        sw.write_text(json.dumps({"estado": estado, "motivo": "teste"}), encoding="utf-8")


class _Resposta:
    def __init__(self, conteudo: str) -> None:
        self._c = conteudo

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return {"message": {"content": self._c}}


def _nunca(*a, **k):
    raise AssertionError("o LLM não podia ter sido chamado")


# ------------------------------------------------------------------ motor_local
@pytest.mark.parametrize("estado,codigo", [(None, "gpu_desconhecida"), ("off", "gpu_reservada"),
                                           ("", "gpu_desconhecida")])
def test_gpu_nao_liberada_recusa_sem_nenhuma_chamada_http(monkeypatch, cfg, estado, codigo):
    _gpu(estado)
    monkeypatch.setattr(motor_local.httpx, "post", _nunca)
    with pytest.raises(motor_local.MotorIndisponivel) as e:
        motor_local.chat_json("s", "u", {"type": "object"})
    assert e.value.codigo == codigo


def test_gpu_liberada_chama_com_esquema_sem_think_e_temperatura_zero(monkeypatch, cfg):
    _gpu("on")
    corpos = []
    monkeypatch.setattr(motor_local.httpx, "post",
                        lambda url, json, timeout: corpos.append(json) or _Resposta('{"ok": 1}'))
    r = motor_local.chat_json("s", "u", {"type": "object"})
    assert r["dados"] == {"ok": 1}
    (corpo,) = corpos
    assert corpo["format"] == {"type": "object"} and corpo["think"] is False
    assert corpo["options"]["temperature"] == 0 and corpo["stream"] is False


def test_interruptor_e_relido_depois_da_espera_pela_vez(monkeypatch, cfg):
    """O dono reservou a GPU enquanto a chamada esperava a trava: não pode chamar."""
    from omica.travas import trava

    _gpu("on")
    monkeypatch.setattr(motor_local.httpx, "post", _nunca)
    erros = []

    def chamar():
        try:
            motor_local.chat_json("s", "u", {"type": "object"}, espera_s=5)
        except motor_local.MotorIndisponivel as e:
            erros.append(e.codigo)

    with trava(cfg.work_dir / "locks" / "llm.lock"):
        t = threading.Thread(target=chamar)
        t.start()
        time.sleep(0.3)
        _gpu("off")
    t.join(5)
    assert erros == ["gpu_reservada"]


def test_uma_chamada_ao_llm_por_vez(monkeypatch, cfg):
    _gpu("on")
    ativos, pico = [0], [0]
    trava_contador = threading.Lock()

    def lento(url, json, timeout):
        with trava_contador:
            ativos[0] += 1
            pico[0] = max(pico[0], ativos[0])
        time.sleep(0.2)
        with trava_contador:
            ativos[0] -= 1
        return _Resposta("{}")

    monkeypatch.setattr(motor_local.httpx, "post", lento)
    threads = [threading.Thread(target=motor_local.chat_json, args=("s", "u", "json")) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert pico[0] == 1


def test_professor_respeita_o_interruptor(monkeypatch, cfg):
    from omica import professor

    _gpu("off")
    monkeypatch.setattr(professor, "motor_disponivel", _nunca)
    monkeypatch.setattr(motor_local.httpx, "post", _nunca)
    r = professor.perguntar("rodar o painel demo")
    assert r["status"] == "motor_indisponivel" and "GPU" in r["detalhe"]


# ------------------------------------------------------------------ aplicador
def test_aplicador_normaliza_csv_com_bom_e_renomeia(tmp_path):
    ent = _gravar(tmp_path / "in.csv", [[" GeneID ", "a", "b"], ["G1", "1", "2"]], delim=",", bom=True)
    rel = aplicar(ent, [{"op": "renomear", "origem": "GeneID", "destino": "gene_id"}], tmp_path / "out.tsv")
    assert _ler(tmp_path / "out.tsv") == [["gene_id", "a", "b"], ["G1", "1", "2"]]
    assert rel["bom"] and rel["delimitador"] == "virgula" and rel["cabecalho_com_espaco"] == [" GeneID "]
    assert not (tmp_path / "out.tsv").read_bytes().startswith(b"\xef\xbb\xbf")


def test_aplicador_seleciona_reordena_e_transpoe(tmp_path):
    ent = _gravar(tmp_path / "in.tsv", [["amostra", "G1", "G2"], ["s1", "1", "2"], ["s2", "3", "4"]])
    aplicar(ent, [{"op": "transpor"}, {"op": "renomear", "origem": "amostra", "destino": "gene_id"},
                  {"op": "selecionar", "colunas": ["gene_id", "s2", "s1"]}], tmp_path / "out.tsv")
    assert _ler(tmp_path / "out.tsv") == [["gene_id", "s2", "s1"], ["G1", "3", "1"], ["G2", "4", "2"]]


@pytest.mark.parametrize("ops,trecho", [
    ([{"op": "renomear", "origem": "geneid", "destino": "gene_id"}], "não existe"),          # caixa errada
    ([{"op": "renomear", "origem": "a", "destino": "b"}], "já é outra coluna"),             # colisão
    ([{"op": "renomear", "origem": "a", "destino": "x y"}], "destino inválido"),            # nome ruim
    ([{"op": "selecionar", "colunas": ["a", "a"]}], "repetida"),
    ([{"op": "selecionar", "colunas": []}], "vazia"),
    ([{"op": "apagar_linhas"}], "desconhecida"),
    ([{"op": "renomear", "origem": "a", "destino": "z", "valor": "1"}], "campos desconhecidos"),
])
def test_aplicador_recusa_o_que_nao_ancora(tmp_path, ops, trecho):
    ent = _gravar(tmp_path / "in.tsv", [["GeneID", "a", "b"], ["G1", "1", "2"]])
    with pytest.raises(SpecInvalida, match=trecho):
        aplicar(ent, ops, tmp_path / "out.tsv")
    assert not (tmp_path / "out.tsv").exists()


def test_cabecalho_duplicado_e_ambiguo(tmp_path):
    ent = _gravar(tmp_path / "in.tsv", [["id", "a", "a"], ["G1", "1", "2"]])
    with pytest.raises(SpecInvalida, match="ambígua"):
        aplicar(ent, [{"op": "renomear", "origem": "a", "destino": "b"}], tmp_path / "out.tsv")


def test_proibicao_de_criar_morde():
    """Canário direto da checagem: uma célula que não existia na entrada."""
    nada_criado(["g", "a"], [["G1", "1"]], ["g", "a"], [["G1", "1"]], [])  # controle
    with pytest.raises(SpecInvalida, match="criaria"):
        nada_criado(["g", "a"], [["G1", "1"]], ["g", "a"], [["G1", "0"]], [])
    with pytest.raises(SpecInvalida, match="criaria"):  # duplicar também é criar
        nada_criado(["g", "a"], [["G1", "1"]], ["g", "a"], [["G1", "1"], ["G1", "1"]], [])


def test_perfil_mostra_nomes_e_niveis_nunca_a_tabela(tmp_path):
    p = perfil_tabela(COLDATA)
    assert p["cabecalho"] == ["amostra", "condicao"] and p["n_linhas"] == 8
    assert sorted(p["niveis"]["condicao"]) == ["summer_heat", "winter_control"]
    assert len(p["amostra"]) == 5
    big = perfil_tabela(COUNTS)
    assert "gene_id" not in big["niveis"] and big["tipos"]["gene_id"] == "texto"


# ------------------------------------------------------------------ adaptador
def _demo_baguncado(tmp_path: Path) -> Path:
    """O demo com o id renomeado para algo SEM sinônimo, colunas de amostra permutadas e uma
    coluna extra — o caso do critério de aceite da F3."""
    linhas = _ler(COUNTS)
    cab = linhas[0]
    ordem = [0, 1] + list(range(len(cab) - 1, 1, -1))
    # coluna extra de TEXTO (descrição): numérica, o validador a tomaria por amostra — e o
    # DESeq2 a ignoraria, porque escolhe as colunas pelos nomes do coldata
    novas = [[l[i] for i in ordem] + [f"desc_{l[1]}"] for l in linhas]
    novas[0][0] = "feature"
    novas[0][-1] = "descricao"
    return _gravar(tmp_path / "baguncado.csv", novas, delim=",")


def test_arquivo_ja_no_formato_nao_chama_ninguem(tmp_path):
    r = adaptar(COUNTS, "contagens_genes", tmp_path / "out.tsv", chamar=_nunca)
    assert r.ok and r.modo == "nenhuma" and not (tmp_path / "out.tsv").exists()


def test_sinonimo_inequivoco_e_deterministico(tmp_path):
    linhas = _ler(COUNTS)
    linhas[0][0] = "Gene ID"
    ent = _gravar(tmp_path / "in.csv", linhas, delim=",", bom=True)
    r = adaptar(ent, "contagens_genes", tmp_path / "out.tsv", chamar=_nunca)
    assert r.ok and r.modo == "deterministico"
    assert r.ops == [{"op": "renomear", "origem": "Gene ID", "destino": "gene_id"}]
    assert (tmp_path / "out.tsv").read_bytes() == COUNTS.read_bytes().replace(b"\r\n", b"\n")


def test_ambiguidade_escala_sem_consultar_o_modelo(tmp_path):
    ent = _gravar(tmp_path / "cd.tsv", [["sample", "Sample_ID", "condicao"], ["a", "a", "x"], ["b", "b", "x"],
                                        ["c", "c", "y"], ["d", "d", "y"]])
    r = adaptar(ent, "coldata", tmp_path / "out.tsv", chamar=_nunca)
    assert not r.ok and "casam todas com 'amostra'" in r.excecoes[0]
    assert r.opcoes["candidatos"]["amostra"] == ["sample", "Sample_ID"]
    assert not (tmp_path / "out.tsv").exists()


def test_llm_correto_reproduz_o_demo_byte_a_byte(tmp_path):
    ent = _demo_baguncado(tmp_path)
    amostras = _ler(COUNTS)[0][2:]
    proposta = {"ops": [{"op": "renomear", "origem": "feature", "destino": "gene_id"},
                        {"op": "selecionar", "colunas": ["gene_id", "gene_name", *amostras]}],
                "excecoes": [], "justificativa": "id + reordenar amostras + tirar length"}
    chamadas = []

    def falso(sistema, usuario, esquema):
        chamadas.append(json.loads(usuario))
        return {"dados": proposta, "modelo": "falso"}

    r = adaptar(ent, "contagens_genes", tmp_path / "out.tsv", chamar=falso)
    assert r.ok and r.modo == "llm", r.excecoes
    assert (tmp_path / "out.tsv").read_bytes() == COUNTS.read_bytes().replace(b"\r\n", b"\n")
    # o modelo viu NOMES e uma amostra curta — nunca a matriz de 20 mil genes
    assert len(chamadas) == 1 and len(chamadas[0]["perfil"]["amostra"]) <= 5
    assert chamadas[0]["perfil"]["cabecalho"][0] == "feature"


@pytest.mark.parametrize("dados,trecho", [
    # coluna que não existe (o modelo "corrigiu" a caixa): descartada e listada, nunca corrigida
    ({"ops": [{"op": "renomear", "origem": "Feature", "destino": "gene_id"}]}, "descartada"),
    # o próprio modelo declarou dúvida
    ({"ops": [], "excecoes": ["não sei qual coluna é o gene"]}, "não sei"),
    # fora do esquema (campo extra)
    ({"ops": [], "confianca": 0.9}, "fora do esquema"),
    # operação inventada
    ({"ops": [{"op": "preencher_na", "colunas": ["x"]}]}, "fora do esquema"),
])
def test_proposta_ruim_escala_e_nao_grava(tmp_path, dados, trecho):
    ent = _demo_baguncado(tmp_path)
    r = adaptar(ent, "contagens_genes", tmp_path / "out.tsv",
                chamar=lambda s, u, e: {"dados": dados, "modelo": "falso"})
    assert not r.ok and any(trecho in x for x in r.excecoes), r.excecoes
    assert not (tmp_path / "out.tsv").exists()


def test_proposta_que_ancora_mas_nao_fecha_o_formato_escala(tmp_path):
    ent = _demo_baguncado(tmp_path)
    dados = {"ops": [{"op": "renomear", "origem": "feature", "destino": "gene_id"}]}  # esqueceu a descricao
    r = adaptar(ent, "contagens_genes", tmp_path / "out.tsv",
                chamar=lambda s, u, e: {"dados": dados, "modelo": "falso"})
    assert not r.ok and any("não fecha o formato" in x for x in r.excecoes)
    assert not (tmp_path / "out.tsv").exists()


def _largo(tmp_path: Path, n: int = 24) -> tuple[Path, list[dict]]:
    """Tabela com n amostras de nome feio e sem símbolo do gene: a proposta tem n+1
    renomeações (≥20 → auditoria) e a cópia declarada gene_id → gene_name."""
    cab = ["feature"] + [f"amostra.{i}.bruta" for i in range(n)]
    linhas = [cab] + [[f"G{j}"] + [str(i + j) for i in range(n)] for j in range(3)]
    ops = [{"op": "renomear", "origem": "feature", "destino": "gene_id"}] + \
          [{"op": "renomear", "origem": f"amostra.{i}.bruta", "destino": f"S{i}"} for i in range(n)] + \
          [{"op": "copiar", "origem": "gene_id", "destino": "gene_name"}]
    return _gravar(tmp_path / "largo.tsv", linhas), ops


def test_auditoria_independente_aprova_quando_concorda(tmp_path):
    ent, ops = _largo(tmp_path)
    respostas = iter([{"dados": {"ops": ops}, "modelo": "falso"}])

    def falso(sistema, usuario, esquema):
        pedido = json.loads(usuario)
        if "colunas_a_decidir" in pedido:  # a 2ª chamada, independente
            mapa = {o["origem"]: o["destino"] for o in ops}
            return {"dados": {"destinos": {c: mapa[c] for c in pedido["colunas_a_decidir"]}}}
        return next(respostas)

    r = adaptar(ent, "contagens_genes", tmp_path / "out.tsv", chamar=falso)
    assert r.ok and r.auditoria["aplicada"] and r.auditoria["amostra"] >= 3


def test_auditoria_reprova_com_uma_discordancia(tmp_path):
    ent, ops = _largo(tmp_path)

    mapa = {o["origem"]: o["destino"] for o in ops}

    def falso(sistema, usuario, esquema):
        pedido = json.loads(usuario)
        if "colunas_a_decidir" in pedido:  # concorda com todas menos a primeira
            cols = pedido["colunas_a_decidir"]
            return {"dados": {"destinos": {c: ("OUTRO" if i == 0 else mapa[c]) for i, c in enumerate(cols)}}}
        return {"dados": {"ops": ops}, "modelo": "falso"}

    r = adaptar(ent, "contagens_genes", tmp_path / "out.tsv", chamar=falso)
    assert not r.ok and "auditoria reprovou" in r.excecoes[0]
    assert not (tmp_path / "out.tsv").exists()


def test_modo_autor_passa_pelas_mesmas_conferencias(tmp_path):
    ent = _demo_baguncado(tmp_path)
    amostras = _ler(COUNTS)[0][2:]
    boas = [{"op": "renomear", "origem": "feature", "destino": "gene_id"},
            {"op": "selecionar", "colunas": ["gene_id", "gene_name", *amostras]}]
    r = adaptar(ent, "contagens_genes", tmp_path / "out.tsv", ops_autor=boas, chamar=_nunca)
    assert r.ok and r.modo == "autor"
    ruins = [{"op": "renomear", "origem": "coluna_que_nao_existe", "destino": "gene_id"}]
    r2 = adaptar(ent, "contagens_genes", tmp_path / "out2.tsv", ops_autor=ruins, chamar=_nunca)
    assert not r2.ok and r2.descartes and not (tmp_path / "out2.tsv").exists()


def test_resultado_adaptado_passa_no_validador_e_na_checagem_cruzada(tmp_path):
    ent = _demo_baguncado(tmp_path)
    amostras = _ler(COUNTS)[0][2:]
    dados = {"ops": [{"op": "renomear", "origem": "feature", "destino": "gene_id"},
                     {"op": "selecionar", "colunas": ["gene_id", "gene_name", *amostras]}]}
    r = adaptar(ent, "contagens_genes", tmp_path / "out.tsv",
                chamar=lambda s, u, e: {"dados": dados, "modelo": "falso"})
    assert r.ok
    assert formatos.contagens_e_coldata(tmp_path / "out.tsv", COLDATA).ok


def _demo_baguncado_sem_simbolo(tmp_path: Path) -> Path:
    """O _demo_baguncado SEM a coluna gene_name (a matriz de featureCounts/tximport)."""
    linhas = _ler(COUNTS)
    ordem = [0] + list(range(len(linhas[0]) - 1, 1, -1))
    novas = [[l[i] for i in ordem] + [f"desc_{l[1]}"] for l in linhas]
    novas[0][0], novas[0][-1] = "feature", "descricao"
    return _gravar(tmp_path / "baguncado_sem_simbolo.csv", novas, delim=",")


def test_matriz_sem_gene_name_nao_fecha_o_formato(tmp_path):
    """Revisão F2 #18 (canário): o expressao_deseq2.R faz setNames(bruto$gene_name, ...) e quebra
    sem a coluna DEPOIS de rodar o DESeq inteiro. Antes este teste dizia 'sem gene_name: também
    vale' — o portão aprovava uma matriz que o consumidor rejeita. (A entrada não tem gene_name:
    descartar uma coluna que já tem o nome-alvo é recusado à parte, no resíduo do #16.)"""
    ent = _demo_baguncado_sem_simbolo(tmp_path)
    amostras = _ler(COUNTS)[0][2:]
    dados = {"ops": [{"op": "renomear", "origem": "feature", "destino": "gene_id"},
                     {"op": "selecionar", "colunas": ["gene_id", *amostras]}]}
    r = adaptar(ent, "contagens_genes", tmp_path / "out.tsv",
                chamar=lambda s, u, e: {"dados": dados, "modelo": "falso"})
    assert not r.ok and any("gene_name" in x for x in r.excecoes), r.excecoes
    assert not (tmp_path / "out.tsv").exists()
    # controle: a mesma proposta com a cópia declarada do gene_id fecha o formato
    dados["ops"].append({"op": "copiar", "origem": "gene_id", "destino": "gene_name"})
    r2 = adaptar(ent, "contagens_genes", tmp_path / "out2.tsv",
                 chamar=lambda s, u, e: {"dados": dados, "modelo": "falso"})
    assert r2.ok, r2.excecoes
    assert formatos.contagens_e_coldata(tmp_path / "out2.tsv", COLDATA).ok


def test_copia_declarada_fecha_matriz_sem_simbolo_sem_chamar_o_modelo(tmp_path):
    """featureCounts/tximport costumam sair só com gene_id + amostras: o determinístico copia o
    gene_id para gene_name (declarado nas ops) e o resultado passa no validador e no cruzamento."""
    linhas = _ler(COUNTS)
    sem_nome = [[l[0], *l[2:]] for l in linhas]
    ent = _gravar(tmp_path / "sem_nome.tsv", sem_nome)
    assert not formatos.validar("contagens_genes", ent).ok  # canário: sem gene_name reprova
    r = adaptar(ent, "contagens_genes", tmp_path / "out.tsv", chamar=_nunca)
    assert r.ok and r.modo == "deterministico", r.excecoes
    assert r.ops == [{"op": "copiar", "origem": "gene_id", "destino": "gene_name"}]
    saida = _ler(tmp_path / "out.tsv")
    assert saida[0][:2] == ["gene_id", "gene_name"] and all(l[0] == l[1] for l in saida[1:])
    assert formatos.contagens_e_coldata(tmp_path / "out.tsv", COLDATA).ok


def test_simbolo_com_nome_fora_dos_sinonimos_sobe_para_o_modelo(tmp_path):
    """A cópia determinística NÃO esconde uma coluna de símbolo de nome inesperado: ela é texto,
    o validador reprova a tentativa, e o caso vai ao modelo (que a renomeia)."""
    linhas = _ler(COUNTS)
    linhas[0][1] = "external_gene_name"
    ent = _gravar(tmp_path / "ext.tsv", linhas)
    chamadas = []

    def falso(sistema, usuario, esquema):
        chamadas.append(1)
        return {"dados": {"ops": [{"op": "renomear", "origem": "external_gene_name", "destino": "gene_name"}]},
                "modelo": "falso"}

    r = adaptar(ent, "contagens_genes", tmp_path / "out.tsv", chamar=falso)
    assert chamadas and r.ok and r.modo == "llm"
    assert (tmp_path / "out.tsv").read_bytes() == COUNTS.read_bytes().replace(b"\r\n", b"\n")


@pytest.mark.parametrize("op", [
    {"op": "copiar", "origem": _ler(COUNTS)[0][2], "destino": "S_inventada"},  # réplica inventada
    {"op": "copiar", "origem": "gene_name", "destino": "gene_id2"},
])
def test_copiar_fora_do_par_autorizado_e_descartado(tmp_path, op):
    """Copiar uma coluna de amostra seria inventar uma réplica que passa no validador: o
    aplicador só aceita os pares de `copia_se_ausente` do formato."""
    with pytest.raises(SpecInvalida, match="não é permitido"):
        aplicar(COUNTS, [op], tmp_path / "out.tsv")  # sem autorização nenhuma
    with pytest.raises(SpecInvalida, match="não é permitido"):
        aplicar(COUNTS, [op], tmp_path / "out.tsv", frozenset({("gene_id", "gene_name")}))
    r = adaptar(_demo_baguncado(tmp_path), "contagens_genes", tmp_path / "out.tsv",
                ops_autor=[{"op": "renomear", "origem": "feature", "destino": "gene_id"}, op], chamar=_nunca)
    assert not r.ok and any("descartada" in x for x in r.descartes), r.descartes


def test_proibicao_de_criar_admite_so_a_copia_autorizada():
    """nada_criado com `copias`: a duplicação de UMA cópia passa; a de duas, não."""
    antes, cab_antes = [["G1", "1"], ["G2", "2"]], ["gene_id", "a"]
    depois = [["G1", "G1", "1"], ["G2", "G2", "2"]]
    from collections import Counter
    with pytest.raises(SpecInvalida, match="criaria"):  # canário: sem `copias`, duplicar é criar
        nada_criado(cab_antes, antes, ["gene_id", "gene_name", "a"], depois, ["gene_name"])
    nada_criado(cab_antes, antes, ["gene_id", "gene_name", "a"], depois, ["gene_name"],
                Counter(["G1", "G2"]))  # controle
    with pytest.raises(SpecInvalida, match="criaria"):
        nada_criado(cab_antes, antes, ["gene_id", "gene_name", "x", "a"],
                    [["G1", "G1", "G1", "1"], ["G2", "G2", "G2", "2"]], ["gene_name", "x"],
                    Counter(["G1", "G2"]))


def test_opcoes_do_formulario_separam_alvo_de_coluna_real_e_alvo_copiavel(tmp_path):
    """O n8n monta o formulário do autor com isto: dropdown obrigatório para `alvos` e, para
    gene_name, coluna real ou a cópia do gene_id."""
    r = adaptar(_demo_baguncado(tmp_path), "contagens_genes", tmp_path / "out.tsv",
                chamar=lambda s, u, e: {"dados": {"ops": [], "excecoes": ["dúvida"]}, "modelo": "falso"})
    assert r.opcoes["alvos"] == ["gene_id"]
    assert r.opcoes["copia_se_ausente"] == {"gene_name": "gene_id"}


# ----------------------- resíduo do #16: falha de VALOR não vai ao modelo; alvo não troca
def _registrar():
    chamadas = []

    def falso(sistema, usuario, esquema):
        chamadas.append(json.loads(usuario))
        return {"dados": {"ops": [{"op": "renomear", "origem": "condicao", "destino": "condicao_original"},
                                  {"op": "renomear", "origem": "sexo", "destino": "condicao"}]},
                "modelo": "falso"}
    return chamadas, falso


def _coldata_tres_condicoes(tmp_path: Path, id_amostra: str = "amostra") -> Path:
    """Experimento do verificador (exp2): 3 condições (o validador exige 2) e uma coluna sexo
    com 2 níveis de 3 réplicas — trocar sexo para 'condicao' FECHA o formato."""
    sexo = ["macho" if i % 2 else "femea" for i in range(6)]
    conds = ["ctrl", "ctrl", "frio", "frio", "calor", "calor"]
    return _gravar(tmp_path / "cd3.tsv", [[id_amostra, "condicao", "sexo"],
                                          *[[f"s{i}", c, s] for i, (c, s) in enumerate(zip(conds, sexo))]])


def test_falha_de_valor_escala_sem_chamar_o_modelo(tmp_path):
    """Canário do resíduo do #16. Antes: o modelo falso trocava sexo → condicao, e o adaptador
    devolvia ok=True, modo=llm, níveis {femea: 3, macho: 3} — o DESeq2 testaria SEXO no lugar
    do tratamento. Agora a falha é reconhecida como de valor e escala antes de chamar o modelo."""
    ent = _coldata_tres_condicoes(tmp_path)
    chamadas, falso = _registrar()
    r = adaptar(ent, "coldata", tmp_path / "out.tsv", chamar=falso)
    assert not r.ok and not chamadas, (r.ok, r.modo, r.ops)
    assert "falha de VALOR" in r.excecoes[0] and "corrija o arquivo" in r.excecoes[0]
    assert any("exatamente 2 condições" in x for x in r.excecoes)
    assert not (tmp_path / "out.tsv").exists()


@pytest.mark.parametrize("formato,linhas", [
    # nível com acento: o validador exige nível que vá para a linha de comando
    ("coldata", [["amostra", "condicao"], ["a", "controle"], ["b", "controle"], ["c", "estresse térmico"],
                 ["d", "estresse térmico"]]),
    # contagem NA numa matriz com os alvos na frente e só colunas numéricas
    ("contagens_genes", [["gene_id", "gene_name", "s1", "s2"], ["G1", "g1", "NA", "2"], ["G2", "g2", "3", "4"]]),
    # matriz sem símbolo (a cópia determinística entra) e uma amostra com contagem negativa
    ("contagens_genes", [["gene_id", "s1", "s2"], ["G1", "-1", "2"], ["G2", "3", "4"]]),
])
def test_outras_falhas_de_valor_tambem_nao_chamam_o_modelo(tmp_path, formato, linhas):
    ent = _gravar(tmp_path / "in.tsv", linhas)
    r = adaptar(ent, formato, tmp_path / "out.tsv", chamar=_nunca)
    assert not r.ok and r.modo == "deterministico" and "falha de VALOR" in r.excecoes[0], r.excecoes


def test_coluna_de_texto_na_matriz_ainda_vai_ao_modelo(tmp_path):
    """Controle do resíduo do #16: com gene_id e gene_name no lugar, uma coluna de TEXTO entre as
    amostras é caso de coluna (sai com `selecionar`) — o modelo é chamado e fecha o formato."""
    linhas = _ler(COUNTS)
    com_desc = [l[:2] + ["descricao" if i == 0 else f"desc_{l[1]}"] + l[2:] for i, l in enumerate(linhas)]
    ent = _gravar(tmp_path / "desc.tsv", com_desc)
    chamadas = []

    def falso(sistema, usuario, esquema):
        chamadas.append(1)
        return {"dados": {"ops": [{"op": "selecionar", "colunas": linhas[0]}]}, "modelo": "falso"}

    r = adaptar(ent, "contagens_genes", tmp_path / "out.tsv", chamar=falso)
    assert chamadas and r.ok and r.modo == "llm", r.excecoes
    assert (tmp_path / "out.tsv").read_bytes() == COUNTS.read_bytes().replace(b"\r\n", b"\n")


@pytest.mark.parametrize("ops", [
    # o experimento do verificador: renomear a coluna-alvo para fora e outra para dentro
    [{"op": "renomear", "origem": "id", "destino": "amostra"},
     {"op": "renomear", "origem": "condicao", "destino": "condicao_original"},
     {"op": "renomear", "origem": "sexo", "destino": "condicao"}],
    # o mesmo, por `selecionar`: descarta a coluna-alvo e põe outra no lugar
    [{"op": "renomear", "origem": "id", "destino": "amostra"},
     {"op": "selecionar", "colunas": ["amostra", "sexo"]},
     {"op": "renomear", "origem": "sexo", "destino": "condicao"}],
])
def test_modelo_nao_troca_a_coluna_que_ja_tem_o_nome_alvo(tmp_path, ops):
    """Defesa (b) do resíduo do #16: 'id' não é sinônimo de amostra, então a falha NÃO é só de
    valor e o modelo é chamado — mas a operação que tira o nome 'condicao' da coluna que já o
    tinha é descartada e escala. Sem a defesa, as duas sequências davam ok=True com sexo
    no lugar do tratamento."""
    ent = _coldata_tres_condicoes(tmp_path, id_amostra="id")
    chamadas = []

    def falso(sistema, usuario, esquema):
        chamadas.append(1)
        return {"dados": {"ops": ops}, "modelo": "falso"}

    r = adaptar(ent, "coldata", tmp_path / "out.tsv", chamar=falso)
    assert chamadas and not r.ok, (r.modo, r.ops)
    assert any("['condicao']" in d and "muda o experimento" in d for d in r.descartes), r.descartes
    assert not (tmp_path / "out.tsv").exists()


def test_autor_tambem_nao_troca_a_coluna_que_ja_tem_o_nome_alvo(tmp_path):
    ent = _coldata_tres_condicoes(tmp_path)
    troca = [{"op": "renomear", "origem": "condicao", "destino": "condicao_original"},
             {"op": "renomear", "origem": "sexo", "destino": "condicao"}]
    r = adaptar(ent, "coldata", tmp_path / "out.tsv", ops_autor=troca, chamar=_nunca)
    assert not r.ok and r.modo == "autor" and r.descartes, r.ops
    assert not (tmp_path / "out.tsv").exists()


def test_transpor_tabela_que_ja_tem_coluna_alvo_e_descartado(tmp_path):
    """Matriz genes × amostras com gene_id: transpor e copiar gene_id → gene_name dava uma
    matriz VÁLIDA com amostras no lugar dos genes (o gene_id fica na 1ª coluna, com outro
    conteúdo). Controle: a tabela amostras × genes (sem coluna-alvo) continua transpondo."""
    certa = _gravar(tmp_path / "genes.tsv", [["gene_id", "S1", "S2", "S3"], ["G1", "1", "2", "3"],
                                            ["G2", "4", "5", "6"], ["G3", "7", "8", "9"]])
    virar = [{"op": "transpor"}, {"op": "copiar", "origem": "gene_id", "destino": "gene_name"}]
    r = adaptar(certa, "contagens_genes", tmp_path / "out.tsv", ops_autor=virar, chamar=_nunca)
    assert not r.ok and any("transpor" in d for d in r.descartes), r.descartes
    deitada = _gravar(tmp_path / "amostras.tsv", [["amostra", "G1", "G2", "G3"], ["S1", "1", "4", "7"],
                                                 ["S2", "2", "5", "8"], ["S3", "3", "6", "9"]])
    r2 = adaptar(deitada, "contagens_genes", tmp_path / "out2.tsv", chamar=_nunca, ops_autor=[
        {"op": "transpor"}, {"op": "renomear", "origem": "amostra", "destino": "gene_id"},
        {"op": "copiar", "origem": "gene_id", "destino": "gene_name"}])
    assert r2.ok, r2.excecoes
    assert _ler(tmp_path / "out2.tsv")[:2] == [["gene_id", "gene_name", "S1", "S2", "S3"], ["G1", "G1", "1", "2", "3"]]


def test_descartar_gene_name_real_para_copiar_o_gene_id_e_recusado(tmp_path):
    """A mesma regra na matriz: a coluna gene_name que existe não é trocada pela cópia do
    gene_id (ela é o símbolo do gene; a cópia é só para quando não há símbolo)."""
    ent = _demo_baguncado(tmp_path)
    amostras = _ler(COUNTS)[0][2:]
    dados = {"ops": [{"op": "renomear", "origem": "feature", "destino": "gene_id"},
                     {"op": "selecionar", "colunas": ["gene_id", *amostras]},
                     {"op": "copiar", "origem": "gene_id", "destino": "gene_name"}]}
    r = adaptar(ent, "contagens_genes", tmp_path / "out.tsv",
                chamar=lambda s, u, e: {"dados": dados, "modelo": "falso"})
    assert not r.ok and any("['gene_name']" in d for d in r.descartes), r.descartes


# ------------------------------------- A04: campo gigante não vira exceção no adaptador
def test_adaptar_arquivo_com_campo_gigante_reprova_sem_levantar(tmp_path):
    """Mutante A04 do verificador: sem o except csv.Error do ler_tabela, os testes passavam —
    e o runs.adaptar_entrada só captura MotorIndisponivel: o csv.Error virava 500."""
    ent = _gravar(tmp_path / "cd.tsv", [["amostra", "condicao"], ["a", "x"], ["b", "y" + "z" * 200_000]])
    r = adaptar(ent, "coldata", tmp_path / "out.tsv", chamar=_nunca)
    assert not r.ok and any("ilegível" in x for x in r.excecoes), r.excecoes
    with pytest.raises(SpecInvalida, match="ilegível"):
        aplicar(ent, [], tmp_path / "out.tsv")
    # controle: campo no limite do csv (131000) é lido
    ok = _gravar(tmp_path / "ok.tsv", [["amostra", "condicao"], ["a", "x"], ["b", "y" + "z" * 131_000]])
    aplicar(ok, [], tmp_path / "out_ok.tsv")
    assert (tmp_path / "out_ok.tsv").exists()


def test_aspas_do_r_e_linhas_so_de_tab_o_deterministico_limpa(tmp_path):
    """write.table(quote=TRUE) do R e o resíduo de TABs do Excel: o validador reprova (o R
    leria diferente do Python), e o adaptador normaliza sem chamar o modelo, declarando."""
    linhas = _ler(COUNTS)
    texto = "\n".join("\t".join(f'"{c}"' if not c.isdigit() else c for c in l) for l in linhas)
    ent = tmp_path / "aspas.tsv"
    ent.write_text(texto + "\n\t\t\t\n", encoding="utf-8")
    v = formatos.validar("contagens_genes", ent)
    assert not v.ok and any("aspas" in f for f in v.falhas), v.falhas
    r = adaptar(ent, "contagens_genes", tmp_path / "out.tsv", chamar=_nunca)
    assert r.ok and r.modo == "deterministico" and r.ops == []
    assert r.relatorio["linhas_so_separadores_descartadas"] == 1
    assert (tmp_path / "out.tsv").read_bytes() == COUNTS.read_bytes().replace(b"\r\n", b"\n")


# ------------------------------------------------- resíduos da 2ª rodada (teto e transpor)
def test_coldata_largo_e_transposto_pelo_modelo(tmp_path):
    """Coldata 'largo' (amostras nas colunas): virar a tabela TRAZ o alvo 'condicao' ao
    cabeçalho — é a correção certa e não pode ser descartada só porque 'amostra' já existe."""
    ent = _gravar(tmp_path / "largo.tsv", [["amostra", "S1", "S2", "S3", "S4"],
                                           ["condicao", "ctrl", "ctrl", "calor", "calor"]])

    def falso(sistema, usuario, esquema):
        return {"dados": {"ops": [{"op": "transpor"}], "excecoes": []}, "modelo": "falso"}

    r = adaptar(ent, "coldata", tmp_path / "out.tsv", chamar=falso)
    assert r.ok and r.modo == "llm", (r.excecoes, r.descartes)
    assert formatos.validar("coldata", tmp_path / "out.tsv").ok


def test_arquivo_acima_do_teto_nao_e_lido_pelo_adaptador(tmp_path, monkeypatch):
    """O validador para no teto (linhas físicas, vazias contam); o adaptador leria a tabela
    inteira na memória três vezes. Acima do teto: ok=False sem ler nem chamar o modelo."""
    ent = tmp_path / "cd.tsv"
    ent.write_text("amostra\tcondicao\nS1\tc\nS2\tc\nS3\tt\nS4\tt\n" + "\n" * 20_000, encoding="utf-8")

    def nao_le(*a, **k):
        raise AssertionError("o adaptador não devia ler a tabela")

    monkeypatch.setattr(adaptador, "ler_tabela", nao_le)
    r = adaptar(ent, "coldata", tmp_path / "out.tsv", chamar=_nunca)
    assert not r.ok and "teto" in r.excecoes[0]


def test_arquivo_grande_demais_nao_e_lido_pelo_adaptador(tmp_path, monkeypatch):
    linhas = _ler(COUNTS)
    linhas[0][0] = "feature"
    ent = _gravar(tmp_path / "c.tsv", linhas)
    monkeypatch.setattr(adaptador, "MAX_BYTES_ADAPTAVEL", 1024)

    def nao_le(*a, **k):
        raise AssertionError("o adaptador não devia ler a tabela")

    monkeypatch.setattr(adaptador, "ler_tabela", nao_le)
    r = adaptar(ent, "contagens_genes", tmp_path / "out.tsv", chamar=_nunca)
    assert not r.ok and "teto do adaptador" in r.excecoes[0]
