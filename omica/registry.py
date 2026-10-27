"""registry.py — carrega e valida os manifests dos módulos de ômica.

Herda o conceito de "fonte única da verdade" do pipeline_registry.py da
genomes_platform (fases → etapas com status colorido), generalizado em YAML por
módulo (modules/<id>/manifest.yaml) e validado por schema pydantic no boot.

Anti-lição nº 2: manifest inválido → o módulo NÃO carrega e o erro aparece no
doctor e em /saude. Nunca módulo morto silencioso.
"""
from __future__ import annotations

import re
import string
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field, field_validator

from omica.config import REPO_ROOT

MODULES_DIR = REPO_ROOT / "modules"

# Mesmos status da genomes_platform + "planejado" (módulo previsto, ainda sem código).
STATUS_TYPES = {
    "concluido": {"cor": "#27ae60", "rotulo_pt": "Concluído", "rotulo_en": "Completed"},
    "em_andamento": {"cor": "#16a085", "rotulo_pt": "Em andamento", "rotulo_en": "In progress"},
    "em_desenvolvimento": {"cor": "#3498db", "rotulo_pt": "Em desenvolvimento", "rotulo_en": "In development"},
    "validado_sem_dados": {"cor": "#f39c12", "rotulo_pt": "Validado sem dados", "rotulo_en": "Validated w/o data"},
    "validado_com_erros": {"cor": "#e74c3c", "rotulo_pt": "Validado com erros", "rotulo_en": "Validated w/ errors"},
    "planejado": {"cor": "#95a5a6", "rotulo_pt": "Planejado", "rotulo_en": "Planned"},
}

RUNTIMES_VALIDOS = ("image:", "conda:", "profile:")

# Campo de format aceito num cmd: um nome simples, cru. Sem índice (`{dados[0]}`), sem
# atributo (`{dados.x}`) e sem posição (`{}`, `{0}`) — o dispatch formata só por nome.
_CAMPO_SIMPLES = re.compile(r"[A-Za-z_][A-Za-z0-9_]*", re.ASCII)


def campos_do_cmd(cmd: str) -> tuple[set[str], list[str]]:
    """(nomes, problemas) dos campos que o `str.format` do dispatch substitui no cmd.

    Os nomes saem de `string.Formatter().parse`, o MESMO parser do `str.format`. As travas
    de {dados}/{saida} comparavam o TEXTO literal "{dados}" (revisão F2, resíduo do achado
    #37): `{dados!s}` e `{dados:}` escapavam e o caminho fixo gravava no demo selado, e
    `{{dados}}` (chave escapada, que vira texto) era tomado por campo.

    Problema = campo com conversão (`!s`, `!r`, `!a`) ou especificador (`:...`), campo que
    não é nome simples, ou chave malformada. Nenhum manifest precisa disso, e cada um é uma
    forma de um nome passar por outro numa comparação de texto. (`{dados:}`, especificador
    vazio, é idêntico a `{dados}` para o parser — e sai com o nome `dados`, como deve.)"""
    nomes: set[str] = set()
    problemas: list[str] = []
    try:
        partes = list(string.Formatter().parse(cmd))
    except ValueError as e:
        return nomes, [f"chaves malformadas no cmd ({e})"]
    for _, nome, especificador, conversao in partes:
        if nome is None:
            continue  # trecho literal (inclui `{{`/`}}` escapadas)
        if not _CAMPO_SIMPLES.fullmatch(nome):
            problemas.append(f"campo {{{nome}}} não é um nome simples")
            continue
        nomes.add(nome)
        if conversao is not None or especificador:
            problemas.append(f"campo {nome!r} com conversão/especificador "
                             f"({'!' + conversao if conversao else ''}{':' + especificador if especificador else ''})")
    return nomes, problemas


class Etapa(BaseModel):
    key: str
    desc: str
    auto: bool = False
    status_type: str = "planejado"
    script: Optional[str] = None  # relativo a modules/<id>/scripts/

    @field_validator("status_type")
    @classmethod
    def _status_conhecido(cls, v: str) -> str:
        if v not in STATUS_TYPES:
            raise ValueError(f"status_type desconhecido: {v!r} (válidos: {sorted(STATUS_TYPES)})")
        return v


class Fase(BaseModel):
    id: str
    nome: str
    descricao: str = ""
    compartilhada: bool = False
    etapas: list[Etapa] = Field(default_factory=list)


