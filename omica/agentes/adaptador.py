"""adaptador.py — agente adaptador (Camada 1): põe uma ENTRADA do usuário no formato que o
primeiro script do pipeline consome.

Ordem, do mais barato e verificável ao mais caro:
  1. `nenhuma`        — o arquivo já passa no validador do formato;
  2. `deterministico` — normalização (BOM, separador, aspas, espaço no cabeçalho), sinônimos
                        INEQUÍVOCOS de nome de coluna (uma coluna só casa com o alvo) e a
                        cópia DECLARADA gene_id → gene_name quando nenhuma coluna casa com
                        gene_name (o expressao_deseq2.R exige a coluna; sem símbolo, o nome
                        do gene é o próprio identificador — e as operações dizem isso);
  3. `llm`            — o modelo local PROPÕE operações sobre os cabeçalhos reais; este
                        código confere e o aplicador determinístico executa;
  4. `autor`          — o autor escolhe as operações num formulário com dropdowns que só
                        contêm cabeçalhos reais (mesmas conferências do item 3).

Falha de VALOR de célula nunca vai ao modelo (revisão F2, resíduo do #16): se as colunas-alvo
já existem pelo nome EXATO e nenhuma operação de coluna legítima resta, o que reprova é valor
(3 condições, nível com acento, NA na contagem) — o modelo só sabe mexer em colunas, e a única
"correção" que fechava o formato era pôr OUTRA coluna no lugar da certa (sexo no lugar de
condicao: ok=True e o DESeq2 testando outro experimento). Escala "corrija o arquivo" antes
de gastar uma inferência. E, venha do modelo ou do autor, operação que TIRA o nome-alvo de
uma coluna que já o tinha (renomear condicao → x, selecionar sem ela, transpor a tabela) é
descartada e escala.

As cinco salvaguardas (doutrina do autor para LLM na Camada 1), em código:
  - ESQUEMA RÍGIDO: PropostaAdaptacao com extra="forbid"; fora do esquema = exceção.
  - ANCORAGEM: toda `origem`/`colunas` é cabeçalho real naquele ponto da sequência; o que
    não ancora é DESCARTADO e LISTADO, nunca corrigido — e qualquer descarte escala.
  - PROIBIÇÃO DE CRIAR: o aplicador (contrato/aplicar_spec.py) só renomeia, seleciona,
    reordena, transpõe e copia — e copiar, só os pares de `copia_se_ausente` do formato
    (copiar uma coluna de amostra seria inventar uma réplica) —, e confere que nenhum valor
    novo apareceu; depois o validador do formato roda de novo sobre o resultado.
  - AUDITORIA: com ≥20 operações, uma 2ª chamada independente refaz 10% (mín. 2) delas;
    discordância acima de 2% invalida a proposta inteira.
  - EXCEÇÃO ESCALA: dúvida do modelo, descarte, ambiguidade ou formato que não fecha viram
    `excecoes` — o n8n leva ao autor (workflow omica_erro); nada segue sozinho.
O detector de AMBIGUIDADE é determinístico e tem a última palavra: se duas colunas reais
casam com o mesmo alvo pelos sinônimos, é exceção, diga o modelo o que disser.
"""
from __future__ import annotations

import csv
import json
import re
import shutil
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from omica.contrato import formatos
from omica.contrato.aplicar_spec import (SpecInvalida, aplicar, ler_tabela, perfil_tabela,
                                         simular_cabecalho)

LIMIAR_AUDITORIA = 20
MAX_BYTES_ADAPTAVEL = 64 * 1024 * 1024  # matriz de 60 mil genes × 100 amostras ≈ 30 MB
TAXA_MAX_DISCORDANCIA = 0.02

