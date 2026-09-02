"""build_index.py — INDICE_CONSOLIDADO.md (Markdown) → data/skills_index.json.

A biblioteca skills_bioinfo (815 skills, 19 domínios) só tinha índice em Markdown
de 259 KB — inconsumível programaticamente. Este parser lê as seções
`## <dominio> (N skills)` e as tabelas `| Skill | Origem | Descricao | Stack | Pasta |`
e gera um JSON leve, versionado no repo, que a página /skills consome offline
(zero tokens, zero LLM). Só stdlib.

Uso: python -m omica reindex
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from omica.config import REPO_ROOT, get_config

RE_DOMINIO = re.compile(r"^##\s+([a-z0-9\-]+)\s+\((\d+)\s+skills?\)\s*$")
INDICE_JSON = REPO_ROOT / "data" / "skills_index.json"


def parse_indice(texto: str) -> dict[str, Any]:
    skills: list[dict[str, Any]] = []
    dominios: dict[str, int] = {}
    dominio_atual: str | None = None
    for linha in texto.splitlines():
        m = RE_DOMINIO.match(linha.strip())
        if m:
            dominio_atual = m.group(1)
            dominios[dominio_atual] = int(m.group(2))
            continue
        if dominio_atual is None or not linha.lstrip().startswith("|"):
            continue
        celulas = [c.strip() for c in linha.strip().strip("|").split("|")]
        if len(celulas) < 5:
            continue
        skill_id = celulas[0]
        if not skill_id or skill_id.lower() == "skill" or set(skill_id) <= {"-", ":", " "}:
            continue  # cabeçalho ou separador da tabela
        skills.append(
            {
                "id": skill_id,
                "dominio": dominio_atual,
                "origem": celulas[1],
                "descricao": celulas[2],
                "stack": [s.strip() for s in celulas[3].split(",") if s.strip()],
                "pasta": celulas[4].strip("`"),
            }
        )
    return {
        "gerado_em": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "total": len(skills),
        "dominios": dominios,
        "skills": skills,
    }


def main() -> int:
    fonte = get_config().skills_dir / "INDICE_CONSOLIDADO.md"
    if not fonte.exists():
        print(f"FALHA: índice-fonte não encontrado em {fonte}")
        return 1
    indice = parse_indice(fonte.read_text(encoding="utf-8"))
    declarado = sum(indice["dominios"].values())
    if indice["total"] != declarado:
        print(f"AVISO: parser extraiu {indice['total']} skills, cabeçalhos declaram {declarado}.")
    INDICE_JSON.parent.mkdir(parents=True, exist_ok=True)
    INDICE_JSON.write_text(json.dumps(indice, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"OK: {indice['total']} skills de {len(indice['dominios'])} domínios → {INDICE_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