class ScriptSpec(BaseModel):
    """Evolução do SCRIPTS_META da genomes_platform: metadados + runtime + custo."""
    key: str
    titulo: str
    descricao: str = ""
    runtime: str            # "image:omica-bioinfo" | "conda:datascience" | "profile:nfcore"
    cmd: str                # comando com placeholders {dados} e {<param.name>}
    params: list["ParamSpec"] = Field(default_factory=list)
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    mem_gb: float = 1.0     # insumo da guarda de RAM
    cpus: int = 1
    tempo_estimado: str = ""
    # Contrato tipado de passagem (só nas entradas de `scripts_api`, F2 da integração com o
    # n8n): que formato cada parâmetro consome e que portas o script produz em {saida}.
    # É o que deixa omica/pipelines.py RECUSAR um encadeamento cujo formato não bate.
    consome: dict[str, str] = Field(default_factory=dict)
    produz: dict[str, "PortaSpec"] = Field(default_factory=dict)

    @field_validator("runtime")
    @classmethod
    def _runtime_valido(cls, v: str) -> str:
        if not v.startswith(RUNTIMES_VALIDOS):
            raise ValueError(f"runtime deve começar com um de {RUNTIMES_VALIDOS}: {v!r}")
        return v


class PortaSpec(BaseModel):
    """Uma saída de um script de `scripts_api`: o arquivo (relativo a {saida}) e o formato
    que omica/contrato/formatos.py sabe validar."""
    arquivo: str
    formato: str


class ParamSpec(BaseModel):
    """Parâmetro editável de um script na UI. O valor preenche o placeholder
    {name} do cmd (sanitizado no dispatch — nunca shell livre)."""
    name: str
    rotulo: str
    default: str = ""
    # Como o dispatch valida o valor (omica/jobs/dispatch.py::_validar_valor). Não entra
    # no hash do selo — hash_dos_passos só lê name=default —, então declarar o tipo num
    # script selado não vence o selo.
    tipo: Literal["texto", "caminho", "dir", "inteiro", "decimal", "grupo"] = "texto"


class WorkflowSpec(BaseModel):
    """Sequência de scripts do módulo em que o output de um passo alimenta o
    input do seguinte. `testado_em`/`testado_por` só são preenchidos quando o
    encadeamento rodou de ponta a ponta de verdade — é o selo da aba do
    Professor Ômico (nunca marcar workflow não validado)."""
    id: str
    titulo: str
    descricao: str = ""
    passos: list[str]  # keys de scripts deste módulo, na ordem
    testado_em: str = ""
    testado_por: str = ""
    # Impressão digital dos scripts NO DIA do teste. Sem ela o selo é decorativo:
    # sobrevive à reescrita do próprio script que certifica (auditoria 2026-09-14).
    # Preenchida por `python -m omica selos --gravar` DEPOIS de um E2E real.
    hash_scripts: str = ""
    nota_didatica: str = ""


class Manifest(BaseModel):
    id: str
    nome_pt: str
    nome_en: str
    icone: str = "🧬"
    ordem: int = 99
    descricao_pt: str = ""
    descricao_en: str = ""
    # Domínios da biblioteca de skills (data/skills_index.json) que este módulo cobre.
    # É o elo com /skills ("skills relacionadas" na página do módulo) e o que o plugin
    # Bioinfo lê para saber que um pedido desse domínio já tem análise pronta aqui,
    # antes de buscar receita genérica. Domínio inexistente é pego pelo doctor.
    skills_dominios: list[str] = Field(default_factory=list)
    fases: list[Fase] = Field(default_factory=list)
    scripts: list[ScriptSpec] = Field(default_factory=list)
    # Entradas ORQUESTRÁVEIS (n8n): os mesmos arquivos de script, mas com entrada e saída
    # como parâmetro (`--saida {saida}` = pasta do run). Seção separada de propósito: UI,
    # Professor, plugin Bioinfo e o hash dos selos leem só `scripts:` — nada aqui vence selo
    # nem muda o que a UI mostra.
    scripts_api: list[ScriptSpec] = Field(default_factory=list)
    workflows: list[WorkflowSpec] = Field(default_factory=list)
    # página → arquivo de resultado (relativo a results_dir). Único caminho para
    # números na UI (anti-lição nº 4: nada de número científico hardcoded).
    results_spec: dict[str, str] = Field(default_factory=dict)

    def dir(self) -> Path:
        return MODULES_DIR / self.id

    def em_construcao(self) -> bool:
        return all(e.status_type == "planejado" for f in self.fases for e in f.etapas)


