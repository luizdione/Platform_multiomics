---
titulo: Visão geral da plataforma ômica
nivel: iniciante
modulo: core
tempo_estimado: 10 min
roteiro_aula: false
---

# 01 · Visão geral da plataforma ômica

A **ômica** é uma plataforma localhost (roda inteira no seu computador, sem nuvem)
que reúne três abordagens de análise do genoma num só lugar:

| Módulo | Pergunta biológica que responde |
|---|---|
| 🧬 **Genômica** | Quais variantes (SNPs) existem nos genes de interesse e o que elas mudam na proteína? |
| 🧭 **Epigenômica** | Quais regiões do genoma estão metiladas de forma diferente entre grupos (ex.: indicino × taurino)? |
| 🧵 **Transcriptômica** | Quais genes estão mais ou menos expressos em cada condição (ex.: estresse térmico)? |

## Como a plataforma funciona (o caminho de todo job)

1. Você escolhe uma análise na interface (ou um passo de tutorial no modo aula).
2. A plataforma confere se há **memória RAM suficiente** (guarda de RAM) — se não
   houver, ela recusa com uma mensagem clara em vez de travar o computador.
3. O job entra numa **fila** e é executado por um trabalhador (worker) em
   container Docker, usando a ferramenta científica certa (bcftools, methylKit,
   DESeq2...).
4. O **console ao vivo** mostra o progresso linha a linha.
5. O resultado vira um **arquivo em disco** (`E:\data\omica\results\...`) — e a
   interface só mostra números lidos desses arquivos. Se o arquivo não existe, a
   plataforma diz "sem dados". Ela nunca inventa número.

## De onde a plataforma veio

A ômica substitui duas plataformas anteriores do laboratório (GenOmics e
GenOmics Epigenoma), congeladas em 2026-09-02. A auditoria delas gerou as regras
deste projeto — por exemplo: uma única fonte de configuração, nenhum número
científico fixo na interface, e todo script só entra com teste que rode.

## Onde ficam as coisas

- **Código**: `C:\github\omica` (versionado no Git).
- **Dados pesados** (VCFs, FASTQ, resultados): `E:\data\omica`.
- **Interface**: <http://localhost:8710> (inicie com `abrir_omica.bat` ou
  `docker compose up -d`).
