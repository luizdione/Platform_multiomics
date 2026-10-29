"""subprojetos.py — subprojetos da v2 multiespécie, de DUAS fontes.

  repo  — projetos/<especie>/<slug>/subprojeto.yaml (curados, versionados; `bos_taurus/demo`
          só existe aqui);
  dados — <OMICA_DATA>/projetos/<especie>/<slug>/ (rascunhos da UI e privados, fora do git).

O id SAI DO CAMINHO e o YAML tem de concordar. O repositório carrega ANTES de <dados>: um id
repetido em <dados> vai para `erros` e o curado continua carregado. `executavel` é calculado
pelo status, nunca declarado (um YAML não consegue se dizer executável).

Endurecimento da crítica v2 (cada item com canário em tests/test_m0_contratos.py):
  - médio 10a: pasta de subprojeto (ou de espécie) que é symlink é recusada, e a pasta, o
    `amostras` e o `painel` resolvidos têm de ficar dentro da raiz/da pasta;
  - médio 10b: `amostras` e `painel` são NOME SIMPLES (fullmatch em Python — o `pattern` do
    pydantic v2 é busca sem âncora e aceitaria "../x");
  - médio 10c: `datasets` e `dados_legados` só com caminho declarado (/data/..., modules/...);
  - médio 11: `criar_rascunho` grava com yaml.safe_dump e revalida pelo modelo antes do
    os.replace (uma pergunta com "\\nstatus: valido" continua rascunho), limita tamanhos,
    recusa caracteres de controle, confere aliases e traduz o os.replace concorrente em
    `id_duplicado`;
  - médio 14: chave de `parametros` que não existe no registry é erro (não default em silêncio);
  - baixo: aliases `legado` comparados com casefold ("sub01" × "SUB01" colidem).
"""
from __future__ import annotations

import os
import re
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from omica.chaves_contexto import ID_DEMO, ID_SUBPROJETO, MODULOS_RESERVADOS, SLUG, SLUG_NU
from omica.config import REPO_ROOT, get_config
from omica.especies import (CHAVE_PARAMETRO, Especie, caminho_declarado_valido, carregar_especies,
                            conferir_parametros)

if TYPE_CHECKING:
    from omica.registry import Registry

# lido NA CHAMADA (raizes), para os testes poderem apontar para um tmp
PROJETOS_REPO = REPO_ROOT / "projetos"
# fora do demo, executável = só por PIPELINE (a regra é do contexto/dispatch, não daqui)
STATUS_EXECUTAVEIS = ("valido", "draft", "a_revisar")

_FORBID = ConfigDict(extra="forbid")
_ARQ_SIMPLES = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-]{0,99}", re.ASCII)
_NOME_DATASET = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-]{0,63}", re.ASCII)
# controle C0 e DEL; a pergunta aceita '\t' e '\n' (bloco `|` do YAML), o título nada
_CONTROLE_TITULO = re.compile(r"[\x00-\x1f\x7f]")
_CONTROLE_PERGUNTA = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
TITULO_MAX = 200
PERGUNTA_MIN = 10
PERGUNTA_MAX = 2000
ALIAS_MAX = 64
AMOSTRAS_MAX_BYTES = 1024 * 1024


class Contraste(BaseModel):
    model_config = _FORBID
    id: str
    coluna: str
    a: str
    b: str