class Registry(BaseModel):
    modules: list[Manifest] = Field(default_factory=list)
    erros: dict[str, str] = Field(default_factory=dict)  # pasta → mensagem de validação

    def por_id(self, mod_id: str) -> Optional[Manifest]:
        return next((m for m in self.modules if m.id == mod_id), None)


def load_registry(modules_dir: Path = MODULES_DIR) -> Registry:
    reg = Registry()
    if not modules_dir.exists():
        return reg
    for pasta in sorted(p for p in modules_dir.iterdir() if p.is_dir()):
        manifesto = pasta / "manifest.yaml"
        if not manifesto.exists():
            reg.erros[pasta.name] = "manifest.yaml ausente"
            continue
        try:
            bruto = yaml.safe_load(manifesto.read_text(encoding="utf-8"))
            m = Manifest.model_validate(bruto)
            if m.id != pasta.name:
                raise ValueError(f"id {m.id!r} difere do nome da pasta {pasta.name!r}")
            _validar_referencias(m, modules_dir)
            reg.modules.append(m)
        except Exception as e:  # erro de UM módulo nunca derruba a plataforma
            reg.erros[pasta.name] = str(e)
    reg.modules.sort(key=lambda m: (m.ordem, m.id))
    return reg


def _validar_referencias(m: Manifest, modules_dir: Path = MODULES_DIR) -> None:
    """Classe de bug das plataformas antigas: referência a arquivo não versionado.

    O cmd do manifest é SEMPRE "<interpretador> <caminho relativo à raiz do repo>"
    (script_job.py roda com cwd=REPO_ROOT). A versão anterior olhava só o PRIMEIRO
    token — que é "python3"/"Rscript" e nunca termina em .py/.R/.sh —, de modo que
    o laço inteiro era código morto: um manifest podia apontar para script apagado
    e carregar limpo, com o doctor dizendo "4 módulos válidos".
    """
    faltando: list[str] = []
    raiz = modules_dir.parent  # cwd dos jobs
    base_modulo = modules_dir / m.id
    for fase in m.fases:
        for etapa in fase.etapas:
            if etapa.script and not (base_modulo / "scripts" / etapa.script).exists():
                faltando.append(f"etapa {etapa.key} → scripts/{etapa.script}")
    for spec in [*m.scripts, *m.scripts_api]:
        alvo = next((t for t in spec.cmd.split() if t.endswith((".py", ".R", ".sh"))), None)
        if alvo is None:
            faltando.append(f"script {spec.key} → cmd sem arquivo executável: {spec.cmd!r}")
        elif not (raiz / alvo).exists():
            faltando.append(f"script {spec.key} → {alvo}")
        faltando.extend(f"script {spec.key}: {p}" for p in campos_do_cmd(spec.cmd)[1])
    # chave única nas duas seções: a API resolve `scripts_api` pela chave, e uma chave
    # igual a uma de `scripts` faria a UI e a API rodarem coisas diferentes com o mesmo nome
    chaves = [s.key for s in m.scripts] + [s.key for s in m.scripts_api]
    repetidas = sorted({k for k in chaves if chaves.count(k) > 1})
    if repetidas:
        faltando.append(f"chave de script repetida entre scripts/scripts_api: {repetidas}")
    for spec in m.scripts_api:
        nomes = {p.name for p in spec.params}
        for param in spec.consome:
            if param not in nomes:
                faltando.append(f"scripts_api {spec.key}: consome {param!r} que não é parâmetro")
        # pelos NOMES dos campos (campos_do_cmd), não pelo texto: `{dados!s}` é {dados}
        nomes_cmd = campos_do_cmd(spec.cmd)[0]
        if "saida" not in nomes_cmd:
            faltando.append(f"scripts_api {spec.key}: cmd sem {{saida}} (gravaria fora da pasta do run)")
        if "dados" in nomes_cmd:
            # Meia-edição de um cmd copiado de `scripts:` — `--saida {dados}/results/<mod>/demo/x
            # --log {saida}` — passava na checagem acima. Num scripts_api a entrada vem por
            # parâmetro e a saída por {saida}; caminho fixo sob {dados} fica fora do plano
            # congelado e pode ser o demo selado (revisão F2, achado #37).
            faltando.append(f"scripts_api {spec.key}: cmd cita {{dados}} (caminho fixo fora do plano do run)")
    if faltando:
        raise ValueError("referências quebradas: " + "; ".join(faltando))
    _validar_workflows(m)