# O que cada formato exige, em linguagem para o modelo E para o detector de sinônimos.
ALVOS: dict[str, dict[str, Any]] = {
    "contagens_genes": {
        "descricao": ("Matriz de contagens com GENES NAS LINHAS. 1ª coluna 'gene_id' (identificador "
                      "do gene); 2ª coluna 'gene_name' (nome/símbolo do gene) é OBRIGATÓRIA — se o "
                      "arquivo não tiver coluna de nome ou símbolo, use 'copiar' de 'gene_id' para "
                      "'gene_name' (o nome passa a ser o próprio identificador); todas as demais "
                      "colunas são AMOSTRAS com contagens ≥0. Se as amostras estiverem nas linhas, "
                      "use 'transpor'. Colunas que não são gene_id, gene_name nem amostra (ex.: "
                      "comprimento, descrição) devem ficar de fora com 'selecionar'."),
        "sinonimos": {"gene_id": ["gene_id", "geneid", "gene", "ensembl_gene_id", "ensembl_id",
                                  "ensembl", "id_gene"],
                      "gene_name": ["gene_name", "genename", "symbol", "gene_symbol", "simbolo"]},
        "obrigatorios": ["gene_id", "gene_name"],
        # alvo obrigatório que pode vir de CÓPIA de outro alvo quando nenhuma coluna real casa:
        # é o único `copiar` que o aplicador aceita para este formato
        "copia_se_ausente": {"gene_name": "gene_id"},
        # as demais colunas são AMOSTRAS (numéricas): coluna de texto ainda é caso de coluna
        # (sai com `selecionar`); com os alvos na frente e só colunas numéricas, sobra valor
        "extras": "amostras",
    },
    "coldata": {
        "descricao": ("Tabela de amostras, uma linha por amostra. Coluna 'amostra' com o nome EXATO "
                      "da coluna da amostra na matriz de contagens; coluna 'condicao' com o grupo "
                      "experimental. Outras colunas podem ficar (são ignoradas)."),
        "sinonimos": {"amostra": ["amostra", "amostras", "sample", "samples", "sample_id",
                                  "sampleid", "sample_name", "id_amostra"],
                      "condicao": ["condicao", "condition", "grupo", "group", "tratamento",
                                   "treatment"]},
        "obrigatorios": ["amostra", "condicao"],
        # as demais colunas o validador IGNORA: com os alvos presentes pelo nome exato, nenhuma
        # operação de coluna muda o veredito sem trocar a coluna certa por outra
        "extras": "ignoradas",
    },
}

SISTEMA = """Você converte o CABEÇALHO de uma tabela para um formato-alvo. Você NÃO vê nem altera
os dados: só propõe operações sobre nomes de colunas que EXISTEM.

Operações permitidas (aplicadas em ordem):
- {"op": "renomear", "origem": "<nome EXATO de uma coluna atual>", "destino": "<nome-alvo>"}
- {"op": "selecionar", "colunas": ["<nomes EXATOS atuais, na ordem desejada>"]}
- {"op": "transpor"}  (linhas viram colunas; o cabeçalho passa a ser a 1ª coluna)
- {"op": "copiar", "origem": "gene_id", "destino": "gene_name"}  (SÓ este par, e só quando
  não existe coluna de nome/símbolo do gene; a cópia entra logo depois da origem)

Regras:
1. Copie os nomes de origem LETRA POR LETRA do cabeçalho mostrado. Nunca invente coluna.
2. Se não tiver certeza de qual coluna é qual, NÃO chute: descreva a dúvida em "excecoes".
3. Se duas colunas parecerem servir ao mesmo alvo, é exceção.
4. Coluna que JÁ tem o nome-alvo exato fica como está: não a renomeie, não a descarte, não
   ponha outra no lugar dela. Problema nos VALORES não se resolve com colunas: é exceção.
5. Responda só o objeto JSON do esquema."""


