"""gate.py — gate anti-vazamento para repositórios de CÁLCULO de artigo.

Portado da genomes_platform (app/modules/github_publish.py) por cópia curada:
mesmas classes de regra — BLOCK_TOKENS (rastro de IA/redação), BLOCK_FILE
(arquivos de manuscrito) e WARN_PATH (caminhos absolutos do servidor). Varre
uma árvore e devolve o veredito; NÃO commita nem pusha (a publicação continua
sendo decisão humana + skill git_dione).

IMPORTANTE: este gate audita árvores de EXPORTAÇÃO (repos de cálculo gerados a
partir de resultados da plataforma). Não se aplica ao próprio repo da ômica,
que cita legitimamente as ferramentas usadas no seu desenvolvimento.

Uso: python -m omica gate <diretório>
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

BLOCK_TOKENS = re.compile(
    r"(?i)\b(claude|anthropic|chatgpt|openai|copilot|gemini|build_paper|"
    r"marcar_versao)\b|declaration of generative ai|declara\w* de uso de ia|"
    r"co-authored-by:\s*claude",
)
BLOCK_FILE = re.compile(
    r"(?i)(^|/)(manuscript_|manuscrito_|cover_letter|MANUAL_|MEMORIA)|"
    r"\.docx$|(^|/)build_[^/]*\.sh$",
)
WARN_PATH = re.compile(r"(C:\\Users\\|/home/|/Users/|/mnt/[a-z]/|E:\\)")

TEXT_EXTS = {".py", ".r", ".sh", ".ps1", ".bat", ".md", ".txt", ".json",
             ".yaml", ".yml", ".cfg", ".ini", ".tsv", ".csv"}
IGNORAR_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules"}


@dataclass
class Veredito:
    bloqueios: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    @property
    def aprovado(self) -> bool:
        return not self.bloqueios


def auditar(raiz: Path) -> Veredito:
    v = Veredito()
    raiz = raiz.resolve()
    for caminho in sorted(raiz.rglob("*")):
        if not caminho.is_file():
            continue
        rel = caminho.relative_to(raiz).as_posix()
        if any(parte in IGNORAR_DIRS for parte in caminho.parts):
            continue
        if BLOCK_FILE.search(rel):
            v.bloqueios.append(f"[arquivo] {rel} — nome/tipo de manuscrito proibido")
            continue
        if caminho.suffix.lower() not in TEXT_EXTS:
            continue
        try:
            texto = caminho.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, linha in enumerate(texto.splitlines(), start=1):
            if BLOCK_TOKENS.search(linha):
                v.bloqueios.append(f"[token] {rel}:{i} — rastro de IA/redação")
            elif WARN_PATH.search(linha):
                v.avisos.append(f"[path] {rel}:{i} — caminho absoluto do servidor")
    return v


def main(alvo: str) -> int:
    raiz = Path(alvo)
    if not raiz.is_dir():
        print(f"FALHA: diretório inexistente: {raiz}")
        return 2
    v = auditar(raiz)
    for b in v.bloqueios[:50]:
        print(f"❌ {b}")
    for a in v.avisos[:20]:
        print(f"⚠️  {a}")
    extra_b = max(0, len(v.bloqueios) - 50)
    extra_a = max(0, len(v.avisos) - 20)
    if extra_b or extra_a:
        print(f"… (+{extra_b} bloqueios, +{extra_a} avisos)")
    if v.aprovado:
        print(f"✅ GATE APROVADO: {raiz} ({len(v.avisos)} aviso(s))")
        return 0
    print(f"❌ GATE REPROVADO: {len(v.bloqueios)} bloqueio(s) — nada deve ser publicado")
    return 1
