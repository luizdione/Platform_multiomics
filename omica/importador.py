"""importador.py — importador de subprojetos legados (v2 multiespécie, M5), SÓ em --dry-run.

    python -m omica importar-subprojetos --dry-run --catalogo <yaml> [--saida <pasta>] [--json <relatorio.json>]

O catálogo fica FORA do repositório (traz perguntas de artigo — regra 7) e é uma LISTA de

    - subprojeto: {<campos do modelo omica.subprojetos.Subprojeto>}
      importador: {razao, canarios_ausentes, host_somente, excluir_do_indice, invalidos_conhecidos,
                   nunca_usar, painel_fonte, status_proposto, fonte_titulo, fonte_pergunta,
                   fonte_assembly, amostras: {gerador: sub03_nfcore, planilha, matriz,
                                              esperado: {total, por_tecido, por_raca}}}

Por entrada (ESPEC v2 §4 D2): o modelo Subprojeto + `validar_contra_especie`; nenhum id ou
alias (casefold) colide com o que já carrega nem com outra entrada; todo `dados_legados` existe;
todo `canarios_ausentes` NÃO existe; `excluir_do_indice`/`invalidos_conhecidos`/`nunca_usar`/
`painel_fonte` existem; e, das espécies do repositório, `recursos`/`nunca_usar`/`nao_referencia`
existem. Tudo por stat — o único conteúdo lido é a planilha do gerador e a 1ª linha da matriz.

Endurecimento (crítica v2; cada regra com canário em tests/test_importador.py):
  - bloqueante 2: o catálogo é mapeado EXPLICITAMENTE para o modelo (`mapear_proposta`): `razao`
    vira `validade` só em invalido/arquivado (senão `importador.razao`), `relacoes` dict vira
    ["depende_de:<id>", "refazer_com:<id>"], as demais chaves vão para `importador.*`, e chave
    desconhecida é erro (nada some em silêncio). Título e pergunta NUNCA são escritos por agente:
    vêm do texto do autor (com a fonte em `importador.fonte_*`) ou ficam MARCADOR_AUTOR, e uma
    entrada com o marcador tem de estar em `draft` (uma inválida não vira draft: draft executa);
  - médio 9: `--json` tem a MESMA recusa do `--saida` (dentro do repositório, de GENOME_DATA ou
    de OMICA_DATA → exit 2, antes de qualquer E/S);
  - médio 12: legado com rótulo de raça lido do NOME da amostra (gerador sub03_nfcore) só fica
    `valido` com `proveniencia_rotulos` e um aviso declarados.

Nada é gravado sem `--saida`; com ele, só `<saida>/<esp>/<slug>/{subprojeto.yaml, amostras.tsv}`
e só se não houver falha. Não existe `--gravar` nesta etapa.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import secrets
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, Mapping, Optional, Sequence

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from omica.chaves_contexto import ID_DEMO, ID_SUBPROJETO
from omica.config import REPO_ROOT, get_config
from omica.especies import (Especie, caminho_declarado_valido, carregar_especies, conferir_parametros,
                            estado, no_disco)
from omica.subprojetos import Registro, Subprojeto, carregar_subprojetos, validar_contra_especie

if TYPE_CHECKING:
    from omica.registry import Registry

MENSAGEM_SO_DRY_RUN = "nesta etapa o importador só roda em --dry-run"
# título/pergunta que o autor ainda não escreveu: um agente não inventa pergunta de artigo
MARCADOR_AUTOR = "A DEFINIR PELO AUTOR"
STATUS = ("valido", "draft", "a_revisar", "invalido", "arquivado")
# relação entre subprojetos: "<tipo>:<especie>/<slug>"
TIPOS_RELACAO = ("depende_de", "refazer_com")
_RELACAO = re.compile(r"(?P<tipo>[a-z_]+):(?P<alvo>[a-z][a-z0-9_]{2,31}/[a-z][a-z0-9_]{2,47})", re.ASCII)
# todo modelo do catálogo recusa chave desconhecida (nada some em silêncio)
_FORBID = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------- modelos

class Esperado(BaseModel):
    """Contagens que o gerador TEM de reproduzir (conferidas no disco no levantamento)."""
    model_config = _FORBID
    total: int = Field(ge=1)
    por_tecido: dict[str, int]
    por_raca: dict[str, int]


class AmostrasGerador(BaseModel):
    model_config = _FORBID
    gerador: Literal["sub03_nfcore"]
    planilha: str
    matriz: str
    esperado: Esperado

    @field_validator("planilha", "matriz")
    @classmethod
    def _declarado(cls, v: str) -> str:
        if not caminho_declarado_valido(v):
            raise ValueError(f"caminho fora do padrão (/data/..., sem '..'): {v!r}")
        return v


class Importador(BaseModel):
    """O que serve só ao importador (não vai para o subprojeto.yaml)."""
    model_config = _FORBID
    razao: str = ""                                           # por que o status (fora de invalido/arquivado)
    canarios_ausentes: list[str] = Field(default_factory=list)  # citados no legado e que NÃO podem existir
    host_somente: list[str] = Field(default_factory=list)       # proveniência no host (o container não vê)
    excluir_do_indice: list[str] = Field(default_factory=list)  # existem, mas ficam fora do índice
    invalidos_conhecidos: list[str] = Field(default_factory=list)  # existem e são sabidamente inválidos
    nunca_usar: list[str] = Field(default_factory=list)         # existem e nada pode lê-los
    painel_fonte: str = ""                                    # /data/... do painel (a pasta só aceita nome simples)
    status_proposto: Literal["", "valido", "draft", "a_revisar", "invalido", "arquivado"] = ""
    fonte_titulo: str = ""
    fonte_pergunta: str = ""
    fonte_assembly: str = ""
    amostras: Optional[AmostrasGerador] = None

    @field_validator("canarios_ausentes", "excluir_do_indice", "invalidos_conhecidos", "nunca_usar")
    @classmethod
    def _declarados(cls, v: list[str]) -> list[str]:
        ruins = [c for c in v if not caminho_declarado_valido(c)]
        if ruins:
            raise ValueError(f"caminho fora do padrão (/data/..., sem '..'): {ruins!r}")
        return v

    @field_validator("painel_fonte")
    @classmethod
    def _painel(cls, v: str) -> str:
        if v and not caminho_declarado_valido(v):
            raise ValueError(f"painel_fonte fora do padrão (/data/..., sem '..'): {v!r}")
        return v

    @field_validator("host_somente")
    @classmethod
    def _host(cls, v: list[str]) -> list[str]:
        ruins = [c for c in v if not c.strip() or re.search(r"[\x00-\x1f\x7f]", c)]
        if ruins:
            raise ValueError(f"host_somente vazio ou com caractere de controle: {ruins!r}")
        return v


class EntradaCatalogo(BaseModel):
    model_config = _FORBID
    subprojeto: dict[str, Any]
    importador: Importador = Field(default_factory=Importador)


# -------------------------------------------------------- mapeamento da proposta (bloqueante 2)

# chaves do formato de PROPOSTA (subprojetos_propostos.yaml do levantamento) que não são campo do
# modelo e vão para `importador.*`
_PROPOSTA_PARA_IMPORTADOR = {
    "host_somente": "host_somente",
    "excluir_do_indice": "excluir_do_indice",
    "invalidos_conhecidos": "invalidos_conhecidos",
    "nunca_usar": "nunca_usar",
    "canario_inexistente": "canarios_ausentes",
}
# vêm do AUTOR (argumentos), nunca da proposta
_DO_AUTOR = ("titulo", "pergunta", "assembly", "especie")


def mapear_proposta(proposta: Mapping[str, Any], *, origem_legado: str, titulo: str, pergunta: str,
                    assembly: str, fonte_titulo: str = "", fonte_pergunta: str = "",
                    fonte_assembly: str = "", amostras: Optional[Mapping[str, Any]] = None
                    ) -> dict[str, Any]:
    """Uma entrada no formato de PROPOSTA → uma entrada do catálogo ({subprojeto, importador}).

    Mapeamento explícito (crítica v2, bloqueante 2):
      - `razao` → `validade` se o status é invalido/arquivado; senão → `importador.razao`;
      - `relacoes` dict {tipo: id | [ids]} → ["<tipo>:<id>", ...];
      - `painel` em /data/... → `importador.painel_fonte` (a pasta do subprojeto só aceita nome simples);
      - `host_somente`/`excluir_do_indice`/`invalidos_conhecidos`/`nunca_usar` → `importador.*`;
        `canario_inexistente` → `importador.canarios_ausentes`;
      - cada alias de `legado` ganha o prefixo da plataforma de origem ("genomes:sub01" ×
        "epigenoma:SUB01": com casefold os nus colidem);
      - título e pergunta vêm dos ARGUMENTOS (texto do autor, ou MARCADOR_AUTOR). Com o marcador
        o status vira `draft` e o proposto fica em `importador.status_proposto`; uma proposta
        invalido/arquivado com o marcador é recusada (draft é executável).
    Chave desconhecida é ValueError: nada da proposta some em silêncio."""
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,31}", origem_legado or "", re.ASCII):
        raise ValueError(f"origem_legado fora do padrão: {origem_legado!r}")
    campos = set(Subprojeto.model_fields)
    conhecidas = (campos - set(_DO_AUTOR)) | set(_PROPOSTA_PARA_IMPORTADOR) | {"razao"}
    desconhecidas = sorted(set(proposta) - conhecidas)
    if desconhecidas:
        raise ValueError(f"chaves sem mapeamento na proposta {proposta.get('id')!r}: {desconhecidas}")
    id_sub = proposta.get("id")
    m = ID_SUBPROJETO.fullmatch(id_sub) if isinstance(id_sub, str) else None
    if m is None:
        raise ValueError(f"id fora do padrão <especie>/<slug>: {id_sub!r}")
    status = proposta.get("status")
    if status not in STATUS:
        raise ValueError(f"{id_sub}: status {status!r} fora de {STATUS}")

    imp: dict[str, Any] = {}
    sub: dict[str, Any] = {"id": id_sub, "especie": m["esp"], "assembly": assembly,
                           "titulo": titulo, "pergunta": pergunta}
    for chave in Subprojeto.model_fields:
        if chave in proposta and chave not in sub and chave not in ("relacoes", "painel", "legado"):
            sub[chave] = proposta[chave]

    pendente = MARCADOR_AUTOR in titulo or MARCADOR_AUTOR in pergunta
    if pendente:
        if status in ("invalido", "arquivado"):
            raise ValueError(f"{id_sub}: proposta {status} precisa do texto do autor — com o marcador "
                             f"ela viraria draft, e draft é executável")
        if status != "draft":
            imp["status_proposto"] = status
        sub["status"] = "draft"

    razao = proposta.get("razao", "")
    if razao:
        if sub["status"] in ("invalido", "arquivado"):
            if sub.get("validade"):
                raise ValueError(f"{id_sub}: `razao` e `validade` juntas — qual vale?")
            sub["validade"] = razao
        else:
            imp["razao"] = razao

    if "legado" in proposta:
        sub["legado"] = [f"{origem_legado}:{a}" for a in proposta["legado"]]

    relacoes = proposta.get("relacoes")
    if isinstance(relacoes, Mapping):
        lista: list[str] = []
        for tipo, alvos in relacoes.items():
            for alvo in ([alvos] if isinstance(alvos, str) else list(alvos)):
                lista.append(f"{tipo}:{alvo}")
        sub["relacoes"] = lista
    elif relacoes is not None:
        sub["relacoes"] = list(relacoes)

    painel = proposta.get("painel", "")
    if painel:
        if isinstance(painel, str) and painel.startswith("/data/"):
            imp["painel_fonte"] = painel
        else:
            sub["painel"] = painel

    for de, para in _PROPOSTA_PARA_IMPORTADOR.items():
        if de in proposta:
            imp[para] = list(proposta[de])
    for chave, valor in (("fonte_titulo", fonte_titulo), ("fonte_pergunta", fonte_pergunta),
                         ("fonte_assembly", fonte_assembly)):
        if valor:
            imp[chave] = valor
    if amostras is not None:
        imp["amostras"] = dict(amostras)

    ordem = list(Subprojeto.model_fields)
    sub = {k: sub[k] for k in ordem if k in sub}
    entrada = {"subprojeto": sub, "importador": imp}
    # a entrada mapeada TEM de passar nos dois modelos (o resto é conferência de disco)
    EntradaCatalogo.model_validate(entrada)
    Subprojeto.model_validate(sub)
    return entrada


# ------------------------------------------------------------------ gerador sub03_nfcore

# prefixo do nome da amostra (tudo antes de "_SRR…") → campos. TABELA FECHADA de 13 prefixos
# (levantamento SUBPROJETOS §3): o nome NÃO é quebrado em "_", porque "Holstein_F",
# "Kedah_Kelantan", "whole_blood" e "heat_stress_d7" têm "_" no meio. Prefixo fora daqui = falha.
# (raca, raca_rotulo_original, tecido, condicao, condicao_rotulo_original, tempo, estacao,
#  contraste_legado — deseq2_sub03_heat_stress.R:31-43)
PREFIXOS_SUB03: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "Holstein_hypothalamus_heat_stress": ("Holstein", "Holstein", "hipotalamo", "calor", "heat_stress", "NA", "NA", "hypothalamus"),
    "Holstein_hypothalamus_control": ("Holstein", "Holstein", "hipotalamo", "controle", "control", "NA", "NA", "hypothalamus"),
    "Holstein_pituitary_heat_stress": ("Holstein", "Holstein", "hipofise", "calor", "heat_stress", "NA", "NA", "pituitary"),
    "Holstein_pituitary_control": ("Holstein", "Holstein", "hipofise", "controle", "control", "NA", "NA", "pituitary"),
    "Angus_granulosa_summer_heat": ("Angus", "Angus", "granulosa", "calor", "summer_heat", "NA", "verao", "granulosa"),
    "Angus_granulosa_winter_control": ("Angus", "Angus", "granulosa", "controle", "winter_control", "NA", "inverno", "granulosa"),
    "Crossbred_PBMC_baseline_d0": ("Crossbred", "Crossbred", "PBMC", "controle", "baseline_d0", "d0", "NA", "PBMC"),
    "Crossbred_PBMC_heat_stress_d7": ("Crossbred", "Crossbred", "PBMC", "calor", "heat_stress_d7", "d7", "NA", "PBMC"),
    "Tharparkar_PBMC_baseline_d0": ("Tharparkar", "Tharparkar", "PBMC", "controle", "baseline_d0", "d0", "NA", "PBMC"),
    "Tharparkar_PBMC_heat_stress_d7": ("Tharparkar", "Tharparkar", "PBMC", "calor", "heat_stress_d7", "d7", "NA", "PBMC"),
    "Brangus_whole_blood_tropical_heat": ("Brangus", "Brangus", "sangue_total", "calor", "tropical_heat", "NA", "NA", "NA"),
    # decisão D-7 (pendente: conferir no BioSample o que é o "_F")
    "Holstein_F_whole_blood_tropical_heat": ("Holstein", "Holstein_F", "sangue_total", "calor", "tropical_heat", "NA", "NA", "NA"),
    "Kedah_Kelantan_whole_blood_tropical_heat": ("Kedah-Kelantan", "Kedah_Kelantan", "sangue_total", "calor", "tropical_heat", "NA", "NA", "NA"),
})
# pasta do FASTQ (dado da planilha, não do nome) → dataset
DATASETS_SUB03: Mapping[str, str] = MappingProxyType({
    "PRJNA885909_hypothalamus": "PRJNA885909",
    "PRJNA885909_pituitary": "PRJNA885909",
    "GSE235171_granulosa": "GSE235171",
    "PRJNA562925_PBMC": "PRJNA562925",
    "PRJNA1424279_whole_blood": "PRJNA1424279",
})
COLUNAS_SUB03 = ("amostra", "srr", "raca", "raca_rotulo_original", "tecido", "condicao",
                 "condicao_rotulo_original", "tempo", "estacao", "dataset", "layout",
                 "contraste_legado", "fastq_1", "fastq_2", "quant_sf")
# geradores que tiram a RAÇA do nome da amostra (crítica v2, médio 12)
GERADORES_RACA_DO_NOME = frozenset({"sub03_nfcore"})
_NOME_SUB03 = re.compile(r"(?P<prefixo>[A-Za-z][A-Za-z0-9_]*)_(?P<srr>SRR[0-9]+)", re.ASCII)
PLANILHA_MAX_BYTES = 1024 * 1024
CABECALHO_MAX_BYTES = 1024 * 1024


def para_container(caminho: str) -> Optional[str]:
    """Caminho da planilha (gravada no host: "/e/data/...", "E:\\data\\...", "E:/data/...") →
    "/data/..." (docker-compose: E:\\data = /data). None se não for de /data."""
    p = (caminho or "").replace("\\", "/")
    for padrao in (r"/data/(?P<resto>.+)", r"/[A-Za-z]/data/(?P<resto>.+)", r"[A-Za-z]:/data/(?P<resto>.+)"):
        m = re.fullmatch(padrao, p)
        if m:
            p = "/data/" + m["resto"]
            return p if caminho_declarado_valido(p) else None
    return None


def _existe(p: Path) -> bool:
    try:
        return p.exists()   # só stat
    except OSError:
        return False


def _pasta_pai(declarado: str) -> str:
    return declarado.rsplit("/", 1)[0]


def gerar_amostras_sub03(planilha: str, matriz: str) -> tuple[list[dict[str, str]], list[str]]:
    """(linhas do amostras.tsv, falhas). Lê a planilha nf-core e SÓ a 1ª linha da matriz merged.

    Falha: prefixo fora da tabela, pasta de FASTQ sem dataset, caminho fora de /data, FASTQ ou
    quant.sf ausente, amostra/SRR repetido, ou nomes da planilha ≠ colunas da matriz."""
    falhas: list[str] = []
    arq_planilha, arq_matriz = no_disco(planilha), no_disco(matriz)
    try:
        if arq_planilha.stat().st_size > PLANILHA_MAX_BYTES:
            return [], [f"planilha maior que {PLANILHA_MAX_BYTES} bytes: {planilha}"]
        texto = arq_planilha.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        return [], [f"planilha ilegível ({planilha}): {e}"]
    leitor = csv.DictReader(io.StringIO(texto, newline=""))
    faltam = {"sample", "fastq_1", "fastq_2"} - set(leitor.fieldnames or ())
    if faltam:
        return [], [f"planilha sem as colunas {sorted(faltam)}: {planilha}"]
    pasta_salmon = _pasta_pai(matriz)
    linhas: list[dict[str, str]] = []
    for r in leitor:
        nome = (r.get("sample") or "").strip()
        m = _NOME_SUB03.fullmatch(nome)
        if not m or m["prefixo"] not in PREFIXOS_SUB03:
            falhas.append(f"prefixo fora da tabela fechada: {nome!r}")
            continue
        raca, raca_orig, tecido, cond, cond_orig, tempo, estacao, contraste = PREFIXOS_SUB03[m["prefixo"]]
        fastqs = []
        for col in ("fastq_1", "fastq_2"):
            bruto = (r.get(col) or "").strip()
            if not bruto:
                fastqs.append("")
                continue
            dec = para_container(bruto)
            if dec is None:
                falhas.append(f"{nome}: {col} fora de /data: {bruto!r}")
                fastqs.append("")
                continue
            if not _existe(no_disco(dec)):
                falhas.append(f"{nome}: {col} ausente: {dec}")
            fastqs.append(dec)
        if not fastqs[0]:
            falhas.append(f"{nome}: sem fastq_1")
            continue
        pasta = _pasta_pai(fastqs[0]).rsplit("/", 1)[-1]
        if pasta not in DATASETS_SUB03:
            falhas.append(f"{nome}: pasta de FASTQ sem dataset na tabela: {pasta!r}")
            continue
        quant = f"{pasta_salmon}/{nome}/quant.sf"
        if not _existe(no_disco(quant)):
            falhas.append(f"{nome}: quant.sf ausente: {quant}")
        linhas.append({
            "amostra": nome, "srr": m["srr"], "raca": raca, "raca_rotulo_original": raca_orig,
            "tecido": tecido, "condicao": cond, "condicao_rotulo_original": cond_orig,
            "tempo": tempo, "estacao": estacao, "dataset": DATASETS_SUB03[pasta],
            "layout": "PE" if fastqs[1] else "SE", "contraste_legado": contraste,
            "fastq_1": fastqs[0], "fastq_2": fastqs[1], "quant_sf": quant,
        })
    nomes = [x["amostra"] for x in linhas]
    if len(set(nomes)) != len(nomes):
        falhas.append("amostra repetida na planilha")
    if len({x["srr"] for x in linhas}) != len(linhas):
        falhas.append("SRR repetido na planilha")
    try:
        with arq_matriz.open("r", encoding="utf-8") as f:
            cabecalho = f.readline(CABECALHO_MAX_BYTES)
    except (OSError, UnicodeDecodeError) as e:
        return linhas, falhas + [f"matriz ilegível ({matriz}): {e}"]
    colunas = cabecalho.rstrip("\r\n").split("\t")[2:]   # gene_id, gene_name, <amostras>
    lidos = [(r.get("sample") or "").strip() for r in csv.DictReader(io.StringIO(texto, newline=""))]
    if sorted(colunas) != sorted(lidos) or len(set(colunas)) != len(colunas):
        so_planilha = sorted(set(lidos) - set(colunas))
        so_matriz = sorted(set(colunas) - set(lidos))
        falhas.append(f"nomes da planilha ({len(lidos)}) ≠ colunas da matriz ({len(colunas)}): "
                      f"só na planilha {so_planilha[:5]}, só na matriz {so_matriz[:5]}")
    return linhas, falhas


def contagens(linhas: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    return {"total": len(linhas),
            "por_tecido": dict(sorted(Counter(x["tecido"] for x in linhas).items())),
            "por_raca": dict(sorted(Counter(x["raca"] for x in linhas).items()))}


def conferir_contagens(obtido: Mapping[str, Any], esperado: Esperado) -> list[str]:
    falhas = []
    if obtido["total"] != esperado.total:
        falhas.append(f"total de amostras {obtido['total']} ≠ esperado {esperado.total}")
    if dict(obtido["por_tecido"]) != dict(esperado.por_tecido):
        falhas.append(f"por tecido {dict(obtido['por_tecido'])} ≠ esperado {dict(esperado.por_tecido)}")
    if dict(obtido["por_raca"]) != dict(esperado.por_raca):
        falhas.append(f"por raça {dict(obtido['por_raca'])} ≠ esperado {dict(esperado.por_raca)}")
    return falhas


def amostras_tsv(linhas: Sequence[Mapping[str, str]]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(COLUNAS_SUB03), delimiter="\t", lineterminator="\n")
    w.writeheader()
    w.writerows(linhas)
    return buf.getvalue()


# ---------------------------------------------------------------------- conferência

@dataclass
class RelatorioEntrada:
    rotulo: str
    id: str = ""
    status: str = ""
    tipo: str = ""
    privado: Optional[bool] = None
    falhas: list[str] = field(default_factory=list)
    pendencias: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    caminhos: int = 0
    amostras: Optional[dict[str, Any]] = None
    # só para a gravação (nunca vão ao JSON: trazem a pergunta de artigo)
    sub: Optional[Subprojeto] = None
    linhas_amostras: Optional[list[dict[str, str]]] = None

    def para_json(self) -> dict[str, Any]:
        return {"rotulo": self.rotulo, "id": self.id, "status": self.status, "tipo": self.tipo,
                "privado": self.privado, "falhas": self.falhas, "pendencias": self.pendencias,
                "avisos": self.avisos, "caminhos_conferidos": self.caminhos, "amostras": self.amostras}


@dataclass
class Relatorio:
    catalogo: str
    entradas: list[RelatorioEntrada] = field(default_factory=list)
    especies: dict[str, dict[str, Any]] = field(default_factory=dict)
    falhas_globais: list[str] = field(default_factory=list)

    @property
    def n_falhas(self) -> int:
        return len(self.falhas_globais) + sum(len(e.falhas) for e in self.entradas)

    @property
    def codigo_saida(self) -> int:
        return 1 if self.n_falhas else 0

    def para_json(self) -> dict[str, Any]:
        return {"catalogo": self.catalogo, "dry_run": True, "exit": self.codigo_saida,
                "falhas": self.n_falhas, "falhas_globais": self.falhas_globais,
                "especies": self.especies, "entradas": [e.para_json() for e in self.entradas]}


def _dentro_declarado(caminho: str, base: str) -> bool:
    """Igual a `base` ou dentro dela, LEXICAMENTE (os dois são caminhos declarados)."""
    return caminho == base or caminho.startswith(base.rstrip("/") + "/")


def conferir_especies(especies: Mapping[str, Especie]) -> dict[str, dict[str, Any]]:
    """Por espécie: `recursos`, `nunca_usar` e `nao_referencia` têm de existir (stat); o estado
    das refs vai junto, só informativo (humano e camundongo são sem_referencia de propósito)."""
    saida: dict[str, dict[str, Any]] = {}
    for e in especies.values():
        falhas, n = [], 0
        for campo, valores in (("recursos", list(e.recursos.values())), ("nunca_usar", e.nunca_usar),
                               ("nao_referencia", e.nao_referencia)):
            for v in valores:
                n += 1
                if not _existe(no_disco(v)):
                    falhas.append(f"espécie {e.id}: {campo} ausente: {v}")
        est = estado(e)
        saida[e.id] = {"estado": est.estado, "refs_presentes": est.presentes, "refs_total": est.total,
                       "caminhos_conferidos": n, "falhas": falhas}
    return saida


def _carregar_entradas(bruto: Any) -> list[Any]:
    if not isinstance(bruto, list):
        raise ValueError("o catálogo tem de ser uma LISTA de {subprojeto, importador}")
    if not bruto:
        raise ValueError("catálogo vazio")
    return bruto


def conferir_catalogo(bruto: Any, catalogo: str = "", *, especies: Optional[dict[str, Especie]] = None,
                      registros: Optional[dict[str, Registro]] = None,
                      reg: Optional["Registry"] = None) -> Relatorio:
    """Confere o catálogo inteiro SEM gravar nada. `especies`/`registros` default: os do
    repositório + <dados> (carregar_especies / carregar_subprojetos)."""
    rel = Relatorio(catalogo=catalogo)
    if especies is None:
        especies, erros_esp = carregar_especies(reg=reg)
        rel.falhas_globais += [f"espécie {arq}: {msg}" for arq, msg in sorted(erros_esp.items())]
    if registros is None:
        registros, _ = carregar_subprojetos(especies, reg=reg)
    rel.especies = conferir_especies(especies)
    for info in rel.especies.values():
        rel.falhas_globais += info["falhas"]
    try:
        entradas = _carregar_entradas(bruto)
    except ValueError as e:
        rel.falhas_globais.append(str(e))
        return rel

    # ids e aliases (casefold) num só espaço: os já carregados, depois as entradas na ordem
    dono: dict[str, str] = {}
    for r in registros.values():
        for c in (r.sub.id, *r.sub.legado):
            dono.setdefault(c.casefold(), f"{r.sub.id} ({r.origem})")
    ids_catalogo: set[str] = set()
    validos: list[tuple[RelatorioEntrada, Subprojeto, Importador]] = []
    pipelines_cache: list[Any] = []

    for i, item in enumerate(entradas, start=1):
        er = RelatorioEntrada(rotulo=f"entrada #{i}")
        rel.entradas.append(er)
        try:
            ent = EntradaCatalogo.model_validate(item)
        except ValidationError as e:
            er.falhas.append(f"formato da entrada: {_erro_curto(e)}")
            continue
        bruto_sub = ent.subprojeto
        if isinstance(bruto_sub.get("id"), str):
            er.id = er.rotulo = bruto_sub["id"]
        try:
            sub = Subprojeto.model_validate(bruto_sub)
        except ValidationError as e:
            er.falhas.append(f"subprojeto: {_erro_curto(e)}")
            continue
        er.status, er.tipo, er.privado, er.sub = sub.status, sub.tipo, sub.privado, sub
        imp = ent.importador
        er.falhas += validar_contra_especie(sub, especies)
        if sub.id == ID_DEMO or sub.tipo == "demo":
            er.falhas.append(f"{ID_DEMO} e tipo demo só existem no repositório")
        if sub.tipo == "pesquisa" and not sub.privado:
            er.falhas.append("tipo pesquisa sem privado: true (pergunta de artigo — regra 7)")
        if sub.painel:
            er.falhas.append("painel na pasta não é gravado nesta etapa: declare a origem em "
                             "importador.painel_fonte e deixe `painel` vazio")
        # colisões: com o que já carrega e entre as entradas
        for c in (sub.id, *sub.legado):
            chave = c.casefold()
            if chave in dono:
                er.falhas.append(f"id/alias {c!r} colide com {dono[chave]}")
            else:
                dono[chave] = f"{sub.id} (catálogo)"
        ids_catalogo.add(sub.id)
        # texto do autor (bloqueante 2)
        pendentes = [c for c in ("titulo", "pergunta") if MARCADOR_AUTOR in getattr(sub, c)]
        if pendentes:
            er.pendencias.append(f"{' e '.join(pendentes)} a escrever pelo autor")
            if sub.status != "draft":
                er.falhas.append(f"{' e '.join(pendentes)} com {MARCADOR_AUTOR!r} exige status draft "
                                 f"(está {sub.status})")
        if imp.status_proposto and imp.status_proposto != sub.status:
            er.pendencias.append(f"status proposto {imp.status_proposto}; fica {sub.status} até o autor decidir")
        # caminhos (só stat)
        for p in sub.dados_legados:
            er.caminhos += 1
            if not _existe(no_disco(p)):
                er.falhas.append(f"dados_legados ausente: {p}")
        for nome, p in sub.datasets.items():
            er.caminhos += 1
            if not _existe(no_disco(p)):
                er.falhas.append(f"datasets.{nome} ausente: {p}")
        for campo in ("excluir_do_indice", "invalidos_conhecidos", "nunca_usar"):
            for p in getattr(imp, campo):
                er.caminhos += 1
                if not _existe(no_disco(p)):
                    er.falhas.append(f"{campo} ausente: {p}")
        if imp.painel_fonte:
            er.caminhos += 1
            if not _existe(no_disco(imp.painel_fonte)):
                er.falhas.append(f"painel_fonte ausente: {imp.painel_fonte}")
        for p in imp.canarios_ausentes:
            er.caminhos += 1
            if _existe(no_disco(p)):
                er.falhas.append(f"canário EXISTE (devia estar ausente): {p}")
        for p in imp.host_somente:
            if not _existe(Path(p)):
                er.avisos.append(f"host_somente ausente neste host (informativo): {p}")
        # nada declarado pode morar num caminho proibido (da espécie ou da própria entrada)
        proibidos = [*imp.nunca_usar, *imp.invalidos_conhecidos, *imp.excluir_do_indice]
        if sub.especie in especies:
            proibidos += especies[sub.especie].nunca_usar
        usados = [*sub.dados_legados, *sub.datasets.values()]
        if imp.painel_fonte:
            usados.append(imp.painel_fonte)
        if imp.amostras is not None:
            usados += [imp.amostras.planilha, imp.amostras.matriz]
        for u in usados:
            for p in proibidos:
                if _dentro_declarado(u, p):
                    er.falhas.append(f"{u} está em caminho proibido (nunca_usar/invalidos/excluido): {p}")
        # parâmetros e pipelines (o loader não confere pipelines)
        if sub.parametros:
            if reg is None:
                from omica.registry import load_registry

                reg = load_registry()
            modulos = sub.modulos or (especies[sub.especie].modulos if sub.especie in especies else [])
            er.falhas += conferir_parametros(sub.parametros, modulos, reg)
        if sub.pipelines:
            if not pipelines_cache:
                from omica.pipelines import carregar_pipelines

                pipelines_cache.append(carregar_pipelines()[0])
            todos = pipelines_cache[0]
            for pid in sub.pipelines:
                p = todos.get(pid)
                if p is None:
                    er.falhas.append(f"pipeline inexistente: {pid}")
                    continue
                if sub.especie not in p.especies:
                    er.falhas.append(f"pipeline {pid} não declara a espécie {sub.especie}")
                if sub.modulos and p.modulo not in sub.modulos:
                    er.falhas.append(f"pipeline {pid} é do módulo {p.modulo}, fora de {sub.modulos}")
        # amostras geradas
        if imp.amostras is not None:
            linhas, falhas = gerar_amostras_sub03(imp.amostras.planilha, imp.amostras.matriz)
            er.falhas += falhas
            er.caminhos += 2
            cont = contagens(linhas)
            er.amostras = cont
            er.falhas += conferir_contagens(cont, imp.amostras.esperado)
            er.linhas_amostras = linhas
            if (imp.amostras.gerador in GERADORES_RACA_DO_NOME and sub.status == "valido"
                    and (not sub.proveniencia_rotulos.strip() or not sub.avisos)):
                # crítica v2, médio 12: o projeto já teve rótulo de raça errado no 1000BG
                er.falhas.append("legado frágil marcado valido: a raça sai do NOME da amostra; use "
                                 "a_revisar, ou declare proveniencia_rotulos e um aviso")
        validos.append((er, sub, imp))

    # alvos de relações e de substituido_por: no catálogo ou já carregados
    conhecidos = set(registros) | ids_catalogo
    for er, sub, _ in validos:
        if sub.substituido_por and sub.substituido_por not in conhecidos:
            er.falhas.append(f"substituido_por aponta para id inexistente: {sub.substituido_por}")
        for r in sub.relacoes:
            m = _RELACAO.fullmatch(r)
            if not m or m["tipo"] not in TIPOS_RELACAO:
                er.falhas.append(f"relação fora do padrão <{'|'.join(TIPOS_RELACAO)}>:<especie>/<slug>: {r!r}")
            elif m["alvo"] not in conhecidos:
                er.falhas.append(f"relação aponta para id inexistente: {r}")
            elif m["alvo"] == sub.id:
                er.falhas.append(f"relação consigo mesmo: {r}")
    return rel


def _erro_curto(e: ValidationError) -> str:
    partes = []
    for err in e.errors():
        onde = ".".join(str(x) for x in err.get("loc", ()))
        partes.append(f"{onde}: {err.get('msg', '')}" if onde else str(err.get("msg", "")))
    return "; ".join(partes)


# ---------------------------------------------------------------------- destinos (I7)

def raizes_proibidas() -> list[tuple[str, Path]]:
    """Onde o importador NUNCA grava (relatório nem subprojeto): o repositório e os dados."""
    cfg = get_config()
    return [("o repositório", REPO_ROOT), ("GENOME_DATA", cfg.genome_data), ("OMICA_DATA", cfg.data_dir)]


def _resolver(p: Path) -> Path:
    try:
        return p.resolve(strict=False)
    except (OSError, RuntimeError):
        return p.absolute()


def destino_proibido(caminho: Path) -> Optional[str]:
    """Descrição da raiz proibida que contém `caminho` (resolvido: link simbólico não fura), ou None."""
    alvo = _resolver(caminho)
    for nome, raiz in raizes_proibidas():
        r = _resolver(raiz)
        if alvo == r or alvo.is_relative_to(r):
            return f"{nome} ({raiz})"
    return None


def _gravar_atomico(destino: Path, texto: str) -> None:
    tmp = destino.with_name(f".{destino.name}.{secrets.token_hex(3)}.tmp")
    try:
        tmp.write_text(texto, encoding="utf-8", newline="\n")
        os.replace(tmp, destino)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _yaml_subprojeto(sub: Subprojeto, catalogo: str) -> str:
    cabecalho = ("# gerado por `python -m omica importar-subprojetos --dry-run` (NÃO importado: a gravação\n"
                 f"# em <dados>/projetos fica para uma etapa futura). Catálogo: {Path(catalogo).name}\n")
    dados = sub.model_dump(mode="json", exclude_defaults=True)
    dados["privado"] = sub.privado
    return cabecalho + yaml.safe_dump(dados, allow_unicode=True, sort_keys=False, width=100)


def gravar_saida(rel: Relatorio, saida: Path) -> list[Path]:
    """Grava <saida>/<esp>/<slug>/{subprojeto.yaml, amostras.tsv} de cada entrada e revalida o
    YAML gravado pelo modelo. Recusa (ValueError) se QUALQUER destino resolver numa raiz proibida."""
    planos: list[tuple[Path, str, Subprojeto]] = []
    for er in rel.entradas:
        if er.sub is None:
            raise ValueError(f"entrada sem subprojeto válido: {er.rotulo}")
        pasta = saida / er.sub.especie / er.sub.slug
        planos.append((pasta / "subprojeto.yaml", _yaml_subprojeto(er.sub, rel.catalogo), er.sub))
        if er.linhas_amostras is not None:
            planos.append((pasta / er.sub.amostras, amostras_tsv(er.linhas_amostras), er.sub))
    for destino, _, _ in planos:   # tudo conferido ANTES de criar qualquer pasta
        proibido = destino_proibido(destino)
        if proibido:
            raise PermissionError(f"destino {destino} resolve dentro de {proibido}")
    gravados = []
    for destino, texto, sub in planos:
        destino.parent.mkdir(parents=True, exist_ok=True)
        if destino_proibido(destino):   # uma pasta-link criada no meio do caminho
            raise PermissionError(f"destino {destino} resolve dentro de uma raiz proibida")
        _gravar_atomico(destino, texto)
        if destino.name == "subprojeto.yaml":
            relido = Subprojeto.model_validate(yaml.safe_load(destino.read_text(encoding="utf-8")))
            if relido != sub:
                raise RuntimeError(f"o YAML gravado não reproduz o subprojeto: {destino}")
        gravados.append(destino)
    return gravados


# ---------------------------------------------------------------------------- CLI

def _stdout_tolerante() -> None:
    """Título com "→" ou "×" num console cp1252 não derruba o relatório (cf. selos, crítica v2 bl. 3)."""
    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(errors="backslashreplace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def imprimir(rel: Relatorio) -> None:
    print(f"importar-subprojetos --dry-run — catálogo: {rel.catalogo}")
    for esp, info in rel.especies.items():
        print(f"  espécie {esp}: refs {info['estado']} ({info['refs_presentes']}/{info['refs_total']}); "
              f"recursos/nunca_usar/nao_referencia: {info['caminhos_conferidos'] - len(info['falhas'])}"
              f"/{info['caminhos_conferidos']} presentes")
    for f in rel.falhas_globais:
        print(f"  [FALHA] {f}")
    for er in rel.entradas:
        marca = "FALHA" if er.falhas else ("pendente" if er.pendencias else "ok")
        extra = ""
        if er.amostras:
            extra = (f"; amostras {er.amostras['total']}: "
                     + ", ".join(f"{t} {n}" for t, n in er.amostras["por_tecido"].items()))
        print(f"  [{marca}] {er.rotulo} ({er.status or '?'}, {er.caminhos} caminhos{extra})")
        for f in er.falhas:
            print(f"      falha: {f}")
        for p in er.pendencias:
            print(f"      pendência do autor: {p}")
        for a in er.avisos:
            print(f"      aviso: {a}")
    n_pend = sum(len(e.pendencias) for e in rel.entradas)
    print(f"resumo: {len(rel.entradas)} entradas, {rel.n_falhas} falhas, {n_pend} pendências do autor "
          f"-> exit {rel.codigo_saida}")


def main(*, dry_run: bool, catalogo: str, saida: Optional[str] = None,
         json_saida: Optional[str] = None) -> int:
    """0 sem falhas; 1 com qualquer falha; 2 recusa de uso (sem --dry-run, destino proibido,
    catálogo ausente ou dentro do repositório) — a recusa vem ANTES de qualquer E/S."""
    _stdout_tolerante()
    if not dry_run:
        print(MENSAGEM_SO_DRY_RUN, file=sys.stderr)
        return 2
    for opcao, valor in (("--saida", saida), ("--json", json_saida)):
        if valor:
            proibido = destino_proibido(Path(valor))
            if proibido:
                # crítica v2, médio 9: --json tem a mesma recusa do --saida (I7)
                print(f"recusado: {opcao} {valor} fica dentro de {proibido}; o importador não grava "
                      f"no repositório nem nos dados", file=sys.stderr)
                return 2
    arq = Path(catalogo)
    if not arq.is_file():
        print(f"recusado: catálogo inexistente: {catalogo}", file=sys.stderr)
        return 2
    alvo = _resolver(arq)
    if alvo.is_relative_to(_resolver(REPO_ROOT)):
        print("recusado: o catálogo traz perguntas de artigo e não pode viver no repositório (regra 7)",
              file=sys.stderr)
        return 2
    try:
        rel = conferir_catalogo(yaml.safe_load(arq.read_text(encoding="utf-8")), str(catalogo))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
        rel = Relatorio(catalogo=str(catalogo), falhas_globais=[f"catálogo ilegível: {e}"])
    imprimir(rel)
    codigo = rel.codigo_saida
    gravacao: dict[str, Any] = {"pedida": bool(saida), "arquivos": []}
    if saida:
        if codigo != 0:
            gravacao["motivo"] = "há falhas: nada gravado"
            print("--saida: nada gravado (há falhas)")
        else:
            try:
                gravados = gravar_saida(rel, Path(saida))
            except PermissionError as e:
                print(f"recusado: {e}", file=sys.stderr)
                gravacao["motivo"] = f"recusado: {e}"
                codigo = 2
            else:
                gravacao["arquivos"] = [str(g) for g in gravados]
                print(f"--saida: {len(gravados)} arquivos gravados em {saida} (revalidados pelo modelo)")
    if json_saida:
        destino = Path(json_saida)
        destino.parent.mkdir(parents=True, exist_ok=True)
        corpo = {**rel.para_json(), "exit": codigo, "saida": gravacao}
        _gravar_atomico(destino, json.dumps(corpo, ensure_ascii=False, indent=2) + "\n")
        print(f"relatório JSON: {destino}")
    return codigo
