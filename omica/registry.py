"""registry.py — carrega e valida os manifests dos módulos de ômica.

Herda o conceito de "fonte única da verdade" do pipeline_registry.py da
genomes_platform (fases → etapas com status colorido), generalizado em YAML por
módulo (modules/<id>/manifest.yaml) e validado por schema pydantic no boot.

Anti-lição nº 2: manifest inválido → o módulo NÃO carrega e o erro aparece no
doctor e em /saude. Nunca módulo morto silencioso.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

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

    @field_validator("runtime")
    @classmethod
    def _runtime_valido(cls, v: str) -> str:
        if not v.startswith(RUNTIMES_VALIDOS):
            raise ValueError(f"runtime deve começar com um de {RUNTIMES_VALIDOS}: {v!r}")
        return v


class ParamSpec(BaseModel):
    """Parâmetro editável de um script na UI. O valor preenche o placeholder
    {name} do cmd (sanitizado no dispatch — nunca shell livre)."""
    name: str
    rotulo: str
    default: str = ""


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
    for spec in m.scripts:
        alvo = next((t for t in spec.cmd.split() if t.endswith((".py", ".R", ".sh"))), None)
        if alvo is None:
            faltando.append(f"script {spec.key} → cmd sem arquivo executável: {spec.cmd!r}")
        elif not (raiz / alvo).exists():
            faltando.append(f"script {spec.key} → {alvo}")
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
