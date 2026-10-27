# Changelog — plataforma ômica

Formato: [Keep a Changelog](https://keepachangelog.com/pt-BR/), versão em `VERSION`.

## [Unreleased] · Correção dos rótulos de raça nas demos de Fst e ML (2026-10-19)

As listas de raça do 1000 Bull Genomes (`E:\data\1000bg_metadata\samples_*.txt`) estavam com os
rótulos trocados em relação ao campo Breed do BioSample; o autor mandou corrigir na fonte para o erro
não se espalhar (ver `LEIA-ME_ROTULOS.md` e `validar_rotulos.py` nessa pasta). O par "Angus × Nelore"
(112 × 385) das demos **não era um contraste de raças**. As entradas antigas deste CHANGELOG (0.3.0,
0.6.0, 0.8.0) descrevem o que foi entregue na época e ficam como histórico; os números delas (Fst 0,283;
bal.acc 0,948; 311 SNPs) **não valem**.

### Corrigido
- **Fst por raça** (`genomica/fst_racas`) e **dataset, classificação e robustez do ML**: os defaults
  passam ao par válido **Angus (102) × Brahman (55)** (`samples_angus.txt`, `samples_brahman.txt`; 157
  amostras, todas presentes no BCF Run 8). Títulos, descrições, nomes de resultado ("Fst
  Angus×Brahman"), docstrings, glossário e README acompanham.
- **Re-execução pela própria plataforma**, nos caminhos padrão (jobs
  `genomica-fst_racas-20261019-040231-d325f5` e `ml-ml_robustez_demo-20261019-040957-b4ecfc`,
  workflow `wf-ml-wf_ml_aims-20261019-040805-460bea`):

  | | antes (rótulos inválidos) | agora (Angus 102 × Brahman 55) |
  |---|---|---|
  | Fst global (Hudson, BTA14:1–2,5 Mb) | 0,283 | 0,389 |
  | variantes na matriz do ML | 311 (de 4.662) | 515 (de 7.718) |
  | bal.acc. RF / XGBoost, painel completo | 0,936 / 0,948 | 0,991 / 1,000 |
  | núcleo estável do painel (≥ 80 %) | n/d | 50 de 50 (recorrência 5/5) |

  A acurácia ≈ 1,0 é esperada: taurino × indicino são subespécies. O painel mínimo continua sendo
  escolhido dentro de cada fold, então não é vazamento.
- **Selo `ml/wf_ml_aims`** recarimbado DEPOIS da re-execução (`testado_em` 2026-10-19, `hash_scripts`
  `9563a9c0e7dbc8de`); os outros quatro selos seguem válidos (`python -m omica selos`).
- Resultados antigos **arquivados sem alteração** em
  `E:\data\omica\results\_ARQUIVO_INVALIDOS_rotulos_2026-10-19` (md5 e LEIA-ME). Os jobs antigos
  continuam no histórico, cada um com a nota `INVALIDO_rotulos_2026-10-19.txt` ao lado (o
  `status.json` e o `job.log` não foram editados).
- Testes: o smoke do Fst e o do dataset ML passam a usar Brahman; os comentários dos testes de
  NaN/cobertura (linhas do contraste antigo) dizem que a lógica independe do par de raças.

## [1.0.0] — 2026-10-06 · Ponte com o plugin Bioinfo: a biblioteca de skills passa a responder em português

Numerada 1.0 pelo autor na publicação (era 0.9.0 nos commits `b9d3493`…`833b47f`):
primeira versão com os 5 workflows selados válidos depois da auditoria.

A plataforma e o plugin Bioinfo (roteador das mesmas 815 skills no assistente de
código) indexavam a MESMA biblioteca sem se conhecer. O defeito que os dois
compartilhavam: o índice é em inglês e o público digita em português — na página
/skills, "metilação" devolvia zero. O plugin já tinha resolvido isso do lado dele;
esta versão traz a solução para cá sem criar uma segunda fonte.

### Adicionado
- **Busca de /skills no servidor, em português ou inglês** (`omica/skills/busca.py`,
  `GET /api/skills`): sem acento e sem caixa, palavras de ligação ignoradas, cada
  termo vale por si ou pela tradução, AND entre termos e — se nada casa tudo — modo
  parcial ordenado por IDF (o termo raro pesa mais). Medido no índice real:
  `metilação` = `methylation` = 21 skills; a frase "quero integrar os lotes do meu
  single-cell com scvi" devolve as 2 receitas de integração com scVI.
- **Glossário de busca PT→EN** (297 termos, 148 palavras de ligação) em
  `data/skills_glossario.json`: é CÓPIA, com o sha do CONTEÚDO da origem (reformatar o
  arquivo ou trocar CRLF por LF não acusa cópia atrasada); a fonte única é o
  plugin (`BIOINFO_PLUGIN_DIR`, só no host). `python -m omica reindex` atualiza a
  cópia; sem o plugin à vista (container, CI) a cópia existente é mantida.
- **Guias de domínio no índice**: os 55 cards do `INDICE_CONSOLIDADO.md` (quando
  usar / regras / stack), que o parser descartava, agora entram em
  `skills_index.json` e aparecem ao escolher o domínio. As 815 skills do índice
  ficaram idênticas às da versão anterior.
- **Elo módulo → domínio no manifest** (`skills_dominios`): a página do módulo
  ganha "Skills relacionadas na biblioteca", a página da receita avisa quando a
  plataforma já tem análise naquele domínio, e o plugin lê o mesmo campo para pôr
  o catálogo ANTES da biblioteca no roteamento.
- **Professor: fora do catálogo não é mais beco sem saída.** Quando o plano é
  "explicar", inválido, ou o motor falha, a aba mostra as receitas da biblioteca que
  casam a frase — busca determinística, sem LLM, e sem botão de executar.
- **Doctor: `Ponte Bioinfo (skills)`** — reprova domínio de manifest que a biblioteca
  não tem; avisa quando falta o glossário ou quando a cópia ficou para trás do plugin.
- Ajuda `04_biblioteca_de_skills.md` (catálogo × biblioteca, como buscar, guias).

### Alterado
- **/skills deixou de embutir o índice inteiro** (330 KB por abertura): a página traz
  só o cabeçalho e a lista vem de `/api/skills`. `?q=` e `?dominio=` deixam a busca
  endereçável. O `<script>` ganhou `?v=<versão>`: o JS mudou de contrato com a página
  e a cópia antiga em cache do navegador deixaria a tabela vazia.
- **Página da receita**: o front-matter YAML do `SKILL.md` saía como parágrafo cru no
  topo; agora a descrição completa vira o lead (o índice a trunca em 140 caracteres),
  há cabeçalho com domínio/origem/stack, e "Voltar" leva ao domínio, não a /ajuda.
- Ordem dos resultados: quem traz o termo no próprio nome vem primeiro. Antes valia a
  ordem alfabética do índice: `deseq2` abria com anotação de repetições e ATAC-seq, e
  as skills de DESeq2 só apareciam depois.

### Corrigido
- Descrição e stack das skills entravam no `innerHTML` sem escape (texto de terceiros).
- **Busca sem teto travava a plataforma** (achado da revisão desta versão). Com
  centenas de siglas na frase — um trecho de Métodos colado no Professor — a busca
  recompilava um regex por termo × skill e levava 42 s (676 siglas); e a rota
  `POST /professor/perguntar`, `async def` desde a 0.8.0, rodava motor local e
  busca DENTRO do event loop, parando o servidor inteiro enquanto o Professor
  pensava. Agora: regex de sigla compilado uma vez, teto de 20 termos e 2.000
  caracteres (o mesmo do plugin, para a busca continuar gêmea; a UI avisa "frase
  longa") e rota síncrona, no threadpool. Medido: 676 siglas → 0,18 s.
- Os 2 testes de `test_correcoes_auditoria.py` que importam `perfil_tss.py` (numpy)
  agora PULAM na imagem `web`, que instala só o lock mínimo de propósito
  (`pytest.importorskip`); no host e na imagem `methyl` continuam rodando. Antes
  falhavam no container e no CI.

### Testes
- 32 novos (`tests/test_ponte_bioinfo.py` e cards em `test_skills_index.py`); a
  suíte passa de 111 para 143 coletados — host: 131 passam, 12 pulam; container
  `web` (lock real): 129 passam, 14 pulam, 0 falham. Nenhum lê o plugin ou a
  biblioteca reais: `conftest.py` aponta `BIOINFO_PLUGIN_DIR` para um caminho
  inexistente.

### Selos e resultados publicados (re-execução de 2026-10-05/06)
- Os 4 workflows com selo vencido desde a auditoria (`genomica/wf_painel_demo`,
  `epigenomica/wf_metilacao_demo`, `epigenomica/wf_mofa_demo`,
  `transcriptomica/wf_de_granulosa`) e os 5 scripts avulsos cujo resultado era
  mais velho que o script (integridade 1000BG, Fst Angus×Nelore, metaperfil TSS,
  motifs HOMER, Salmon quant) foram re-executados NA PLATAFORMA (fila RQ, workers
  de container, submetidos pela ponte do plugin Bioinfo) — 25 arquivos
  regenerados. Só então `python -m omica selos --gravar` recarimbou
  `hash_scripts`, e `testado_em`/`testado_por` passaram a dizer o dia e os
  números da re-execução. O que mudou no que estava publicado: `fst_top.tsv` só
  lista variantes com Fst finito e OBS_CT ≥ 249 (antes trazia NaN no topo); a
  interseção DMC × painel conta cada DMC uma vez (229 de 275 DMCs em 43 genes);
  MAF multialélica pela definição corrigida; perfil TSS com a fita invertida.
- Resto de teste `work/jobs/epi-intersecao_demo-2` (queued desde 14/09, 6 GB
  presos no orçamento de RAM) removido.

## [0.8.0] — 2026-09-14 · Auditoria adversarial: correções científicas e validadores que reprovam

Auditoria de 12 dimensões com verificação adversarial por achado (95 achados
brutos; 21 confirmados + 8 plausíveis; 6 refutados pelos próprios céticos —
entre eles a suspeita de vazamento de dados no ML, que o código já tratava
corretamente). Os achados de maior impacto vieram da leitura dos ARTEFATOS
reais em `E:/data/omica/results`, não só do código.

### Corrigido — ciência (mudam números publicados)
- **Fst: o "top 20" era lixo.** `float("nan")` não levanta `ValueError`, então a
  guarda nunca disparava e `sorted` com chaves NaN produzia ordem incoerente:
  no contraste Angus×Nelore real, 4 das 5 primeiras linhas do top publicado
  tinham Fst NaN — 29.617 das 122.372 variantes (24%) são NaN, e 28 delas têm
  `OBS_CT=0` — com o maior Fst real (0,529) só na 5ª linha. Agora o descarte
  acontece ANTES da ordenação e é contado no resumo, em três baldes separados
  (não finitas, sem genótipo, cobertura abaixo do limiar). O Fst global (0,283)
  sempre veio do `.fst.summary` do plink2 e está correto.
- **Fst, 2ª volta (revisão do próprio patch):** descartar só `OBS_CT==0` removia
  0,02% das variantes e deixava passar a cauda de cobertura ínfima — o topo
  passou a ser encabeçado por uma variante com **4 genótipos observados de 497 e
  Fst = 1,0**, que num manuscrito se lê como fixação completa entre raças. Novo
  `--min-obs` (padrão: metade da coorte; 249 no caso real), com o limiar efetivo
  gravado no resumo. Trocar "NaN no topo" por "1,0 espúrio no topo" seria pior
  que o bug original: o número convence.
- **Fst lia fóssil de outro contraste**: `glob("fst*.fst.var")` pegava a saída
  de uma execução anterior (plink2 nomeia o arquivo pelos grupos). Limpeza
  antes de rodar + erro explícito se o plink2 não produzir saída.
- **MAF multialélica errada**: `min(AF1, 1-AF1)` ignora AF2..AFn — com ALT1=0,5
  e ALT2=0,3 o REF vale 0,2 e a MAF é 0,3, não 0,5. E a tabela é ORDENADA por
  MAF, então esses sítios subiam indevidamente. Nova coluna `N_ALT`.
- **Metaperfil TSS espelhado na fita −**: o TSS era escolhido certo, mas
  `d = pos - tss` não invertia o sinal — genes da fita negativa entravam com
  upstream e downstream trocados, achatando o degrau de metilação no TSS.
- **Interseção DMC × painel contava a mesma DMC uma vez por janela**: o resumo
  publicava 320 DMCs "no painel" de um total de 314 (impossível). Janelas de
  genes vizinhos se sobrepõem (MX1/MX2 no BTA1). `n_dmc_no_painel` agora conta
  cada DMC uma vez; a atribuição por gene continua (e fica declarada).
- **DMC declarava BH e rodava SLIM**: `calculateDiffMeth` usa `adjust="SLIM"`
  por padrão, mas o cabeçalho, o manifest e o resumo sempre disseram BH (como
  no paper). Agora passa `adjust="BH"` explicitamente.
- **View "corpo gênico" do MOFA continha o promotor inteiro** nos 108 genes:
  parte da covariância entre as duas views era a MESMA CpG contada duas vezes.
  O corpo agora exclui a janela do promotor, respeitando a fita.
- **R² do MOFA medido em outra escala**: o modelo treina com
  `scale_views=True`, mas a variância explicada era calculada contra a matriz
  só centrada. Y passa pela mesma transformação, com sanidade de faixa.
- **DESeq2 truncava decimais em silêncio**: `storage.mode(x) <- "integer"`
  desliga a checagem de contagens inteiras do próprio DESeq2 (2,9 → 2). Agora
  recusa NA/negativo e ARREDONDA declarando quantos valores foram afetados.
- **Salmon aceitava qualquer taxa de mapeamento**: 2% mapeado virava job verde.
  Novo `--min-mapeado` (padrão 50%).

### Corrigido — falsos-verdes (validadores que nunca reprovavam)
- **A checagem de referência quebrada do registry era código morto**: olhava só
  `cmd.split()[0]`, que é sempre `python3`/`Rscript` e nunca termina em
  `.py`/`.R`/`.sh`. Um manifest podia apontar para script apagado, carregar
  limpo e o doctor dizer "4 módulos válidos" — a falha só apareceria no worker.
- **Job virava `done` só por returncode 0**: o `RESULTADO: <path>` anunciado não
  era conferido. Agora o executor exige que o artefato exista e não esteja
  vazio, e registra aviso quando o script não declara resultado nenhum.
- **`integridade_vcf` nunca exercitava o índice**: checava só a existência do
  `.tbi`/`.csi` (e `bcftools view -H` lê sequencialmente). Agora confere se o
  índice é mais novo que o dado e se o bcftools consegue usá-lo.
- **HOMER sem `knownResults.txt` concluía com tabela vazia** — que se lê como
  "nenhum motif enriquecido". Agora falha. E o `aviso_didatico`, que era texto
  fixo afirmando "1 DMC estrita" enquanto o `dmc_resumo.json` do mesmo dado
  reportava 314, passou a ser gerado a partir do dado.
- **Tabela de resultado vazia era exibida como "0 linhas"**: arquivo truncado
  agora é ausência de dado (`None`), e header-só vem com `vazia: true`.
- **A suíte não era hermética**: lia a RAM REAL da máquina — 4 testes de fila
  quebravam quando havia outro job pesado rodando, e o verde do CI só
  significava "o runner estava vazio". `conftest` fixa a memória; os testes da
  guarda continuam monkeypatchando e agora valem de verdade.

### Adicionado — selo de workflow com lastro
- `WorkflowSpec.hash_scripts` + `registry.hash_dos_passos/selos_vencidos` e
  checagem no doctor. O selo "testado de ponta a ponta" era texto livre que
  sobrevivia à reescrita do próprio script que certifica. Os 5 selos foram
  carimbados com o hash do commit efetivamente testado (11a9a7a) — e, como
  esta versão mudou scripts de 4 deles, **o diagnóstico já acusa esses 4 como
  vencidos: os números publicados por eles ainda são os da versão anterior e
  os workflows precisam ser re-executados.**

### Adicionado — segurança e diagnóstico
- **Gate de publicação era cego a credenciais** (provado com canária: token do
  GitHub, chave AWS, `sk-ant-`, chave privada SSH e senha em `.ini` passavam
  APROVADOS). Novos `BLOCK_SECRET` e `NOME_CREDENCIAL`; formatos de redação
  (.tex/.ipynb/.Rmd/.bib/.html) entraram na varredura, e o que fica fora dela
  agora é declarado como `[nao-lido]` em vez de aprovado em silêncio.
- **doctor**: porta 8710 exige a assinatura do `/saude` da ômica (antes,
  qualquer processo na porta dava OK); referências pesadas são checadas pelo
  CONTEÚDO (cache VEP com `110_*`, `info.json` do Salmon,
  `snpEffectPredictor.bin`, VCFs, listas de raça) e não pela pasta-pai; worker
  RQ exige heartbeat recente (registro órfão no Redis não conta mais); e
  `--fix-soft` ABORTA se houver job em andamento, em vez de reiniciar os
  containers por cima de uma análise de horas.
- **Versão com fonte única**: `pyproject.toml` fossilizara em 0.1.0 enquanto
  `VERSION` e a tag estavam em 0.7.0; agora a versão é dinâmica a partir de
  `VERSION`, com teste que impede a volta do literal.
- **CI**: instala o LOCK dos containers (`requirements.txt`), lista os SKIPPED
  com motivo (os 13 smokes de container nunca rodaram aqui), tem piso de
  cobertura contra "verde por suíte vazia" e uma canária que falha o job se o
  gate aprovar uma árvore com credencial.

### Revisado — auditoria adversarial DO PRÓPRIO PATCH (8 lentes, 36 apontamentos)
Antes de commitar, o patch acima passou por uma segunda rodada adversarial: 12
problemas confirmados, 10 refutados pelos céticos. O que entrou por causa dela:
- o limiar de cobertura do Fst (acima) — o achado mais grave da rodada;
- **alelo `*` (spanning deletion)** deixou de contar como alelo do sítio na MAF;
- **o gate bloqueava o padrão CORRETO de manejo de segredo**
  (`api_key = os.environ[...]`, `getenv(...)`, `cfg.get(...)`) e casava
  `mask-<md5>` como chave da OpenAI; a regra genérica também tinha retrocesso
  quadrático. Um gate que pune quem faz certo é desligado pelo usuário;
- **o selo vencido não chegava à UI**: a vitrine do Professor exibia ✅ só por
  existir `testado_em`. Agora marca "Selo VENCIDO" e diz que os números são da
  versão anterior;
- **o hash do selo era cego ao `_comum.py`** (importado por todos os passos) e
  aos parâmetros científicos do `cmd` — trocar `--qvalue` no manifest mudava o
  resultado sem invalidar o selo;
- **`python -m omica selos [--gravar]`**, comando que o doctor e o CHANGELOG já
  citavam e que não existia (`omica/selos.py`);
- **frescor dos resultados** no doctor: o selo só cobre workflows, e scripts
  avulsos (perfil_tss, motifs_homer) publicavam resultado velho sem aviso —
  hoje o diagnóstico acusa 11 deles;
- **a porta** deixou de gritar "outro serviço" durante o startup (o Docker
  publica a porta antes de o uvicorn aceitar) e o **`--fix-soft`** deixou de
  criar impasse permanente com `status.json` órfão;
- **cadeia no RQ**: o passo dependente de um passo que falhou ficava `queued`
  para sempre — a UI mostrava a cadeia viva sem nada rodando e a guarda de RAM
  seguia somando o `mem_gb` do fantasma;
- **testes fracos meus**: o da correção do MOFA era um grep no código-fonte
  (passaria com os ramos das fitas trocados) e o do smoke declarava BH com
  `startswith("BH")` — que a string do bug, `"BH (methylKit/SLIM)"`, satisfazia.
  A canária do gate no CI tratava qualquer saída ≠ 0 como sucesso, então um gate
  QUEBRADO passaria por "reprovou": agora exige código 1 e a mensagem certa,
  mais um controle negativo com árvore limpa.

### Testes
- `tests/test_correcoes_auditoria.py` (novo): cada correção tem um teste que
  REPROVA a versão anterior, e cada gate tem canária + controle negativo —
  inclusive as duas canárias que a revisão exigiu (a variante de cobertura
  ínfima no Fst e o `min_obs` permissivo que precisa deixá-la em 1º para provar
  que a canária morde). Os smokes de container deixaram de comparar zero com
  zero e de conferir número vindo do painel em vez do dado.
- Suíte: **99 passed, 12 skipped** (era 46 passed / 4 failed / 12 skipped —
  e os 4 vermelhos eram a RAM da máquina, não o código).

## [0.7.0] — 2026-09-04 · Backlog zerado: robustez ML + HOMER + MOFA

### Adicionado — robustez ML (A1/A2/A3, herdada do thermalcore)
- `ml_robustez.py` (migração curada de A1A2A3_bootstrap_folds_ic.py): A1
  bootstrap estratificado da seleção (núcleo estável ≥80%), A2 recorrência do
  top-k entre os 5 folds externos, A3 IC95% percentílico. E2E no dataset real:
  núcleo de 5 marcadores, recorrência 5/5=4 e 4/5=18.

### Adicionado — HOMER (motifs de TF)
- HOMER core compilado na imagem methyl; genoma = FASTA do SnpEff local
  (assembly coerente), preparse persistido em refs (1ª vez ~35 min; depois
  minutos). `motifs_homer.py` seleciona top CpGs com limiar RELAXADO E
  DECLARADO (o demo tem 1 DMC estrita) — E2E: 15 motifs conhecidos, top
  Zic(Zf). `metilacao_dmc.R` agora exporta a tabela completa do teste
  (todas_cpgs.tsv), insumo do HOMER.

### Adicionado — MOFA (promotor × corpo do painel 108)
- `mofa_preparar.py`: 2 views REAIS pareadas por amostra (metilação de
  promotor ±2 kb × corpo gênico, por gene do painel). Workflow
  **wf_mofa_demo SELADO**: separação Nelore×HF = 1,12 no Fator 1.
- **Decisão de engenharia documentada**: o caminho MOFA2-R(volume)+reticulate
  empilhou 4 incompatibilidades de época (check de versão exige mofapy2 0.6.7;
  scipy.shape removido → scipy 1.11.4; np.float removido; save→"interrupted"
  quebrando). O treino migrou para o **entry_point oficial do mofapy2 em
  python** (`mofa_treinar.py`), com R² por fator/view computado explicitamente.
  Armadilhas adicionais domadas e comentadas no código: sys.exit() do 0.6.x
  pós-convergência (SAI COM rc=0 — mascararia falso-done), `set_data_matrix`
  MUTA a lista de dados recebida, features precisam de nomes únicos entre
  views, orientação amostras×features. Wrapper R removido (sem fóssil).

### Testes
- Suíte 50 passed (sets de scripts/workflows dos manifests atualizados).

## [0.6.0] — 2026-09-03 · Fase 5 (ML + skills + diagnóstico + didática) — PLANO COMPLETO

### Adicionado — módulo ML (migração curada do bovine_aim_gwas_ml_thermalcore)
- Fila **ml** + imagem `omica-ml` (sklearn/xgboost/shap pinados). Decisões
  herdadas explícitas: CV ANINHADA 5×3, métricas balanceadas (bal.acc, F1-macro,
  MCC, ROC-AUC), painel mínimo top-k selecionado DENTRO do fold (sem vazamento),
  imputação por mediana, semente 42.
- `ml_dataset_genotipos.py` (fila bioinfo): matriz 0/1/2 real do BCF Run 8
  (MAF mínimo + afinamento). `ml_classificar.py` (fila ml): RF+XGBoost, SHAP
  top-k com figura. Workflow **wf_ml_aims SELADO** — cadeia ENTRE filas
  (bioinfo→ml): 497 amostras reais (112 Angus × 385 Nelore), 311 SNPs,
  **balanced accuracy 0,948**. Smoke sintético prova que o SHAP recupera ≥7/10
  variantes informativas plantadas.

### Adicionado — /skills, glossário, /diagnostico
- Página **/skills**: as 815 skills (19 domínios) pesquisáveis OFFLINE (índice
  JSON embutido; clique renderiza o SKILL.md da biblioteca montada read-only).
- **Glossário fundido e curado** (didatica/glossario.json, 40 termos das 3
  ômicas + ML + core, origem marcada) com página própria.
- **Doctor v2** + página **/diagnostico**: workers RQ por fila (as 5), cache
  VEP, índice Salmon, banco SnpEff, VCFs/listas 1000BG, glossário — no
  container: 15 checagens ✅ com workers `bioinfo×1, methyl×1, ml×1, omica×1,
  vep×1`.

### Adicionado — modo aula + gate
- **Modo aula**: front-matter com `roteiro_aula: true` + `passos:` vira cards
  "▶ Executar este passo" que disparam as MESMAS rotas dos módulos; Aula 1
  ("do painel ao efeito funcional") publicada e executável de ponta a ponta.
- **Gate anti-vazamento** portado (`omica/publish/gate.py`, CLI `python -m
  omica gate <dir>`): audita árvores de EXPORTAÇÃO (repos de cálculo) com as
  regras do legado (tokens de IA, arquivos de manuscrito, paths absolutos) —
  testes provam que reprova e que aprova.

### Testes
- Suíte **50 passed**; smoke ML 1/1 sintético + dataset real no bioinfo.
- Correção honesta no porte: guarda `y_prob is not None` do roc_auc restaurada
  (perdida na 1ª cópia; o smoke pegou).

## [0.5.0] — 2026-09-03 · Fase 4 (transcriptômica)

### Adicionado
- **Demo real versionado**: subset granulosa Angus da matriz nf-core do sub03
  (20.543 genes × 8 amostras, 4 summer_heat × 4 winter_control, 1,1 MB) + FASTQ
  paired-end real de 25 mil spots (SRR24953465, 3 MB) gerado pelo próprio
  script de download.
- 5 análises: `expressao_deseq2.R` (worker methyl — DESeq2 do volume
  reaproveitado; 16.350 genes testados no demo), `volcano_plot.py` (destaques
  dos genes top), `qc_fastq.py` (FastQC+MultiQC — o QC do legado nunca rodou),
  `baixar_sra.py` (fastq-dump com `--spots`), `salmon_quant.py` (índice bovino
  construído 1× em refs/salmon_bostau a partir dos transcritos do nf-core
  legado: 82.061 transcritos; demo com 89,5% de mapeamento).
- Workflow **wf_de_granulosa** SELADO (fila methyl real: DESeq2 → volcano).
- UI: resultados `.html` viram link "abrir relatório" (MultiQC embutido).
- `tools/consertar_docker.ps1`: conserto em 1 clique do Docker Desktop após
  queda suja (sockets órfãos + configs nulos — incidentes de 2026-09-02/03).
- Imagem bioinfo: FastQC 0.12.1, SRA Toolkit 3.4.1, MultiQC 1.25.1 e **Salmon
  1.10.3 via biocontainer (multi-stage)** — o tarball standalone 1.12.1 do
  GitHub tem o subcomando `index` quebrado (aborta com exceção de locale
  independente do env; testado com C, C.UTF-8 e sem env).
- Etapa nf-core DinD declarada `planejado` com motivo: o acervo legado de 34
  amostras (E:\data\rnaseq\nfcore_output\sub03) é reaproveitado como fonte.

### Notas de operação
- A guarda de RAM protege a VM WSL (onde os jobs vivem); o teto de 80% do HOST
  é regra operacional externa à plataforma (documentado no PMM).

### Testes
- Suíte 42 passed; smoke RNA: methyl 1/1 (DESeq2+volcano), bioinfo 1/1 (QC).
  E2E via UI: download demo, QC (50.000 sequências), workflow DE, Salmon — done.

## [0.4.0] — 2026-09-02 · VEP + Fase 3 (epigenômica)

### Adicionado — VEP (fecha a Fase 2)
- Fila **vep** + worker `omica-vep`: binário release_113 (a base 110.1 é
  bionic/py3.6, velha demais p/ RQ) com **cache PINADO em release 110 via
  `--cache_version`** — último cache bos_taurus em ARS-UCD1.2, compatível com o
  Run 8 (cache de 1,29 GB baixado para `E:\data\omica\refs\vep`).
- `vep_anotacao.py` + card na UI + results_spec; smoke 1/1 (60 s, >1000
  variantes anotadas do demo).

### Adicionado — Fase 3 · Epigenômica
- **Painel 108 VERSIONADO** (`data/paineis/painel_108.{json,bed}`) convertido da
  TableS1 curada do manuscrito, com proveniência no `_meta` — 108 genes exatos,
  fim das contagens divergentes (45/49/53); teste trava a contagem canônica.
- Fila **methyl** + worker `omica-methyl`: rocker/rstudio:4.2 + **volume
  `epigenoma_r_lib` reaproveitado** (methylKit/GenomicRanges/ChIPseeker/DESeq2 já
  compilados pela plataforma antiga — zero build Bioconductor).
- Demo real versionado: 6 coverages Bismark CD4 (3 Nelore × 3 Holstein, ~430 KB)
  do depósito Zenodo do próprio paper.
- 3 análises migradas por cópia curada: `metilacao_dmc.R` (methylKit, limiares
  do paper |diff|>15%, q<0,05), `intersecao_dmc_painel.py` (Python puro, rótulo
  honesto — o legado dizia "BEDTools"), `perfil_tss.py` (metaperfil ±2 kb do TSS
  dos 108 genes, PNG matplotlib).
- Workflow **wf_metilacao_demo** SELADO (fila methyl real, depends_on).
- UI: resultados de imagem (PNG/SVG) servidos por `/resultados/{rel}` com guarda
  de caminho; figura do TSS embutida na página do módulo.

### Testes
- Suíte 42 passed; smokes por container: methyl 2/2 (17 s), vep 1/1 (60 s),
  bioinfo 5/5. E2E via UI: workflow metilação (2 passos), TSS e VEP — todos done.

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