class Subprojeto(BaseModel):
    model_config = _FORBID
    id: str
    especie: str
    assembly: str
    titulo: str = Field(min_length=1, max_length=TITULO_MAX)
    pergunta: str = Field(min_length=PERGUNTA_MIN, max_length=PERGUNTA_MAX)
    tipo: Literal["demo", "pesquisa", "aula"]
    status: Literal["valido", "draft", "a_revisar", "invalido", "arquivado"]
    validade: str = ""
    substituido_por: str = ""
    proveniencia_rotulos: str = ""
    avisos: list[str] = Field(default_factory=list)
    legado: list[str] = Field(default_factory=list)
    amostras: str = "amostras.tsv"          # nome simples dentro da pasta
    datasets: dict[str, str] = Field(default_factory=dict)
    contrastes: list[Contraste] = Field(default_factory=list)
    painel: str = ""                        # nome simples dentro da pasta, ou vazio
    modulos: list[str] = Field(default_factory=list)
    pipelines: list[str] = Field(default_factory=list)
    parametros: dict[str, str] = Field(default_factory=dict)
    relacoes: list[str] = Field(default_factory=list)
    dados_legados: list[str] = Field(default_factory=list)
    privado: bool = False

    @property
    def slug(self) -> str:
        return self.id.partition("/")[2]

    @property
    def executavel(self) -> bool:
        """Calculado, nunca declarado."""
        return self.status in STATUS_EXECUTAVEIS

    @field_validator("titulo")
    @classmethod
    def _titulo(cls, v: str) -> str:
        if _CONTROLE_TITULO.search(v):
            raise ValueError("titulo com caractere de controle")
        return v

    @field_validator("pergunta")
    @classmethod
    def _pergunta(cls, v: str) -> str:
        if _CONTROLE_PERGUNTA.search(v):
            raise ValueError("pergunta com caractere de controle")
        return v

    @field_validator("amostras")
    @classmethod
    def _amostras(cls, v: str) -> str:
        if not _ARQ_SIMPLES.fullmatch(v):
            raise ValueError(f"amostras deve ser um nome simples de arquivo na pasta do subprojeto: {v!r}")
        return v

    @field_validator("painel")
    @classmethod
    def _painel(cls, v: str) -> str:
        if v and not _ARQ_SIMPLES.fullmatch(v):
            raise ValueError(f"painel deve ser vazio ou um nome simples de arquivo na pasta do subprojeto: {v!r}")
        return v

    @model_validator(mode="after")
    def _coerente(self) -> "Subprojeto":
        m = ID_SUBPROJETO.fullmatch(self.id)
        if not m:
            raise ValueError(f"id fora do padrão <especie>/<slug>: {self.id!r}")
        if m["esp"] != self.especie:
            raise ValueError(f"id {self.id!r} não é da espécie {self.especie!r}")
        if SLUG_NU.fullmatch(m["slug"]):
            raise ValueError(f"slug nu {m['slug']!r} recusado (as numerações sub0X colidem entre plataformas)")
        if self.status in ("invalido", "arquivado") and not self.validade.strip():
            raise ValueError(f"status {self.status!r} exige `validade` (o motivo)")
        if self.substituido_por:
            if self.status not in ("invalido", "arquivado"):
                raise ValueError("substituido_por só com status invalido/arquivado")
            if not ID_SUBPROJETO.fullmatch(self.substituido_por):
                raise ValueError(f"substituido_por fora do padrão <especie>/<slug>: {self.substituido_por!r}")
        for chave, valor in self.parametros.items():
            if not CHAVE_PARAMETRO.fullmatch(chave):
                raise ValueError(f"parametros: chave {chave!r} fora do padrão <modulo>.<script>.<param>")
            if "{" in valor or "}" in valor:
                raise ValueError(f"parametros.{chave}: chave '{{' ou '}}' no valor")
        reservados = sorted(set(self.modulos) & MODULOS_RESERVADOS)
        if reservados:
            raise ValueError(f"modulos com id reservado: {reservados}")
        for a in self.legado:
            if not a.strip() or len(a) > ALIAS_MAX or _CONTROLE_TITULO.search(a):
                raise ValueError(f"alias legado vazio, longo ou com caractere de controle: {a!r}")
        for nome, caminho in self.datasets.items():
            if not _NOME_DATASET.fullmatch(nome):
                raise ValueError(f"datasets: nome fora do padrão {nome!r}")
            if not caminho_declarado_valido(caminho):
                raise ValueError(f"datasets.{nome}: caminho fora do padrão (/data/..., modules/... ou "
                                 f"projetos/..., sem '..'): {caminho!r}")
        ruins = [c for c in self.dados_legados if not caminho_declarado_valido(c)]
        if ruins:
            raise ValueError(f"dados_legados: caminho fora do padrão (/data/..., modules/... ou projetos/..., "
                             f"sem '..'): {ruins!r}")
        return self


@dataclass(frozen=True)
class Registro:
    sub: Subprojeto
    pasta: Path
    origem: Literal["repo", "dados"]


