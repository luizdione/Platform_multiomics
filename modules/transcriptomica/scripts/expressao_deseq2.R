#!/usr/bin/env Rscript
# expressao_deseq2.R — expressão diferencial com DESeq2 (design ~ condicao).
#
# Fase 4: roda no worker omica-methyl reaproveitando o DESeq2 JÁ COMPILADO no
# volume epigenoma_r_lib (mesma decisão de reuso da fase 3). O demo usa a
# matriz REAL do nf-core do sub03 (granulosa Angus, 4 summer_heat × 4
# winter_control) versionada em demo_data — contagens de tximport arredondadas.
#
# Uso:
#   Rscript modules/transcriptomica/scripts/expressao_deseq2.R \
#     --counts modules/transcriptomica/demo_data/counts_granulosa.tsv \
#     --coldata modules/transcriptomica/demo_data/coldata_granulosa.tsv \
#     --referencia winter_control --padj 0.05 --lfc 1 --saida <dir>

.libPaths(c("/rlib", .libPaths()))
suppressPackageStartupMessages({ library(DESeq2); library(jsonlite) })

args <- commandArgs(trailingOnly = TRUE)
pegar <- function(nome, padrao = NULL) {
  i <- which(args == nome)
  if (length(i) == 1 && i < length(args)) args[i + 1] else padrao
}
counts_path <- pegar("--counts"); coldata_path <- pegar("--coldata")
referencia <- pegar("--referencia", "winter_control")
padj_corte <- as.numeric(pegar("--padj", "0.05"))
lfc_corte  <- as.numeric(pegar("--lfc", "1"))
saida      <- pegar("--saida")
stopifnot(!is.null(counts_path), !is.null(coldata_path), !is.null(saida))
dir.create(saida, recursive = TRUE, showWarnings = FALSE)

bruto <- read.delim(counts_path, check.names = FALSE)
coldata <- read.delim(coldata_path, check.names = FALSE)
stopifnot(all(c("amostra", "condicao") %in% colnames(coldata)))
contagens <- as.matrix(bruto[, coldata$amostra])
# DESeq2 exige contagens INTEIRAS e recusa decimais com erro. storage.mode()
# TRUNCA em silêncio (2.9 -> 2), o que desliga essa proteção: uma matriz de
# NumReads do Salmon passaria adiante com ~0,5 contagem perdida por gene.
if (any(!is.finite(contagens))) stop("matriz de contagens com NA/Inf")
if (any(contagens < 0)) stop("matriz com valores negativos — não são contagens")
nao_inteiros <- sum(abs(contagens - round(contagens)) > 1e-8)
if (nao_inteiros > 0)
  cat(sprintf("AVISO: %d valores não inteiros (ex.: NumReads do Salmon) ARREDONDADOS\n",
              nao_inteiros))
contagens <- round(contagens)
storage.mode(contagens) <- "integer"
rownames(contagens) <- bruto$gene_id
coldata$condicao <- relevel(factor(coldata$condicao), ref = referencia)
cat(sprintf("Matriz: %d genes x %d amostras; referência: %s\n",
            nrow(contagens), ncol(contagens), referencia))

dds <- DESeqDataSetFromMatrix(contagens, coldata, design = ~condicao)
dds <- dds[rowSums(counts(dds)) >= 10, ]
dds <- DESeq(dds, quiet = TRUE)
res <- results(dds)
contraste <- resultsNames(dds)[length(resultsNames(dds))]
cat(sprintf("Contraste: %s; genes testados: %d\n", contraste, nrow(res)))

tab <- as.data.frame(res)
tab$gene_id <- rownames(tab)
mapa_nome <- setNames(bruto$gene_name, bruto$gene_id)
tab$gene_name <- mapa_nome[tab$gene_id]
tab <- tab[order(tab$padj), c("gene_id", "gene_name", "baseMean",
                              "log2FoldChange", "pvalue", "padj")]
tsv <- file.path(saida, "de_table.tsv")
write.table(tab, tsv, sep = "\t", quote = FALSE, row.names = FALSE)

deg <- subset(tab, !is.na(padj) & padj < padj_corte)
deg_lfc <- subset(deg, abs(log2FoldChange) >= lfc_corte)
cat(sprintf("DEGs (padj<%.2g): %d; com |log2FC|>=%.1f: %d (up %d / down %d)\n",
            padj_corte, nrow(deg), lfc_corte, nrow(deg_lfc),
            sum(deg_lfc$log2FoldChange > 0), sum(deg_lfc$log2FoldChange < 0)))

resumo <- file.path(saida, "de_resumo.json")
writeLines(toJSON(list(
  gerado_em = format(Sys.time(), "%Y-%m-%dT%H:%M:%S"),
  contraste = contraste,
  n_amostras = ncol(contagens),
  n_genes_testados = nrow(res),
  criterios = list(padj = padj_corte, lfc = lfc_corte),
  n_deg_padj = nrow(deg),
  n_deg_padj_e_lfc = nrow(deg_lfc),
  n_up = sum(deg_lfc$log2FoldChange > 0),
  n_down = sum(deg_lfc$log2FoldChange < 0),
  top_gene = if (nrow(tab) > 0) tab$gene_name[1] else NA,
  tabela_tsv = tsv
), auto_unbox = TRUE, pretty = TRUE, digits = 6, na = "null"), resumo)
cat(sprintf("RESULTADO: %s\n", resumo))
