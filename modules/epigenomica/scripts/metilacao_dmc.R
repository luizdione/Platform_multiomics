#!/usr/bin/env Rscript
# metilacao_dmc.R — DMCs (citosinas diferencialmente metiladas) com methylKit.
#
# Migração curada da Fase 1 da epigenome_platform (congelada): mesmos limiares
# do paper (|diff| > 15%, q < 0,05, correção BH), reescrita para o contrato de
# jobs da ômica (stdout → console; linha "RESULTADO: <path>" → UI).
# Bibliotecas R: volume /rlib (compiladas pela plataforma antiga — reuso).
#
# Uso:
#   Rscript modules/epigenomica/scripts/metilacao_dmc.R \
#     --dir modules/epigenomica/demo_data --grupo-a Nelore --grupo-b HF \
#     --min-cov 5 --diff 15 --qvalue 0.05 --saida /data/omica/results/epigenomica/demo/dmc

.libPaths(c("/rlib", .libPaths()))
suppressPackageStartupMessages({ library(methylKit); library(jsonlite) })

args <- commandArgs(trailingOnly = TRUE)
pegar <- function(nome, padrao = NULL) {
  i <- which(args == nome)
  if (length(i) == 1 && i < length(args)) args[i + 1] else padrao
}
dir_cov  <- pegar("--dir");        grupo_a <- pegar("--grupo-a", "Nelore")
grupo_b  <- pegar("--grupo-b", "HF")
min_cov  <- as.integer(pegar("--min-cov", "5"))
diff_pct <- as.numeric(pegar("--diff", "15"))
q_corte  <- as.numeric(pegar("--qvalue", "0.05"))
saida    <- pegar("--saida")
stopifnot(!is.null(dir_cov), !is.null(saida))
dir.create(saida, recursive = TRUE, showWarnings = FALSE)

todos <- list.files(dir_cov, pattern = "\\.cov(\\.gz)?$", full.names = TRUE)
arq_a <- todos[grepl(grupo_a, basename(todos), fixed = TRUE)]
arq_b <- todos[grepl(grupo_b, basename(todos), fixed = TRUE)]
cat(sprintf("Grupo %s: %d amostras; grupo %s: %d amostras (dir %s)\n",
            grupo_a, length(arq_a), grupo_b, length(arq_b), dir_cov))
if (length(arq_a) < 2 || length(arq_b) < 2) stop("cada grupo precisa de >=2 amostras .cov(.gz)")

obj <- methRead(as.list(c(arq_a, arq_b)),
                sample.id = as.list(c(basename(arq_a), basename(arq_b))),
                assembly = "ARS-UCD1.2", treatment = c(rep(1, length(arq_a)), rep(0, length(arq_b))),
                context = "CpG", pipeline = "bismarkCoverage", mincov = min_cov)
unido <- methylKit::unite(obj, destrand = FALSE)
cat(sprintf("CpGs cobertos (>=%dx) em TODAS as amostras: %d\n", min_cov, nrow(unido)))
if (nrow(unido) == 0) stop("nenhum CpG comum às amostras com essa cobertura")

dif <- calculateDiffMeth(unido, mc.cores = 1)
dmc <- getMethylDiff(dif, difference = diff_pct, qvalue = q_corte)
tab <- getData(dmc)
cat(sprintf("DMCs (|diff|>%.0f%%, q<%.2g): %d — hiper: %d, hipo: %d\n",
            diff_pct, q_corte, nrow(tab), sum(tab$meth.diff > 0), sum(tab$meth.diff < 0)))

tsv <- file.path(saida, "dmc.tsv")
write.table(tab, tsv, sep = "\t", quote = FALSE, row.names = FALSE)

resumo <- file.path(saida, "dmc_resumo.json")
writeLines(toJSON(list(
  gerado_em = format(Sys.time(), "%Y-%m-%dT%H:%M:%S"),
  grupo_a = list(nome = grupo_a, n = length(arq_a)),
  grupo_b = list(nome = grupo_b, n = length(arq_b)),
  criterios = list(min_cov = min_cov, diff_pct = diff_pct, qvalue = q_corte, correcao = "BH (methylKit/SLIM)"),
  n_cpg_testados = nrow(unido),
  n_dmc = nrow(tab),
  n_hiper_no_grupo_a = sum(tab$meth.diff > 0),
  n_hipo_no_grupo_a = sum(tab$meth.diff < 0),
  tabela_tsv = tsv
), auto_unbox = TRUE, pretty = TRUE, digits = 6), resumo)
cat(sprintf("RESULTADO: %s\n", resumo))