def raizes() -> list[tuple[str, Path]]:
    """As duas fontes, NA ORDEM de carga (o repositório vence)."""
    return [("repo", PROJETOS_REPO), ("dados", get_config().projetos_dir)]


def validar_contra_especie(sub: Subprojeto, especies: dict[str, Especie]) -> list[str]:
    """Problemas do subprojeto contra a espécie (vazio = ok): espécie existe, a assembly é
    dela e os módulos estão entre os da espécie."""
    esp = especies.get(sub.especie)
    if esp is None:
        return [f"espécie inexistente: {sub.especie!r}"]
    problemas = []
    if sub.assembly not in esp.assemblies:
        problemas.append(f"assembly {sub.assembly!r} não é da espécie {sub.especie} "
                         f"(conhecidas: {sorted(esp.assemblies)})")
    fora = sorted(set(sub.modulos) - set(esp.modulos))
    if fora:
        problemas.append(f"módulos fora da espécie {sub.especie}: {fora}")
    return problemas


def _dentro(p: Path, raiz: Path) -> bool:
    try:
        return p.resolve(strict=False).is_relative_to(raiz.resolve(strict=False))
    except (OSError, RuntimeError):
        return False


def _conferir_arquivo_na_pasta(pasta: Path, nome: str, campo: str) -> None:
    """O arquivo, SE existir, é regular (não symlink) e resolve dentro da pasta."""
    alvo = pasta / nome
    if alvo.is_symlink():
        raise ValueError(f"{campo} {nome!r} é link simbólico")
    if alvo.exists() and not _dentro(alvo, pasta):
        raise ValueError(f"{campo} {nome!r} resolve fora da pasta do subprojeto")


def _pastas(raiz: Path, origem: str, erros: dict[str, str]) -> list[tuple[str, Path]]:
    """(rótulo, pasta) de cada <raiz>/<especie>/<slug>/, sem seguir symlink e sem pastas ocultas
    (o `.tmp-<slug>-<hex>` de um rascunho em gravação não é subprojeto)."""
    achadas: list[tuple[str, Path]] = []
    if not raiz.is_dir():
        return achadas
    for esp_dir in sorted(raiz.iterdir()):
        if esp_dir.name.startswith(".") or not (esp_dir.is_dir() or esp_dir.is_symlink()):
            continue
        if esp_dir.is_symlink() or not _dentro(esp_dir, raiz):
            erros[f"{origem}:{esp_dir.name}"] = "pasta de espécie é link simbólico ou resolve fora da raiz"
            continue
        for pasta in sorted(esp_dir.iterdir()):
            if pasta.name.startswith(".") or not (pasta.is_dir() or pasta.is_symlink()):
                continue
            rotulo = f"{origem}:{esp_dir.name}/{pasta.name}"
            if pasta.is_symlink() or not _dentro(pasta, raiz):
                erros[rotulo] = "pasta do subprojeto é link simbólico ou resolve fora da raiz"
                continue
            achadas.append((rotulo, pasta))
    return achadas


