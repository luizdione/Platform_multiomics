# 🧬 ômica — plataforma localhost unificada de ômicas

Genômica, epigenômica e transcriptômica bovina num só lugar — **local, sem nuvem,
sem LLM no caminho crítico**, com camada didática em PT-BR para o autor e alunos
do IFRJ. Sucessora das plataformas `genomes_platform` e `epigenome_platform`
(congeladas em 2026-09-02, tag `archive/2026-09-final`).

## Início rápido

```bash
copy .env.example .env
docker compose up -d
```

Interface: <http://localhost:8710> · API docs: <http://localhost:8710/api/docs>
· ou simplesmente rode `abrir_omica.bat` (espera o Docker, sobe tudo e abre o navegador).

Diagnóstico e operação:

```bash
python -m omica doctor            # checa Docker, dados, RAM, manifests, skills...
python -m omica reindex           # regenera data/skills_index.json (815 skills)
docker compose run --rm web pytest -q   # testes dentro do container (mesmo lock)
```

## Arquitetura (Fase 1)

```
navegador → omica-web (FastAPI+Jinja2, :8710) → fila RQ/Redis → omica-worker
                                     ↘ fallback de thread local (sem Redis)
Estado de job = arquivos (status.json + job.log) em E:\data\omica\work\jobs\
Resultados    = arquivos em E:\data\omica\results\ (a UI SÓ mostra o que lê daí)
```

- **Código**: `C:\github\omica` (este repo). **Dados pesados**: `E:\data\omica`
  (+ legados em `E:\data`, ex.: VCFs 1000 Bull). **Memória/relatórios leves**:
  `OneDrive\Data_Science\Projetos\omica`. Código nunca no OneDrive (corrupção
  por null-bytes já documentada).
- **Módulos** são plugins: `modules/<id>/manifest.yaml` (fases, etapas, scripts,
  `results_spec`) validado por schema no boot. Manifest inválido → módulo não
  carrega e o erro aparece em `/saude` e no doctor.

## Regras do projeto (anti-lições da auditoria de 2026-09-01)

1. **UMA config**: tudo passa por `omica/config.py` (ambiente > `.env` > defaults).
   PR com segundo arquivo de paths é rejeitado.
2. **Zero módulo morto**: código só entra com smoke test que roda (`pytest -m smoke`).
3. **Lockfiles**: faixas no `pyproject.toml`, lock pinado no `requirements.txt`
   (containers R terão `renv.lock`).
4. **Nenhum número científico hardcoded na UI** — sempre lido de arquivo de
   resultado via `omica/results.py`.
5. **Commits atômicos frequentes**; migração de script legado = cópia curada
   1-a-1 com teste (proibido xcopy em massa).
6. **Manuscrito/IP nunca neste repo** (barreira no `.gitignore`).
7. **Guarda de RAM**: nenhum job entra se o sistema passar de 80% de uso.
8. **CI desde o commit 1**: manifest inválido, teste vermelho ou referência
   quebrada bloqueiam merge.

## Roadmap (plano aprovado em 2026-09-01)

| Fase | Conteúdo | Status |
|---|---|---|
| 0 | Congelar plataformas antigas (tag `archive/2026-09-final`) | ✅ 2026-09-02 |
| 1 | Esqueleto: casca web, fila, guarda de RAM, doctor, CI | ✅ 0.1.0 |
| 2 | Genômica: bcftools/SnpEff/VEP/plink2 (migração curada) | ⏳ |
| 3 | Epigenômica: methylKit/HOMER + painel 108 versionado | ⏳ |
| 4 | Transcriptômica: SRA + nf-core/rnaseq + DESeq2 | ⏳ |
| 5 | ML (RF/XGBoost/SHAP), /skills (815), doctor completo, modo aula | ⏳ |
