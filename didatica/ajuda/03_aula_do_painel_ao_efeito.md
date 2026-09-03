---
titulo: "Aula 1 — do painel de genes ao efeito funcional"
nivel: iniciante
modulo: genomica
tempo_estimado: 30 min
roteiro_aula: true
passos:
  - rotulo: "Extrair as variantes do painel demo"
    modulo: genomica
    tipo: script
    alvo: extracao_demo
    esperado: "Console mostra ~52,9 mil variantes extraídas das 3 janelas do BTA14; o resumo aparece em Resultados do módulo Genômica."
  - rotulo: "Calcular as frequências alélicas (AF/MAF)"
    modulo: genomica
    tipo: script
    alvo: frequencias_demo
    esperado: "Tabela de frequências ordenada por MAF; milhares de variantes comuns (MAF ≥ 0,05) — compare a MAF média com a tabela top."
  - rotulo: "Anotar o efeito funcional (SnpEff)"
    modulo: genomica
    tipo: script
    alvo: anotacao_demo
    esperado: "Dezenas de efeitos distintos; observe qual é o mais comum e discuta por que variantes intergênicas dominam."
  - rotulo: "Ou rode tudo de uma vez (workflow encadeado)"
    modulo: genomica
    tipo: workflow
    alvo: wf_painel_demo
    esperado: "Os 3 passos executam em sequência — o passo N só roda se o N−1 terminar. Acompanhe na página do workflow."
---

# 🎒 Aula 1 · Do painel de genes ao efeito funcional

**Pergunta da aula:** dado um painel de regiões de interesse no genoma bovino,
quais variantes existem ali, quão comuns elas são, e o que fazem com as
proteínas?

## O que você vai usar

- Uma **fatia real** do 1000 Bull Genomes (BTA14, início do cromossomo — inclui
  a janela do **DGAT1**, o gene clássico de gordura do leite), já versionada na
  plataforma.
- Três análises do módulo **Genômica**: extração → frequências → anotação.

## Roteiro

1. **Extração** — a plataforma recorta do arquivo de variantes só o que cai nas
   janelas do painel (arquivo BED). *Pense:* por que trabalhar com um painel em
   vez do genoma inteiro?
2. **Frequências** — cada variante ganha AF (frequência do alelo alternativo) e
   MAF. *Pense:* uma variante com MAF 0,49 separa bem duas raças? E uma com
   MAF 0,01?
3. **Anotação** — o SnpEff diz onde cada variante cai (íntron? troca de
   aminoácido? ganho de stop?). *Pense:* por que a maioria das variantes tem
   efeito "modificador" (não-codificante)?
4. **Workflow** — rode a cadeia inteira e observe a validação: o passo de
   frequências CONSOME o arquivo que a extração produz.

## Onde conferir os números

Os resultados aparecem na página do módulo **Genômica → Resultados (lidos do
disco agora)** — a interface nunca mostra número que não venha de um arquivo
gerado pela SUA execução.

> Dica: depois da aula, pergunte ao **Professor Ômico** (aba 🎓) "quais variantes
> do painel demo têm efeito na proteína?" e compare o plano proposto com o que
> você acabou de fazer manualmente.