def carregar_subprojetos(especies: dict[str, Especie], fontes: Optional[list[tuple[str, Path]]] = None,
                         *, reg: Optional["Registry"] = None
                         ) -> tuple[dict[str, Registro], dict[str, str]]:
    """(registros por id, erros por "<origem>:<especie>/<slug>"). Um subprojeto ruim nunca
    derruba os outros; o curado do repositório nunca é derrubado por uma cópia em <dados>."""
    validos: dict[str, Registro] = {}
    erros: dict[str, str] = {}
    # casefold → id dono (ids e aliases no MESMO espaço: um alias não pode ser id de outro)
    nomes: dict[str, str] = {}
    for origem, raiz in (fontes if fontes is not None else raizes()):
        for rotulo, pasta in _pastas(raiz, origem, erros):
            try:
                arq = pasta / "subprojeto.yaml"
                if arq.is_symlink():
                    raise ValueError("subprojeto.yaml é link simbólico")
                if not arq.is_file():
                    raise ValueError("pasta sem subprojeto.yaml")
                bruto = yaml.safe_load(arq.read_text(encoding="utf-8"))
                if not isinstance(bruto, dict):
                    raise ValueError("o YAML não é um mapeamento")
                sub = Subprojeto.model_validate(bruto)
                esperado = f"{pasta.parent.name}/{pasta.name}"
                if sub.id != esperado:
                    raise ValueError(f"id {sub.id!r} difere do caminho {esperado}")
                problemas = validar_contra_especie(sub, especies)
                if problemas:
                    raise ValueError("; ".join(problemas))
                if sub.id == ID_DEMO and origem != "repo":
                    raise ValueError(f"{ID_DEMO} é reservado ao repositório")
                if sub.privado and origem == "repo":
                    raise ValueError("privado: true não pode viver no repositório (regra 7)")
                _conferir_arquivo_na_pasta(pasta, sub.amostras, "amostras")
                if sub.painel:
                    _conferir_arquivo_na_pasta(pasta, sub.painel, "painel")
                if sub.parametros:
                    if reg is None:
                        from omica.registry import load_registry

                        reg = load_registry()
                    modulos = sub.modulos or especies[sub.especie].modulos
                    problemas = conferir_parametros(sub.parametros, modulos, reg)
                    if problemas:
                        raise ValueError("; ".join(problemas))
                if sub.id in validos:
                    raise ValueError(f"id duplicado: já carregado de {validos[sub.id].origem}")
                chaves = [sub.id.casefold(), *(a.casefold() for a in sub.legado)]
                if len(set(chaves)) != len(chaves):
                    raise ValueError(f"aliases legado repetidos ou iguais ao id: {sub.legado!r}")
                colisao = next((c for c in chaves if c in nomes), None)
                if colisao is not None:
                    raise ValueError(f"id/alias {colisao!r} colide com {nomes[colisao]!r}")
                validos[sub.id] = Registro(sub, pasta, origem)  # type: ignore[arg-type]
                nomes.update({c: sub.id for c in chaves})
            except Exception as ex:
                erros[rotulo] = str(ex)
    return validos, erros


def n_amostras(reg: Registro) -> Optional[int]:
    """Linhas não vazias menos o cabeçalho, lendo em fluxo. None se o arquivo não existir."""
    arq = reg.pasta / reg.sub.amostras
    if not arq.is_file():
        return None
    linhas = 0
    with arq.open("rb") as f:
        for linha in f:
            if linha.strip():
                linhas += 1
    return max(0, linhas - 1)


# ------------------------------------------------------------------- rascunho pela UI

class RascunhoRecusado(Exception):
    """Recusa de `criar_rascunho`. `erro` é o código estável (a UI traduz):
    rascunho_desligado | slug_invalido | id_duplicado | especie_inexistente | assembly_invalida |
    pergunta_curta | amostras_invalidas | titulo_invalido | pergunta_invalida."""

    def __init__(self, erro: str, detalhe: str = "") -> None:
        super().__init__(f"{erro}: {detalhe}" if detalhe else erro)
        self.erro = erro
        self.detalhe = detalhe


def _conferir_amostras(conteudo: bytes) -> None:
    if len(conteudo) > AMOSTRAS_MAX_BYTES:
        raise RascunhoRecusado("amostras_invalidas", f"maior que {AMOSTRAS_MAX_BYTES} bytes")
    try:
        texto = conteudo.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise RascunhoRecusado("amostras_invalidas", "não é UTF-8") from None
    if "{" in texto or "}" in texto:
        raise RascunhoRecusado("amostras_invalidas", "chave '{' ou '}' no conteúdo")
    if re.search(r"\r(?!\n)", texto):
        raise RascunhoRecusado("amostras_invalidas", "'\\r' isolado (quebra de linha de Mac antigo)")
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", texto):
        raise RascunhoRecusado("amostras_invalidas", "caractere de controle no conteúdo")
    primeira = texto.lstrip("﻿").split("\n", 1)[0].rstrip("\r")
    if primeira.split("\t", 1)[0] != "amostra":
        raise RascunhoRecusado("amostras_invalidas", "a primeira coluna do cabeçalho tem de ser 'amostra'")


def _limpar(caminho: Path) -> None:
    shutil.rmtree(caminho, ignore_errors=True)


