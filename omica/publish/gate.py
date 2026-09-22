"""gate.py — gate anti-vazamento para repositórios de CÁLCULO de artigo.

Portado da genomes_platform (app/modules/github_publish.py) por cópia curada:
mesmas classes de regra — BLOCK_TOKENS (rastro de IA/redação), BLOCK_FILE
(arquivos de manuscrito), BLOCK_SECRET/NOME_CREDENCIAL (credenciais —
acrescentados na auditoria de 2026-09-14, que provou com canária que o gate
original aprovava token do GitHub, chave AWS e chave privada SSH) e
WARN_PATH (caminhos absolutos do servidor). Varre
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

# CREDENCIAIS. O gate nasceu só com rastro de IA e nome de manuscrito: uma árvore
# com token do GitHub, chave da AWS ou chave privada SSH passava APROVADA. Regra
# global do autor: segredo nunca é versionado (credencial só por referência).
BLOCK_SECRET = re.compile(
    r"gh[pousr]_[A-Za-z0-9]{36,}"                       # GitHub clássico
    r"|github_pat_[A-Za-z0-9_]{22,}"                    # GitHub fine-grained
    r"|sk-ant-[A-Za-z0-9_\-]{20,}"                      # Anthropic
    r"|(?<![A-Za-z0-9])sk-[A-Za-z0-9]{32,}"             # OpenAI (com borda: "mask-<md5>" não é chave)
    r"|AKIA[0-9A-Z]{16}"                                # AWS access key id
    r"|ASIA[0-9A-Z]{16}"                                # AWS temporária
    r"|xox[abposr]-[A-Za-z0-9-]{10,}"                   # Slack
    r"|AIza[0-9A-Za-z_\-]{35}"                          # Google API
    r"|-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"
    # Regra genérica (chave = valor). Três cuidados aprendidos na revisão:
    # (a) NÃO pode acusar o padrão CORRETO de manejo de segredo —
    #     `api_key = os.environ["X"]`, `getenv(...)`, `cfg.get(...)` — senão o gate
    #     pune quem faz certo e o autor aprende a ignorá-lo;
    # (b) sem `\s*["']?\s*` adjacentes, que davam retrocesso quadrático;
    # (c) valor não pode conter ( [ { $ < — isso é código ou placeholder, não literal.
    r"|(?i:(?:senha|password|passwd|secret|api[_-]?key|access[_-]?token)"
    r"[\s\"']{0,8}[:=][\s\\\"']{0,8}"
    r"(?![A-Za-z_]+[.(\[])(?!x{3})(?!your)(?!seu)(?!exemplo)(?!example)"
    r"(?!changeme)(?!redacted)(?!\.\.\.)[^\s\"'\\(\[{$<]{8,})"
)
# Arquivos que NUNCA devem viajar numa árvore de exportação, pelo nome.
NOME_CREDENCIAL = re.compile(
    r"(?i)(^|/)(\.env(\.(?!example|template|sample)[^/]*)?|[^/]*\.pem|[^/]*\.key|"
    r"[^/]*\.p12|[^/]*\.pfx|id_rsa[^/]*|id_ed25519[^/]*|id_ecdsa[^/]*|"
    r"[^/]*credentials[^/]*|[^/]*\.kdbx)$"
)

# Formatos de REDAÇÃO também entram: antes, .tex/.ipynb/.Rmd/.bib ficavam de fora
# da allowlist e o gate "aprovava" arquivos que nunca tinha lido.
TEXT_EXTS = {".py", ".r", ".sh", ".ps1", ".bat", ".md", ".txt", ".json",
             ".yaml", ".yml", ".cfg", ".ini", ".tsv", ".csv", ".toml",
             ".tex", ".rmd", ".qmd", ".bib", ".html", ".htm", ".ipynb",
             ".env", ".conf", ".properties", ".log", ".xml", ".sql"}
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
    nao_lidos: list[str] = []
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
        if NOME_CREDENCIAL.search(rel):
            v.bloqueios.append(f"[credencial] {rel} — arquivo de segredo não pode ser publicado")
            continue
        if caminho.suffix.lower() not in TEXT_EXTS:
            nao_lidos.append(rel)  # agregados no fim: um por arquivo afogaria os avisos reais
            continue
        try:
            texto = caminho.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, linha in enumerate(texto.splitlines(), start=1):
            if BLOCK_SECRET.search(linha):
                v.bloqueios.append(f"[segredo] {rel}:{i} — credencial/chave no conteúdo")
            elif BLOCK_TOKENS.search(linha):
                v.bloqueios.append(f"[token] {rel}:{i} — rastro de IA/redação")
            elif WARN_PATH.search(linha):
                v.avisos.append(f"[path] {rel}:{i} — caminho absoluto do servidor")
    if nao_lidos:
        amostra = ", ".join(sorted(nao_lidos)[:5])
        v.avisos.append(
            f"[nao-lido] {len(nao_lidos)} arquivo(s) de extensão fora da allowlist "
            f"NÃO auditados (ex.: {amostra})")
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
