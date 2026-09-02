# Changelog — plataforma ômica

Formato: [Keep a Changelog](https://keepachangelog.com/pt-BR/), versão em `VERSION`.

## [0.3.0] — 2026-09-02 · Workflows encadeados + Professor Ômico + Fase 2 (restante)

### Adicionado — motor de workflows (determinístico, zero LLM)
- `workflows:` no manifest (`WorkflowSpec`): passos são scripts do módulo e a
  validação de boot EXIGE encadeamento real (todo passo N>1 consome output de
  passo anterior; manifest solto é rejeitado). Selo `testado_em`/`testado_por`
  reservado a workflows validados de ponta a ponta de verdade.
- Cadeia na fila: `enqueue_cadeia` usa `depends_on` do RQ (passo N só roda se o
  N-1 concluir) com fallback sequencial em thread; run agregado em
  `work/workflows/<id>.json` e página `/workflows/<id>` com auto-refresh.
- `wf_painel_demo` selado (extração → frequências → anotação; testado 2026-09-02
  pela fila real).

### Adicionado — Professor Ômico (aba 🎓)
- Agente didático LOCAL sobre o Ollama (motor do LILO; `OMICA_OLLAMA_URL/MODEL`,
  default qwen3:14b): frase humana (+ arquivo opcional) → o modelo recebe o
  CATÁLOGO dos manifests e devolve um plano JSON escolhendo DO catálogo; o plano
  é validado contra o registry e só roda com o clique de Executar, pelas mesmas
  rotas determinísticas. Sem Ollama, a aba segue útil (vitrine + instrução).
- Vitrine "workflows prontos e testados nesta máquina" (só selados).

### Adicionado — Fase 2 (restante)
- **Params editáveis por script** (`ScriptSpec.params` → formulário na UI),
  sanitizados no dispatch (charset estrito; sem shell no caminho).
- `coordenadas_painel.py`: painel de símbolos → BED ±flanco via **GTF local do
  SnpEff** — correção de fonte: o Ensembl REST atual serve ARS-UCD2.0
  (verificado ao vivo), incompatível com os VCFs Run 8 (ARS-UCD1.2). Painel real
  versionado: `data/paineis/termotolerancia.txt` (14 genes dos papers do autor).
- `integridade_vcf.py`: saúde dos 31 VCFs/BCFs do 1000BG (índice/header/registro).
- `fst_racas.py`: **Fst Hudson Angus(112)×Nelore(385) com plink2** sobre o BCF
  real — validado: Fst global 0,283 em 122.372 variantes (BTA14:1–2,5 Mb).
- plink2 na imagem omica-bioinfo; fluxograma SVG do módulo gerado do manifest
  (`omica/web/flux.py`) — impossível divergir do registry.
- VEP registrado como etapa `planejado` com motivo técnico (imagem/worker
  dedicados + cache bovino ~2 GB) — entra na próxima leva.

### Testes
- 41 passed + 5 smoke no container bioinfo (coordenadas, integridade, Fst real,
  extração+frequências, anotação SnpEff).

## [0.2.0] — 2026-09-02 · Fase 2 (genômica — núcleo)

### Adicionado
- **Execução científica sem docker.sock** (anti-lição nº 11): fila RQ por runtime —
  `image:omica-bioinfo` → fila `bioinfo`, consumida pelo serviço `worker-bioinfo`
  cujo container É a imagem de ferramentas (`omica/jobs/dispatch.py`,
  `script_job.py`; `queue.enqueue` ganhou `fila` e recusa fila especializada sem
  Redis com mensagem acionável).
- Imagem **omica-bioinfo** (`docker/Dockerfile.bioinfo`): herda a receita da
  genomes_platform (Java 21 + samtools/bcftools/tabix + SnpEff) corrigindo o bug
  de case `/opt/snpEff`; banco custom fica fora da imagem (`E:\data\snpeff_data`).
- Módulo genômica com 3 análises reais (migração curada): extração por painel BED,
  frequências AF/MAF (INFO do Run 8) e anotação SnpEff ARS-UCD1.3.113 — com
  registro do genome via `-configOption` (o endpoint legado nunca rodou: o config
  não tinha a entrada, verificado em 2026-09-02).
- **Demo real versionado**: fatia do 1000 Bull Genomes Run 8 (BTA14:1–2,5 Mb,
  122.372 variantes, sem genótipos, 6,8 MB) + painel BED de 3 janelas (inclui a
  janela do DGAT1).
- UI do módulo: cards de análises com ▶ Rodar (runtime/memória/tempo) e seção
  **Resultados lidos do disco agora** (JSON → tabela chave/valor; TSV → primeiras
  linhas via `results.carregar_tabela`).
- Smoke tests (`pytest -m smoke tests/test_scripts_smoke.py` no container
  bioinfo): extração+frequências e anotação SnpEff sobre o demo — 2/2 verdes.

### Corrigido
- Aceite ponta a ponta validado pela fila real: extração (52.881 variantes nas 3
  janelas) → frequências → anotação (41 efeitos distintos) via POST na UI.

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
