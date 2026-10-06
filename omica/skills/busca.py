"""busca.py — busca determinística nas skills da biblioteca, em português e inglês.

O índice das 815 skills é em INGLÊS; o autor e os alunos do IFRJ digitam em
PORTUGUÊS. A busca antiga (substring no navegador) devolvia zero para
"metilação" e exigia a frase inteira contígua. Esta roda no servidor, uma vez,
para a página /skills e para o Professor Ômico:

  - sem acento e sem caixa; palavras de ligação ("de", "quero", "meu") saem;
  - cada termo vale por si OU por sua tradução (data/skills_glossario.json);
  - todos os termos precisam casar (AND); se nenhuma skill casa tudo, entra o
    modo parcial, ordenado pelo termo mais raro — é o que dá resposta a uma
    frase humana inteira;
  - teto de MAX_TERMOS termos e MAX_CARACTERES caracteres (o mesmo do plugin): um
    texto longo colado por engano não custa a busca inteira.

Zero LLM, zero rede. Só stdlib.

GÊMEA: o plugin Bioinfo tem o mesmo algoritmo em `scripts/bioinfo_find.py`
(lá ele roda no host, para o assistente de código; aqui roda no container, para
a UI — um não importa o outro). Mudou a regra de casamento aqui, mude lá: o
`bioinfo_doctor.py` do plugin compara as duas e acusa a divergência.
"""
from __future__ import annotations

import functools
import json
import math
import re
import unicodedata
from pathlib import Path
from typing import Any, Callable, Optional

from omica.skills.build_index import GLOSSARIO_JSON, INDICE_JSON

# Teto da consulta — o MESMO do plugin (`bioinfo_find.py`), para a busca continuar gêmea.
# Sem ele, um trecho de Métodos colado no Professor (centenas de siglas) levava 40–80 s
# (um regex por termo × skill) e, na rota do Professor, parava o servidor inteiro.
MAX_TERMOS = 20
MAX_CARACTERES = 2000

LIMITE_PADRAO = 200

# arquivo → (mtime, conteúdo). Os dois JSON são relidos quando mudam em disco
# (reindex vale sem reiniciar o uvicorn), não a cada tecla digitada na busca.
_cache: dict[Path, tuple[float, Any]] = {}