def criar_rascunho(especie: str, slug: str, *, titulo: str, pergunta: str, assembly: str,
                   amostras: Optional[bytes] = None) -> Registro:
    """Cria <OMICA_DATA>/projetos/<especie>/<slug>/ com `status: draft`, `tipo: pesquisa` e
    `privado: true` (e o amostras.tsv, se dado). Atômico: grava numa pasta oculta temporária
    ao lado e faz os.replace; em qualquer falha a temporária é removida."""
    cfg = get_config()
    if not cfg.permitir_rascunho:
        # antes de QUALQUER E/S: com a flag desligada nada cria <dados>/projetos (D-11)
        raise RascunhoRecusado("rascunho_desligado")
    if not isinstance(slug, str) or not SLUG.fullmatch(slug) or SLUG_NU.fullmatch(slug):
        raise RascunhoRecusado("slug_invalido", f"{slug!r}")
    titulo = (titulo or "").strip()
    pergunta = (pergunta or "").replace("\r\n", "\n").strip()
    if not titulo or len(titulo) > TITULO_MAX or _CONTROLE_TITULO.search(titulo):
        raise RascunhoRecusado("titulo_invalido", f"1 a {TITULO_MAX} caracteres, sem caractere de controle")
    if len(pergunta) < PERGUNTA_MIN:
        raise RascunhoRecusado("pergunta_curta", f"mínimo de {PERGUNTA_MIN} caracteres")
    if len(pergunta) > PERGUNTA_MAX or _CONTROLE_PERGUNTA.search(pergunta):
        raise RascunhoRecusado("pergunta_invalida", f"até {PERGUNTA_MAX} caracteres, sem caractere de controle")
    especies, _ = carregar_especies()
    esp = especies.get(especie)
    if esp is None:
        raise RascunhoRecusado("especie_inexistente", f"{especie!r}")
    if assembly not in esp.assemblies:
        raise RascunhoRecusado("assembly_invalida", f"{assembly!r} não é de {especie}")
    if amostras is not None:
        _conferir_amostras(amostras)

    id_sub = f"{especie}/{slug}"
    registros, _ = carregar_subprojetos(especies)
    ocupados = {r.sub.id.casefold() for r in registros.values()}
    ocupados |= {a.casefold() for r in registros.values() for a in r.sub.legado}
    # a pasta conta mesmo se o subprojeto que mora nela não carregou (está em `erros`)
    if id_sub.casefold() in ocupados or any((raiz / especie / slug).exists() for _, raiz in raizes()):
        raise RascunhoRecusado("id_duplicado", id_sub)

    dados = {"id": id_sub, "especie": especie, "assembly": assembly, "titulo": titulo,
             "pergunta": pergunta, "tipo": "pesquisa", "status": "draft", "privado": True}
    # yaml.safe_dump, nunca f-string: uma pergunta com "\nstatus: valido" promoveria o rascunho
    texto = yaml.safe_dump(dados, allow_unicode=True, sort_keys=False)
    sub = Subprojeto.model_validate(yaml.safe_load(texto))
    if (sub.status, sub.privado, sub.tipo, sub.titulo, sub.pergunta) != ("draft", True, "pesquisa", titulo, pergunta):
        raise RuntimeError("o YAML do rascunho não reproduz os campos dados (gravação recusada)")
    problemas = validar_contra_especie(sub, especies)
    if problemas:
        raise RascunhoRecusado("assembly_invalida", "; ".join(problemas))

    pasta_esp = cfg.projetos_dir / especie
    criadas = [d for d in (cfg.projetos_dir, pasta_esp) if not d.exists()]
    final = pasta_esp / slug
    tmp = pasta_esp / f".tmp-{slug}-{secrets.token_hex(3)}"
    try:
        pasta_esp.mkdir(parents=True, exist_ok=True)
        tmp.mkdir()
        (tmp / "subprojeto.yaml").write_text(texto, encoding="utf-8", newline="\n")
        if amostras is not None:
            (tmp / sub.amostras).write_bytes(amostras)
        try:
            os.replace(tmp, final)
        except OSError:
            if final.exists():  # outro pedido gravou o mesmo slug entre a checagem e o replace
                raise RascunhoRecusado("id_duplicado", id_sub) from None
            raise
    except BaseException:
        _limpar(tmp)
        for d in reversed(criadas):  # só as pastas que ESTE pedido criou, e só se vazias
            try:
                d.rmdir()
            except OSError:
                pass
        raise
    return Registro(sub, final, "dados")
