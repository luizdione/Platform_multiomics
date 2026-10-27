"""esquema.py — o formato do RASCUNHO que o redator (Claude, via n8n) devolve.

Dois usos do mesmo contrato:
- `ESQUEMA_SAIDA` vai no `output_config.format` da chamada à Messages API (saída
  estruturada): só o subconjunto de JSON Schema que ela aceita — tipos, `required`, `enum`,
  `additionalProperties: false`; nada de limite de tamanho.
- `Rascunho` (pydantic) revalida do lado da ômica, COM os limites: o esquema no provedor é
  ajuda, não garantia, e o rascunho pode vir de uma edição manual do autor.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SECOES = ["resumo", "metodos", "resultados", "interpretacao", "limitacoes"]

ESQUEMA_SAIDA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["secoes", "legendas"],
    "properties": {
        "secoes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "titulo", "paragrafos"],
                "properties": {
                    "id": {"type": "string", "enum": SECOES},
                    "titulo": {"type": "string"},
                    "paragrafos": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "legendas": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["fig", "texto"],
                "properties": {"fig": {"type": "string"}, "texto": {"type": "string"}},
            },
        },
    },
}


class Secao(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Literal["resumo", "metodos", "resultados", "interpretacao", "limitacoes"]
    titulo: str = Field(min_length=1, max_length=200)
    paragrafos: list[str] = Field(min_length=1, max_length=12)


class Legenda(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fig: str = Field(max_length=120)
    texto: str = Field(max_length=1500)


class Rascunho(BaseModel):
    model_config = ConfigDict(extra="forbid")
    secoes: list[Secao] = Field(min_length=1, max_length=len(SECOES))
    legendas: list[Legenda] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _coerente(self) -> "Rascunho":
        ids = [s.id for s in self.secoes]
        if len(set(ids)) != len(ids):
            raise ValueError("seção repetida")
        for s in self.secoes:
            if any(len(p) > 3000 for p in s.paragrafos):
                raise ValueError(f"parágrafo longo demais em {s.id}")
        return self