def _ler_json(caminho: Path) -> Optional[Any]:
    try:
        mtime = caminho.stat().st_mtime
    except OSError:
        return None
    guardado = _cache.get(caminho)
    if guardado is None or guardado[0] != mtime:
        try:
            _cache[caminho] = (mtime, json.loads(caminho.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return None
    return _cache[caminho][1]


def carregar_indice() -> Optional[dict[str, Any]]:
    """data/skills_index.json, ou None se ainda não foi gerado (python -m omica reindex)."""
    return _ler_json(INDICE_JSON)


def normalizar(texto: str) -> str:
    """Minúsculo e sem acento."""
    return "".join(c for c in unicodedata.normalize("NFD", texto.lower())
                   if unicodedata.category(c) != "Mn")


def carregar_glossario() -> tuple[dict[str, list[str]], set[str]]:
    """(termos PT→radicais EN, palavras de ligação). Sem a cópia em data/, a busca
    continua funcionando — só que literal, em inglês."""
    doc = _ler_json(GLOSSARIO_JSON) or {}
    termos = {normalizar(k): list(v) for k, v in doc.get("termos", {}).items()}
    return termos, {normalizar(p) for p in doc.get("stopwords", [])}


@functools.lru_cache(maxsize=4096)
def _padrao_palavra(parte: str) -> re.Pattern[str]:
    # Compilado UMA vez por sigla: com centenas de termos o cache interno do `re` (512
    # entradas) estourava e cada par termo × skill recompilava — 42 s numa busca.
    return re.compile(r"(?<![a-z0-9])" + re.escape(parte) + r"(?![a-z0-9])")


def _parte_casa(parte: str, alvo: str) -> bool:
    # Sigla curta como substring casa dentro de outra palavra ("ms" em "systems"):
    # até 3 caracteres, só como palavra inteira.
    if len(parte) <= 3:
        return _padrao_palavra(parte).search(alvo) is not None
    return parte in alvo


def variante_casa(variante: str, alvo: str) -> bool:
    """Radical simples casa por substring; "a+b" exige os dois na mesma skill."""
    return all(_parte_casa(parte, alvo) for parte in variante.split("+"))


def separar_termos(frase: str, stopwords: set[str]) -> tuple[list[str], list[str], bool]:
    """(termos, ignorados, truncado). Tira a pontuação colada na palavra ("lotes,") e
    repetições. Acima de MAX_CARACTERES, ou do MAX_TERMOS-ésimo termo, o resto não é
    lido e `truncado` avisa a UI."""
    termos: list[str] = []
    ignorados: list[str] = []
    vistos: set[str] = set()
    truncado = len(frase) > MAX_CARACTERES
    for palavra in frase[:MAX_CARACTERES].split():
        palavra = re.sub(r"^\W+|\W+$", "", palavra)
        chave = normalizar(palavra)
        if not chave or chave in vistos:
            continue
        if chave in stopwords:
            vistos.add(chave)
            ignorados.append(palavra)
            continue
        if len(termos) >= MAX_TERMOS:
            truncado = True
            break
        vistos.add(chave)
        termos.append(palavra)
    return termos, ignorados, truncado


def alvo_da_skill(s: dict[str, Any]) -> str:
    """O texto em que a busca casa: id, descrição, stack, pasta (traz o domínio) e origem."""
    return normalizar(" ".join((s["id"], s["descricao"], ", ".join(s["stack"]), s["pasta"], s["origem"])))


def buscar_em(itens: list[dict[str, Any]], frase: str, glossario: dict[str, list[str]],
              stopwords: set[str], limite: int = LIMITE_PADRAO,
              alvo_fn: Callable[[dict[str, Any]], str] = alvo_da_skill,
              nome_fn: Callable[[dict[str, Any]], str] = lambda s: normalizar(s["id"]),
              ) -> dict[str, Any]:
    """Núcleo sem I/O (testável com fixture). `limite` 0 = todos.

    Modo "todos": a ordem é "quem traz mais termos no próprio nome primeiro" — o
    índice vem agrupado por domínio em ordem alfabética, e sem isto "deseq2" (71
    skills) mostraria as primeiras do alfabeto, não as que são sobre DESeq2.
    Modo "parcial": soma de IDF dos termos casados; termo raro (scvi) pesa mais
    que termo comum (cell), então o topo é o que a frase tem de específico.
    """
    termos, ignorados, truncado = separar_termos(frase, stopwords)
    relato: dict[str, Any] = {
        "modo": "todos", "termos": termos, "ignorados": ignorados, "truncado": truncado,
        "sem_casar": [],
        "traducao": [{"termo": t, "para": glossario[normalizar(t)]}
                     for t in termos if normalizar(t) in glossario],
    }

    def fechar(achados: list[dict[str, Any]]) -> dict[str, Any]:
        mostrados = achados[:limite] if limite else achados
        return {**relato, "total": len(achados), "mostrando": len(mostrados), "resultados": mostrados}

    if not termos:
        return fechar(list(itens))
    alts = [sorted({normalizar(t), *glossario.get(normalizar(t), [])}) for t in termos]
    alvos = [alvo_fn(s) for s in itens]
    casam = [[any(variante_casa(v, alvo) for v in vs) for vs in alts] for alvo in alvos]
    df = [sum(1 for linha in casam if linha[j]) for j in range(len(termos))]
    relato["sem_casar"] = [t for t, n in zip(termos, df) if n == 0]
    todos = [s for s, linha in zip(itens, casam) if all(linha)]
    if todos or len(termos) < 2:
        def no_nome(s: dict[str, Any]) -> int:
            nome = nome_fn(s)
            return sum(1 for vs in alts if any(variante_casa(v, nome) for v in vs))
        return fechar(sorted(todos, key=lambda s: -no_nome(s)))  # estável: empate mantém o índice
    n = len(itens)
    idf = [math.log((n + 1) / (d + 1)) + 1 if d else 0.0 for d in df]
    parciais = []
    for pos, (s, linha) in enumerate(zip(itens, casam)):
        pontos = sum(peso for peso, ok in zip(idf, linha) if ok)
        if pontos > 0:
            parciais.append((-pontos, pos, {
                **s, "casou": [t for t, ok in zip(termos, linha) if ok], "pontos": round(pontos, 3)}))
    parciais.sort(key=lambda x: (x[0], x[1]))
    relato["modo"] = "parcial"
    return fechar([p[2] for p in parciais])


def buscar(frase: str, dominio: str = "", limite: int = LIMITE_PADRAO) -> dict[str, Any]:
    """Busca no índice versionado. Sem índice, devolve resultado vazio (a página
    de skills já redireciona nesse caso)."""
    indice = carregar_indice()
    skills = indice["skills"] if indice else []
    if dominio:
        skills = [s for s in skills if s["dominio"].lower() == dominio.lower()]
    glossario, stopwords = carregar_glossario()
    return buscar_em(skills, frase, glossario, stopwords, limite)
