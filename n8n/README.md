# n8n — workflows que orquestram os pipelines da ômica

O n8n só **orquestra**. Sanitização, plano congelado por sha256, portões, envelope e a
conferência de cada número do relatório são da ômica (`/api/v1`). O n8n nunca fala com o
Ollama nem com o Redis; a chave da Anthropic fica só na credencial dele.

| Arquivo | O que é |
|---|---|
| `build_workflows.py` | gera `dist/<pipeline>.json` a partir de `pipelines/<id>.yaml` |
| `dist/*.json` | workflows gerados (versionados; nunca editar à mão — regerar) |
| `prompts/relatorio_sistema.md` | prompt do redator, servido pela ômica em `/relatorio/pedido` |

Infraestrutura do n8n (compose, rede `omica_api`, backup): repositório `C:\github\n8n-infra`.

## Desenho de cada workflow

Formulário (arquivos e/ou caminhos no servidor) → **contexto do run** (subprojeto, da lista que a
ômica devolve na hora; ver abaixo) → `POST /runs` com `{pipeline, especie, subprojeto}` →
`PUT /entradas/<nome>` (upload
cru; depois, em série, o `{"caminho"}` de cada entrada por caminho) → entradas fora do formato e
marcadas `adaptavel` vão ao agente (`/adaptar`); se ele duvidar, o **autor** mapeia num
formulário que só oferece cabeçalhos reais → escolhas do autor (dropdown só com valores reais)
→ `/plano` → **aprovação única** (resumo do plano com o sha256) → para cada passo:
`/executar` (429/503 = espera 2 min e repete) e `/aguardar` (espera longa na ômica; o laço não
usa nó Wait) → falha pergunta ao autor (repetir/abortar) → `/relatorio/pedido` → Claude
(`claude-opus-5-5`, saída estruturada) → `/relatorio/rascunho` (portão de números; até 2
tentativas automáticas, depois revisão do autor) → link do relatório.

## Contexto do run (D-10, F6)

Logo depois do gatilho, nos 6 workflows: `Contextos do pipeline` (`GET
/subprojetos?pipeline=<id>&privados=false`) → `Campos do contexto` → página `Contexto do run`
(dropdown "Subprojeto") → `Conferir contexto` → `Contexto válido?` → `Criar run`.

- A lista é a **mesma regra do `POST /runs`** (`contexto.execucao_permitida`, no servidor) e
  vem **sem subprojetos privados**: o formulário não tem login e o n8n guarda o token do autor;
  um privado na lista seria um run privado criado por formulário público, e o título dele
  ficaria nos registros de execução do n8n. Pelo mesmo motivo, com `privados=false` os `erros`
  da resposta só trazem os do repositório (`repo:*`): o erro de carga de um subprojeto de
  `<dados>` traz o slug e, na mensagem, título e pergunta — e não há como saber se ele é privado.
- A lista **nunca é embutida** no `dist` (versionado): subprojeto novo não exige regerar nem
  reimportar nada. Só o id (`<especie>/<slug>`) vai para a opção — nem título, nem pergunta.