def hash_dos_passos(m: Manifest, wf: WorkflowSpec, modules_dir: Path = MODULES_DIR) -> str:
    """sha256 dos ARQUIVOS de script que o workflow encadeia, na ordem dos passos.

    É o que dá sentido ao selo: se qualquer script mudar, o hash muda e o
    diagnóstico passa a dizer que o workflow precisa ser re-executado antes de o
    selo (e os números que ele publica) valerem de novo.

    As quebras de linha são normalizadas para LF: com core.autocrlf=true o mesmo
    arquivo é CRLF no host Windows e LF dentro do container, e sem isso o selo
    apareceria vencido em um dos dois lados sempre.
    """
    import hashlib

    raiz = modules_dir.parent
    specs = {s.key: s for s in m.scripts}
    h = hashlib.sha256()
    # _comum.py entra: é importado por TODOS os scripts do módulo (escrever_resumo,
    # o contrato "RESULTADO:", rodar()), e mexer nele muda o resultado de todos os
    # passos sem mudar nenhum script individualmente.
    comum = modules_dir / m.id / "scripts" / "_comum.py"
    h.update(comum.read_bytes().replace(b"\r\n", b"\n") if comum.exists() else b"<sem _comum>")
    for passo in wf.passos:
        spec = specs.get(passo)
        h.update(passo.encode("utf-8"))
        if spec is None:
            h.update(b"<passo inexistente>")
            continue
        # o cmd inteiro entra: trocar um parâmetro científico no manifest (--maf,
        # --qvalue, --regiao) muda o resultado sem tocar numa linha de código.
        h.update(" ".join(spec.cmd.split()).encode("utf-8"))
        for par in spec.params:
            h.update(f"{par.name}={par.default}".encode("utf-8"))
        alvo = next((t for t in spec.cmd.split() if t.endswith((".py", ".R", ".sh"))), None)
        caminho = (raiz / alvo) if alvo else None
        if caminho and caminho.exists():
            h.update(caminho.read_bytes().replace(b"\r\n", b"\n"))
        else:
            h.update(b"<ausente>")
    return h.hexdigest()[:16]


def selos_vencidos(modules_dir: Path = MODULES_DIR) -> list[tuple[str, str, str]]:
    """(modulo, workflow, motivo) para todo workflow selado cujo script mudou."""
    fora = []
    for m in load_registry(modules_dir).modules:
        for wf in m.workflows:
            if not wf.testado_em:
                continue
            atual = hash_dos_passos(m, wf, modules_dir)
            if not wf.hash_scripts:
                fora.append((m.id, wf.id, "selo sem hash_scripts (não verificável)"))
            elif wf.hash_scripts != atual:
                fora.append((m.id, wf.id,
                             f"script mudou desde {wf.testado_em} "
                             f"({wf.hash_scripts} → {atual}) — re-executar"))
    return fora


def _validar_workflows(m: Manifest) -> None:
    """Um workflow só é válido se cada passo N>1 consumir pelo menos um output
    de passo anterior (encadeamento real, não lista solta) e se todos os passos
    existirem em scripts. Inputs fora de results/ são insumos externos (dados
    versionados ou E:\\data) e não contam para o encadeamento."""
    specs = {s.key: s for s in m.scripts}
    problemas: list[str] = []
    for wf in m.workflows:
        desconhecidos = [p for p in wf.passos if p not in specs]
        if desconhecidos:
            problemas.append(f"workflow {wf.id}: passos inexistentes {desconhecidos}")
            continue
        if len(wf.passos) < 2:
            problemas.append(f"workflow {wf.id}: precisa de >=2 passos")
            continue
        acumulados: set[str] = set()
        for i, passo in enumerate(wf.passos):
            spec = specs[passo]
            if i > 0:
                internos = [x for x in spec.inputs if x.startswith("results/")]
                if internos and not any(x in acumulados for x in internos):
                    problemas.append(
                        f"workflow {wf.id}: passo {passo} não consome nenhum output "
                        f"dos passos anteriores (inputs {internos})"
                    )
            acumulados.update(spec.outputs)
    if problemas:
        raise ValueError("workflows inválidos: " + "; ".join(problemas))
