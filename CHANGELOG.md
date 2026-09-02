# Changelog — plataforma ômica

Formato: [Keep a Changelog](https://keepachangelog.com/pt-BR/), versão em `VERSION`.

## [0.1.0] — 2026-09-02 · Fase 1 (esqueleto + casca)

### Adicionado
- Casca da plataforma: FastAPI + Jinja2 server-rendered (PT/EN via `omica/web/i18n.py`,
  herdado da genomes_platform), porta **8710**, um único entry point (`python -m omica serve`).
- Fila de jobs com dois backends (padrão herdado do bonder_platform): RQ+Redis no
  compose, fallback de thread local sem Redis; estado por arquivos (`status.json` +
  `job.log`) em volume compartilhado; console ao vivo via SSE.
- **Guarda de RAM** (`omica/jobs/guarda_ram.py`): recusa job se o sistema passar de
  80% de uso ou se a soma de `mem_gb` dos jobs ativos estourar `OMICA_MAX_RAM_GB`.
- Registry de módulos (`omica/registry.py`): manifests YAML validados por pydantic
  no boot, com verificação de referências quebradas (classe de bug das plataformas
  antigas); 3 módulos declarados (genomica, epigenomica, transcriptomica) com fases
  `planejado` — a UI os mostra honestamente como "em construção".
- Job demo de ponta a ponta (fila → execução → log → arquivo de resultado → UI).
- `python -m omica doctor` (v0): Docker, dados, RAM, manifests, Redis, skills,
  porta, scan de null-bytes (herança do watchdog anti-corrupção do OneDrive).
- Parser da biblioteca de skills (`omica/skills/build_index.py`):
  `INDICE_CONSOLIDADO.md` → `data/skills_index.json` (consumo offline; página
  /skills chega na fase 5).
- Camada didática inicial: `didatica/ajuda/01_visao_geral.md` renderizada em /ajuda.
- Infra: docker-compose (web+worker+redis, redis sem porta no host), lock pinado
  `requirements.txt` (faixas no `pyproject.toml`), CI GitHub Actions desde o
  commit 1, `.claude/launch.json`, `abrir_omica.bat`.

### Decisões registradas
- Console/SSE com JS próprio mínimo (EventSource) em vez de htmx: zero asset
  externo, funciona offline em sala de aula; htmx pode entrar vendorizado depois.
- `Dockerfile.worker` separado só entra na fase de ML (mesma imagem do web por ora).
- Repositórios antigos congelados em 2026-09-02 com tag `archive/2026-09-final`
  (genomes_platform `f736a91`, epigenome_platform `5876dfe`).