- O par escolhido é conferido contra a lista antes do POST; fora dela, a execução termina em
  `Fim: contexto recusado`, sem criar run. Lista vazia ou erro da API (404
  `pipeline_inexistente`) param a execução com o motivo ("nenhum subprojeto aceita este
  pipeline").
- O `Resumo do plano` abre com `Contexto: <especie>/<subprojeto> · assembly · sha` e a lista
  `Avisos do subprojeto`: os `avisos` do `subprojeto.yaml` **que valem para o módulo do plano**,
  escapados — os MESMOS que o relatório declara (`servico.avisos_que_valem`, X5′). O prefixo
  antes do primeiro `:`, normalizado como no Python (sem acento, minúsculas, sem espaço nas
  pontas), tem de ser o módulo do plano ou não ser id de módulo nenhum (aviso geral): no demo,
  `epigenômica: painel_108 v1 … 13 loci errados` aparece nos dois workflows da epigenômica e
  em nenhum outro. A lista de ids entra no JS na geração do `dist` (a do relatório, via
  `load_registry`): módulo novo exige regerar e reimportar. Se o plano voltar para
  um contexto diferente do escolhido (ex.: o corpo do POST perdeu os campos e o run caiu no
  demo), o resumo **recusa** e vai a `Fim: plano recusado` — nada é aprovado.
- Subprojeto privado continua só pela UI/API com o token do autor.

## Contrato do `PUT /runs/<id>/entradas/<nome>` (Content-Type)

O ramo é escolhido **só pelo `Content-Type`** (o parâmetro `charset` é ignorado):

| Content-Type | Corpo | Teto |
|---|---|---|
| `application/octet-stream` | o arquivo **cru** | 256 MiB (`runs._MAX_ENTRADA`) |
| `application/json` | `{"caminho": "..."}` de um arquivo que já está no servidor: caixa `<dados>/entradas/` ou `modules/*/demo_data` / `data` (**cópia**), ou uma raiz de referência como `/data/1000bg_vcf` (**referência**, sem cópia; só entrada com `referencia` em `modos`) | 64 KiB |
| qualquer outro, ou ausente | — | **415** `tipo_de_conteudo_nao_suportado` |

Caem no 415, entre outros: `text/tab-separated-values`, `text/csv`, `text/plain`,
`multipart/form-data`, `application/x-www-form-urlencoded`, `text/json` e o pedido **sem**
`Content-Type`. Num nó HTTP Request montado à mão com body "n8n Binary File", o n8n manda o
mimeType do próprio arquivo (um `.tsv` sai como `text/tab-separated-values`) e recebe 415: é
preciso acrescentar o cabeçalho **`Content-Type: application/octet-stream`** explícito, como
faz o nó "Enviar entrada" do workflow gerado. O cabeçalho do usuário vence o do binário, e a
resposta 415 traz `aceitos` e a dica de como corrigir.

Quem decide cópia ou referência é o **servidor**, pela raiz em que o caminho resolvido cai; o
cliente nunca diz o modo nem manda o índice. Formato com arquivo irmão obrigatório
(`vcf_indexado`: `.vcf.gz` + `.tbi`/`.csi`, `.bcf` + `.csi`) só entra por caminho — o servidor
acha `<principal><sufixo>` sozinho. Upload desse formato dá 422 `entrada_exige_indice`, e
upload numa entrada sem `copia` em `modos` dá 422 `entrada_so_por_referencia`. Caminho fora
das raízes, inexistente ou num modo que a entrada não aceita dá **sempre** o mesmo 422
`caminho_recusado` (sem oráculo). `GET /pipelines` diz, por entrada, `modos` e `envio`
(`"arquivo"` ou `"caminho"`), calculado por `pipelines.envio_formulario` — a mesma função que
o builder usa para decidir o tipo do campo do formulário.

O `POST /passos/<passo>/executar` não aceita **nada** além do token: corpo não vazio dá 422
`corpo_nao_aceito`, e query string não vazia dá 422 `parametro_nao_aceito` — o passo roda
exatamente o plano aprovado. No nó HTTP: sem "Send Body" e sem "Send Query Parameters".

Todo erro sob `/api/v1` vem como `{"detail": {"erro": "<código>", ...}}`, inclusive rota
inexistente (404 `rota_inexistente`), método errado (405 `metodo_nao_permitido`) e falha
interna (500 `erro_interno`): `$json.body.detail.erro` existe em qualquer resposta de erro.
Sem credencial, a resposta é 401 (ou 503 com a API desligada) antes de qualquer outra — com
uma exceção inofensiva: uma rota existente escrita com barra no fim (`/api/v1/saude/`) recebe
307 para a forma sem barra antes de o token ser conferido (o redirecionamento não executa nada).

## Entrada por caminho no formulário (genômica, ML e pasta da epigenômica)

Quando o contrato diz `envio = "caminho"`, o campo do formulário é de **texto** (com o
`exemplo` do contrato como dica), não de arquivo. O workflow ganha um segmento só para isso,
em série depois dos uploads: `Separar caminhos` → `Enviar caminhos` (PUT JSON, timeout de
600 s: o sha completo de um BCF de ~1,9 GB pelo bind mount) → `Conferir caminhos`. Caminho
recusado vai à página `Fim: caminho recusado`, com o motivo, e o run é cancelado (senão conta
no limite de runs abertos). Um VCF aceito mas reprovado no conteúdo (ex.: sem índice ao lado)
segue o caminho de sempre e termina em `Fim: entrada inválida`.

No resumo do plano, cada entrada por caminho mostra se entrou **por referência, sem cópia**,
qual **índice** foi achado (`+ índice .tbi`) e, no VCF, os **contigs com variantes** (do
veredito, lido pelo índice), com o aviso de alcance do módulo — o portão confere números, não
alcance:

- genômica: um BCF do Run 8 tem um cromossomo só, e genes do painel (ou regiões) em outros
  cromossomos não terão variantes extraídas;
- ML: "o dataset usa só a região `<regiao>` (parâmetro do subprojeto): um VCF sem esse contig
  dá 0 marcadores", com a região lida do **plano** (o subprojeto pode sobrescrevê-la), nunca
  do YAML;
- epigenômica: sem bloco de contigs; a pasta mostra quantos arquivos entraram (`— pasta com
  6 arquivos`).

Workflows só com upload (a transcriptômica) não têm esse segmento.

**Pasta (epigenômica).** A entrada `coverages` é uma PASTA de coverages Bismark e só entra por
caminho, por **cópia** (o servidor copia os membros para a pasta do run e grava o sha da
árvore). Upload de pasta dá 422 `entrada_exige_caminho`. Pasta grande demais dá 413
`entrada_grande_demais`; a validação de uma pasta no teto leva minutos, dentro do timeout de
600 s do `Enviar caminhos`.

| Workflow (id) | Entradas no formulário |
|---|---|
| `genomica_painel` (`omicaGenomPain01`) | `painel`: upload `.txt` (um símbolo por linha); `vcf`: caminho (ex.: `modules/genomica/demo_data/demo_bta14_inicio.vcf.gz` ou `/data/1000bg_vcf/Chr14-Run8-TAUIND-public.bcf`) |
| `genomica_regioes` (`omicaGenomRegi01`) | `regioes`: upload `.bed` (0-based; o formulário nem oferece `.txt`, que o bcftools leria 1-based); `vcf`: caminho |
| `ml_aims_vcf` (`omicaMLAimsVcf01`) | `vcf`: caminho, VCF/BCF indexado **com genótipos** (ex.: `/data/1000bg_vcf/Chr14-Run8-TAUIND-public.bcf`, por referência); `rotulos`: upload `.tsv`/`.txt` (amostra, grupo; ex.: `modules/ml/data/rotulos_1000bg_racas.tsv`) |
| `epigenomica_dmc` (`omicaEpigDMC0001`) | `coverages`: caminho de PASTA (ex.: `modules/epigenomica/demo_data`); `amostras`: upload `.tsv`/`.csv`/`.txt` (amostra, condicao; adaptável); `painel`: upload `.json` |
| `epigenomica_mofa` (`omicaEpigMOFA001`) | as mesmas 3 entradas do `epigenomica_dmc` |

Os dois da genômica e o `epigenomica_mofa` não têm escolhas do autor (ramo `Sem escolhas`); os
da genômica também não têm figura (o relatório sai só com texto e tabelas, e o redator é
avisado). No `ml_aims_vcf` (classe A e classe B, de `rotulos.grupo`) e no `epigenomica_dmc`
(grupo A e grupo B, de `amostras.condicao`) as duas escolhas têm de ser diferentes: a página
avisa, e A = B volta da ômica como 422 `escolhas_iguais`, em `Fim: plano recusado`. Dados com
cromossomos `chr*` (ex.: W15) reprovam cedo no `epigenomica_dmc` (checagem
`coverages_contigs_ars_ucd`, portão de contig do HOMER) e só rodam no `epigenomica_mofa`.

## Gerar

```bash
python n8n/build_workflows.py
```

`--checar` só confere se `dist/` está em dia (para o CI). O builder também reprova o JSON com
padrão de segredo, aresta para nó inexistente ou nó sem entrada.

## Importar e publicar (n8n 2.34.5)

O n8n é compartilhado com o scijournal: **backup antes** (o script para o n8n por ~1 min).

```powershell
powershell -File C:\github\n8n-infra\scripts\backup_n8n.ps1
docker cp C:\github\omica\n8n\dist n8n:/tmp/omica_wf
docker exec n8n n8n import:workflow --separate --input=/tmp/omica_wf
docker exec n8n n8n publish:workflow --id=omicaTranscrDE01
docker restart n8n
```

**Genômica (ordem de import na F5; histórico — na F6 vale o bloco abaixo).** Os dois workflows novos entram sem tocar no da transcriptômica,
que segue publicado e idêntico — reimportá-lo exigiria publicar de novo à toa. Antes: a caixa
`E:\data\omica\entradas` tem de existir (sem ela a raiz é ignorada em silêncio) e nenhum
`*-Run8-TAUIND-public.vcf.gz` pode estar em `/data/1000bg_vcf` (o supervisor relançaria o
GT-only, que reescreve os `.bcf`).

```powershell
powershell -File C:\github\n8n-infra\scripts\backup_n8n.ps1
docker exec n8n mkdir -p /tmp/omica_wf_genomica
docker cp C:\github\omica\n8n\dist\genomica_regioes.json n8n:/tmp/omica_wf_genomica/
docker cp C:\github\omica\n8n\dist\genomica_painel.json n8n:/tmp/omica_wf_genomica/
docker exec n8n n8n import:workflow --separate --input=/tmp/omica_wf_genomica
docker exec n8n n8n publish:workflow --id=omicaGenomRegi01
docker exec n8n n8n publish:workflow --id=omicaGenomPain01
docker restart n8n
```

O `genomica_regioes` primeiro: é o da paridade com o selo (E2E-1, `painel_demo.bed` × demo); o
`genomica_painel` (E2E-2) só depois de ele bater. No primeiro envio por caminho, conferir que o
gatilho 2.6 devolve o campo de **texto** pelo `fieldName` (até aqui só arquivos usaram
`fieldName` no gatilho); o `Separar caminhos` também procura pelo rótulo, por segurança.

**F6 (D-10 + ML + epigenômica): os 6 de uma vez.** Os 3 já publicados MUDARAM (página de
contexto e corpo do `POST /runs`): reimportar e publicar de novo — as URLs `/form/<webhookId>`
não mudam — e refazer o E2E-0. Os 3 novos entram junto. Antes: a F5 aplicada (o
`projetos/bos_taurus/demo/subprojeto.yaml` lista os 6 pipelines; sem isso, o formulário do ML e
os da epigenômica param em "nenhum subprojeto aceita este pipeline"), web e workers com o
código novo e a caixa `E:\data\omica\entradas` criada.

```powershell
powershell -File C:\github\n8n-infra\scripts\backup_n8n.ps1
docker exec n8n rm -rf /tmp/omica_wf_f6
docker cp C:\github\omica\n8n\dist n8n:/tmp/omica_wf_f6
docker exec n8n n8n import:workflow --separate --input=/tmp/omica_wf_f6
docker exec n8n n8n publish:workflow --id=omicaTranscrDE01
docker exec n8n n8n publish:workflow --id=omicaGenomRegi01
docker exec n8n n8n publish:workflow --id=omicaGenomPain01
docker exec n8n n8n publish:workflow --id=omicaMLAimsVcf01
docker exec n8n n8n publish:workflow --id=omicaEpigMOFA001
docker exec n8n n8n publish:workflow --id=omicaEpigDMC0001
docker restart n8n
```

Ordem do E2E (adendo F6 §A11): E2E-0 (transcriptômica na aula e no demo; `genomica_regioes` no
demo, com o dropdown só com `bos_taurus/demo`) → E2E-ML → E2E-EPI-B (`epigenomica_mofa`) →
E2E-EPI-A (`epigenomica_dmc`, o mais longo: HOMER com `mem_gb` 6).

Os ids dos workflows são fixos (`IDS_WORKFLOW` no builder): reimportar substitui o anterior,
e todo reimport exige publicar de novo. As credenciais são referenciadas pelo id (`omica
Bearer` e `Anthropic x-api-key`, criadas pelo autor no n8n); nenhum segredo entra no JSON.

Formulário publicado: o n8n 2.34.5 registra a URL de produção pelo `webhookId` do gatilho
(o `path` só vale no editor), que o builder imprime (fixo: o id é derivado do pipeline):

| Pipeline | Formulário |
|---|---|
| `transcriptomica_de` | `http://127.0.0.1:5678/form/bf0000ba-ffcc-539f-a7d2-8d12515f60db` |
| `genomica_regioes` | `http://127.0.0.1:5678/form/55903e71-5266-5a5f-af4e-95a133058456` |
| `genomica_painel` | `http://127.0.0.1:5678/form/0526ce60-c843-5b08-aec4-b49cbb441aee` |
| `ml_aims_vcf` | `http://127.0.0.1:5678/form/ba2d82eb-e07f-58a7-9c5e-b2eed161ebb8` |
| `epigenomica_dmc` | `http://127.0.0.1:5678/form/66cd3172-badc-5e3a-8db6-a5959b7fff35` |
| `epigenomica_mofa` | `http://127.0.0.1:5678/form/662d9978-6a96-5d7d-8058-4ab06a5d4ed5` |

Logo depois do `docker restart`, o n8n leva alguns segundos para ativar os workflows:
um 404 nesse intervalo é normal.
