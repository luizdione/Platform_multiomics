"""omica — plataforma localhost unificada de ômicas (genômica, epigenômica, transcriptômica).

Sucessora das plataformas genomes_platform e epigenome_platform (congeladas em
2026-09-02, tag archive/2026-09-final). Regras de projeto no README.md.
"""
from pathlib import Path

_VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"
__version__ = _VERSION_FILE.read_text(encoding="utf-8").strip() if _VERSION_FILE.exists() else "0.0.0"
