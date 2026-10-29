"""especies.py — espécies-referência da v2 multiespécie (só do REPOSITÓRIO: especies/<id>.yaml).

Cada espécie declara as assemblies que conhece e, em cada uma, as REFERÊNCIAS (`refs`) que
os cmds citam como placeholders de contexto (`{ref_gtf}`, `{ref_salmon_indice}`...; lista
fechada em omica/chaves_contexto.py). O estado — pronta | parcial | sem_referencia — é
CALCULADO por stat no disco, nunca declarado no YAML: um YAML não consegue se dizer pronto.

Regras do load (cada uma com canário em tests/test_m0_contratos.py):
  - `id` = nome do arquivo; todo modelo com extra="forbid" (chave digitada errado reprova);
  - `refs`: chave em CHAVES_REF, valor sem chaves e aprovado pelo MESMO sanitizador do
    dispatch (`_validar_valor`, com o tipo de CHAVES_CONTEXTO), e caminho/dir na forma que
    `no_disco` sabe ler (/data/..., modules/..., projetos/...);
  - `equivalencias`: chave em CHAVES_REF (crítica v2, médio 14);
  - `parametros` ("<modulo>.<script>.<param>"): a chave tem de existir no registry e ser de
    um módulo da espécie, e o valor passa no sanitizador do próprio parâmetro — uma chave
    digitada errado fazia o default bovino valer em silêncio (crítica v2, médio 14);
  - `recursos`, `nunca_usar` e `nao_referencia`: só caminho declarado (/data/..., modules/...,
    projetos/...), sem `..` — `no_disco` vira oráculo de existência de arquivo na página e na
    API, e `REPO_ROOT / "/etc/shadow"` é `/etc/shadow` (crítica v2, médio 10c);
  - erro de UMA espécie vai para `erros` e não derruba as outras.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Mapping, Optional, Sequence

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from omica.chaves_contexto import CHAVES_CONTEXTO, CHAVES_REF, ID_ESPECIE, MODULOS_RESERVADOS
from omica.config import REPO_ROOT, get_config

if TYPE_CHECKING:
    from omica.registry import Registry

# lido NA CHAMADA (carregar_especies), para os testes poderem apontar para um tmp
ESPECIES_DIR = REPO_ROOT / "especies"

_FORBID = ConfigDict(extra="forbid")
_ID_ASSEMBLY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\-]{1,31}", re.ASCII)
# "<modulo>.<script>.<param>" — os três nomes simples, minúsculos
CHAVE_PARAMETRO = re.compile(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*", re.ASCII)
# Caminho DECLARADO num YAML de espécie/subprojeto: /data/... (dados pesados montados; no host,
# GENOME_DATA) ou relativo ao repositório (modules/..., projetos/...). Cada segmento começa por
# letra, dígito ou '_': nada de '.', '..', oculto, '~', barra invertida, espaço ou absoluto do host.
CAMINHO_DECLARADO = re.compile(r"(?:/data|modules|projetos)(?:/[A-Za-z0-9_][A-Za-z0-9_.\-]*)+", re.ASCII)


def caminho_declarado_valido(valor: object) -> bool:
    return isinstance(valor, str) and bool(CAMINHO_DECLARADO.fullmatch(valor))


def _conferir_caminhos(valores: Sequence[str], onde: str) -> None:
    ruins = [v for v in valores if not caminho_declarado_valido(v)]
    if ruins:
        raise ValueError(f"{onde}: caminho fora do padrão (/data/..., modules/... ou projetos/..., "
                         f"sem '..'): {ruins!r}")


class Equivalencia(BaseModel):
    """Uma ref que, nesta assembly, é de OUTRA assembly (ex.: cache VEP 110 em ARS-UCD1.2
    servindo ARS-UCD1.3) e o que se afirma sobre isso."""
    model_config = _FORBID
    assembly: str
    relacao: Literal["identica", "equivalente_autossomos"]

    @field_validator("assembly")
    @classmethod
    def _assembly(cls, v: str) -> str:
        if not _ID_ASSEMBLY.fullmatch(v):
            raise ValueError(f"assembly fora do padrão: {v!r}")
        return v


class Assembly(BaseModel):
    model_config = _FORBID
    fonte: str = ""                 # "Ensembl 113", "SnpEff ARS-UCD1.3.113"
    contigs: Literal["sem_chr", "chr"]
    prefixo_gene: str               # ENSBTAG / ENSG / ENSMUSG
    refs: dict[str, str] = Field(default_factory=dict)          # "gtf" → valor de {ref_gtf}
    equivalencias: dict[str, Equivalencia] = Field(default_factory=dict)

    @field_validator("refs")
    @classmethod
    def _refs_fechadas(cls, v: dict[str, str]) -> dict[str, str]:
        fora = sorted(set(v) - CHAVES_REF)
        if fora:
            raise ValueError(f"refs fora da lista fechada: {fora} (aceitas: {sorted(CHAVES_REF)})")
        return v

    @field_validator("equivalencias")
    @classmethod
    def _equivalencias_fechadas(cls, v: dict[str, Equivalencia]) -> dict[str, Equivalencia]:
        fora = sorted(set(v) - CHAVES_REF)
        if fora:
            raise ValueError(f"equivalencias com chave fora de CHAVES_REF: {fora}")
        return v


class Especie(BaseModel):
    model_config = _FORBID
    id: str
    nome_cientifico: str
    nome_exibido: str
    nome_exibido_en: str
    taxon_ids: list[int]
    ordem: int
    assembly_padrao: str
    assemblies: dict[str, Assembly]
    modulos: list[str]
    # sobrescrita de default por "<modulo>.<script>.<param>" (o subprojeto vence a espécie)
    parametros: dict[str, str] = Field(default_factory=dict)
    recursos: dict[str, str] = Field(default_factory=dict)     # nome → caminho declarado
    nunca_usar: list[str] = Field(default_factory=list)        # caminhos que nada pode ler
    nao_referencia: list[str] = Field(default_factory=list)    # existem, mas não habilitam módulo

    @model_validator(mode="after")
    def _coerente(self) -> "Especie":
        if not ID_ESPECIE.fullmatch(self.id):
            raise ValueError(f"id de espécie fora do padrão: {self.id!r}")
        for nome in self.assemblies:
            if not _ID_ASSEMBLY.fullmatch(nome):
                raise ValueError(f"assembly fora do padrão: {nome!r}")
        if self.assembly_padrao not in self.assemblies:
            raise ValueError(f"assembly_padrao {self.assembly_padrao!r} não está em assemblies "
                             f"({sorted(self.assemblies)})")
        reservados = sorted(set(self.modulos) & MODULOS_RESERVADOS)
        if reservados:
            raise ValueError(f"modulos com id reservado: {reservados}")
        for nome, asm in self.assemblies.items():
            for chave, valor in asm.refs.items():
                _conferir_ref(f"{self.id}.{nome}.{chave}", chave, valor)
        for chave, valor in self.parametros.items():
            _conferir_parametro_estatico(chave, valor)
        _conferir_caminhos(list(self.recursos.values()), "recursos")
        _conferir_caminhos(self.nunca_usar, "nunca_usar")
        _conferir_caminhos(self.nao_referencia, "nao_referencia")
        return self


def _conferir_ref(rotulo: str, chave: str, valor: str) -> None:
    """O valor de uma ref vira UM token do argv: a mesma regra do sanitizador do dispatch, já
    no load (função existente; esta frente não edita dispatch.py)."""
    from omica.jobs.dispatch import _validar_valor
    from omica.registry import ParamSpec

    if "{" in valor or "}" in valor:
        raise ValueError(f"ref {rotulo}: chave '{{' ou '}}' no valor")
    tipo = CHAVES_CONTEXTO[f"ref_{chave}"]
    _validar_valor(ParamSpec(name=f"ref_{chave}", rotulo=rotulo, tipo=tipo), valor)
    if tipo in ("caminho", "dir") and not caminho_declarado_valido(valor):
        # o sanitizador aceita também a pasta de dados ABSOLUTA do host; aqui só a forma que
        # no_disco lê igual no host e no container
        raise ValueError(f"ref {rotulo}: caminho fora do padrão (/data/... ou modules/...): {valor!r}")


def _conferir_parametro_estatico(chave: str, valor: str) -> None:
    if not isinstance(chave, str) or not CHAVE_PARAMETRO.fullmatch(chave):
        raise ValueError(f"parametros: chave {chave!r} fora do padrão <modulo>.<script>.<param>")
    if "{" in valor or "}" in valor:
        raise ValueError(f"parametros.{chave}: chave '{{' ou '}}' no valor")


def conferir_parametros(parametros: Mapping[str, str], modulos: Sequence[str],
                        reg: Optional["Registry"] = None) -> list[str]:
    """Problemas das sobrescritas "<modulo>.<script>.<param>" contra o registry (vazio = ok).

    A chave tem de apontar para um parâmetro que EXISTE (em `scripts:` ou `scripts_api`) de
    um módulo em `modulos`, e o valor tem de passar no sanitizador desse parâmetro. Módulo
    que o registry recusou (está em `reg.erros`) não é conferido: ele já aparece como erro
    no doctor e nada dele executa."""
    from omica.jobs.dispatch import _validar_valor
    from omica.registry import load_registry

    reg = reg if reg is not None else load_registry()
    problemas: list[str] = []
    for chave, valor in parametros.items():
        try:
            _conferir_parametro_estatico(chave, valor)
        except ValueError as e:
            problemas.append(str(e))
            continue
        mod, script, nome = chave.split(".")
        if mod not in modulos:
            problemas.append(f"parametros.{chave}: módulo {mod!r} fora dos módulos declarados {list(modulos)}")
            continue
        m = reg.por_id(mod)
        if m is None:
            if mod not in reg.erros:
                problemas.append(f"parametros.{chave}: módulo {mod!r} inexistente")
            continue
        spec = next((s for s in [*m.scripts, *m.scripts_api] if s.key == script), None)
        if spec is None:
            problemas.append(f"parametros.{chave}: script {script!r} inexistente em {mod}")
            continue
        param = next((p for p in spec.params if p.name == nome), None)
        if param is None:
            problemas.append(f"parametros.{chave}: {script} não tem o parâmetro {nome!r}")
            continue
        try:
            _validar_valor(param, valor)
        except ValueError as e:
            problemas.append(f"parametros.{chave}: {e}")
    return problemas


def carregar_especies(pasta: Optional[Path] = None, *, reg: Optional["Registry"] = None
                      ) -> tuple[dict[str, Especie], dict[str, str]]:
    """(espécies válidas por id, na ordem de `ordem`; erros por nome de arquivo)."""
    pasta = ESPECIES_DIR if pasta is None else pasta
    validas: list[Especie] = []
    erros: dict[str, str] = {}
    if not pasta.is_dir():
        return {}, erros
    for arq in sorted(pasta.glob("*.yaml")):
        try:
            bruto = yaml.safe_load(arq.read_text(encoding="utf-8"))
            if not isinstance(bruto, dict):
                raise ValueError("o YAML não é um mapeamento")
            e = Especie.model_validate(bruto)
            if e.id != arq.stem:
                raise ValueError(f"id {e.id!r} difere do nome do arquivo {arq.stem!r}")
            if e.parametros:
                if reg is None:
                    from omica.registry import load_registry

                    reg = load_registry()
                problemas = conferir_parametros(e.parametros, e.modulos, reg)
                if problemas:
                    raise ValueError("; ".join(problemas))
            validas.append(e)
        except Exception as ex:  # uma espécie ruim nunca derruba as outras
            erros[arq.name] = str(ex)
    validas.sort(key=lambda e: (e.ordem, e.id))
    return {e.id: e for e in validas}, erros


def no_disco(valor: str) -> Path:
    """Onde ler AQUI um caminho declarado: "/data/x" → GENOME_DATA/x (no container
    GENOME_DATA=/data, então vale nos dois lados); "modules/..."/"projetos/..." → no repositório.
    Qualquer outra forma é ValueError (nunca vira caminho absoluto do host)."""
    if not caminho_declarado_valido(valor):
        raise ValueError(f"caminho fora do padrão (/data/..., modules/... ou projetos/...): {valor!r}")
    if valor.startswith("/data/"):
        return get_config().genome_data / valor[len("/data/"):]
    return REPO_ROOT / valor


@dataclass(frozen=True)
class EstadoEspecie:
    estado: Literal["pronta", "parcial", "sem_referencia"]
    presentes: int
    total: int
    faltando: tuple[str, ...]   # chaves de `refs` (ex.: "gtf") de caminho/dir ausentes


def _existe(p: Path) -> bool:
    try:
        return p.exists()   # só stat: nunca abre nem lê o arquivo
    except OSError:
        return False


def estado(e: Especie, assembly: Optional[str] = None) -> EstadoEspecie:
    """Estado das referências de caminho/dir da assembly (padrão: `assembly_padrao`).

    Só conta refs de tipo caminho/dir: texto e inteiro (snpeff_db, plink_chr_set) não são
    arquivo. Sem nenhuma → sem_referencia; todas no disco → pronta; algumas → parcial."""
    nome = e.assembly_padrao if assembly is None else assembly
    asm = e.assemblies.get(nome)
    if asm is None:
        raise ValueError(f"assembly {nome!r} não é da espécie {e.id} ({sorted(e.assemblies)})")
    arquivos = sorted((k, v) for k, v in asm.refs.items()
                      if CHAVES_CONTEXTO[f"ref_{k}"] in ("caminho", "dir"))
    faltando = tuple(k for k, v in arquivos if not _existe(no_disco(v)))
    total, presentes = len(arquivos), len(arquivos) - len(faltando)
    if total == 0 or presentes == 0:
        situacao = "sem_referencia"
    elif presentes == total:
        situacao = "pronta"
    else:
        situacao = "parcial"
    return EstadoEspecie(situacao, presentes, total, faltando)
