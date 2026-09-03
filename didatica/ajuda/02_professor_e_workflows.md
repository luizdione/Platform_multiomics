---
titulo: Professor Ômico e workflows encadeados
nivel: iniciante
modulo: core
tempo_estimado: 10 min
roteiro_aula: false
---

# 02 · Professor Ômico e workflows encadeados

## O que é um workflow aqui dentro

Um **workflow** é uma sequência de análises em que **o arquivo de saída de um
passo é a entrada do passo seguinte**. Isso não é só uma lista: a plataforma
**valida o encadeamento** ao carregar cada módulo — se um passo declarado não
consome nenhum resultado do passo anterior, o módulo nem carrega e o erro
aparece no diagnóstico. Na fila, o passo N só executa se o passo N−1 terminar
bem.

Exemplos que já rodam de ponta a ponta nesta máquina:

| Workflow | Passos | O que responde |
|---|---|---|
| Do painel ao efeito funcional (genômica) | extração → frequências → anotação SnpEff | quais variantes existem nos genes do painel, quão comuns são, e o que mudam na proteína |
| Da cobertura Bismark ao painel 108 (epigenômica) | DMCs methylKit → interseção painel 108 | quais citosinas mudam de metilação entre raças e em quais genes do painel elas caem |

O selo **"✅ testado de ponta a ponta"** só aparece em workflow que foi de fato
executado e conferido — nunca é decorativo.

## O Professor Ômico (aba 🎓)

O Professor é o jeito de começar **em português**, sem saber o nome das
ferramentas:

1. Você escreve o que quer ("quero saber quais variantes do meu painel têm
   efeito na proteína") e, se quiser, aponta um arquivo.
2. Um **modelo de linguagem local** (Ollama — o mesmo motor do LILO; nada sai
   da sua máquina) recebe o **catálogo** de workflows e análises da plataforma
   e **propõe** o item que melhor responde, explicando cada passo.
3. A proposta é **validada contra o catálogo** — o modelo não pode inventar
   comando nem análise — e **nada roda sem o seu clique** em Executar.

Regra de ouro da plataforma: o modelo de linguagem **sugere e ensina**; quem
executa é sempre a mesma engrenagem determinística dos módulos (fila, worker,
ferramenta científica, arquivo de resultado). Se o Ollama estiver desligado, a
aba continua útil: a vitrine de workflows testados fica disponível do mesmo
jeito.

## Como ler a página de um workflow em execução

- Cada passo mostra seu estado (na fila → rodando → concluído/erro) e leva ao
  **console ao vivo** daquele job.
- Se um passo falha, os seguintes **não rodam** — você corrige e roda de novo.
- Ao final, os números aparecem na seção "Resultados" do módulo — sempre lidos
  dos arquivos em `E:\data\omica\results\`, nunca decorados na tela.
