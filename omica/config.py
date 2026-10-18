"""config.py — a ÚNICA fonte de configuração da plataforma.

Anti-lição nº 1 da auditoria (2026-09-01): a genomes_platform tinha 3 arquivos de
config divergentes. Aqui TODO caminho e TODA opção passam por este módulo, que lê
exclusivamente variáveis de ambiente com fallback no arquivo .env da raiz do repo.
Nenhum outro módulo pode ler os.environ diretamente nem hardcodar paths.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_DEFAULTS = {
    "OMICA_PORT": "8710",
    "OMICA_DATA": "/data/omica" if os.environ.get("OMICA_IN_DOCKER") else r"E:\data\omica",
    "GENOME_DATA": "/data" if os.environ.get("OMICA_IN_DOCKER") else r"E:\data",
    "OMICA_MAX_RAM_GB": "24",
    "OMICA_RAM_MAX_PCT": "80",
    "SKILLS_DIR": "/skills" if os.environ.get("OMICA_IN_DOCKER") else r"C:\Users\luizd\.claude\skills_bioinfo",
    "OMICA_REDIS_URL": "",
    "OMICA_LANG": "pt",
    # Professor Ômico: motor local (Ollama, o mesmo do LILO). Nunca no caminho
    # crítico — o LLM só PROPÕE planos do catálogo; a execução é determinística.
    "OMICA_OLLAMA_URL": "http://host.docker.internal:11434" if os.environ.get("OMICA_IN_DOCKER") else "http://localhost:11434",
    "OMICA_OLLAMA_MODEL": "qwen3:14b",
    # Plugin Bioinfo: roteia as MESMAS 815 skills no assistente de código do autor e é a
    # fonte única do glossário de busca PT→EN. Só o host o enxerga (não é montado nos
    # containers): lá fica vazio e a plataforma usa a cópia que o reindex deixou em data/.
    "BIOINFO_PLUGIN_DIR": "" if os.environ.get("OMICA_IN_DOCKER") else r"C:\github\bioinfo-plugin",
    # API JSON v1 (orquestração externa, ex.: n8n). Vazio = API DESLIGADA (responde 503):
    # falha fechada. O valor real fica só no .env (gitignored) — nunca no compose
    # `environment:` (apareceria em `docker inspect`) nem no .env.example.
    "OMICA_API_TOKEN": "",
    # Interruptor da GPU do autor (convenção do LILO/ollama-delegate). Toda chamada a LLM
    # local passa por ele. No container chega montado SOMENTE LEITURA em /gpu_switch.
    "OMICA_GPU_SWITCH": "/gpu_switch/switch.json" if os.environ.get("OMICA_IN_DOCKER")
                        else str(Path.home() / ".ollama_delegate" / "switch.json"),
}


def _ler_dotenv(caminho: Path) -> dict[str, str]:
    """Parser mínimo de .env (KEY=VALUE, ignora comentários). Sem dependência externa."""
    valores: dict[str, str] = {}
    if not caminho.exists():
        return valores
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        valores[chave.strip()] = valor.strip().strip('"').strip("'")
    return valores


def _obter(chave: str, dotenv: dict[str, str]) -> str:
    # Precedência: variável de ambiente real > .env > default.
    return os.environ.get(chave) or dotenv.get(chave) or _DEFAULTS[chave]


@dataclass(frozen=True)
class Config:
    porta: int
    data_dir: Path          # E:\data\omica (host) ou /data/omica (container)
    genome_data: Path       # raiz dos dados pesados legados (E:\data)
    max_ram_gb: float       # orçamento de RAM para a soma dos jobs ativos
    ram_max_pct: int        # uso total do sistema acima disso = não lançar job
    skills_dir: Path        # biblioteca skills_bioinfo (read-only)
    redis_url: str          # vazio = fallback de thread local
    lang: str
    ollama_url: str         # motor do Professor Ômico (Ollama local, mesmo do LILO)
    ollama_model: str
    plugin_dir: Path | None  # plugin Bioinfo no host; None onde ele não existe (container, CI)
    # repr=False: o pytest imprime `cfg = Config(...)` quando um teste falha — o token
    # iria parar no log.
    api_token: str = field(default="", repr=False)
    gpu_switch: Path = field(default_factory=lambda: Path(_DEFAULTS["OMICA_GPU_SWITCH"]))
    in_docker: bool = field(default_factory=lambda: bool(os.environ.get("OMICA_IN_DOCKER")))

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def refs_dir(self) -> Path:
        return self.data_dir / "refs"

    @property
    def results_dir(self) -> Path:
        return self.data_dir / "results"

    @property
    def work_dir(self) -> Path:
        return self.data_dir / "work"

    @property
    def jobs_dir(self) -> Path:
        return self.work_dir / "jobs"

    def ensure_dirs(self) -> None:
        for d in (self.raw_dir, self.refs_dir, self.results_dir, self.work_dir, self.jobs_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_config() -> Config:
    dotenv = _ler_dotenv(REPO_ROOT / ".env")
    return Config(
        porta=int(_obter("OMICA_PORT", dotenv)),
        data_dir=Path(_obter("OMICA_DATA", dotenv)),
        genome_data=Path(_obter("GENOME_DATA", dotenv)),
        max_ram_gb=float(_obter("OMICA_MAX_RAM_GB", dotenv)),
        ram_max_pct=int(_obter("OMICA_RAM_MAX_PCT", dotenv)),
        skills_dir=Path(_obter("SKILLS_DIR", dotenv)),
        redis_url=_obter("OMICA_REDIS_URL", dotenv),
        lang=_obter("OMICA_LANG", dotenv),
        ollama_url=_obter("OMICA_OLLAMA_URL", dotenv),
        ollama_model=_obter("OMICA_OLLAMA_MODEL", dotenv),
        plugin_dir=Path(plugin) if (plugin := _obter("BIOINFO_PLUGIN_DIR", dotenv)) else None,
        api_token=_obter("OMICA_API_TOKEN", dotenv),
        gpu_switch=Path(_obter("OMICA_GPU_SWITCH", dotenv)),
    )


def reset_config_cache() -> None:
    """Para testes: força reler o ambiente na próxima chamada de get_config()."""
    get_config.cache_clear()
