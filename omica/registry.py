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
    cmd: str                # comando com templates {sub_id}, {input}, {output}
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


class Manifest(BaseModel):
    id: str
    nome_pt: str
    nome_en: str
    icone: str = "🧬"
    ordem: int = 99
    descricao_pt: str = ""
    descricao_en: str = ""
    fases: list[Fase] = Field(default_factory=list)
    scripts: list[ScriptSpec] = Field(default_factory=list)
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
            _validar_referencias(m)
            reg.modules.append(m)
        except Exception as e:  # erro de UM módulo nunca derruba a plataforma
            reg.erros[pasta.name] = str(e)
    reg.modules.sort(key=lambda m: (m.ordem, m.id))
    return reg


def _validar_referencias(m: Manifest) -> None:
    """Classe de bug das plataformas antigas: referência a arquivo não versionado."""
    faltando: list[str] = []
    for fase in m.fases:
        for etapa in fase.etapas:
            if etapa.script and not (m.dir() / "scripts" / etapa.script).exists():
                faltando.append(f"etapa {etapa.key} → scripts/{etapa.script}")
    for spec in m.scripts:
        candidato = m.dir() / "scripts" / spec.cmd.split()[0]
        if spec.cmd.split()[0].endswith((".py", ".R", ".sh")) and not candidato.exists():
            faltando.append(f"script {spec.key} → {candidato.name}")
    if faltando:
        raise ValueError("referências quebradas: " + "; ".join(faltando))
