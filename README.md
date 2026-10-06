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
python -m omica reindex           # regenera data/skills_index.json (815 skills) + glossário PT→EN
docker compose run --rm web pytest -q   # testes dentro do container (mesmo lock)
```

## Arquitetura (v0.4)

```
navegador → omica-web (FastAPI+Jinja2, :8710) → Redis → FILA POR RUNTIME:
  "omica"   → omica-worker          (jobs leves, Python)
  "bioinfo" → omica-worker-bioinfo  (bcftools/samtools/SnpEff/plink2 no PATH)
  "vep"     → omica-worker-vep      (VEP 113 + cache PINADO release 110 ARS-UCD1.2)
  "methyl"  → omica-worker-methyl   (R 4.2 + volume epigenoma_r_lib: methylKit etc.)
Sem docker.sock em worker nenhum: o container do worker É a imagem de ferramentas.
Fallback sem Redis: thread local (só fila "omica"; filas especializadas recusam).

Workflows = passos encadeados no manifest (validação output→input no boot) →
RQ depends_on; selo "✅ testado" só após validação real de ponta a ponta.
🎓 Professor Ômico (/professor): frase em PT → modelo local (Ollama/LILO,
qwen3:14b) propõe DO CATÁLOGO → você executa com um clique. Zero LLM no
caminho crítico.

Estado de job = arquivos (status.json + job.log) em E:\data\omica\work\jobs\
Resultados    = arquivos em E:\data\omica\results\ (a UI SÓ mostra o que lê daí;
                figuras servidas por /resultados/<rel> com guarda de caminho)
```

- **Código**: `C:\github\omica` (este repo). **Dados pesados**: `E:\data\omica`
  (+ legados em `E:\data`, ex.: VCFs 1000 Bull). **Memória/relatórios leves**:
  `OneDrive\Data_Science\Projetos\omica`. Código nunca no OneDrive (corrupção
  por null-bytes já documentada).
- **Módulos** são plugins: `modules/<id>/manifest.yaml` (fases, etapas, scripts,
  `results_spec`) validado por schema no boot. Manifest inválido → módulo não
  carrega e o erro aparece em `/saude` e no doctor.

## Biblioteca de skills e plugin Bioinfo (v1.0)

A aba **/skills** e o plugin **Bioinfo** (`C:\github\bioinfo-plugin`, o roteador das
mesmas 815 skills no assistente de código) deixaram de ser duas ilhas:

```
INDICE_CONSOLIDADO.md ──reindex──► data/skills_index.json   (skills + cards de domínio)
plugin: glossario_pt_en.json ──reindex──► data/skills_glossario.json   (cópia com sha)
                                                  │
        omica/skills/busca.py ◄───────────────────┘   busca no servidor, PT/EN
          ├─ GET /api/skills  → página /skills (?q=  ?dominio=)
          └─ Professor: pedido fora do catálogo → receitas da biblioteca (sem LLM)
modules/<id>/manifest.yaml: skills_dominios ──► "skills relacionadas" no módulo
                                             └► lido pelo plugin: catálogo vem ANTES da biblioteca
```

- **Busca em português**: o índice é em inglês; o glossário traduz (`metilação` →
  `methylation`), ignora palavras de ligação e aceita a frase inteira. Sem casamento
  total, ordena pelo termo mais raro. Fonte única do glossário = o plugin; aqui fica
  a cópia, e o doctor (`Ponte Bioinfo (skills)`) acusa quando ela fica para trás.
- **Elo módulo → domínio**: cada manifest declara `skills_dominios`. Domínio que a
  biblioteca não tem reprova o doctor.
- **Do lado do plugin**: `bioinfo_omica.py` lê estes manifests, os `status.json` e os
  resultados (funciona com a plataforma desligada) e só submete job pela mesma rota
  determinística dos módulos, depois de confirmação — a regra "quem propõe não
  executa" continua valendo.
- `omica/skills/busca.py` é **gêmea** de `bioinfo_find.py` do plugin (um roda no
  container, o outro no host). Quem mudar a regra de casamento muda nos dois; o
  `bioinfo_doctor.py` do plugin compara os resultados.

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
| 2 | Genômica: extração/SnpEff/frequências (0.2.0) + painéis via GTF local, integridade, **Fst Angus×Nelore plink2** (0.3.0) + **VEP** cache pinado 110 (0.4.0) | ✅ 0.4.0 |
| — | Workflows encadeados (selo "testado") + 🎓 **Professor Ômico** (Ollama/LILO) | ✅ 0.3.0 |
| 3 | Epigenômica: **painel 108 versionado canônico**, DMCs methylKit, interseção, metaperfil TSS (HOMER/MOFA2 `planejado` c/ motivo) | ✅ núcleo 0.4.0 |
| 4 | Transcriptômica: SRA (demo real 25k spots), FastQC/MultiQC, Salmon (índice bovino em refs, biocontainer 1.10.3) e **DESeq2 granulosa 4×4 real** (nf-core DinD `planejado` c/ motivo: acervo legado de 34 amostras reaproveitado) | ✅ 0.5.0 |
| 5 | ML (nested CV+SHAP+painel mínimo, **bal.acc 0,948** Angus×Nelore real — XGBoost com o painel COMPLETO de 311 variantes; o painel mínimo de 50 dá 0,939), **/skills** (815 offline), doctor v2 + /diagnostico, glossário fundido (40 termos), **modo aula** executável, gate de publicação | ✅ 0.6.0 |
| — | Backlog do plano: HOMER (motifs), MOFA2 (promotor × corpo), robustez ML A1/A2/A3 | ✅ 0.7.0 |
| — | **Auditoria adversarial** (12 dimensões, 95 achados) → correções científicas, selo com lastro, gate de credenciais | ✅ 0.8.0 |
| — | **Ponte com o plugin Bioinfo**: busca PT/EN em /skills (servidor), guias de domínio, elo módulo→domínio no manifest, Professor aponta a biblioteca quando o catálogo não cobre | ✅ 1.0.0 |

**Plano de 2026-09-01: fases 0–5 e backlog completos.** Pendência declarada com
motivo no manifest: nf-core DinD (o acervo legado de 34 amostras é reaproveitado).

Se o Docker Desktop cair com erro de socket/config após queda suja da máquina:
`tools\consertar_docker.ps1` (diagnóstico completo na memória do ambiente).

Workflows encadeados e selados: `genomica/wf_painel_demo`,
`epigenomica/wf_metilacao_demo`, `epigenomica/wf_mofa_demo`,
`transcriptomica/wf_de_granulosa` e `ml/wf_ml_aims` — visíveis na vitrine do
Professor Ômico. Demos usam DADOS REAIS versionados (fatia do 1000 Bull Genomes
Run 8 e coverages Bismark do Zenodo do autor).

> ✅ **Selos recarimbados em 2026-10-06.** As correções científicas da auditoria
> (v0.8.0) tinham vencido 4 dos 5 selos. Os 4 workflows e os 5 scripts avulsos
> cujo resultado publicado era mais velho que o script foram re-executados na
> plataforma real (fila RQ, workers de container) e só então
> `python -m omica selos --gravar` recarimbou o `hash_scripts`: os números em
> `results/` são os dos scripts atuais. Se um script mudar de novo, o doctor volta
> a acusar `Selos de workflow: N vencido(s) — re-executar` — é o ciclo previsto.
