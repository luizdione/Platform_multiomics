---
titulo: Biblioteca de skills — quando o catálogo não cobre
nivel: iniciante
modulo: core
tempo_estimado: 8 min
roteiro_aula: false
---

# 04 · Biblioteca de skills — quando o catálogo não cobre

## Duas coisas diferentes: catálogo e biblioteca

| | Catálogo da plataforma | Biblioteca de skills |
|---|---|---|
| O que é | as análises dos módulos (genômica, epigenômica, transcriptômica, ML) | 815 receitas de bioinformática, de 3 repositórios públicos, em 19 domínios |
| Onde está | páginas dos módulos e aba 🎓 Professor | aba 🧰 Skills |
| O que você faz com ela | **executa** — fila, container, resultado em arquivo | **lê** — passo a passo, armadilhas, ferramentas |
| Cobre | o que foi migrado e testado nesta máquina | quase tudo: single-cell, metagenômica, docking, filogenia… |

O catálogo é pequeno e confiável; a biblioteca é enorme e é só texto. Quando o
que você quer **não está no catálogo**, a biblioteca quase sempre tem a receita.

## Buscar na aba Skills

Digite **em português ou em inglês**. O índice das receitas é em inglês, mas a
busca traduz antes de casar e mostra o que fez:

- `metilação` acha o mesmo que `methylation`;
- `expressão diferencial` acha o mesmo que `differential expression`;
- pode escrever a frase inteira — *"quero integrar os lotes do meu single-cell
  com scvi"* — que as palavras de ligação (quero, os, do, meu, com) são
  ignoradas;
- também vale o nome da ferramenta: `deseq2`, `salmon`, `kraken2`.

A linha abaixo da caixa de busca conta o que aconteceu: **glossário PT→EN**
(tradução aplicada), **ignorado** (palavras de ligação) e **sem nenhuma skill**
(termo que não existe em receita alguma).

Quando nenhuma receita casa **todos** os termos, a página avisa e mostra as que
casam os termos mais **específicos** primeiro — um nome de ferramenta raro pesa
mais do que uma palavra comum como *genome*.

## Os guias de domínio

Ao escolher um domínio na lista (por exemplo `epigenomics`), aparecem os
**guias** dele: *quando usar*, as **regras** que não se negociam (por exemplo,
"ORA sempre com universo explícito") e as ferramentas. Vale ler o guia antes de
abrir uma receita: ele diz qual receita serve para o seu caso.

## Do módulo para a biblioteca, e de volta

- No fim da página de cada **módulo** há *Skills relacionadas na biblioteca*:
  os domínios que aquele módulo toca. Serve para o que o módulo ainda não faz —
  e para entender o método por trás do que ele faz.
- Na página de uma **receita**, se a plataforma já tem análise pronta naquele
  domínio, um aviso aponta o módulo. Prefira o módulo: ele roda com as
  referências certas e deixa o resultado em arquivo.
- No **Professor**, quando o pedido cai fora do catálogo, aparecem as receitas
  que casam a sua frase. Essa lista é uma busca comum (o modelo de linguagem não
  participa) e **não tem botão de executar**: receita é para ler.

## Para o autor: de onde vem o glossário

A tradução português→inglês não mora aqui: a fonte é o plugin **Bioinfo** do
assistente de código, que roteia as mesmas 815 receitas (`/bioinfo <o que você
quer fazer>`). A plataforma guarda uma cópia em `data/skills_glossario.json`,
refeita por `python -m omica reindex` no host. Faltou um termo? Acrescente no
plugin e reindexe; o diagnóstico avisa quando a cópia fica para trás.