class Operacao(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["renomear", "selecionar", "transpor", "copiar"]
    origem: Optional[str] = Field(default=None, max_length=200)
    destino: Optional[str] = Field(default=None, max_length=80)
    colunas: Optional[list[str]] = Field(default=None, max_length=5000)

    @model_validator(mode="after")
    def _campos_da_op(self) -> "Operacao":
        exigidos = {"renomear": {"origem", "destino"}, "selecionar": {"colunas"}, "transpor": set(),
                    "copiar": {"origem", "destino"}}[self.op]
        presentes = {c for c in ("origem", "destino", "colunas") if getattr(self, c) is not None}
        if presentes != exigidos:
            raise ValueError(f"{self.op} usa exatamente {sorted(exigidos)} (veio {sorted(presentes)})")
        return self

    def como_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class PropostaAdaptacao(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ops: list[Operacao] = Field(default_factory=list, max_length=200)
    excecoes: list[str] = Field(default_factory=list, max_length=20)
    justificativa: str = Field(default="", max_length=2000)


def _normalizar(nome: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", sem_acento.lower())


def candidatos_por_alvo(formato: str, cabecalho: list[str]) -> dict[str, list[str]]:
    """Para cada coluna-alvo, as colunas REAIS que casam por sinônimo (comparação sem acento,
    caixa nem pontuação). Lista com 2+ nomes = ambiguidade."""
    saida: dict[str, list[str]] = {}
    for alvo, sinonimos in ALVOS.get(formato, {}).get("sinonimos", {}).items():
        chaves = {_normalizar(s) for s in sinonimos}
        saida[alvo] = [c for c in cabecalho if _normalizar(c) in chaves]
    return saida


class Resultado(BaseModel):
    modo: Literal["nenhuma", "deterministico", "llm", "autor"]
    ok: bool
    ops: list[dict[str, Any]] = Field(default_factory=list)
    excecoes: list[str] = Field(default_factory=list)
    descartes: list[str] = Field(default_factory=list)
    veredito: dict[str, Any] = Field(default_factory=dict)
    relatorio: dict[str, Any] = Field(default_factory=dict)
    modelo: str = ""
    auditoria: dict[str, Any] = Field(default_factory=dict)
    opcoes: dict[str, Any] = Field(default_factory=dict)  # cabeçalhos reais p/ o formulário do autor


def _copias(formato: str) -> frozenset[tuple[str, str]]:
    """Pares (origem, destino) que um `copiar` pode usar neste formato — e nenhum outro."""
    return frozenset((fonte, alvo) for alvo, fonte in ALVOS.get(formato, {}).get("copia_se_ausente", {}).items())


def _tentar(entrada: Path, ops: list[dict[str, Any]], formato: str, destino: Path) -> tuple[Optional[dict], formatos.Veredito]:
    try:
        relatorio = aplicar(entrada, ops, destino, _copias(formato))
    except SpecInvalida as e:
        return None, formatos.Veredito(formato, False, [f"aplicação recusada: {e}"])
    return relatorio, formatos.validar(formato, destino)


def _ambiguidades(formato: str, cab: list[str]) -> list[str]:
    """Alvo sem coluna de nome EXATO e com 2+ colunas reais casando por sinônimo."""
    return [f"colunas {reais} casam todas com '{alvo}'"
            for alvo, reais in candidatos_por_alvo(formato, cab).items()
            if len(reais) > 1 and alvo not in cab]


def _deterministico(formato: str, cab: list[str]) -> list[dict[str, Any]]:
    """Renomeações por sinônimo INEQUÍVOCO (uma coluna real só, e o alvo ainda ausente) e, para
    o alvo de `copia_se_ausente` que nenhuma coluna real casa, a cópia declarada da fonte. A
    cópia só fecha o formato se todas as outras colunas forem amostras numéricas — se houver
    uma coluna de símbolo com nome fora dos sinônimos, ela é texto, o validador reprova e o
    caso sobe para o modelo, que a renomeia."""
    candidatos = candidatos_por_alvo(formato, cab)
    ops = [{"op": "renomear", "origem": reais[0], "destino": alvo}
           for alvo, reais in candidatos.items() if len(reais) == 1 and alvo not in cab]
    nomes = set(cab) | {op["destino"] for op in ops}
    for alvo, fonte in ALVOS.get(formato, {}).get("copia_se_ausente", {}).items():
        if alvo not in nomes and fonte in nomes and not candidatos.get(alvo):
            ops.append({"op": "copiar", "origem": fonte, "destino": alvo})
    return ops


def _alvos(formato: str) -> frozenset[str]:
    """Nomes-alvo do formato: a coluna que já tem um deles pelo nome exato não sai do lugar."""
    alvo = ALVOS.get(formato, {})
    return frozenset(alvo.get("obrigatorios", [])) | frozenset(alvo.get("sinonimos", {}))


def _cabecalho_tsv(caminho: Path) -> list[str]:
    """Cabeçalho de um TSV que o aplicador gravou (só a 1ª linha; o aplicador escreve com o csv)."""
    with caminho.open(encoding="utf-8", newline="") as f:
        return next(csv.reader(f, delimiter="\t"), [])


def _so_falha_de_valor(formato: str, cab: list[str], perfil: dict[str, Any]) -> bool:
    """O cabeçalho (já com as operações determinísticas) tem todos os alvos pelo nome EXATO e
    nenhuma operação de coluna legítima resta: o que reprova é VALOR de célula. Em coldata as
    outras colunas são ignoradas; na matriz, sobra coluna a tirar só se houver texto entre as
    amostras ou os alvos fora da frente (aí o modelo ainda pode `selecionar`)."""
    alvo = ALVOS[formato]
    obrig = list(alvo["obrigatorios"])
    if not all(a in cab for a in obrig):
        return False
    if alvo.get("extras") == "ignoradas":
        return True
    tipos = perfil.get("tipos", {})
    return cab[:len(obrig)] == obrig and all(tipos.get(c) == "numerica" for c in cab[len(obrig):])


def _ancorar(entrada: Path, ops: list[Operacao], pasta: Path,
             copias_permitidas: frozenset[tuple[str, str]] = frozenset(), *,
             alvos: frozenset[str] = frozenset(),
             cab: Optional[list[str]] = None) -> tuple[list[dict], list[str]]:
    """Confere a sequência contra a tabela REAL, aplicando operação por operação com o MESMO
    aplicador que vai gravar o resultado (simular só o cabeçalho erraria depois de um
    `transpor`). A que não ancora é descartada e listada — nunca corrigida — e as seguintes
    são conferidas contra a tabela sem ela.

    `alvos` + `cab` (cabeçalho da entrada): a operação que TIRA o nome-alvo de uma coluna que
    já o tem — renomear, selecionar sem ela, ou transpor a tabela que já tem coluna-alvo (genes
    viram amostras com o gene_id no mesmo lugar) — é descartada, venha do modelo ou do autor:
    pôr outra coluna no lugar da certa muda o experimento sem o validador ver."""
    aceitas, descartes = [], []
    atual = entrada
    cab_atual = list(cab) if cab is not None else []
    for i, op in enumerate(ops):
        d = op.como_dict()
        presentes = sorted(a for a in alvos if a in cab_atual)
        proximo = pasta / f"ancora_{i}.tsv"
        try:
            aplicar(atual, [d], proximo, copias_permitidas)
        except SpecInvalida as e:
            descartes.append(f"op {i + 1} ({d['op']}) descartada: {e}")
            continue
        depois = _cabecalho_tsv(proximo)
        if d["op"] == "transpor" and presentes and not [a for a in alvos if a in depois and a not in presentes]:
            # virar a tabela só se justifica se TRAZ alvos novos ao cabeçalho (coldata "largo":
            # amostra|S1|S2… vira amostra|condicao). Matriz com gene_id que vira amostras×genes
            # continua só com o gene_id: troca o que é linha e coluna sem ganho — descarta.
            descartes.append(f"op {i + 1} (transpor) descartada: a tabela já tem {presentes} pelo nome "
                             "exato e virá-la não traz nenhum alvo novo — trocaria o que é linha e o que é coluna")
            continue
        perdidos = [a for a in presentes if a not in depois]
        if perdidos:
            descartes.append(f"op {i + 1} ({d['op']}) descartada: tira o nome {perdidos} da coluna que já o "
                             "tinha pelo nome exato — pôr outra coluna no lugar dela muda o experimento "
                             "(ex.: sexo no lugar de condicao); se a coluna certa é outra, corrija o arquivo")
            continue
        aceitas.append(d)
        atual, cab_atual = proximo, depois
    return aceitas, descartes


def _auditar(aceitas: list[dict[str, Any]], perfil: dict[str, Any], formato: str,
             chamar) -> dict[str, Any]:
    """2ª chamada INDEPENDENTE refaz uma amostra determinística das renomeações (10%,
    mín. 2). Discordância > 2% invalida tudo."""
    renomes = [op for op in aceitas if op["op"] == "renomear"]
    if len(aceitas) < LIMIAR_AUDITORIA or not renomes:
        return {"aplicada": False, "motivo": f"{len(aceitas)} operações (< {LIMIAR_AUDITORIA})"}
    k = max(2, -(-len(renomes) // 10))
    passo = max(1, len(renomes) // k)
    amostra = renomes[::passo][:k]
    pergunta = {"formato_alvo": ALVOS[formato]["descricao"], "cabecalho": perfil["cabecalho"],
                "colunas_a_decidir": [op["origem"] for op in amostra]}
    esquema = {"type": "object", "properties": {"destinos": {"type": "object",
               "additionalProperties": {"type": "string"}}}, "required": ["destinos"]}
    resposta = chamar(
        "Para cada coluna em 'colunas_a_decidir', diga o nome-alvo dela no formato descrito. "
        "Responda {\"destinos\": {\"<coluna>\": \"<alvo>\"}}.",
        json.dumps(pergunta, ensure_ascii=False), esquema)
    destinos = resposta["dados"].get("destinos", {}) if isinstance(resposta.get("dados"), dict) else {}
    discordam = [op["origem"] for op in amostra if destinos.get(op["origem"]) != op["destino"]]
    taxa = len(discordam) / len(amostra)
    return {"aplicada": True, "amostra": len(amostra), "discordancias": discordam,
            "taxa": round(taxa, 4), "aprovada": taxa <= TAXA_MAX_DISCORDANCIA}


def adaptar(entrada: Path, formato: str, destino: Path, *, ops_autor: Optional[list[dict]] = None,
            chamar=None) -> Resultado:
    """Adapta `entrada` ao `formato`, gravando em `destino` só se o resultado passar no
    validador. `ops_autor` = operações escolhidas pelo autor no formulário (modo autor).
    `chamar(sistema, usuario, esquema)` = motor (padrão: motor_local.chat_json)."""
    if formato not in ALVOS:
        return Resultado(modo="nenhuma", ok=False, excecoes=[f"formato {formato!r} não é adaptável"])
    v = formatos.validar(formato, entrada)
    if v.ok:
        return Resultado(modo="nenhuma", ok=True, veredito=v.to_dict())
    # O adaptador lê a tabela INTEIRA na memória (ler, perfilar, aplicar) e o n8n o chama para
    # toda entrada reprovada: arquivo acima do teto do validador ou grande demais nem é lido.
    if any("teto da leitura" in f for f in v.falhas):
        return Resultado(modo="nenhuma", ok=False, veredito=v.to_dict(),
                         excecoes=["arquivo acima do teto da leitura do formato: não é adaptável — corrija o arquivo"])
    tamanho = entrada.stat().st_size
    if tamanho > MAX_BYTES_ADAPTAVEL:
        return Resultado(modo="nenhuma", ok=False, veredito=v.to_dict(),
                         excecoes=[f"arquivo de {tamanho / 2**20:.0f} MiB acima do teto do adaptador "
                                   f"({MAX_BYTES_ADAPTAVEL // 2**20} MiB) — corrija o arquivo"])
    try:
        _, cab, _, _ = ler_tabela(entrada)
        perfil = perfil_tabela(entrada)
    except SpecInvalida as e:
        return Resultado(modo="nenhuma", ok=False, excecoes=[str(e)], veredito=v.to_dict())
    # formulário do autor (n8n): `alvos` = dropdown obrigatório de coluna REAL; para o alvo de
    # `copia_se_ausente`, o autor escolhe uma coluna real ou deixa vazio, e aí o formulário
    # manda {"op": "copiar", "origem": <fonte>, "destino": <alvo>}
    copia = ALVOS[formato].get("copia_se_ausente", {})
    opcoes = {"cabecalho": cab, "alvos": [a for a in ALVOS[formato]["obrigatorios"] if a not in copia],
              "copia_se_ausente": copia, "candidatos": candidatos_por_alvo(formato, cab)}

    with tempfile.TemporaryDirectory() as tmp:
        pasta = Path(tmp)
        teste = pasta / "adaptado.tsv"

        def _fechar(modo: str, ops: list[dict], **extra: Any) -> Resultado:
            relatorio, vv = _tentar(entrada, ops, formato, teste)
            if relatorio is None or not vv.ok:
                return Resultado(modo=modo, ok=False, ops=ops, veredito=vv.to_dict(), opcoes=opcoes,
                                 excecoes=[f"a proposta não fecha o formato {formato}: {f}" for f in vv.falhas[:5]],
                                 **extra)
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(teste, destino)
            return Resultado(modo=modo, ok=True, ops=ops, veredito=vv.to_dict(), relatorio=relatorio, **extra)

        if ops_autor is not None:
            try:
                prop = PropostaAdaptacao(ops=ops_autor)
            except ValidationError as e:
                return Resultado(modo="autor", ok=False, excecoes=[f"operações fora do esquema: {e.errors()[:3]}"], opcoes=opcoes)
            aceitas, descartes = _ancorar(entrada, prop.ops, pasta, _copias(formato),
                                          alvos=_alvos(formato), cab=cab)
            if descartes:
                return Resultado(modo="autor", ok=False, descartes=descartes, excecoes=descartes, opcoes=opcoes)
            return _fechar("autor", aceitas)

        # 2. determinístico
        ambiguas = _ambiguidades(formato, cab)
        if ambiguas:
            return Resultado(modo="deterministico", ok=False, excecoes=ambiguas, opcoes=opcoes,
                             veredito=v.to_dict())
        ops_det = _deterministico(formato, cab)
        r = _fechar("deterministico", ops_det)
        if r.ok:
            return r

        # 2b. falha de VALOR: nada de coluna a mexer — escala sem gastar a inferência (que só
        # acharia "correção" trocando a coluna certa por outra)
        try:
            cab_det = simular_cabecalho(cab, ops_det)
        except SpecInvalida:
            cab_det = cab
        if _so_falha_de_valor(formato, cab_det, perfil):
            obrig = ALVOS[formato]["obrigatorios"]
            return Resultado(
                modo="deterministico", ok=False, ops=ops_det, veredito=r.veredito, opcoes=opcoes,
                excecoes=[f"falha de VALOR, não de mapeamento de colunas: {obrig} já existem pelo nome exato "
                          "e o arquivo ainda reprova — corrija o arquivo (motivos abaixo). O adaptador só "
                          "renomeia e seleciona colunas, e pôr outra coluna no lugar de uma delas mudaria "
                          "o experimento"]
                + [f"o validador reprova: {f}" for f in r.veredito.get("falhas", [])[:5]])

        # 3. LLM — só propõe
        if chamar is None:
            from omica.agentes.motor_local import chat_json as chamar  # noqa: N806
        usuario = json.dumps({"formato_alvo": ALVOS[formato]["descricao"], "perfil": perfil,
                              "falhas_do_validador": v.falhas[:8]}, ensure_ascii=False)
        resposta = chamar(SISTEMA, usuario, PropostaAdaptacao.model_json_schema())
        modelo = resposta.get("modelo", "")
        try:
            prop = PropostaAdaptacao.model_validate(resposta["dados"])
        except (ValidationError, KeyError, TypeError) as e:
            return Resultado(modo="llm", ok=False, modelo=modelo, opcoes=opcoes,
                             excecoes=[f"resposta fora do esquema: {str(e)[:300]}"])
        # (a ambiguidade por sinônimo já escalou acima, ANTES de chamar o modelo: com 2+
        # colunas casando com o mesmo alvo o modelo nem é consultado)
        aceitas, descartes = _ancorar(entrada, prop.ops, pasta, _copias(formato),
                                      alvos=_alvos(formato), cab=cab)
        excecoes = list(prop.excecoes) + descartes
        if excecoes:
            return Resultado(modo="llm", ok=False, ops=aceitas, excecoes=excecoes, descartes=descartes,
                             modelo=modelo, opcoes=opcoes)
        auditoria = _auditar(aceitas, perfil, formato, chamar)
        if auditoria.get("aplicada") and not auditoria["aprovada"]:
            return Resultado(modo="llm", ok=False, ops=aceitas, modelo=modelo, auditoria=auditoria,
                             opcoes=opcoes, excecoes=[f"auditoria reprovou: {auditoria['discordancias']}"])
        return _fechar("llm", aceitas, modelo=modelo, auditoria=auditoria)
