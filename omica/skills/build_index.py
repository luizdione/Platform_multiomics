"""build_index.py — INDICE_CONSOLIDADO.md (Markdown) → data/skills_index.json.

A biblioteca skills_bioinfo (815 skills, 19 domínios) só tinha índice em Markdown
de 259 KB — inconsumível programaticamente. Este parser lê as seções
`## <dominio> (N skills)`, os cards de domínio (`> **Título** — Quando… Regras:…
Stack:…`) e as tabelas `| Skill | Origem | Descricao | Stack | Pasta |`, e gera um
JSON leve, versionado no repo, que a página /skills consome offline (zero tokens,
zero LLM). Só stdlib.

O reindex também traz para data/ a cópia do glossário de BUSCA PT→EN, cuja fonte
única é o plugin Bioinfo: o índice é em inglês e o público da plataforma digita
em português (ver copiar_glossario).

Uso: python -m omica reindex
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from omica.config import REPO_ROOT, get_config

RE_DOMINIO = re.compile(r"^##\s+([a-z0-9\-]+)\s+\((\d+)\s+skills?\)\s*$")
RE_CARD = re.compile(r"^>\s+\*\*(.+?)\*\*\s+—\s+(.+?)\s+Regras:\s+(.+?)\s+Stack:\s+(.+)$")
INDICE_JSON = REPO_ROOT / "data" / "skills_index.json"
GLOSSARIO_JSON = REPO_ROOT / "data" / "skills_glossario.json"
# caminho do glossário DENTRO do plugin Bioinfo (cfg.plugin_dir)
GLOSSARIO_NO_PLUGIN = Path("data") / "glossario_pt_en.json"


def _card(linha: str) -> dict[str, str]:
    """Card de domínio → quando usar / regras duras / stack. Linha fora do padrão
    não se perde: entra inteira em `quando`."""
    m = RE_CARD.match(linha)
    if not m:
        return {"titulo": "", "quando": linha.lstrip("> ").strip(), "regras": "", "stack": ""}
    titulo, quando, regras, stack = (parte.strip() for parte in m.groups())
    return {"titulo": titulo, "quando": quando, "regras": regras, "stack": stack}


def parse_indice(texto: str) -> dict[str, Any]:
    skills: list[dict[str, Any]] = []
    dominios: dict[str, int] = {}
    cards: dict[str, list[dict[str, str]]] = {}
    dominio_atual: str | None = None
    for linha in texto.splitlines():
        m = RE_DOMINIO.match(linha.strip())
        if m:
            dominio_atual = m.group(1)
            dominios[dominio_atual] = int(m.group(2))
            continue
        if dominio_atual is None:
            continue
        if linha.startswith(">"):
            cards.setdefault(dominio_atual, []).append(_card(linha))
            continue
        if not linha.lstrip().startswith("|"):
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
        "cards": cards,
        "skills": skills,
    }


def sha_do_glossario(termos: dict[str, Any], stopwords: list[str]) -> str:
    """Impressão digital do CONTEÚDO do glossário, não dos bytes do arquivo: reformatar
    o JSON ou trocar CRLF por LF não é mudança de glossário e não pode fazer o doctor
    acusar cópia atrasada. O plugin calcula a mesma coisa do lado dele
    (`bioinfo_omica.py`), então a forma canônica abaixo é contrato entre os dois."""
    canonico = json.dumps({"stopwords": sorted(stopwords), "termos": termos},
                          sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def ler_glossario_do_plugin(fonte: Path) -> tuple[dict[str, Any], list[str]]:
    """(termos, stopwords) do arquivo do plugin. Levanta ValueError se não for legível."""
    try:
        doc = json.loads(fonte.read_text(encoding="utf-8"))
        return dict(doc["termos"]), list(doc.get("stopwords", []))
    except (OSError, KeyError, TypeError) as e:
        raise ValueError(f"{e.__class__.__name__}: {e}") from e


def copiar_glossario() -> str:
    """Copia o glossário de busca PT→EN do plugin Bioinfo para data/.

    A fonte é UMA (o plugin, que roteia as mesmas 815 skills no assistente de
    código do autor); aqui fica uma cópia com o sha da origem, porque a plataforma
    roda em container e no CI, onde o plugin não existe. O doctor compara os sha
    e avisa quando a cópia fica para trás. Sem a origem à vista, a cópia que já
    existe é mantida — reindexar dentro do container não pode apagar a busca em
    português. Devolve a linha de relato.
    """
    cfg = get_config()
    fonte = (cfg.plugin_dir / GLOSSARIO_NO_PLUGIN) if cfg.plugin_dir else None
    if fonte is None or not fonte.exists():
        estado = "cópia existente mantida" if GLOSSARIO_JSON.exists() else "nenhuma cópia ainda"
        return f"glossário PT→EN: plugin Bioinfo não visível daqui — {estado}"
    try:
        termos, stopwords = ler_glossario_do_plugin(fonte)
    except ValueError as e:
        return f"AVISO: glossário PT→EN ilegível em {fonte} ({e}) — cópia não atualizada"
    sha = sha_do_glossario(termos, stopwords)
    if GLOSSARIO_JSON.exists():
        try:
            atual = json.loads(GLOSSARIO_JSON.read_text(encoding="utf-8"))
            if atual.get("_meta", {}).get("sha256") == sha:
                return f"glossário PT→EN: cópia já em dia ({len(termos)} termos)"
        except ValueError:
            pass  # cópia corrompida: regravar
    copia = {
        "_meta": {
            "aviso": "CÓPIA gerada por `python -m omica reindex`. Edite a origem, nunca este arquivo.",
            "origem": f"plugin Bioinfo — {GLOSSARIO_NO_PLUGIN.as_posix()}",
            "sha256": sha,
            "copiado_em": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "n_termos": len(termos),
        },
        "stopwords": stopwords,
        "termos": termos,
    }
    GLOSSARIO_JSON.parent.mkdir(parents=True, exist_ok=True)
    GLOSSARIO_JSON.write_text(json.dumps(copia, indent=1, ensure_ascii=False), encoding="utf-8")
    return f"glossário PT→EN: {len(termos)} termos copiados → {GLOSSARIO_JSON}"


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
    print(copiar_glossario())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
