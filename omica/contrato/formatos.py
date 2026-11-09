"""formatos.py — validadores DETERMINÍSTICOS (Camada 0) dos formatos que passam entre passos.

Respondem "este arquivo está no formato que o próximo script consome?" sem LLM nenhum. O
portão do envelope (F2) e o agente adaptador (F3) dependem daqui: o adaptador só PROPÕE um
mapeamento de colunas; quem decide se o resultado serve é sempre um destes validadores.

Só biblioteca padrão — a imagem web não tem pandas.

Cada validador devolve um Veredito com `ok`, a lista de `falhas` (texto para humano) e um
`perfil` (números que o plano e o relatório podem citar). Formato desconhecido ou arquivo
ausente/vazio é falha, nunca "ok por omissão".

Revisão adversarial da F2 (2026-10-19): o portão tem de ler COMO O CONSUMIDOR LÊ — cada
divergência era um arquivo aprovado aqui e lido de outro jeito pelo script.
  - Aspas reprovam (leitura com QUOTE_NONE). O read.delim do R abre campo entre aspas até no
    MEIO da célula (`x"y` engole as linhas seguintes: conferido no R 4.5.3), o csv do Python
    não, e o volcano faz split('\\t') cru. Sem aspas, os três leem igual. Quem tira as aspas
    de um arquivo exportado pelo R/Excel é o adaptador (F3), deterministicamente.
  - Nada de normalizar o que o read.delim não normaliza: célula de texto com espaço em volta
    e linha só com separadores reprovam (o R lê 'A ' como outro nível e a linha de TABs como
    gene com NA). Só o cabeçalho é aparado, porque o read.table também o apara.
  - Nome de amostra ou nível que o type.convert do R converte (3, 1.0, TRUE, NA, 1e5, 0x1A)
    reprova: com a coluna `amostra` numérica, `bruto[, coldata$amostra]` escolhe as colunas da
    matriz por POSIÇÃO e o DESeq2 devolve outro contraste, sem aviso nenhum.
  - Leitura em FLUXO, com tetos: nenhum validador guarda as linhas (antes: ~10x o arquivo em
    RAM); csv.Error (campo > 131072) e linha gigante viram falha, nunca exceção — uma exceção
    aqui era um 500 com a entrada já trocada no disco.
  - As falhas não ecoam conteúdo do arquivo: só nomes de coluna limitados (_nomes) e
    posições/contagens. Um caminho de entrada mal escolhido não pode virar leitor de arquivo.

Resíduos da 1ª rodada de correção (2026-10-20):
  - Aparar é o que o R apara, não o que o str.strip() do Python apara: cabeçalho, só ESPAÇO
    (o read.delim deixa NBSP e \\x0b no nome: 'S1\\xa0' não acha a coluna 'S1'); número, só o
    espaço ASCII do C (' 30', '30\\f' viram 30; '10\\xa0' vira texto). Conferido no R 4.5.3.
  - O teto conta a linha FÍSICA, inclusive a vazia: 4 linhas válidas + 30 milhões de '\\n'
    passavam, e cada PUT custava ~40 s de CPU em três passadas.
  - "achei ..." de um arquivo que não tem as colunas do formato só ecoa nome com cara de
    identificador curto: a 1ª linha de um arquivo qualquer (um .env) voltava inteira.
  - Nível 'vs_x' / 'x_vs' reprova: com ele, o nome do contraste do DESeq2 fica ambíguo.
  - tabela_de reprova gene_id repetido: é a CHAVE do relatório (COLUNAS_CHAVE).

Genômica (F5, 2026-10-26; especificação G1 §2 + crítica): nove formatos e três checagens,
lidos como o consumidor lê — o script selado, o bcftools/htslib ou o SnpEff/VEP. O parse
binário (BGZF, TBI/CSI, BCF) mora em omica/contrato/bgzf.py, que nunca deixa sair exceção
crua e tem teto de descompressão por bloco e por índice (crítica #2: bomba de 1028×).
  - O NOME muda a leitura (BED só é 0-based com '.bed'; o índice é '<arquivo>.tbi/.csi'; o
    coordenadas_painel nomeia as saídas pelo stem), mas a regra de nome fica FORA do
    validador de conteúdo: o adaptador valida temporários '<nome>.adaptando.<hex>.tsv'.
    `regra_de_nome` roda no contrato (portas, estático) e no registro da entrada — e no modo
    caminho também sobre o nome da ORIGEM (crítica #5b: um BED 1-based '.txt' copiado como
    'regioes.bed' passaria a ser lido 0-based, em silêncio).
  - Com '.tbi' e '.csi' ao lado do mesmo VCF, o htslib carrega o '.csi' (um '.csi' lixo ao
    lado de um '.tbi' bom derruba o bcftools com rc 255): reprova como ambíguo (crítica #4).
  - BED: '\\r' isolado reprova (o bcftools 1.19 extrai 0 variantes com rc 0); CRLF passa
    (mesmas contagens que LF, conferido no 1.19) — crítica #7, que substitui a decisão 13.
  - Painel: os separadores que só o splitlines corta (\\x0b, \\x0c, U+2028…) são procurados
    ANTES do splitlines (crítica #5a: depois dele a regra era código morto).
  - Checagens reprovam, nunca levantam (crítica #6: runs._checagens as chama cruas, e a
    exceção virava 500 com a cópia já gravada), e reprovam VCF ilegível (crítica #5c).

ML e Epigenômica (F6, ESPEC §2 + adendo v2 §A6; 2026-10-29): seis formatos do ML, onze da
epigenômica e quatro checagens. Dois formatos são PASTA (DIRETORIOS): o `validar` desvia
para o validador de pasta, que lista os membros com omica/contrato/diretorio.py e guarda o
veredito num cache pela identidade da pasta e de cada membro.
  - O `.cov.gz` é lido em TODOS os membros gzip, como o gzip do Python e o gzfile do R (crítica
    M11: um decompressobj único parava no 1º e um 2º membro com cabeçalho ou bomba passava).
  - Tetos de descompressão por membro (razão ≤ 20:1 acima de 1 MiB) e por PASTA (linhas e
    bytes somados, em fluxo; crítica M12). Estouro de teto vai no perfil (`teto`), para o
    registro responder 413 e não 422.
  - Resumo com NaN/Infinity reprova (X2: `json_finito`); o JSON da API não os transporta.
"""
from __future__ import annotations

import copy
import csv
import functools
import gzip
import hashlib
import json
import math
import os
import re
import stat
import struct
import time
import zlib
from array import array
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from omica.contrato import bgzf, diretorio

_NA ={"", "NA", "NaN", "nan", "N/A", "null", "None"}
_PNG = b"\x89PNG\r\n\x1a\n"

# Tetos da leitura em fluxo: memória e CPU limitadas mesmo com entrada adversária. Generosos
# para dado real (matriz bovina: 27 mil genes; transcritos humanos: ~250 mil; coldata real:
# dezenas de amostras), apertados para um arquivo que não é tabela de genes.
_MAX_LINHA = 4 * 1024 * 1024     # caracteres numa linha física
_MAX_GENES = 1_000_000           # linhas de uma matriz/tabela de genes
_MAX_AMOSTRAS = 10_000           # colunas de amostra na matriz; linhas no coldata
_MAX_RESUMO = 16 * 1024 * 1024   # bytes de um resumo JSON

# Colunas que cada formato GARANTE ter (o validador reprova sem elas). Fonte única: o
# validar_contrato do pipeline só aceita escolha e tabela de relatório dentro delas.
COLUNAS_GARANTIDAS: dict[str, tuple[str, ...]] = {
    "contagens_genes": ("gene_id", "gene_name"),
    "coldata": ("amostra", "condicao"),
    "tabela_de": ("gene_id", "gene_name", "baseMean", "log2FoldChange", "pvalue", "padj"),
}
# Colunas de NÍVEL: as únicas que podem alimentar uma escolha do autor. A escolha vira
# argumento do script, e só aqui o validador garante valor que o sanitizador aceita.
COLUNAS_DE_NIVEL: dict[str, tuple[str, ...]] = {"coldata": ("condicao",)}
# Colunas-CHAVE: garantidas e de valor ÚNICO e não vazio (o validador reprova repetido). Só
# elas servem de `chave` de relatorio.tabelas — o portão de números exige achar a chave
# exatamente 1 vez, e com chave repetida reprovaria só no fim do run inteiro (revisão F2 #20).
COLUNAS_CHAVE: dict[str, tuple[str, ...]] = {
    "contagens_genes": ("gene_id",),
    "coldata": ("amostra",),
    "tabela_de": ("gene_id",),
}


@dataclass
class Veredito:
    formato: str
    ok: bool
    falhas: list[str] = field(default_factory=list)
    perfil: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class _Ilegivel(ValueError):
    """Leitura abortada: o arquivo não pode ser lido como tabela que o consumidor entenda."""


class _Teto(_Ilegivel):
    """Leitura abortada no teto de linhas: a mensagem já é a falha, sem o prefixo de ilegível."""


# ------------------------------------------------------------------ mensagens
# nome que pode voltar numa falha sobre um arquivo que NÃO tem as colunas do formato (pode nem
# ser tabela): só o que tem cara de identificador curto. Com '=', espaço, ':' ou acento fora,
# 'TOKEN=valor' de um .env não volta inteiro (revisão F2, resíduo do #42).
_NOME_ECOAVEL = re.compile(r"[A-Za-z0-9_.\-]{1,40}", re.ASCII)


def _nome(nome: str, limite: int = 40) -> str:
    """Nome de coluna para mensagem: curto e só imprimível. As falhas vão para o run.json e
    para a resposta da API — não podem virar eco do conteúdo de um arquivo qualquer."""
    curto = "".join(c if c.isprintable() else "?" for c in nome[:limite])
    return curto + ("…" if len(nome) > limite else "")


def _nome_seguro(nome: str) -> str:
    if not nome:
        return "<vazio>"
    return nome if _NOME_ECOAVEL.fullmatch(nome) else "<omitido>"


def _nomes(nomes: list[str], limite: int = 10, so_identificadores: bool = False) -> str:
    """Lista curta de nomes para mensagem. `so_identificadores` = o arquivo ainda não provou
    ser do formato (falta coluna obrigatória): nome fora do padrão vira '<omitido>'."""
    formatar = _nome_seguro if so_identificadores else _nome
    mostrados = [formatar(n) for n in nomes[:limite]]
    resto = len(nomes) - limite
    return f"{mostrados}" + (f" (+{resto} colunas)" if resto > 0 else "")


class _Ocorrencias:
    """Falhas repetidas linha a linha viram UMA mensagem por tipo, com até 5 posições e a
    contagem — o relatório não pode virar um despejo de 20 mil linhas, e a memória da
    leitura em fluxo não cresce com o número de falhas."""

    def __init__(self, limite: int = 5) -> None:
        self._limite = limite
        self._por_tipo: dict[str, list] = {}

    def add(self, tipo: str, posicao: str) -> None:
        reg = self._por_tipo.setdefault(tipo, [0, []])
        reg[0] += 1
        if len(reg[1]) < self._limite:
            reg[1].append(posicao)

    def __bool__(self) -> bool:
        return bool(self._por_tipo)

    def lista(self) -> list[str]:
        saida = []
        for tipo, (n, posicoes) in self._por_tipo.items():
            mais = f" e mais {n - len(posicoes)}" if n > len(posicoes) else ""
            saida.append(f"{tipo} — {', '.join(posicoes)}{mais}")
        return saida


# --------------------------------------------------------------------- leitura
class _Tsv:
    """Leitor em FLUXO de TSV, sem tratar aspas (QUOTE_NONE): cabeçalho + linhas (número
    físico, campos). Pula só a linha realmente vazia (blank.lines.skip do R): a linha só com
    TAB ou espaço chega ao validador, porque o R a lê como linha de dado. Anota as linhas com
    aspas ou NUL — todos os validadores as reprovam (falha_aspas).

    O cabeçalho é aparado só de ESPAÇO, como o read.delim (que deixa NBSP e \\x0b no nome).
    `teto` = (máximo de linhas de dado, mensagem): conta a linha FÍSICA, vazia inclusive — a
    linha vazia é pulada, mas custa leitura (30 milhões de '\\n' eram ~13 s por passada)."""

    def __init__(self, f, aparar_cabecalho: bool, teto: Optional[tuple[int, str]] = None) -> None:
        self.n_suspeitas = 0
        self._suspeitas: list[int] = []
        self._teto = teto
        self._leitor = csv.reader(self._fisicas(f), delimiter="\t", quoting=csv.QUOTE_NONE)
        bruto = next(self._leitor, None) or []
        self.cab = [c.strip(" ") for c in bruto] if aparar_cabecalho else bruto

    def _fisicas(self, f) -> Iterator[str]:
        n = 0
        maximo = self._teto[0] + 1 if self._teto else None  # + a linha do cabeçalho
        while True:
            linha = f.readline(_MAX_LINHA + 1)
            if not linha:
                return
            n += 1
            if maximo is not None and n > maximo:
                raise _Teto(self._teto[1])
            if len(linha) > _MAX_LINHA:
                raise _Ilegivel(f"linha {n} com mais de {_MAX_LINHA} caracteres")
            if '"' in linha or "\x00" in linha:
                self.n_suspeitas += 1
                if len(self._suspeitas) < 5:
                    self._suspeitas.append(n)
            yield linha

    def linhas(self) -> Iterator[tuple[int, list[str]]]:
        for campos in self._leitor:
            if campos:
                yield self._leitor.line_num, campos

    def falha_aspas(self) -> list[str]:
        if not self.n_suspeitas:
            return []
        mais = f" e mais {self.n_suspeitas - len(self._suspeitas)}" if self.n_suspeitas > 5 else ""
        return [f"aspas (\") ou caractere NUL nas linhas {self._suspeitas}{mais}: o read.delim do R "
                "abre campo entre aspas até no meio da célula e juntaria linhas; regrave sem aspas "
                "(o adaptador faz isso sozinho)"]


@contextmanager
def _abrir(caminho: Path, aparar_cabecalho: bool = True,
           teto: Optional[tuple[int, str]] = None) -> Iterator[_Tsv]:
    with caminho.open(encoding="utf-8", newline="") as f:
        yield _Tsv(f, aparar_cabecalho, teto)


def _a_prova_de_leitura(func: Callable[[Path], "Veredito"]) -> Callable[[Path], "Veredito"]:
    """Um portão REPROVA; nunca levanta. Vale também para a chamada direta (sem validar()):
    diretório ou arquivo sem permissão (OSError) também é falha."""
    formato = func.__name__

    @functools.wraps(func)
    def envolto(caminho: Path) -> Veredito:
        try:
            return func(caminho)
        except UnicodeDecodeError:
            return Veredito(formato, False, ["arquivo não é texto UTF-8"])
        except csv.Error as e:
            # a mensagem do csv é só estrutural ("field larger than field limit (131072)")
            return Veredito(formato, False, [f"TSV ilegível ({str(e)[:80]}): campo gigante — "
                                             "o arquivo não é uma tabela separada por TAB?"])
        except _Teto as e:
            return Veredito(formato, False, [str(e)])
        except _Ilegivel as e:
            return Veredito(formato, False, [f"TSV ilegível: {e}"])
        except bgzf.Ilegivel as e:
            # a mensagem do bgzf é só posição/contagem (nunca bytes do arquivo)
            return Veredito(formato, False, [f"binário ilegível: {e}"])
        except EOFError:
            return Veredito(formato, False, ["gzip truncado: o arquivo acaba no meio de um membro"])
        except OSError as e:
            # só o tipo: a mensagem do sistema traz o caminho inteiro
            return Veredito(formato, False, [f"arquivo ilegível ({type(e).__name__})"])
    return envolto


# ------------------------------------------------------- o que o R lê como texto
# O read.delim passa cada coluna pelo type.convert: se TODOS os valores são número, lógico ou
# complexo, a coluna deixa de ser texto; "NA" vira NA em qualquer coluna. O portão é mais
# conservador que o R: reprova o valor que, sozinho, seria convertido. Conferido contra o
# type.convert do R 4.5.3 numa bateria de 100 casos (tests/test_formatos_canarios.py): as
# divergências são todas para o lado que reprova ('0x', 'true'/'false', lógicos no R antigo).
_R_NUM_U = (r"(?:(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?"
            r"|0[xX][0-9a-fA-F.]*(?:[pP][+-]?\d*)?"
            r"|(?i:inf(?:inity)?|nan))")
_R_NAO_TEXTO = re.compile(rf"[+-]?{_R_NUM_U}|(?:[+-]?{_R_NUM_U})?[+-]?{_R_NUM_U}i", re.ASCII)
_R_LOGICO = frozenset({"T", "F", "TRUE", "FALSE", "true", "false"})

# Nível de condição vira argumento do script (a referência do contraste). Subconjunto do que
# dispatch._validar_valor aceita para tipo texto SEM cair na checagem de caminho ('/', '=' e
# ':' ficam de fora): o que passa aqui passa no plano (teste de sincronia em
# tests/test_formatos_canarios.py). formatos fica sem importar o dispatch de propósito.
_NIVEL_ARGV = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-]*", re.ASCII)

# número de contagem: só o que o type.convert do R E o float() do Python leem igual
# (o float aceita '1_000', dígito árabe e 'infinity'; o R não os lê como número)
_DECIMAL = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?", re.ASCII)


def r_le_como_nao_texto(v: str) -> bool:
    """O read.delim converteria este valor para número, lógico, complexo ou NA?"""
    return v == "NA" or v in _R_LOGICO or _R_NAO_TEXTO.fullmatch(v) is not None


_MSG_ESPACO = "espaço no início/fim da célula (o R não apara células: 'A ' e 'A' são coisas diferentes)"
_MSG_CONTROLE = "caractere de controle no texto"
_MSG_AMOSTRA_R = ("nome de amostra que o R lê como número/lógico/NA (ex.: 3, 1e5, TRUE, NA): o "
                  "read.delim converte a coluna e o script escolhe as colunas da matriz por POSIÇÃO — "
                  "resultado errado sem aviso; prefixe os nomes (ex.: a3) na matriz e no coldata")
_MSG_NIVEL_R = ("condição que o R lê como número/lógico/NA: o nível muda ('1.0' vira '1', 'T' vira "
                "'TRUE') e a referência escolhida deixa de existir; use um nome com letra (ex.: dose_1)")
_MSG_NIVEL_ARGV = ("condição que não pode ir para a linha de comando do script (só letras ASCII, "
                   "dígitos, '_', '.' e '-', sem começar por '-' nem ter '..'; sem espaço nem acento): "
                   "a escolha da referência seria recusada no plano; renomeie no coldata "
                   "(ex.: 'estresse térmico' → 'estresse_termico')")
_MSG_NIVEL_VS = ("condição que começa com 'vs_' ou termina com '_vs': o DESeq2 nomeia o contraste "
                 "'<tratamento>_vs_<referência>', e 'tratado_vs' × 'ctrl' dá o mesmo nome que "
                 "'tratado' × 'vs_ctrl' — a figura não teria como dizer qual é a referência; "
                 "renomeie no coldata (ex.: 'tratado_vs' → 'tratado')")

# espaço que o type.convert do R ignora em volta de um número: o ASCII do isspace do C
# (conferido no R 4.5.3: ' 30', '30\f', '\v30' viram número; '10\xa0' fica texto)
_ESPACO_R = " \t\n\r\x0b\x0c"


def _problema_amostra(v: str) -> Optional[str]:
    """Por que um nome de amostra não chegaria intacto ao R (None = chega)."""
    if v != v.strip():
        return _MSG_ESPACO
    if not v.isprintable():
        return _MSG_CONTROLE
    if r_le_como_nao_texto(v):
        return _MSG_AMOSTRA_R
    return None


def _problema_nivel(v: str) -> Optional[str]:
    """Por que um nível de condição não serviria de referência (None = serve)."""
    if v != v.strip():
        return _MSG_ESPACO
    if not v.isprintable():
        return _MSG_CONTROLE
    if r_le_como_nao_texto(v):
        return _MSG_NIVEL_R
    if not _NIVEL_ARGV.fullmatch(v) or ".." in v:
        return _MSG_NIVEL_ARGV
    if v.startswith("vs_") or v.endswith("_vs"):
        return _MSG_NIVEL_VS
    return None


def _contagem(bruto: str) -> str:
    """'ok' | 'decimal' | 'invalida' | 'nao_numerico'. Apara SÓ o espaço que o type.convert do
    R ignora num número (_ESPACO_R): o str.strip() do Python tira também NBSP e \\x1c–\\x1f, e
    '10\\xa0' — texto para o R, que para em 'matriz de contagens com NA/Inf' — passava aqui."""
    t = bruto.strip(_ESPACO_R)
    if t in _NA or t.lstrip("+-").lower() in ("inf", "infinity", "nan"):
        return "invalida"
    if not _DECIMAL.fullmatch(t):
        return "nao_numerico"
    x = float(t)
    if not math.isfinite(x) or x < 0:
        return "invalida"
    return "ok" if x == int(x) else "decimal"


def _numero(texto: str) -> Optional[float]:
    """float do texto, ou None para NA. Levanta ValueError para texto não numérico. É a regra
    do CONSUMIDOR da tabela_de (volcano e relatório, ambos em Python com float())."""
    t = texto.strip()
    if t in _NA:
        return None
    return float(t)


# --------------------------------------------------------------- transcriptômica
@_a_prova_de_leitura
def contagens_genes(caminho: Path) -> Veredito:
    """Matriz genes × amostras que o expressao_deseq2.R consome: 1ª coluna `gene_id`, 2ª
    `gene_name` (OBRIGATÓRIA: o R faz setNames(bruto$gene_name, ...) e quebra sem ela, depois
    de rodar o DESeq inteiro; sem símbolo, repete-se o gene_id — o adaptador faz isso com
    `copiar`), e ≥2 colunas de amostra com contagens ≥0 e finitas. Coluna de anotação (texto)
    reprova: o portão não sabe quais colunas o coldata vai citar, e texto livre é onde moram
    as aspas que o R e o Python leem diferente — o adaptador a tira com `selecionar`.
    Decimais NÃO reprovam (o script arredonda declarando quantos), mas são contados."""
    teto = (_MAX_GENES, f"mais de {_MAX_GENES} linhas: não é matriz de genes (teto da leitura; "
                        "linhas vazias contam)")
    with _abrir(caminho, teto=teto) as t:
        cab = t.cab
        if not cab or cab[0] != "gene_id":
            dica = " — o arquivo começa com BOM (o adaptador o remove)" if cab and cab[0].startswith("﻿") else ""
            return Veredito("contagens_genes", False,
                            [f"1ª coluna deve ser 'gene_id' (achei {_nomes(cab[:1], so_identificadores=True)}){dica}"]
                            + t.falha_aspas())
        if len(cab) < 2 or cab[1] != "gene_name":
            return Veredito("contagens_genes", False, [
                f"2ª coluna deve ser 'gene_name' (achei {_nomes(cab[1:2], so_identificadores=True)}): "
                "o expressao_deseq2.R a lê "
                "(setNames(bruto$gene_name, ...)) e quebra sem ela, depois de rodar o DESeq inteiro; "
                "sem símbolo do gene, repita o gene_id (o adaptador faz isso com 'copiar')"] + t.falha_aspas())
        amostras = cab[2:]
        falhas: list[str] = []
        if len(amostras) > _MAX_AMOSTRAS:
            return Veredito("contagens_genes", False,
                            [f"mais de {_MAX_AMOSTRAS} colunas de amostra (teto da leitura)"])
        if len(amostras) < 2:
            falhas.append(f"precisa de ≥2 colunas de amostra (achei {len(amostras)})")
        if len(set(amostras)) != len(amostras):
            falhas.append("nomes de amostra repetidos no cabeçalho")
        nomes_ruins = _Ocorrencias()
        for j, a in enumerate(amostras, start=3):
            problema = "nome de amostra vazio no cabeçalho" if not a else _problema_amostra(a)
            if problema:
                nomes_ruins.add(problema, f"coluna {j}")
        falhas += nomes_ruins.lista()

        largura = len(cab)
        vistos: set[str] = set()
        ruins = _Ocorrencias()
        nao_inteiros = n_genes = 0
        for n, linha in t.linhas():  # o teto de linhas está no leitor (conta as vazias também)
            n_genes += 1
            gene = linha[0]
            if not gene.strip():
                ruins.add("gene_id vazio", f"linha {n}")
                continue
            if gene != gene.strip() or not gene.isprintable():
                ruins.add("gene_id com espaço em volta ou caractere de controle", f"linha {n}")
            if gene in vistos:
                ruins.add("gene_id repetido", f"linha {n}")
            vistos.add(gene)
            if len(linha) != largura:
                ruins.add(f"nº de campos diferente do cabeçalho ({largura})", f"linha {n}")
                continue
            for j in range(2, largura):
                bruto = linha[j]
                if bruto.isascii() and bruto.isdigit():
                    continue  # caminho rápido: inteiro ≥0, o caso de quase toda célula
                estado = _contagem(bruto)
                if estado == "decimal":
                    nao_inteiros += 1
                elif estado == "nao_numerico":
                    ruins.add("valor não numérico", f"linha {n} coluna {_nome(cab[j])}")
                elif estado == "invalida":
                    ruins.add("contagem inválida (NA, vazia, negativa ou infinita)",
                              f"linha {n} coluna {_nome(cab[j])}")
        falhas += ruins.lista()
        if not n_genes:
            falhas.append("nenhum gene")
        falhas += t.falha_aspas()
    ok = not falhas
    perfil = {"n_genes": n_genes, "n_amostras": len(amostras), "n_valores_nao_inteiros": nao_inteiros}
    if ok:
        perfil["amostras"] = amostras
    return Veredito("contagens_genes", ok, falhas, perfil)


@_a_prova_de_leitura
def coldata(caminho: Path) -> Veredito:
    """Tabela de amostras do DESeq2: colunas `amostra` e `condicao`; amostras únicas; EXATAMENTE
    2 níveis de condição e ≥2 réplicas em cada. Exatamente 2 porque o expressao_deseq2.R só
    escolhe a referência e testa o ÚLTIMO coeficiente: com 3 níveis o "tratamento" seria o
    último em ordem alfabética, sem ninguém escolher. Cada linha com o nº de campos do
    cabeçalho (o read.table usa a 1ª coluna como row.names quando há um campo a mais e
    desalinha amostra e condição). Os níveis já saem prontos para a linha de comando."""
    teto = (_MAX_AMOSTRAS, f"mais de {_MAX_AMOSTRAS} amostras (teto da leitura; linhas vazias contam)")
    with _abrir(caminho, teto=teto) as t:
        cab = t.cab
        if "amostra" not in cab or "condicao" not in cab:
            return Veredito("coldata", False,
                            [f"precisa das colunas 'amostra' e 'condicao' (achei {len(cab)} coluna(s): "
                             f"{_nomes(cab, so_identificadores=True)})"] + t.falha_aspas())
        falhas = [f"coluna {c!r} aparece {cab.count(c)} vezes no cabeçalho"
                  for c in ("amostra", "condicao") if cab.count(c) > 1]
        ia, ic = cab.index("amostra"), cab.index("condicao")
        largura = len(cab)
        amostras: list[str] = []
        unicas: set[str] = set()
        repetidas = 0
        niveis: dict[str, int] = {}
        linha_do_nivel: dict[str, int] = {}
        ruins = _Ocorrencias()
        for n, linha in t.linhas():  # o teto de linhas está no leitor (conta as vazias também)
            if len(linha) != largura:
                ruins.add(f"nº de campos diferente do cabeçalho ({largura}): o R desalinharia as colunas",
                          f"linha {n}")
                continue
            a, c = linha[ia], linha[ic]
            if not a.strip() or not c.strip():
                ruins.add("amostra ou condição vazia", f"linha {n}")
                continue
            pa, pc = _problema_amostra(a), _problema_nivel(c)
            if pa:
                ruins.add(f"amostra: {pa}", f"linha {n}")
            if pc:
                ruins.add(f"condição: {pc}", f"linha {n}")
            if pa or pc:
                continue
            if a in unicas:
                repetidas += 1
            unicas.add(a)
            amostras.append(a)
            niveis[c] = niveis.get(c, 0) + 1
            linha_do_nivel.setdefault(c, n)
        falhas += ruins.lista()
        falhas += t.falha_aspas()
    if repetidas:
        falhas.append(f"amostras repetidas ({repetidas})")
    if len(niveis) != 2:
        falhas.append(f"precisa de exatamente 2 condições (achei {len(niveis)}): o expressao_deseq2.R testa "
                      "um único contraste — o último nível em ordem alfabética contra a referência")
    for nivel, n_amostras in niveis.items():
        if n_amostras < 2:
            falhas.append(f"a condição da linha {linha_do_nivel[nivel]} tem {n_amostras} amostra: "
                          "DESeq2 precisa de réplicas (≥2 por condição)")
    ok = not falhas
    perfil: dict = {"n_amostras": len(amostras), "n_niveis": len(niveis)}
    if ok:
        perfil.update({"amostras": amostras, "niveis": niveis})
    return Veredito("coldata", ok, falhas, perfil)


@_a_prova_de_leitura
def tabela_de(caminho: Path) -> Veredito:
    """Saída do DESeq2 que o volcano e o relatório consomem. O volcano lê com split('\\t') cru
    (nem apara nem trata aspas) e exige gene_name: o portão lê igual — cabeçalho sem aparar,
    aspas reprovam, linha com nº de campos diferente reprova (um TAB a mais desloca as colunas
    em silêncio). p e padj em [0,1] ou NA; log2FC finito ou NA. gene_id ÚNICO e não vazio: é a
    chave das tabelas do relatório (COLUNAS_CHAVE), que o portão de números exige achar
    exatamente 1 vez — repetido, o run inteiro rodava para reprovar só no relatório."""
    exigidas = COLUNAS_GARANTIDAS["tabela_de"]
    teto = (_MAX_GENES, f"mais de {_MAX_GENES} linhas (teto da leitura; linhas vazias contam)")
    with _abrir(caminho, aparar_cabecalho=False, teto=teto) as t:
        cab = t.cab
        faltam = [c for c in exigidas if c not in cab]
        if faltam:
            return Veredito("tabela_de", False, [f"colunas ausentes: {faltam}"] + t.falha_aspas())
        ix = {c: cab.index(c) for c in exigidas}
        largura = len(cab)
        ruins = _Ocorrencias()
        vistos: set[str] = set()
        n_genes = n_padj_na = 0
        for n, linha in t.linhas():  # o teto de linhas está no leitor (conta as vazias também)
            n_genes += 1
            if len(linha) != largura:
                ruins.add(f"nº de campos diferente do cabeçalho ({largura})", f"linha {n}")
                continue
            gene = linha[ix["gene_id"]]
            if not gene:
                ruins.add("gene_id vazio", f"linha {n}")
            elif gene in vistos:
                ruins.add("gene_id repetido (é a chave do relatório)", f"linha {n}")
            vistos.add(gene)
            try:
                p = _numero(linha[ix["pvalue"]])
                q = _numero(linha[ix["padj"]])
                lfc = _numero(linha[ix["log2FoldChange"]])
            except ValueError:
                ruins.add("valor não numérico em pvalue, padj ou log2FoldChange", f"linha {n}")
                continue
            for nome, x in (("pvalue", p), ("padj", q)):
                if x is not None and not (0.0 <= x <= 1.0):
                    ruins.add(f"{nome} fora de [0,1]", f"linha {n}")
            if lfc is not None and not math.isfinite(lfc):
                ruins.add("log2FoldChange não finito", f"linha {n}")
            n_padj_na += q is None
        falhas = ruins.lista() + t.falha_aspas()
    if not n_genes:
        falhas.append("tabela vazia")
    return Veredito("tabela_de", not falhas, falhas, {"n_genes": n_genes, "n_padj_na": n_padj_na})


# ------------------------------------------------------------------- genéricos
class NaoFinito(ValueError):
    """Número não finito (NaN, Infinity, -Infinity ou um decimal que estoura, como 1e999) num
    JSON. O json do Python os aceita; o JSONResponse do Starlette serializa com
    allow_nan=False e o GET vira 500 (X2)."""


def json_finito(texto: str, *, nao_finito: str = "erro") -> Any:
    """`json.loads` que não deixa passar número não finito. `nao_finito="erro"` levanta
    NaoFinito (o resumo_json reprova); `"none"` troca o valor por None (o runs._ler_resumo, para
    o GET do envelope devolver 200 com null). A mensagem nunca traz o conteúdo do arquivo."""
    if nao_finito not in ("erro", "none"):
        raise ValueError(f"nao_finito deve ser 'erro' ou 'none' (veio {nao_finito!r})")

    def _constante(_nome: str) -> None:
        if nao_finito == "none":
            return None
        raise NaoFinito("NaN/Infinity")

    def _decimal(t: str) -> Optional[float]:
        x = float(t)
        if math.isfinite(x):
            return x
        if nao_finito == "none":
            return None
        raise NaoFinito("decimal que estoura o float (ex.: 1e999)")

    return json.loads(texto, parse_constant=_constante, parse_float=_decimal)


_MSG_NAO_FINITO = "número não finito (NaN/Infinity) no resumo: o JSON da API não o transporta"


@_a_prova_de_leitura
def resumo_json(caminho: Path) -> Veredito:
    """Objeto JSON não vazio. Roda no envelope de cada passo (GET do estado do run): um resumo
    malformado de script reprova, nunca levanta — nem aninhado fundo demais (RecursionError
    do json, '[' repetido 200 mil vezes), nem ilegível (OSError, pelo decorador). NaN e
    Infinity reprovam (X2): o escrever_resumo dos scripts grava NaN, e o GET virava 500."""
    if caminho.stat().st_size > _MAX_RESUMO:
        return Veredito("resumo_json", False, [f"resumo com mais de {_MAX_RESUMO} bytes"])
    try:
        dados = json_finito(caminho.read_text(encoding="utf-8"), nao_finito="erro")
    except NaoFinito:
        return Veredito("resumo_json", False, [_MSG_NAO_FINITO])
    except UnicodeDecodeError:
        return Veredito("resumo_json", False, ["arquivo não é texto UTF-8"])
    except json.JSONDecodeError as e:
        # a mensagem do json é só posição ("Expecting value: line 1 column 1"), nunca conteúdo
        return Veredito("resumo_json", False, [f"JSON ilegível: {str(e)[:80]}"])
    except RecursionError:
        return Veredito("resumo_json", False, ["JSON aninhado fundo demais para ler"])
    except ValueError as e:  # ex.: número com dígitos demais (limite do int do Python)
        return Veredito("resumo_json", False, [f"JSON ilegível ({type(e).__name__})"])
    if not isinstance(dados, dict) or not dados:
        return Veredito("resumo_json", False, ["resumo deve ser um objeto JSON não vazio"])
    return Veredito("resumo_json", True, perfil={"chaves": sorted(dados)})


@_a_prova_de_leitura
def figura_png(caminho: Path) -> Veredito:
    with caminho.open("rb") as f:
        assinatura = f.read(8)
    tamanho = caminho.stat().st_size
    falhas = []
    if assinatura != _PNG:
        falhas.append("não é PNG (assinatura de arquivo)")
    if tamanho < 1024:
        falhas.append(f"PNG suspeito de vazio ({tamanho} bytes)")
    return Veredito("figura_png", not falhas, falhas, {"bytes": tamanho})


# ================================================================== genômica (F5)
# Tetos da genômica (especificação G1 §2.1). Os de BGZF/índice vivem em bgzf.py.
_MAX_CONTAGEM_FLUXO = 64 << 20   # VCF/BCF até aqui é contado lendo tudo (~2,5 s); acima, pelo índice
_MAX_REGIOES = 1_000_000         # linhas físicas de um BED
_MAX_VARIANTES = 5_000_000       # linhas de frequencias.tsv; registros do anotado do SnpEff
_MAX_LINHAS_VEP = 20_000_000     # linhas do vep.tsv (variante × transcrito)
_MAX_PAINEL = 1 << 20            # bytes de um painel de símbolos
_MAX_SIMBOLOS = 10_000
_MAX_ANOTADO_ABERTO = 4 << 30    # bytes descomprimidos do anotado.vcf.gz (painel do Run 8: ~2 GB)

# inteiro decimal curto: o int() do Python recusa (ValueError) texto de mais de 4300 dígitos,
# e uma exceção aqui seria um 500 — 18 dígitos cabem em qualquer coordenada real
_INT_CURTO = re.compile(r"[0-9]{1,18}", re.ASCII)
# o que o str.splitlines() corta além de \n e \r (o read_text já traduziu \r\n e \r)
_SEP_SPLITLINES = re.compile("[\x0b\x0c\x1c\x1d\x1e\x85  ]")
_FIXAS_VCF = ["#CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO"]


def _linhas_lf(caminho: Path, teto: int, msg_teto: str) -> Iterator[tuple[int, str]]:
    """Linhas FÍSICAS como o htslib as separa: só o '\\n' termina linha (newline='\\n' não
    traduz e não corta no '\\r' — com newline='' o '\\r' isolado virava quebra de linha e o
    portão aprovava o que o bcftools lê como UMA linha). Devolve sem o '\\n'; o '\\r' fica
    para quem chama decidir (CRLF × '\\r' isolado). O teto conta toda linha, vazia inclusive."""
    with caminho.open("r", encoding="utf-8", newline="\n") as f:
        n = 0
        while True:
            linha = f.readline(_MAX_LINHA + 1)
            if not linha:
                return
            n += 1
            if n > teto:
                raise _Teto(msg_teto)
            if len(linha) > _MAX_LINHA:
                raise _Ilegivel(f"linha {n} com mais de {_MAX_LINHA} caracteres")
            yield n, linha[:-1] if linha.endswith("\n") else linha


def _linhas_universais(caminho: Path, teto: int, msg_teto: str) -> Iterator[tuple[int, str]]:
    """Linhas como o csv.reader (newline='') e o open() de texto dos scripts as separam:
    '\\n', '\\r\\n' e '\\r' terminam linha. Para TSV que os PRÓPRIOS scripts gravam e que o
    relatório relê com csv (fatos.ler_tsv)."""
    with caminho.open("r", encoding="utf-8", newline="") as f:
        n = 0
        while True:
            linha = f.readline(_MAX_LINHA + 1)
            if not linha:
                return
            n += 1
            if n > teto:
                raise _Teto(msg_teto)
            if len(linha) > _MAX_LINHA:
                raise _Ilegivel(f"linha {n} com mais de {_MAX_LINHA} caracteres")
            yield n, linha.rstrip("\n").rstrip("\r")


def _float_finito(t: str) -> Optional[float]:
    try:
        x = float(t)
    except ValueError:
        return None
    return x if math.isfinite(x) else None


def _chave_hash(*partes: str) -> int:
    """Chave composta em 8 bytes: 1 milhão de chaves ficam em ~60 MB, não ~200."""
    return int.from_bytes(hashlib.blake2b("\t".join(partes).encode("utf-8", "surrogatepass"),
                                          digest_size=8).digest(), "little")


# ------------------------------------------------------------------ painel_simbolos
# Charset conferido no genes.gtf.gz do SnpEff ARS-UCD1.3.113: 36.075 genes, um só gene_name
# fora de [A-Za-z0-9._-] ('ISG12(B)') — daí os parênteses.
_SIMBOLO = re.compile(r"[A-Za-z0-9._()\-]{1,64}", re.ASCII)
_STEM_SEGURO = re.compile(r"[A-Za-z0-9_\-]{1,40}", re.ASCII)


@_a_prova_de_leitura
def painel_simbolos(caminho: Path) -> Veredito:
    """Lido como o coordenadas_painel.py:45-46 lê: read_text() (newlines universais) e
    splitlines(); pula a linha vazia e a que COMEÇA com '#'; o resto vira strip().upper() e é
    comparado INTEIRO com o gene_name do GTF. Um símbolo por linha; repetido sem distinção de
    caixa reprova (o script colapsa num set e o 'n_simbolos' do resumo mentiria)."""
    if caminho.stat().st_size > _MAX_PAINEL:
        return Veredito("painel_simbolos", False, [f"painel com mais de {_MAX_PAINEL} bytes"])
    bruto = caminho.read_bytes()
    texto = bruto.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")  # = read_text()
    falhas: list[str] = []
    ruins = _Ocorrencias()
    if texto.startswith("﻿"):
        falhas.append("BOM UTF-8 no início: o script leria '\\ufeffSIMBOLO' e o 1º gene nunca casaria")
    # crítica F5 #5a: procurar DEPOIS do splitlines era código morto (ele já cortou ali)
    for n, fisica in enumerate(texto.split("\n"), 1):
        if _SEP_SPLITLINES.search(fisica):
            ruins.add("separador de linha incomum (\\x0b, \\x0c, \\x1c-\\x1e, \\x85, U+2028/9): o script "
                      "corta a linha ali e lê símbolos que o editor mostra numa linha só", f"linha {n}")
    vistos: dict[str, int] = {}
    for n, linha in enumerate(texto.splitlines(), 1):
        if not linha.strip() or linha.startswith("#"):
            continue
        s = linha.strip()
        s0 = s.lstrip("﻿") if n == 1 else s  # o BOM já é falha própria: não repete aqui
        if s.startswith("#"):
            ruins.add("comentário com espaço antes do '#': o script o lê como símbolo", f"linha {n}")
        elif not _SIMBOLO.fullmatch(s0):
            ruins.add("linha não é UM símbolo (espaço/TAB interno, vírgula ou caractere fora de "
                      "[A-Za-z0-9._()-]): o script compara a linha inteira com o gene_name", f"linha {n}")
        else:
            u = s0.upper()
            if u in vistos:
                ruins.add("símbolo repetido (sem distinção de caixa): o script colapsa num set", f"linha {n}")
            vistos.setdefault(u, n)
            if len(vistos) > _MAX_SIMBOLOS:
                return Veredito("painel_simbolos", False, [f"mais de {_MAX_SIMBOLOS} símbolos"])
    falhas += ruins.lista()
    if not vistos:
        falhas.append("nenhum símbolo")
    ok = not falhas
    perfil: dict[str, Any] = {"n_simbolos": len(vistos), "crlf": b"\r" in bruto}
    if ok:  # conteúdo só volta de arquivo que provou ser painel
        perfil["simbolos"] = sorted(vistos)[:50]
    return Veredito("painel_simbolos", ok, falhas, perfil)


# ---------------------------------------------------------------------- bed_regioes
@_a_prova_de_leitura
def bed_regioes(caminho: Path) -> Veredito:
    """Lido como o bcftools -R lê (htslib: linha termina só no '\\n', colunas por TAB, '#' na
    coluna 1 é comentário, início+1 SÓ se o nome termina em .bed — regra de nome à parte) e
    como o extracao_regioes.py:32 conta (n_regioes pula '#' e 'track'). CRLF passa (o
    bcftools 1.19 dá as mesmas contagens); '\\r' isolado reprova (0 variantes com rc 0)."""
    falhas: list[str] = []
    ruins = _Ocorrencias()
    por_contig: dict[str, array] = {}
    ordem_contigs: list[str] = []
    n_reg = n_coment = n_crlf = 0
    ordenado = True
    tem_nome = False
    ultimo: Optional[tuple[str, int]] = None
    teto = (_MAX_REGIOES, f"mais de {_MAX_REGIOES} linhas (teto da leitura; linhas vazias contam)")
    for n, linha in _linhas_lf(caminho, *teto):
        if linha.endswith("\r"):
            linha = linha[:-1]
            n_crlf += 1
        if "\r" in linha:
            ruins.add("'\\r' isolado (sem '\\n' depois): o htslib não o trata como fim de linha e o "
                      "bcftools -R extrai 0 variantes com exit 0", f"linha {n}")
            continue
        if _SEP_SPLITLINES.search(linha):
            ruins.add("separador de linha incomum (\\x0b, \\x0c, U+2028…): o extracao_regioes.py "
                      "(splitlines) contaria regiões que o bcftools não vê", f"linha {n}")
            continue
        if not linha:
            ruins.add("linha vazia (conservador: o parser de regiões do htslib não a pula de forma garantida)",
                      f"linha {n}")
            continue
        if linha.startswith("#"):
            n_coment += 1
            continue
        if linha.startswith(("track", "browser")):
            ruins.add("linha track/browser: o bcftools só pula '#' e aborta nela", f"linha {n}")
            continue
        c = linha.split("\t")
        if len(c) < 3:
            ruins.add("menos de 3 colunas separadas por TAB", f"linha {n}")
            continue
        crom, ini, fim = c[0], c[1], c[2]
        if not crom or any(ch.isspace() for ch in crom):
            ruins.add("contig vazio ou com espaço (o bcftools o procuraria com o espaço e acharia 0 variantes)",
                      f"linha {n}")
            continue
        if not (_INT_CURTO.fullmatch(ini) and _INT_CURTO.fullmatch(fim)):
            ruins.add("início/fim não são inteiros decimais simples (sem sinal, espaço, vírgula ou 1e5)",
                      f"linha {n}")
            continue
        a, b = int(ini), int(fim)
        if a >= b:
            ruins.add("início ≥ fim (BED é 0-based meia-aberta: fim > início)", f"linha {n}")
            continue
        n_reg += 1
        tem_nome = tem_nome or (len(c) >= 4 and bool(c[3]))
        if crom not in por_contig:
            ordem_contigs.append(crom)
            por_contig[crom] = array("q")
        elif ultimo is not None and ultimo[0] != crom:
            ordenado = False  # contig reaparece depois de outro
        if ultimo is not None and ultimo[0] == crom and a < ultimo[1]:
            ordenado = False
        por_contig[crom].extend((a, b))
        ultimo = (crom, a)
    falhas += ruins.lista()
    if not n_reg:
        falhas.append("nenhuma região")
    pb_total = pb_uniao = n_sobrep = 0
    for plano in por_contig.values():
        fim_corrente = -1
        for a, b in sorted(zip(plano[0::2], plano[1::2])):
            pb_total += b - a
            if a < fim_corrente:
                n_sobrep += 1
                if b > fim_corrente:
                    pb_uniao += b - fim_corrente
                    fim_corrente = b
            else:
                pb_uniao += b - a
                fim_corrente = b
    ok = not falhas
    perfil: dict[str, Any] = {
        "n_regioes": n_reg, "n_comentarios": n_coment, "n_contigs": len(ordem_contigs),
        "usa_chr": any(x.lower().startswith("chr") for x in ordem_contigs),
        "pb_total": pb_total, "pb_uniao": pb_uniao, "n_sobreposicoes": n_sobrep,
        "ordenado_por_contig_e_inicio": ordenado, "tem_nome": tem_nome, "crlf": n_crlf > 0}
    if ok:
        perfil["contigs"] = [_nome_seguro(x) for x in ordem_contigs[:50]]
    return Veredito("bed_regioes", ok, falhas, perfil)


def _regioes_bed(caminho: Path) -> Iterator[tuple[str, int, int]]:
    """Regiões (contig, início, fim) de um BED JÁ aprovado por bed_regioes, em fluxo."""
    for _, linha in _linhas_lf(caminho, _MAX_REGIOES, "teto"):
        linha = linha[:-1] if linha.endswith("\r") else linha
        if linha and not linha.startswith(("#", "track", "browser")):
            c = linha.split("\t")
            yield c[0], int(c[1]), int(c[2])


# ------------------------------------------------------------ vcf_indexado (BGZF/BCF)
def _cabecalho_vcf(texto: str) -> dict[str, Any]:
    """##fileformat, ##contig (ID, length, IDX), ##INFO, ##FORMAT e o #CHROM."""
    linhas = texto.split("\n")
    falhas: list[str] = []
    if not linhas or not linhas[0].startswith("##fileformat=VCFv4."):
        falhas.append("1ª linha não é ##fileformat=VCFv4.x")
    contigs: list[tuple[str, Optional[int], Optional[int]]] = []
    info, fmt = set(), set()
    chrom = None
    for l in linhas:
        l = l.rstrip("\r")
        if l.startswith("##contig=<"):
            m = re.search(r"[<,]ID=([^,>]+)", l)
            ln = re.search(r"[<,]length=(\d{1,18})[,>]", l)
            ix = re.search(r"[<,]IDX=(\d{1,9})[,>]", l)
            if m:
                contigs.append((m.group(1), int(ln.group(1)) if ln else None, int(ix.group(1)) if ix else None))
        elif l.startswith("##INFO=<"):
            m = re.search(r"[<,]ID=([^,>]+)", l)
            if m:
                info.add(m.group(1))
        elif l.startswith("##FORMAT=<"):
            m = re.search(r"[<,]ID=([^,>]+)", l)
            if m:
                fmt.add(m.group(1))
        elif l.startswith("#CHROM"):
            chrom = l.split("\t")
    amostras: list[str] = []
    if chrom is None:
        falhas.append("sem a linha #CHROM")
    elif chrom[:8] != _FIXAS_VCF:
        falhas.append("linha #CHROM sem as 8 colunas fixas separadas por TAB")
    elif len(chrom) > 8:
        if chrom[8] != "FORMAT":
            falhas.append("9ª coluna do #CHROM não é FORMAT")
        amostras = chrom[9:]
    # BCF: o dicionário de contigs do htslib usa IDX= quando existe; senão, a ordem das linhas
    por_tid = {}
    for i, (nome, _, ix) in enumerate(contigs):
        por_tid[ix if ix is not None else i] = nome
    return {"falhas": falhas, "contigs": contigs, "por_tid": por_tid, "info": info,
            "format": fmt, "amostras": amostras}


def _primeiro_registro(ini: dict[str, Any], cab: dict[str, Any]) -> list[str]:
    prim = ini["primeiro"]
    if prim is None:
        return []
    if ini["tipo"] == "vcf":
        c = prim.decode("utf-8", "replace").rstrip("\r").split("\t")
        if len(c) < 8 or not _INT_CURTO.fullmatch(c[1]) or int(c[1]) < 1:
            return ["1º registro com menos de 8 campos ou POS não inteiro ≥1"]
        if cab["amostras"] and len(c) != 9 + len(cab["amostras"]):
            return ["1º registro com nº de campos diferente do #CHROM"]
        return []
    falhas = []
    if prim["tid"] not in cab["por_tid"] or prim["pos0"] < 0:
        falhas.append("1º registro BCF aponta contig fora do dicionário do cabeçalho")
    if prim["n_amostras"] != len(cab["amostras"]):
        falhas.append("1º registro BCF com nº de amostras diferente do cabeçalho")
    return falhas


def _conferir_indice(s: str, idx: dict[str, Any], bg: bgzf.Leitor, tamanho: int, tipo: str,
                     cab: dict[str, Any], falhas: list[str]) -> tuple[Optional[int], list[str]]:
    """Prova barata (sem bcftools) de que o índice é DESTE dado: todo chunk termina antes do
    EOF, e o 1º offset de cada contig cai no início de um registro do mesmo contig (VCF) ou
    do mesmo tid (BCF). Devolve (contagem pelo pseudo-bin ou None, contigs com dados)."""
    if idx["tipo"] != s[1:]:
        falhas.append(f"{s} com conteúdo {idx['tipo'].upper()}: o nome diz um tipo de índice e o conteúdo é outro")
    soma, sem_meta, nomes_d = 0, 0, []
    for ref in idx["refs"]:
        if not ref["bins"]:
            continue
        nomeref = ref["nome"] if ref["nome"] is not None else cab["por_tid"].get(ref["tid"])
        nomes_d.append(nomeref if nomeref is not None else f"tid{ref['tid']}")
        if ref["meta"] is None:
            sem_meta += 1
        else:
            soma += ref["meta"][2] + ref["meta"][3]
        if (ref["vmax"] >> 16) > tamanho - len(bgzf.EOF_BGZF):
            falhas.append(f"{s} aponta para além do fim do dado: dado truncado ou índice de OUTRO arquivo")
            break
        voff = ref["meta"][0] if ref["meta"] else ref["vmin"]
        if voff is None:
            falhas.append(f"{s} com bin sem nenhum chunk: índice malformado")
            break
        try:
            alvo = bgzf.registro_em(bg, voff, tipo)
        except bgzf.Ilegivel:
            alvo = None
        certo = ref["tid"] if (tipo == "bcf" and ref["nome"] is None) else nomeref
        if alvo != certo:
            falhas.append(f"{s} não corresponde ao dado: o 1º registro indexado do contig "
                          f"{_nome_seguro(nomes_d[-1])} não está onde o índice aponta")
            break
    if sem_meta:
        falhas.append(f"{s} sem pseudo-bin de metadados em {sem_meta} contig(s) (tabix antigo): sem contagem "
                      "pelo índice")
        return None, nomes_d
    return soma, nomes_d


def _ler_vcf_bruto(caminho: Path) -> dict[str, Any]:
    """Leitura completa de um VCF.gz/BCF e do(s) índice(s) ao lado. Devolve um dicionário que
    NÃO pode ser alterado por quem chama (vai para o cache): falhas e avisos saem em tupla."""
    t0 = time.perf_counter()
    falhas: list[str] = []
    avisos: list[str] = []
    nome = caminho.name
    esperado = "vcf" if nome.endswith(".vcf.gz") else "bcf" if nome.endswith(".bcf") else None
    if esperado is None:
        falhas.append("extensão deve ser .vcf.gz ou .bcf: o índice é procurado como <arquivo>.tbi/.csi e o "
                      "fst_racas.py:132 escolhe --bcf/--vcf do plink2 pelo sufixo")
    r: dict[str, Any] = {}

    def _fechar() -> dict[str, Any]:
        r["falhas"], r["avisos"] = tuple(falhas), tuple(avisos)
        r["segundos"] = round(time.perf_counter() - t0, 3)
        return r

    with caminho.open("rb") as f:
        st = os.fstat(f.fileno())
        tam = st.st_size
        r["bytes"] = tam
        if tam < len(bgzf.EOF_BGZF):
            raise bgzf.Ilegivel("arquivo menor que o bloco EOF do BGZF (28 bytes)")
        bg = bgzf.Leitor(f, tam)
        # 1) bloco EOF (o htslib só avisa "no BGZF EOF marker", mas a falta dele é a assinatura
        #    de cópia/download truncado)
        f.seek(tam - len(bgzf.EOF_BGZF))
        if f.read(len(bgzf.EOF_BGZF)) != bgzf.EOF_BGZF:
            falhas.append("sem o bloco EOF de 28 bytes do BGZF: arquivo truncado (cópia ou download "
                          "interrompido) ou gzip comum")
        # 2) cabeçalho + 1º registro
        try:
            ini = bgzf.inicio(bg)
        except bgzf.NaoBgzf:
            f.seek(0)
            gz = f.read(3) == b"\x1f\x8b\x08"
            falhas.append("não é BGZF (" + ("gzip comum: o bcftools lê em sequência, mas não usa índice"
                                            if gz else "nem gzip") + "); regrave com bgzip ou bcftools -Oz")
            r["tipo"] = "gzip" if gz else "desconhecido"
            return _fechar()
        tipo = ini["tipo"]
        r["tipo"] = tipo
        if esperado and tipo != esperado:
            falhas.append(f"conteúdo {tipo.upper()} com extensão de {esperado.upper()}: o plink2 receberia a "
                          "flag errada e o índice certo do conteúdo não seria procurado")
        cab = _cabecalho_vcf(ini["texto"])
        falhas += cab["falhas"]
        falhas += _primeiro_registro(ini, cab)
        r.update({"contigs_cab": cab["contigs"], "por_tid": cab["por_tid"], "info": frozenset(cab["info"]),
                  "format": frozenset(cab["format"]), "n_amostras": len(cab["amostras"]),
                  "amostras": tuple(cab["amostras"]),  # F6: vcf_e_rotulos (o cache é só leitura)
                  "tem_registro": ini["primeiro"] is not None})
        # 3) índice(s) ao lado — os candidatos são os de IRMAOS (BCF: só .csi)
        sufixos = IRMAOS["vcf_indexado"][".bcf" if tipo == "bcf" else ".vcf.gz"]
        presentes = [s for s in sufixos if Path(str(caminho) + s).is_file()]
        if not presentes:
            falhas.append("sem índice ao lado (" + ("<arquivo>.csi" if tipo == "bcf" else "<arquivo>.tbi ou .csi")
                          + "): o bcftools view -R falha sem ele")
            if tipo == "bcf" and Path(str(caminho) + ".tbi").is_file():
                falhas.append("BCF com .tbi: o tabix não indexa BCF; o índice de BCF é o .csi (bcftools index)")
        if len(presentes) > 1:
            # crítica F5 #4 (conferido no bcftools 1.19): .csi lixo + .tbi bom = "Could not load
            # local index file", rc 255; .tbi lixo + .csi bom = funciona. O validador lia o .tbi.
            falhas.append("dois índices ao lado (.tbi e .csi): o htslib carrega o .csi e ignora o .tbi — um .csi "
                          "velho ou corrompido derruba a extração mesmo com o .tbi bom; deixe só um")
        shas: dict[str, str] = {}
        lidos: dict[str, tuple[Optional[int], list[str], dict[str, Any]]] = {}
        for s in presentes:
            p = Path(str(caminho) + s)
            ist = p.stat()
            if ist.st_size > bgzf.MAX_INDICE:
                falhas.append(f"{s} com mais de {bgzf.MAX_INDICE} bytes: não é índice de VCF")
                continue
            bruto = p.read_bytes()
            # o envelope atesta o sha da PORTA; o do índice vai no perfil (e o registro da entrada
            # confere o irmão contra ele)
            shas[s] = hashlib.sha256(bruto).hexdigest()
            if ist.st_mtime_ns < st.st_mtime_ns:
                falhas.append(f"{s} mais velho que o dado: índice de uma versão anterior do arquivo (o htslib "
                              "só avisa e consulta o índice errado)")
            if not bruto.endswith(bgzf.EOF_BGZF):
                falhas.append(f"{s} sem o bloco EOF do BGZF: índice truncado")
            try:
                idx = bgzf.ler_indice(bgzf.inteiro(bruto))
            except bgzf.Ilegivel as e:
                falhas.append(f"{s} ilegível ({e})")
                continue
            n_idx, contigs = _conferir_indice(s, idx, bg, tam, tipo, cab, falhas)
            lidos[s] = (n_idx, contigs, idx)
        # o índice que o htslib usa: o .csi quando há os dois (conferido no 1.19)
        canonico = next((s for s in (".csi", ".tbi") if s in lidos), None)
        n_idx, contigs_dados, idx_canonico = lidos[canonico] if canonico else (None, [], None)
        r.update({"n_registros_indice": n_idx, "contigs_com_dados": tuple(contigs_dados),
                  "indices": tuple(presentes), "indices_sha256": shas, "indice_canonico": canonico,
                  "_indice": idx_canonico})
        # 4) contagem por fluxo (só no que cabe no teto) — prova a cadeia e confere o índice
        if tam <= _MAX_CONTAGEM_FLUXO:
            try:
                cont: Optional[dict[str, Any]] = (bgzf.contar_fluxo_vcf(bg) if tipo == "vcf"
                                                  else bgzf.contar_fluxo_bcf(bg))
            except bgzf.NaoBgzf as e:
                falhas.append(f"cadeia BGZF quebrada no meio ({e}): parte do arquivo não é BGZF")
                cont = None
            except bgzf.Ilegivel as e:  # truncado/CRC/linha gigante: soma às falhas, não as apaga
                falhas.append(str(e))
                cont = None
            if cont is not None:
                r["n_registros_fluxo"] = cont["n"]
                r["n_alt_so_estrela"] = cont.get("n_alt_so_estrela")
                if cont.get("bytes_sobrando"):
                    falhas.append("último registro BCF truncado (bytes que não fecham um registro)")
                if cont.get("fora_de_ordem"):
                    falhas.append(f"{cont['fora_de_ordem']} registro(s) fora de ordem (contig repetido depois de "
                                  f"outro ou POS que regride; 1º no registro {cont['primeiro_fora_de_ordem']}): o "
                                  "tabix recusa, e regiões sobrepostas podem ter duplicado registros")
                if n_idx is not None and cont["n"] != n_idx:
                    falhas.append(f"o índice conta {n_idx} registros e o arquivo tem {cont['n']}: índice de outra "
                                  "versão do dado")
        else:
            avisos.append(f"arquivo acima de {_MAX_CONTAGEM_FLUXO} bytes: registros contados pelo índice "
                          "(a cadeia BGZF não foi percorrida)")
    n_reg = r.get("n_registros_fluxo", r.get("n_registros_indice"))
    r["n_registros"] = n_reg
    if n_reg == 0 or (n_reg is None and not r.get("tem_registro")):
        falhas.append("0 registros: com contig divergente (chr14 × 14, MT × M) a extração termina com exit 0 "
                      "e devolve só o cabeçalho")
    return _fechar()


@functools.lru_cache(maxsize=8)
def _ler_vcf_cache(caminho: str, _chave: tuple) -> dict[str, Any]:
    return _ler_vcf_bruto(Path(caminho))


def _ler_vcf(caminho: Path) -> dict[str, Any]:
    """Cache pela identidade do dado E de cada índice candidato: caminho resolvido, tamanho,
    mtime, ctime e inode. O ctime entra para que uma escrita com o mtime devolvido (os.utime)
    invalide o cache — no NTFS ele muda à toa, o que só custa um miss. Trocar ou criar só o
    .tbi/.csi também invalida. O dicionário devolvido é compartilhado: só leitura.

    É o cache que deixa as checagens (vcf_tem_info_af, vcf_contigs_ars_ucd, vcf_e_bed) lerem
    só cabeçalho e índice na prática: o registro valida a entrada e logo depois as deriva, no
    mesmo processo. Resíduo declarado: no host Windows o st_ctime é a data de CRIAÇÃO, então
    reescrever o mesmo arquivo no lugar, com o mesmo tamanho e o mtime devolvido, não invalida
    — só fora do container (API e workers rodam em Linux, onde o ctime muda a cada escrita)."""
    st = os.stat(caminho)
    chave: list[Any] = [str(caminho.resolve()), st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino]
    for s in (".tbi", ".csi"):
        try:
            ist = os.stat(str(caminho) + s)
        except OSError:
            chave.append((s, None))
            continue
        chave.append((s, ist.st_size, ist.st_mtime_ns, ist.st_ctime_ns, ist.st_ino, stat.S_ISREG(ist.st_mode)))
    return _ler_vcf_cache(str(caminho), tuple(chave))


@_a_prova_de_leitura
def vcf_indexado(caminho: Path) -> Veredito:
    """VCF.gz (BGZF) com .tbi OU .csi, ou BCF com .csi, lido como o htslib lê. Ordem: sufixo;
    ≥28 B; bloco EOF; cabeçalho de bloco BGZF; conteúdo × sufixo; cabeçalho VCF; 1º registro;
    índice ao lado (frescor por mtime em ns, tipo × sufixo, offsets dentro do dado, 1º offset
    de cada contig num registro do mesmo contig, pseudo-bin); até 64 MiB, contagem por fluxo
    igual à do índice; 0 registros reprova."""
    r = _ler_vcf(caminho)
    contigs = list(r.get("contigs_com_dados") or ())
    info, fmt = r.get("info", frozenset()), r.get("format", frozenset())
    perfil = {
        "conteiner": r.get("tipo"), "bytes": r.get("bytes"), "n_registros": r.get("n_registros"),
        "contagem": "fluxo" if "n_registros_fluxo" in r else "indice",
        "n_registros_indice": r.get("n_registros_indice"), "indices": list(r.get("indices", ())),
        "indices_sha256": dict(r.get("indices_sha256", {})), "indice_canonico": r.get("indice_canonico"),
        "contigs_com_dados": [_nome_seguro(c) for c in contigs[:50]], "n_contigs_com_dados": len(contigs),
        "usa_chr": any(c.lower().startswith("chr") for c in contigs),
        "n_contigs_cabecalho": len(r.get("contigs_cab", ())), "n_amostras": r.get("n_amostras", 0),
        "tem_info_af": "AF" in info, "tem_info_ann": "ANN" in info, "tem_format_gt": "GT" in fmt,
        "n_alt_so_estrela": r.get("n_alt_so_estrela"), "avisos": list(r.get("avisos", ())),
        "segundos": r.get("segundos")}
    return Veredito("vcf_indexado", not r["falhas"], list(r["falhas"]), perfil)


# ------------------------------------------------------------------------ indice_vcf
@_a_prova_de_leitura
def indice_vcf(caminho: Path) -> Veredito:
    """O .tbi/.csi que a extração grava ao lado do VCF (porta própria: o envelope o atesta como
    qualquer outra). BGZF com EOF, magic conforme o sufixo, estrutura inteira e ≥1 contig
    com dados."""
    tamanho = caminho.stat().st_size
    if tamanho > bgzf.MAX_INDICE:
        return Veredito("indice_vcf", False, [f"índice com mais de {bgzf.MAX_INDICE} bytes"])
    bruto = caminho.read_bytes()
    falhas: list[str] = []
    if not bruto.endswith(bgzf.EOF_BGZF):
        falhas.append("sem o bloco EOF do BGZF: índice truncado")
    try:
        idx = bgzf.ler_indice(bgzf.inteiro(bruto))
    except bgzf.NaoBgzf:
        return Veredito("indice_vcf", False, falhas + ["não é BGZF (gzip comum ou outro formato): o htslib "
                                                       "não carrega o índice"])
    sufixo = ".tbi" if caminho.name.endswith(".tbi") else ".csi" if caminho.name.endswith(".csi") else None
    if sufixo and idx["tipo"] != sufixo[1:]:
        falhas.append(f"{sufixo} com conteúdo {idx['tipo'].upper()}: o nome diz um tipo de índice e o conteúdo é outro")
    com_dados = [ref for ref in idx["refs"] if ref["bins"]]
    if not com_dados:
        falhas.append("índice sem nenhum contig com dados")
    n_reg = sum(ref["meta"][2] + ref["meta"][3] for ref in com_dados if ref["meta"] is not None)
    return Veredito("indice_vcf", not falhas, falhas, {
        "tipo": idx["tipo"], "n_ref": idx["n_ref"], "n_refs_com_dados": len(com_dados),
        "n_registros": n_reg, "bytes": tamanho})


# --------------------------------------------------------------- vcf_anotado_snpeff
@_a_prova_de_leitura
def vcf_anotado_snpeff(caminho: Path) -> Veredito:
    """anotado.vcf.gz do anotacao_snpeff.py:55, que grava com gzip.open (gzip COMUM, não BGZF:
    não é indexável, e nenhum passo o consome — é saída para o autor). Lido em fluxo; reprova
    gzip truncado (SnpEff interrompido), cabeçalho sem a marca do SnpEff, 0 registros, nenhum
    ANN e QUALQUER registro com ERROR_CHROMOSOME_NOT_FOUND (contig sem par no banco: Y; M × MT)."""
    with caminho.open("rb") as f:
        if f.read(2) != b"\x1f\x8b":
            return Veredito("vcf_anotado_snpeff", False, ["não é gzip: o anotacao_snpeff.py grava anotado.vcf.gz "
                                                          "com gzip.open"])
    falhas: list[str] = []
    ruins = _Ocorrencias()
    n_reg = n_ann = n_erro = 0
    n_amostras: Optional[int] = None
    tem_versao = tem_ann = False
    chrom_ok = False
    aberto = 0
    try:
        with gzip.open(caminho, "rt", encoding="utf-8", newline="") as f:
            n = 0
            while True:
                linha = f.readline(_MAX_LINHA + 1)
                if not linha:
                    break
                n += 1
                aberto += len(linha)
                if aberto > _MAX_ANOTADO_ABERTO:
                    return Veredito("vcf_anotado_snpeff", False, [f"mais de {_MAX_ANOTADO_ABERTO} bytes "
                                                                  "descomprimidos (teto da leitura)"])
                if len(linha) > _MAX_LINHA:
                    raise _Ilegivel(f"linha {n} com mais de {_MAX_LINHA} caracteres")
                l = linha.rstrip("\n").rstrip("\r")
                if n == 1 and not l.startswith("##fileformat=VCFv4."):
                    falhas.append("1ª linha não é ##fileformat=VCFv4.x")
                if l.startswith("##"):
                    tem_versao = tem_versao or l.startswith("##SnpEffVersion=")
                    tem_ann = tem_ann or l.startswith("##INFO=<ID=ANN,")
                    continue
                if l.startswith("#"):
                    c = l.split("\t")
                    if c[:8] == _FIXAS_VCF:
                        chrom_ok = True
                        n_amostras = max(0, len(c) - 9)
                    else:
                        falhas.append("linha #CHROM sem as 8 colunas fixas separadas por TAB")
                    continue
                n_reg += 1
                if n_reg > _MAX_VARIANTES:
                    raise _Teto(f"mais de {_MAX_VARIANTES} registros (teto da leitura)")
                c = l.split("\t", 8)
                if len(c) < 8 or not _INT_CURTO.fullmatch(c[1]) or int(c[1]) < 1:
                    ruins.add("registro com menos de 8 campos ou POS não inteiro ≥1", f"linha {n}")
                    continue
                info = c[7]
                if info.startswith("ANN=") or ";ANN=" in info:
                    n_ann += 1
                if "ERROR_CHROMOSOME_NOT_FOUND" in info:
                    n_erro += 1
    except (EOFError, zlib.error, gzip.BadGzipFile):
        return Veredito("vcf_anotado_snpeff", False, ["gzip truncado ou corrompido (SnpEff interrompido?)"])
    if not tem_versao:
        falhas.append("cabeçalho sem ##SnpEffVersion=: não é saída do SnpEff")
    if not tem_ann:
        falhas.append("cabeçalho sem ##INFO=<ID=ANN,...>: o SnpEff não anotou")
    if not chrom_ok and not any("#CHROM" in x for x in falhas):
        falhas.append("sem a linha #CHROM")
    falhas += ruins.lista()
    if not n_reg:
        falhas.append("0 registros anotados")
    elif not n_ann:
        falhas.append("nenhum registro com ANN: o banco do SnpEff não reconheceu nada")
    if n_erro:
        falhas.append(f"{n_erro} registro(s) com ERROR_CHROMOSOME_NOT_FOUND: contig sem par no banco SnpEff "
                      "(Y; M × MT)")
    return Veredito("vcf_anotado_snpeff", not falhas, falhas, {
        "n_registros": n_reg, "n_com_ann": n_ann, "n_sem_ann": n_reg - n_ann, "n_erro_cromossomo": n_erro,
        "n_amostras": n_amostras, "indexavel": False})


# --------------------------------------------------------------- tabela_frequencias
_CAB_FREQ = ["CHROM", "POS", "ID", "REF", "ALT", "AF", "MAF", "N_ALT"]


@_a_prova_de_leitura
def tabela_frequencias(caminho: Path) -> Veredito:
    """frequencias.tsv (frequencias.py:79-83): cabeçalho exato, MAF não crescente, N_ALT =
    nº de ALT que não são '*', chave CHROM:POS:REF:ALT única."""
    ruins = _Ocorrencias()
    it = _linhas_universais(caminho, _MAX_VARIANTES + 1, f"mais de {_MAX_VARIANTES} variantes (teto da leitura)")
    _, cab = next(it, (0, ""))
    if cab.split("\t") != _CAB_FREQ:
        return Veredito("tabela_frequencias", False, ["cabeçalho diferente de CHROM POS ID REF ALT AF MAF N_ALT "
                                                      "(separados por TAB)"])
    chaves: set[int] = set()
    n = n_comuns = n_multi = 0
    maf_ant = math.inf
    for k, linha in it:
        c = linha.split("\t")
        if len(c) != 8:
            ruins.add("nº de campos diferente de 8", f"linha {k}")
            continue
        n += 1
        crom, pos, _vid, ref, alt, af_t, maf_t, nalt_t = c
        if not crom or not ref or not alt or " " in linha:
            ruins.add("CHROM/REF/ALT vazio ou espaço no campo", f"linha {k}")
        if not _INT_CURTO.fullmatch(pos) or int(pos) < 1:
            ruins.add("POS não é inteiro ≥1", f"linha {k}")
        af, maf = _float_finito(af_t), _float_finito(maf_t)
        if af is None or maf is None:
            ruins.add("AF ou MAF não numérico/finito", f"linha {k}")
            continue
        if not 0.0 <= af <= 1.0:
            ruins.add("AF fora de [0,1]", f"linha {k}")
        if not 0.0 <= maf <= 0.5:
            ruins.add("MAF fora de [0; 0,5] (AFs do sítio somam mais que 1?)", f"linha {k}")
        if maf > maf_ant:
            ruins.add("MAF crescente: o script grava em ordem de MAF não crescente", f"linha {k}")
        maf_ant = maf
        n_validos = sum(1 for a in alt.split(",") if a != "*")
        if not _INT_CURTO.fullmatch(nalt_t) or not 1 <= int(nalt_t) <= n_validos:
            ruins.add("N_ALT não é inteiro entre 1 e o nº de alelos ALT que não são '*'", f"linha {k}")
        elif int(nalt_t) > 1:
            n_multi += 1
        n_comuns += maf >= 0.05
        h = _chave_hash(crom, pos, ref, alt)
        if h in chaves:
            ruins.add("chave CHROM:POS:REF:ALT repetida", f"linha {k}")
        chaves.add(h)
    falhas = ruins.lista()
    if not n:
        falhas.append("tabela sem variantes")
    return Veredito("tabela_frequencias", not falhas, falhas,
                    {"n_variantes": n, "n_maf_ge_0_05": n_comuns, "n_multialelicos": n_multi})


# ------------------------------------------------- contagens top-15 (SnpEff e VEP)
_EFEITO_SNPEFF = re.compile(r"[A-Za-z0-9_]+(?:&[A-Za-z0-9_]+)*", re.ASCII)
_TERMO_SO = re.compile(r"[A-Za-z0-9_]+", re.ASCII)


def _contagem_top(caminho: Path, formato: str, cab_esperado: str, termo: "re.Pattern[str]",
                  nome_termo: str) -> Veredito:
    """Counter.most_common(15) gravado como '<TERMO>\\tN': 1 a 15 linhas, termo único (é a
    CHAVE do relatório), N inteiro ≥1 e não crescente."""
    ruins = _Ocorrencias()
    it = _linhas_universais(caminho, 100, "mais de 100 linhas: o script grava no máximo 15")
    _, cab = next(it, (0, ""))
    if cab != cab_esperado:
        return Veredito(formato, False, [f"cabeçalho diferente de '{cab_esperado.replace(chr(9), ' TAB ')}'"])
    vistos: set[str] = set()
    n_ant = math.inf
    soma = 0
    topo = None
    n_linhas = 0
    for k, linha in it:
        c = linha.split("\t")
        if len(c) != 2:
            ruins.add("nº de campos diferente de 2", f"linha {k}")
            continue
        n_linhas += 1
        t, nt = c
        if not termo.fullmatch(t):
            ruins.add(f"{nome_termo} vazio ou fora do vocabulário (vírgula = termos não separados)", f"linha {k}")
        elif t in vistos:
            ruins.add(f"{nome_termo} repetido", f"linha {k}")
        vistos.add(t)
        if not _INT_CURTO.fullmatch(nt) or int(nt) < 1:
            ruins.add("N não é inteiro ≥1", f"linha {k}")
            continue
        v = int(nt)
        if v > n_ant:
            ruins.add("N crescente: o Counter.most_common grava em ordem não crescente", f"linha {k}")
        n_ant = v
        soma += v
        topo = topo or (t if termo.fullmatch(t) else None)
    falhas = ruins.lista()
    if not n_linhas:
        falhas.append("nenhuma linha de contagem")
    if n_linhas > 15:
        falhas.append(f"{n_linhas} linhas: o script grava no máximo 15 (most_common(15))")
    return Veredito(formato, not falhas, falhas, {"n_linhas": n_linhas, "soma_N": soma, "topo": topo})


@_a_prova_de_leitura
def contagem_efeitos_snpeff(caminho: Path) -> Veredito:
    """efeitos.tsv (anotacao_snpeff.py:76-80): 1º efeito do ANN de cada variante, top 15."""
    return _contagem_top(caminho, "contagem_efeitos_snpeff", "EFEITO\tN", _EFEITO_SNPEFF, "EFEITO")


@_a_prova_de_leitura
def contagem_consequencias_vep(caminho: Path) -> Veredito:
    """efeitos_vep.tsv (vep_anotacao.py:71-75): ocorrências variante × transcrito, top 15."""
    return _contagem_top(caminho, "contagem_consequencias_vep", "CONSEQUENCIA\tN", _TERMO_SO, "CONSEQUENCIA")


# ------------------------------------------------------------------------ tabela_vep
_COLS_VEP = ("Location", "Allele", "Gene", "Feature", "Feature_type", "Consequence")
_LOCATION = re.compile(r"[^:\s]+:\d{1,18}(?:-\d{1,18})?", re.ASCII)


@_a_prova_de_leitura
def tabela_vep(caminho: Path) -> Veredito:
    """vep --tab, lido como o vep_anotacao.py:56-68: pula '##', o 1º '#' é o cabeçalho. Fica
    FORA de COLUNAS_GARANTIDAS de propósito: o fatos.ler_tsv tomaria a 1ª linha '##' como
    cabeçalho — não pode ir para relatorio.tabelas."""
    ruins = _Ocorrencias()
    cab: Optional[list[str]] = None
    ix: dict[str, int] = {}
    n_linhas = 0
    variantes: set[int] = set()
    cont: Counter[str] = Counter()
    for k, linha in _linhas_universais(caminho, _MAX_LINHAS_VEP, f"mais de {_MAX_LINHAS_VEP} linhas (teto da leitura)"):
        if linha.startswith("##"):
            if cab is not None:
                ruins.add("linha '##' depois do cabeçalho", f"linha {k}")
            continue
        if cab is None:
            if not linha.startswith("#Uploaded_variation\t"):
                return Veredito("tabela_vep", False, ["1ª linha não-## não é o cabeçalho '#Uploaded_variation'"])
            cab = linha.split("\t")
            faltam = [x for x in _COLS_VEP if x not in cab]
            if faltam:
                return Veredito("tabela_vep", False, [f"colunas ausentes no cabeçalho: {faltam}"])
            ix = {x: cab.index(x) for x in _COLS_VEP}
            continue
        if not linha:
            ruins.add("linha vazia", f"linha {k}")
            continue
        c = linha.split("\t")
        if len(c) != len(cab):
            ruins.add(f"nº de campos diferente do cabeçalho ({len(cab)})", f"linha {k}")
            continue
        n_linhas += 1
        if not c[0]:
            ruins.add("Uploaded_variation vazio", f"linha {k}")
        variantes.add(_chave_hash(c[0]))
        if not _LOCATION.fullmatch(c[ix["Location"]]):
            ruins.add("Location fora do padrão crom:pos ou crom:ini-fim", f"linha {k}")
        termos = c[ix["Consequence"]].split(",")
        if not all(_TERMO_SO.fullmatch(t) for t in termos):
            ruins.add("Consequence vazia ou com termo fora do vocabulário SO", f"linha {k}")
        for t in termos:
            if _TERMO_SO.fullmatch(t):
                cont[t] += 1
    falhas = ruins.lista()
    if cab is None:
        falhas.append("sem cabeçalho #Uploaded_variation")
    if not n_linhas:
        falhas.append("nenhuma linha de anotação")
    top = cont.most_common(15)
    return Veredito("tabela_vep", not falhas, falhas, {
        "n_linhas": n_linhas, "n_variantes": len(variantes), "n_consequencias_distintas": len(cont),
        "consequencia_topo": top[0][0] if top else None, "recontagem_top15": [[t, n] for t, n in top]})


# ===================================================================== ML (F6)
# ESPEC §2.2 + adendo §A6.1. Consumidores: ml_contraste.py (tabela de rótulos), o
# ml_dataset_genotipos.py (listas: read_text().splitlines(), strip, set — `_ler_grupo`), o
# ml_classificar.py e o ml_robustez.py (pandas read_csv sep='\t', index_col=0).
_ID_ROTULO = re.compile(r"[A-Za-z0-9_.\-]{1,64}", re.ASCII)
_ID_AMOSTRA = re.compile(r"[A-Za-z0-9._\-]{1,64}", re.ASCII)
_MAX_NIVEIS_ROTULO = 50   # acima disso valores_coluna devolve None: a escolha daria 422 em todo plano
_MIN_POR_NIVEL_ML = 10    # ml_dataset_genotipos.py:65-66
_MIN_POR_NIVEL_CV = 5     # StratifiedKFold(5) do ml_classificar e do ml_robustez
_MAX_LISTA = 4 << 20      # bytes de uma lista de amostras
# decimal que o pandas lê como float e o float() do Python lê igual: sem '1_0', 'infinity', 'NAN'
_DECIMAL_ESTRITO = re.compile(r"-?[0-9]+(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?", re.ASCII)
_RECORRENCIA = re.compile(r"[0-5]/5", re.ASCII)


def _decimal_finito(t: str) -> Optional[float]:
    """float de um texto que casa _DECIMAL (sem espaço, sem inf/nan/'1_0'), ou None."""
    if not _DECIMAL.fullmatch(t):
        return None
    x = float(t)
    return x if math.isfinite(x) else None


def _texto_como_read_text(caminho: Path, max_bytes: int) -> tuple[list[str], bool, bool]:
    """Exatamente o `Path.read_text(encoding='utf-8').splitlines()` dos scripts (newline=None
    traduz \\r\\n e \\r; splitlines ainda corta em \\x0b \\x0c \\x1c-\\x1e \\x85 \\u2028 \\u2029).
    Acima de `max_bytes` levanta _Ilegivel. Devolve (linhas, tem BOM, tem \\r)."""
    if caminho.stat().st_size > max_bytes:
        raise _Teto(f"arquivo com mais de {max_bytes} bytes (teto da leitura)")
    bruto = caminho.read_bytes()
    texto = bruto.decode("utf-8")
    return (texto.replace("\r\n", "\n").replace("\r", "\n").splitlines(),
            bruto.startswith(b"\xef\xbb\xbf"), b"\r" in bruto)


@_a_prova_de_leitura
def rotulos_amostras(caminho: Path) -> Veredito:
    """Tabela amostra → grupo que o autor manda (entrada do ml_aims_vcf; as duas classes são
    escolhas do autor sobre a coluna `grupo`). Precisa de `amostra` e `grupo`; as outras colunas
    são ignoradas, mas toda linha tem o nº de campos do cabeçalho. A amostra vira nome de coluna
    da matriz e índice do pandas: com cara de número (1001) o .loc do ml_classificar dá KeyError;
    'NA' o pandas lê como ausente. O grupo vira argumento do script (_problema_nivel). Entre 2 e
    50 grupos (acima de 50 a escolha não tem lista), cada um com ≥10 amostras (o dataset exige)."""
    teto = (_MAX_AMOSTRAS, f"mais de {_MAX_AMOSTRAS} amostras (teto da leitura; linhas vazias contam)")
    with _abrir(caminho, aparar_cabecalho=False, teto=teto) as t:
        cab = t.cab
        if "amostra" not in cab or "grupo" not in cab:
            return Veredito("rotulos_amostras", False,
                            [f"precisa das colunas 'amostra' e 'grupo' (achei {len(cab)} coluna(s): "
                             f"{_nomes(cab, so_identificadores=True)})"] + t.falha_aspas())
        falhas = [f"coluna {c!r} aparece {cab.count(c)} vezes no cabeçalho"
                  for c in ("amostra", "grupo") if cab.count(c) > 1]
        ia, ig = cab.index("amostra"), cab.index("grupo")
        largura = len(cab)
        ruins = _Ocorrencias()
        vistas: set[str] = set()
        niveis: dict[str, int] = {}
        for k, linha in t.linhas():
            if len(linha) != largura:
                ruins.add(f"nº de campos diferente do cabeçalho ({largura})", f"linha {k}")
                continue
            a, g = linha[ia], linha[ig]
            if not a:
                ruins.add("amostra vazia", f"linha {k}")
                continue
            if a in _NA:
                ruins.add("amostra igual a um token de NA ('NA', 'null'…): o pandas a lê como ausente", f"linha {k}")
                continue
            if not _ID_ROTULO.fullmatch(a):
                ruins.add("amostra fora de [A-Za-z0-9_.-]{1,64} (espaço, acento ou longa demais)", f"linha {k}")
                continue
            if r_le_como_nao_texto(a):
                ruins.add("amostra com cara de número (ex.: 1001): o pandas a lê como int no índice e o .loc do "
                          "ml_classificar dá KeyError; prefixe os IDs", f"linha {k}")
                continue
            if a in vistas:
                ruins.add("amostra repetida", f"linha {k}")
                continue
            vistas.add(a)
            pg = "grupo vazio" if not g else _problema_nivel(g)
            if pg:
                ruins.add(f"grupo: {pg}", f"linha {k}")
                continue
            niveis[g] = niveis.get(g, 0) + 1
        falhas += ruins.lista() + t.falha_aspas()
    if len(niveis) < 2:
        falhas.append(f"precisa de ≥2 grupos (achei {len(niveis)})")
    if len(niveis) > _MAX_NIVEIS_ROTULO:
        falhas.append(f"{len(niveis)} grupos: acima de {_MAX_NIVEIS_ROTULO} a coluna deixa de ser de nível e a "
                      "escolha do autor fica sem lista (todo plano daria 422)")
    pequenos = sorted(g for g, n in niveis.items() if n < _MIN_POR_NIVEL_ML)
    if pequenos:
        falhas.append(f"{len(pequenos)} grupo(s) com menos de {_MIN_POR_NIVEL_ML} amostras (ex.: "
                      f"{_nome_seguro(pequenos[0])} com {niveis[pequenos[0]]}): o ml_dataset_genotipos exige "
                      f"≥{_MIN_POR_NIVEL_ML} por grupo")
    ok = not falhas
    perfil: dict[str, Any] = {"n_amostras": sum(niveis.values()), "n_niveis": len(niveis)}
    if ok:
        perfil["niveis"] = dict(sorted(niveis.items()))
    return Veredito("rotulos_amostras", ok, falhas, perfil)


@_a_prova_de_leitura
def lista_amostras(caminho: Path) -> Veredito:
    """Lida como o ml_dataset_genotipos._ler_grupo (ml_dataset_genotipos.py:34-39):
    read_text().splitlines(), strip, linhas não vazias, set. O script NÃO pula '#': a linha vira
    ID. O mínimo do formato é 2 IDs (o Fst usará o mesmo formato); o mínimo de 10 do ML fica no
    ml_contraste e no dataset. Esboço da F5 (genomica_formatos.py:1005-1035) com os auxiliares
    (crítica M5: sem eles, NameError e 500 no polling)."""
    linhas, bom, crlf = _texto_como_read_text(caminho, _MAX_LISTA)
    falhas: list[str] = []
    ruins = _Ocorrencias()
    if bom:
        falhas.append("BOM UTF-8 no início: o 1º ID viraria '\\ufeffSAMEA…' e sairia da interseção em silêncio")
    ids: list[str] = []
    vistos: set[str] = set()
    for n, l in enumerate(linhas, 1):
        s = l.strip()
        if not s:
            continue
        s0 = s.lstrip("﻿") if n == 1 else s
        if s.startswith("#"):
            ruins.add("linha '#': o _ler_grupo do ml_dataset_genotipos não pula comentário e a leria como ID",
                      f"linha {n}")
        elif not _ID_AMOSTRA.fullmatch(s0):
            ruins.add("ID com espaço/TAB interno ou caractere fora de [A-Za-z0-9._-]", f"linha {n}")
        elif s0 in vistos:
            ruins.add("ID repetido (o script colapsa num set: lista malformada)", f"linha {n}")
        else:
            vistos.add(s0)
            ids.append(s0)
    falhas += ruins.lista()
    if len(ids) < 2:
        falhas.append(f"{len(ids)} ID(s): o formato exige ≥2 (o ML exige ≥10 por grupo no ml_contraste e no "
                      "dataset)")
    pref = Counter(re.match(r"[A-Za-z]*", i).group(0) for i in ids)
    return Veredito("lista_amostras", not falhas, falhas,
                    {"n_ids": len(ids), "prefixos": dict(pref.most_common(5)), "crlf": crlf, "ids": ids[:50]})


@_a_prova_de_leitura
def matriz_marcadores(caminho: Path) -> Veredito:
    """genotipos.tsv do dataset (marcador × amostras), lida pelo pandas com index_col=0 e
    na_values='NA'. Coluna 1 = ID do marcador (nome livre; o dataset grava 'variante'), ≥2
    amostras únicas; toda linha com o nº de campos do cabeçalho (cabeçalho com um campo a menos,
    estilo write.table do R, reprova). Valor: token de NA ou decimal finito por regex estrita
    ('.', '-', texto e 'inf' quebram o SimpleImputer). Linha toda NA reprova: o SimpleImputer a
    descarta e desloca os nomes no shap_top.tsv e no nucleo_estavel.tsv. 0 marcadores reprova
    (contig ausente ou --maf > 0,5 terminam assim, com código 0)."""
    teto = (_MAX_GENES, f"mais de {_MAX_GENES} marcadores (teto da leitura; linhas vazias contam)")
    with _abrir(caminho, aparar_cabecalho=False, teto=teto) as t:
        cab = t.cab
        if not cab or not cab[0]:
            return Veredito("matriz_marcadores", False, ["1ª coluna (ID do marcador) sem nome no cabeçalho"]
                            + t.falha_aspas())
        amostras = cab[1:]
        if len(amostras) > _MAX_AMOSTRAS:
            return Veredito("matriz_marcadores", False, [f"mais de {_MAX_AMOSTRAS} colunas de amostra (teto da leitura)"])
        falhas: list[str] = []
        if len(amostras) < 2:
            falhas.append(f"precisa de ≥2 colunas de amostra (achei {len(amostras)})")
        if any(not a for a in amostras):
            falhas.append("nome de amostra vazio no cabeçalho")
        if len(set(amostras)) != len(amostras):
            falhas.append("nomes de amostra repetidos no cabeçalho")
        if cab[0] in amostras:
            falhas.append("uma coluna de amostra tem o nome da coluna de ID")
        largura = len(cab)
        ruins = _Ocorrencias()
        vistos: set[int] = set()
        n = n_na = n_com_na = n_const = 0
        so_012 = True
        for k, linha in t.linhas():
            n += 1
            if len(linha) != largura:
                if len(linha) == largura + 1:
                    ruins.add("linha com um campo a mais: cabeçalho com um campo a menos (estilo write.table do R "
                              "com row.names) — o pandas deslocaria as colunas", f"linha {k}")
                else:
                    ruins.add(f"nº de campos diferente do cabeçalho ({largura})", f"linha {k}")
                continue
            mid = linha[0]
            if not mid:
                ruins.add("ID do marcador vazio", f"linha {k}")
            else:
                h = _chave_hash(mid)
                if h in vistos:
                    ruins.add("ID do marcador repetido (o ml_robustez usa nomes.index() e um dict)", f"linha {k}")
                vistos.add(h)
            valores: set[float] = set()
            na_linha = 0
            for j in range(1, largura):
                v = linha[j]
                if v in _NA:
                    na_linha += 1
                    continue
                x = float(v) if _DECIMAL_ESTRITO.fullmatch(v) else math.nan
                if not math.isfinite(x):
                    ruins.add("valor que não é NA nem decimal finito ('.', '-', texto, inf): a coluna viraria "
                              "texto no pandas e o SimpleImputer quebraria", f"linha {k} coluna {j + 1}")
                    continue
                valores.add(x)
                if x not in (0.0, 1.0, 2.0):
                    so_012 = False
            n_na += na_linha
            if na_linha:
                n_com_na += 1
            if na_linha == largura - 1:
                ruins.add("linha com todas as amostras NA: o SimpleImputer a descarta e desloca os nomes no "
                          "shap_top.tsv e no nucleo_estavel.tsv", f"linha {k}")
            elif len(valores) == 1:
                n_const += 1
        falhas += ruins.lista() + t.falha_aspas()
    if not n:
        falhas.append("0 marcadores: a matriz só tem o cabeçalho (contig ausente ou --maf acima de 0,5 terminam "
                      "assim, com código 0)")
    celulas = n * max(len(amostras), 1)
    return Veredito("matriz_marcadores", not falhas, falhas, {
        "n_marcadores": n, "n_amostras": len(amostras), "coluna_id": _nome_seguro(cab[0]),
        "frac_na": round(n_na / celulas, 4) if celulas else 0.0, "n_linhas_com_na": n_com_na,
        "n_linhas_constantes": n_const, "codificacao_0_1_2": so_012 and n > 0})


@_a_prova_de_leitura
def rotulos_grupos(caminho: Path) -> Veredito:
    """rotulos.tsv do dataset, lido pelo ml_classificar com index_col=0 e ['grupo']: coluna 1 =
    'amostra' (o ID), 'grupo' na posição 2 ou depois (na 1ª o index_col a consumiria: KeyError).
    EXATAMENTE 2 grupos (com 1, IndexError; com 3, o script binariza classes[1] contra o resto
    sem aviso), ≥5 amostras por grupo (StratifiedKFold(5)), com aviso abaixo de 10. ID único,
    sem cara de número nem token de NA."""
    teto = (_MAX_AMOSTRAS, f"mais de {_MAX_AMOSTRAS} amostras (teto da leitura; linhas vazias contam)")
    with _abrir(caminho, aparar_cabecalho=False, teto=teto) as t:
        cab = t.cab
        if cab and cab[0] == "grupo":
            return Veredito("rotulos_grupos", False, [
                "'grupo' na 1ª coluna: o index_col=0 do ml_classificar a consome como índice e a coluna 'grupo' "
                "deixa de existir (KeyError)"] + t.falha_aspas())
        if not cab or cab[0] != "amostra" or cab.count("grupo") != 1:
            return Veredito("rotulos_grupos", False, [
                f"precisa de 'amostra' na 1ª coluna e de UMA coluna 'grupo' depois dela (achei "
                f"{_nomes(cab, so_identificadores=True)})"] + t.falha_aspas())
        ig = cab.index("grupo")
        largura = len(cab)
        ruins = _Ocorrencias()
        vistas: set[str] = set()
        niveis: dict[str, int] = {}
        for k, linha in t.linhas():
            if len(linha) != largura:
                ruins.add(f"nº de campos diferente do cabeçalho ({largura})", f"linha {k}")
                continue
            a, g = linha[0], linha[ig]
            if not a or a in _NA:
                ruins.add("ID de amostra vazio ou token de NA", f"linha {k}")
                continue
            if r_le_como_nao_texto(a):
                ruins.add("ID de amostra com cara de número (ex.: 1001): o pandas o lê como int no índice e ele "
                          "não casa com o cabeçalho da matriz (KeyError)", f"linha {k}")
                continue
            if a in vistas:
                ruins.add("ID de amostra repetido (o .loc duplicaria a amostra)", f"linha {k}")
                continue
            vistas.add(a)
            pg = "grupo vazio" if not g else _problema_nivel(g)
            if pg:
                ruins.add(f"grupo: {pg}", f"linha {k}")
                continue
            niveis[g] = niveis.get(g, 0) + 1
        falhas = ruins.lista() + t.falha_aspas()
    if len(niveis) != 2:
        falhas.append(f"precisa de exatamente 2 grupos (achei {len(niveis)}): com 1 o ml_classificar dá "
                      "IndexError; com 3 ou mais ele binariza classes[1] contra o resto, sem aviso")
    for g, n in sorted(niveis.items()):
        if n < _MIN_POR_NIVEL_CV:
            falhas.append(f"grupo {_nome_seguro(g)} com {n} amostra(s): o StratifiedKFold(5) precisa de "
                          f"≥{_MIN_POR_NIVEL_CV} por grupo")
    avisos = [f"grupo {_nome_seguro(g)} com {n} amostras (< {_MIN_POR_NIVEL_ML}, o mínimo do dataset)"
              for g, n in sorted(niveis.items()) if _MIN_POR_NIVEL_CV <= n < _MIN_POR_NIVEL_ML]
    ok = not falhas
    perfil: dict[str, Any] = {"n_amostras": sum(niveis.values()), "avisos": avisos}
    if ok:
        perfil.update({"niveis": dict(sorted(niveis.items())), "classe_positiva": max(niveis)})
    return Veredito("rotulos_grupos", ok, falhas, perfil)


@_a_prova_de_leitura
def tabela_shap(caminho: Path) -> Veredito:
    """shap_top.tsv (ml_classificar.py: f'{var}\\t{v:.6g}'): cabeçalho exato `VARIANTE
    SHAP_MEDIO_ABS`, VARIANTE única, valor decimal finito ≥ 0 em ordem não crescente (o .6g usa
    notação científica abaixo de 1e-4: '9.5e-05' passa)."""
    teto = (_MAX_GENES, f"mais de {_MAX_GENES} linhas (teto da leitura)")
    with _abrir(caminho, aparar_cabecalho=False, teto=teto) as t:
        if t.cab != ["VARIANTE", "SHAP_MEDIO_ABS"]:
            return Veredito("tabela_shap", False, ["cabeçalho diferente de 'VARIANTE SHAP_MEDIO_ABS' (separados por "
                                                   "TAB)"] + t.falha_aspas())
        ruins = _Ocorrencias()
        vistas: set[str] = set()
        n, ant, maximo = 0, math.inf, None
        for k, linha in t.linhas():
            if len(linha) != 2:
                ruins.add("nº de campos diferente de 2", f"linha {k}")
                continue
            n += 1
            var, tv = linha
            if not var:
                ruins.add("VARIANTE vazia", f"linha {k}")
            elif var in vistas:
                ruins.add("VARIANTE repetida", f"linha {k}")
            vistas.add(var)
            x = _decimal_finito(tv)
            if x is None or x < 0:
                ruins.add("SHAP_MEDIO_ABS não é decimal finito ≥ 0", f"linha {k}")
                continue
            if x > ant:
                ruins.add("SHAP_MEDIO_ABS crescente: o script grava em ordem não crescente", f"linha {k}")
            ant = x
            maximo = x if maximo is None else max(maximo, x)
        falhas = ruins.lista() + t.falha_aspas()
    if not n:
        falhas.append("tabela sem linhas")
    return Veredito("tabela_shap", not falhas, falhas, {"n_linhas": n, "shap_max": maximo})


@_a_prova_de_leitura
def tabela_nucleo_estavel(caminho: Path) -> Veredito:
    """nucleo_estavel.tsv (ml_robustez.py: f'{nome}\\t{fr:.3f}\\t{rec}/5'): cabeçalho exato,
    MARCADOR único, FREQ_BOOTSTRAP em [0,1] em ordem não crescente, RECORRENCIA_FOLDS casa
    '[0-5]/5'. `alerta_empate` = todas as linhas com FREQ 1.000 e 5/5 (top-k preenchido por
    empates de importância zero, como no demo): aviso, não falha."""
    cab_ok = ["MARCADOR", "FREQ_BOOTSTRAP", "RECORRENCIA_FOLDS"]
    teto = (_MAX_GENES, f"mais de {_MAX_GENES} linhas (teto da leitura)")
    with _abrir(caminho, aparar_cabecalho=False, teto=teto) as t:
        if t.cab != cab_ok:
            return Veredito("tabela_nucleo_estavel", False, ["cabeçalho diferente de 'MARCADOR FREQ_BOOTSTRAP "
                                                             "RECORRENCIA_FOLDS' (separados por TAB)"] + t.falha_aspas())
        ruins = _Ocorrencias()
        vistos: set[str] = set()
        n = n_ge_080 = 0
        ant = math.inf
        recorrencia: Counter[str] = Counter()
        empate = True
        for k, linha in t.linhas():
            if len(linha) != 3:
                ruins.add("nº de campos diferente de 3", f"linha {k}")
                continue
            n += 1
            m, tf, rec = linha
            if not m:
                ruins.add("MARCADOR vazio", f"linha {k}")
            elif m in vistos:
                ruins.add("MARCADOR repetido", f"linha {k}")
            vistos.add(m)
            if not _RECORRENCIA.fullmatch(rec):
                ruins.add("RECORRENCIA_FOLDS fora de '[0-5]/5'", f"linha {k}")
            else:
                recorrencia[rec] += 1
            f = _decimal_finito(tf)
            if f is None or not 0.0 <= f <= 1.0:
                ruins.add("FREQ_BOOTSTRAP fora de [0,1] ou não decimal", f"linha {k}")
                empate = False
                continue
            if f > ant:
                ruins.add("FREQ_BOOTSTRAP crescente: o script grava em ordem não crescente", f"linha {k}")
            ant = f
            n_ge_080 += f >= 0.8
            empate = empate and f == 1.0 and rec == "5/5"
        falhas = ruins.lista() + t.falha_aspas()
    if not n:
        falhas.append("tabela sem linhas")
    return Veredito("tabela_nucleo_estavel", not falhas, falhas, {
        "n_linhas": n, "n_freq_ge_0_80": n_ge_080, "recorrencia": dict(sorted(recorrencia.items())),
        "alerta_empate": bool(n) and empate})


# ============================================================== Epigenômica (F6)
# ESPEC §2.3 + adendo §A6.2. Consumidores: metilacao_dmc.R (methRead bismarkCoverage), o
# perfil_tss.py e o mofa_preparar.py (gzip.open + split('\t'), int(campos[1])), o
# intersecao_dmc_painel.py e o motifs_homer.py (read_text/csv), o mofa_treinar(_api).py.
_MAX_LINHAS_COV = 50_000_000         # linhas por membro
_MAX_DESCOMPRIMIDO_COV = 4 << 30     # bytes descomprimidos por membro
_MAX_CROMOSSOMOS = 10_000
_MAX_PAINEL_JSON = 4 << 20
_MAX_GENES_PAINEL = 10_000
_MAX_LINHAS_METHYLKIT = 10_000_000
_GZ_MAGIC = b"\x1f\x8b"
_BLOCO_GZ = 16 << 10                 # entrada comprimida por chamada: no máximo ~16 MiB de saída, mesmo numa bomba
_MIN_DESCOMPRIMIDO_RAZAO = 1 << 20   # a razão só vale acima de 1 MiB descomprimido
_CROM = re.compile(r"[A-Za-z0-9_.\-]{1,64}", re.ASCII)
_CROM_B = re.compile(rb"[A-Za-z0-9_.\-]{1,64}")
_DECIMAL_B = re.compile(rb"(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_ENSEMBL = re.compile(r"[A-Za-z0-9_.\-]{0,64}", re.ASCII)
_SUFIXOS_COV = (".bismark.cov.gz", ".cov.gz", ".bismark.cov", ".cov")
_SUFIXO_POR_CONDICAO = ".bismark.cov.gz"
_CAB_METHYLKIT = ("chr", "start", "end", "strand", "pvalue", "qvalue", "meth.diff")
_MSG_CAB_COV = ("linha de cabeçalho ou de texto: o bismarkCoverage não tem cabeçalho, e o perfil_tss.py faz "
                "int() do início e para")


def stem_coverage(nome: str) -> Optional[str]:
    """A `amostra` do coldata é o nome do arquivo sem o sufixo (decisão 7), do mais longo ao
    mais curto: .bismark.cov.gz, .cov.gz, .bismark.cov, .cov. None = não é coverage. O
    montar_coverages.py repete esta função (teste de sincronia)."""
    for s in _SUFIXOS_COV:
        if nome.endswith(s):
            return nome[:-len(s)] or None
    return None


def nome_por_condicao(nivel: str, amostra: str) -> str:
    """Nome final na porta montar.coverages: o grupo sai do NOME (a disjunção por substring
    dos scripts selados). O montar_coverages.py repete esta função (teste de sincronia)."""
    return f"{nivel}__{amostra}{_SUFIXO_POR_CONDICAO}"


class _TetoPasta(_Teto):
    """Estouro de teto de tamanho (não de conteúdo): o perfil leva `teto` e o registro
    responde 413 `entrada_grande_demais`, não 422."""

    def __init__(self, motivo: str, limite: dict[str, int]) -> None:
        super().__init__(motivo)
        self.limite = limite


def _blocos_gzip(f, nome: str, lidos: list[int]) -> Iterator[bytes]:
    """Bytes descomprimidos de TODOS os membros gzip em sequência, como o gzip do Python e o
    gzfile do R (crítica M11): a cada fim de membro, um decompressobj(31) novo sobre o que
    sobrou. Depois do último membro, byte que não começa por 1f8b é lixo e reprova. `lidos[0]`
    conta os bytes comprimidos lidos (a razão de compressão)."""
    d = None
    buf = b""
    fim = False
    n_membros = 0
    while True:
        if not buf and not fim:
            buf = f.read(_BLOCO_GZ)
            lidos[0] += len(buf)
            fim = not buf
        if d is None:
            if not buf:
                if not n_membros:
                    raise _Ilegivel(f"{nome}: '.cov.gz' vazio")
                return
            if len(buf) < 2 and not fim:
                mais = f.read(_BLOCO_GZ)
                lidos[0] += len(mais)
                fim = not mais
                buf += mais
                continue
            if buf[:2] != _GZ_MAGIC:
                if not n_membros:
                    raise _Ilegivel(f"{nome}: '.cov.gz' que não é gzip (sem o magic 1f8b)")
                raise _Ilegivel(f"{nome}: lixo depois do gzip (bytes que não são outro membro gzip): o gzip do "
                                "Python recusa e o zlib do R os ignora — os dois leriam coisas diferentes")
            d = zlib.decompressobj(31)
            n_membros += 1
        if not buf:
            raise _Ilegivel(f"{nome}: gzip truncado: o arquivo acaba no meio de um membro")
        try:
            saida = d.decompress(buf)
        except zlib.error:
            raise _Ilegivel(f"{nome}: gzip truncado ou corrompido (erro do zlib)") from None
        buf = b""
        if saida:
            yield saida
        if d.eof:
            buf, d = d.unused_data, None


class _LeituraCov:
    """Contadores da pasta inteira (os tetos agregados valem em fluxo, entre membros)."""

    def __init__(self) -> None:
        self.linhas = 0
        self.descomprimido = 0
        self.bytes = 0
        # a razão AGREGADA: Σ comprimido dos .cov.gz já lidos e Σ do que eles descomprimem. Só
        # por membro, a bomba partida em membros abaixo de 1 MiB passava (200 × (1 MiB − 64 B),
        # 415.600 B → 209,7 MB, 505:1). O .cov não entra: é lido 1:1 do disco, não descomprime
        self.gz_comprimido = 0
        self.gz_descomprimido = 0
        self.n_crlf = 0
        self.cromossomos: dict[str, None] = {}
        self.ruins = _Ocorrencias()
        self.avisos: list[str] = []


def _ler_cov_membro(f, nome: str, est: _LeituraCov) -> None:
    """Um coverage Bismark (6 campos por TAB: cromossomo, início, fim, %, metilados, não
    metilados), lido como os consumidores o leem, linha a linha e em fluxo."""
    lidos = [0]
    if nome.endswith(".gz"):
        blocos: Iterator[bytes] = _blocos_gzip(f, nome, lidos)
        gz = True
    else:
        inicio = f.read(2)
        if inicio == _GZ_MAGIC:
            raise _Ilegivel(f"{nome}: '.cov' com conteúdo gzip: o montar o comprimiria de novo e o perfil_tss.py "
                            "abre tudo como gzip; renomeie para .cov.gz")

        def _cru() -> Iterator[bytes]:
            yield inicio
            yield from iter(lambda: f.read(1 << 20), b"")
        blocos, gz = _cru(), False
    teto_pasta_b, teto_pasta_l = diretorio.MAX_DESCOMPRIMIDO_PASTA, diretorio.MAX_LINHAS_PASTA
    razao = diretorio.MAX_RAZAO_GZIP
    ruins, cromossomos = est.ruins, est.cromossomos
    desc = n = n_crlf = regride = reaparece = 0
    resto = b""
    ultimo, ult_pos = None, -1
    vistos: set[bytes] = set()

    def _linhas(partes: list[bytes]) -> None:
        nonlocal n, n_crlf, regride, reaparece, ultimo, ult_pos
        for linha in partes:
            n += 1
            if linha[-1:] == b"\r":
                linha = linha[:-1]
                n_crlf += 1
            if not linha:
                ruins.add("linha vazia (o bismarkCoverage não grava linha vazia)", f"{nome} linha {n}")
                continue
            if b"\r" in linha:
                ruins.add("'\\r' fora do fim da linha: o R e o Python cortariam a linha em lugares diferentes",
                          f"{nome} linha {n}")
                continue
            c = linha.split(b"\t")
            if len(c) != 6:
                ruins.add(_MSG_CAB_COV if linha.startswith((b"#", b"track", b"browser")) else
                          "nº de campos diferente de 6 (cromossomo, início, fim, %metilação, metilados, não "
                          "metilados)", f"{nome} linha {n}")
                continue
            crom, ini, fim, pct, m, u = c
            if not (ini.isdigit() and fim.isdigit()):
                ruins.add(_MSG_CAB_COV, f"{nome} linha {n}")
                continue
            if not _CROM_B.fullmatch(crom):
                ruins.add("cromossomo fora de [A-Za-z0-9_.-]{1,64} (espaço, acento ou longo demais)", f"{nome} linha {n}")
                continue
            if len(ini) > 10 or len(fim) > 10 or not 1 <= int(ini) <= int(fim):
                ruins.add("início/fim fora de 1 ≤ início ≤ fim (inteiros de até 10 dígitos)", f"{nome} linha {n}")
                continue
            if not (m.isdigit() and u.isdigit()) or len(m) > 12 or len(u) > 12:
                ruins.add("metilados/não metilados não são inteiros ≥ 0", f"{nome} linha {n}")
                continue
            mi, ui = int(m), int(u)
            if mi + ui < 1:
                ruins.add("metilados + não metilados = 0 (CpG sem leitura)", f"{nome} linha {n}")
                continue
            if not _DECIMAL_B.fullmatch(pct) or not 0.0 <= float(pct) <= 100.0:
                ruins.add("%metilação não é decimal em [0, 100]", f"{nome} linha {n}")
                continue
            if abs(float(pct) - 100.0 * mi / (mi + ui)) > 1e-6:
                ruins.add("%metilação incoerente com metilados/(metilados + não metilados) (|Δ| > 1e-6)",
                          f"{nome} linha {n}")
                continue
            pos = int(ini)
            if crom != ultimo:
                if crom in vistos:
                    reaparece += 1
                vistos.add(crom)
                ultimo, ult_pos = crom, pos
                cs = crom.decode("ascii")
                if cs not in cromossomos:
                    cromossomos[cs] = None
                    if len(cromossomos) > _MAX_CROMOSSOMOS:
                        raise _TetoPasta(f"mais de {_MAX_CROMOSSOMOS} cromossomos somando os membros (teto)",
                                         {"limite_cromossomos": _MAX_CROMOSSOMOS})
            else:
                if pos < ult_pos:
                    regride += 1
                ult_pos = pos

    for bloco in blocos:
        desc += len(bloco)
        est.descomprimido += len(bloco)
        if desc > _MAX_DESCOMPRIMIDO_COV:
            raise _TetoPasta(f"{nome}: mais de {_MAX_DESCOMPRIMIDO_COV} bytes descomprimidos num membro (teto)",
                             {"limite_bytes": _MAX_DESCOMPRIMIDO_COV})
        if est.descomprimido > teto_pasta_b:
            raise _TetoPasta(f"mais de {teto_pasta_b} bytes descomprimidos somando os membros da pasta (teto)",
                             {"limite_bytes": teto_pasta_b, "limite_linhas": teto_pasta_l})
        if gz and desc > _MIN_DESCOMPRIMIDO_RAZAO and desc > razao * lidos[0]:
            raise _Ilegivel(f"{nome}: razão de compressão > {razao}:1: não é coverage Bismark (o dado real comprime "
                            "cerca de 5:1)")
        if gz:
            est.gz_descomprimido += len(bloco)
            if (est.gz_descomprimido > _MIN_DESCOMPRIMIDO_RAZAO
                    and est.gz_descomprimido > razao * (est.gz_comprimido + lidos[0])):
                raise _Ilegivel(f"razão de compressão > {razao}:1 somando os membros .cov.gz da pasta: não é "
                                "coverage Bismark (o dado real comprime cerca de 5:1; partir a bomba em membros "
                                "abaixo de 1 MiB não escapa da razão)")
        partes = (resto + bloco).split(b"\n")
        resto = partes.pop()
        if len(resto) > _MAX_LINHA:
            raise _Ilegivel(f"{nome}: linha {n + 1} com mais de {_MAX_LINHA} bytes")
        antes = n
        _linhas(partes)
        est.linhas += n - antes
        if n > _MAX_LINHAS_COV:
            raise _TetoPasta(f"{nome}: mais de {_MAX_LINHAS_COV} linhas num membro (teto)",
                             {"limite_linhas": _MAX_LINHAS_COV})
        if est.linhas > teto_pasta_l:
            raise _TetoPasta(f"mais de {teto_pasta_l} linhas somando os membros da pasta (teto)",
                             {"limite_linhas": teto_pasta_l, "limite_bytes": teto_pasta_b})
    if gz:
        est.gz_comprimido += lidos[0]   # o membro inteiro (lidos[0] = tamanho do arquivo)
    if resto:
        _linhas([resto])
        est.linhas += 1
    est.n_crlf += n_crlf
    if regride or reaparece:
        est.avisos.append(f"{nome}: fora de ordem ({regride} posição(ões) que regridem no mesmo cromossomo, "
                          f"{reaparece} cromossomo(s) que reaparecem)")


def _a_prova_de_pasta(func: Callable[..., Veredito]) -> Callable[..., Veredito]:
    """O _a_prova_de_leitura das pastas: reprova, nunca levanta, e separa TETO (perfil
    `teto`, 413 no registro) de recusa. `membros` = [(nome, inode)] já listados (o cache do
    validar), para validar exatamente o que a chave do cache descreve."""
    formato = func.__name__

    @functools.wraps(func)
    def envolto(caminho: Path, membros: Optional[list[tuple[str, int]]] = None) -> Veredito:
        try:
            return func(Path(caminho), membros)
        except _TetoPasta as e:
            return Veredito(formato, False, [str(e)], {"teto": dict(e.limite)})
        except diretorio.ErroMembros as e:
            return Veredito(formato, False, [str(e)], {"teto": dict(e.limite)} if e.teto else {})
        except _Ilegivel as e:
            return Veredito(formato, False, [str(e)])
        except (EOFError, zlib.error):
            return Veredito(formato, False, ["gzip truncado ou corrompido"])
        except UnicodeDecodeError:
            return Veredito(formato, False, ["arquivo não é texto UTF-8"])
        except OSError as e:
            return Veredito(formato, False, [f"arquivo ilegível ({type(e).__name__})"])
    return envolto


def _ler_coverages(formato: str, caminho: Path,
                   membros: Optional[list[tuple[str, int]]]) -> tuple[Veredito, list[str]]:
    regra = DIRETORIOS[formato]
    with diretorio.abrir_pasta(caminho) as p:
        if membros is None:
            membros = [(nome, st.st_ino) for nome, st in diretorio.listar_membros(p, regra)]
        if not membros:
            return Veredito(formato, False, ["pasta sem membros: nenhum arquivo .cov ou .cov.gz (o R e os scripts "
                                             "não leriam nada)"], {"n_membros": 0}), []
        est = _LeituraCov()
        for nome, ino in membros:
            with os.fdopen(diretorio.abrir_membro(p, nome, ino), "rb") as f:
                est.bytes += os.fstat(f.fileno()).st_size
                _ler_cov_membro(f, nome, est)
    nomes = [n for n, _ in membros]
    falhas = est.ruins.lista()
    perfil: dict[str, Any] = {
        "n_membros": len(nomes), "membros": nomes[:50], "bytes": est.bytes, "n_linhas": est.linhas,
        "descomprimido": est.descomprimido, "n_cromossomos": len(est.cromossomos), "n_crlf": est.n_crlf,
        "avisos": est.avisos[:20]}
    if not falhas:
        perfil["cromossomos"] = list(est.cromossomos)
    return Veredito(formato, not falhas, falhas, perfil), nomes


@_a_prova_de_pasta
def coverages_bismark(caminho: Path, membros: Optional[list[tuple[str, int]]] = None) -> Veredito:
    """PASTA de coverages Bismark (entrada da epigenômica, só por cópia). Membros pela regra de
    DIRETORIOS (.cov e .cov.gz); 0 membros reprova. Cada .cov.gz é lido em todos os membros gzip,
    com razão de compressão ≤ MAX_RAZAO_GZIP acima de 1 MiB, por membro e somando os .cov.gz da
    pasta (em fluxo: a bomba partida em membros < 1 MiB reprova); .cov não pode ser gzip. Linha: 6
    campos por TAB, CRLF aceito (o demo é CRLF dentro do gzip), '\\r' isolado, linha vazia e
    cabeçalho reprovam; cromossomo [A-Za-z0-9_.-]{1,64}; 1 ≤ início ≤ fim; pct em [0,100]
    coerente com m/(m+u) a 1e-6; m+u ≥ 1. Tetos por membro (50 M linhas, 4 GiB) e por pasta
    (MAX_LINHAS_PASTA, MAX_DESCOMPRIMIDO_PASTA), em fluxo; 10 000 cromossomos."""
    return _ler_coverages("coverages_bismark", caminho, membros)[0]


@_a_prova_de_pasta
def coverages_por_condicao(caminho: Path, membros: Optional[list[tuple[str, int]]] = None) -> Veredito:
    """Porta montar.coverages: tudo de coverages_bismark e mais — todo membro é
    '<nivel>__<amostra>.bismark.cov.gz' (gzip), o nível passa em _problema_nivel, e a disjunção
    por SUBSTRING vale entre os nomes (metilacao_dmc.R:32-33 com fixed=TRUE e perfil_tss.py:76-77
    escolhem o grupo por substring do nome do arquivo)."""
    v, nomes = _ler_coverages("coverages_por_condicao", caminho, membros)
    ruins = _Ocorrencias()
    por_condicao: dict[str, list[str]] = {}
    for nome in nomes:
        if not nome.endswith(_SUFIXO_POR_CONDICAO):
            ruins.add("membro que não é '<condição>__<amostra>.bismark.cov.gz' (o montar grava só gzip com esse "
                      "nome)", nome)
            continue
        nivel, sep, amostra = nome[:-len(_SUFIXO_POR_CONDICAO)].partition("__")
        if not sep or not nivel or not amostra:
            ruins.add("membro sem '<condição>__' no nome: o grupo sai do nome do arquivo", nome)
            continue
        problema = _problema_nivel(nivel)
        if problema:
            ruins.add(f"condição do nome: {problema}", nome)
            continue
        por_condicao.setdefault(nivel, []).append(nome)
    falhas = ruins.lista()
    for nivel, proprios in sorted(por_condicao.items()):
        alheios = sorted(n for n in nomes if nivel in n and n not in proprios)
        if alheios:
            falhas.append(f"a condição {_nome_seguro(nivel)} casa por SUBSTRING com {len(alheios)} arquivo(s) de outra "
                          f"condição (ex.: {alheios[0]}): o metilacao_dmc.R e o perfil_tss.py juntariam os grupos")
    perfil = dict(v.perfil)
    if not falhas:
        perfil["por_condicao"] = {k: len(x) for k, x in sorted(por_condicao.items())}
    return Veredito("coverages_por_condicao", v.ok and not falhas, v.falhas + falhas, perfil)


class _ChaveRepetida(ValueError):
    """Chave repetida num objeto JSON (o json do Python fica com a ÚLTIMA, sem aviso)."""


def _objeto_sem_repetidas(pares: list[tuple[str, Any]]) -> dict[str, Any]:
    d: dict[str, Any] = {}
    for k, v in pares:
        if k in d:
            raise _ChaveRepetida(k)
        d[k] = v
    return d


def _ler_painel_json(caminho: Path) -> Any:
    """O painel como o json.loads(read_text()) dos scripts, recusando o que eles aceitariam
    calados: chave repetida, NaN/Infinity, decimal que estoura. BOM: o json.loads recusa."""
    if caminho.stat().st_size > _MAX_PAINEL_JSON:
        raise _Teto(f"painel com mais de {_MAX_PAINEL_JSON} bytes")
    bruto = caminho.read_bytes()
    if bruto.startswith(b"\xef\xbb\xbf"):
        raise _Teto("BOM UTF-8 no início: o json.loads dos scripts recusa o arquivo")
    texto = bruto.decode("utf-8")

    def _recusa(_nome: str) -> None:
        raise NaoFinito("NaN/Infinity")

    def _decimal(t: str) -> float:
        x = float(t)
        if not math.isfinite(x):
            raise NaoFinito("decimal que estoura")
        return x
    return json.loads(texto, object_pairs_hook=_objeto_sem_repetidas, parse_constant=_recusa, parse_float=_decimal)


def _eh_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


@_a_prova_de_leitura
def painel_genes_json(caminho: Path) -> Veredito:
    """painel_<n>.json: {"genes": {símbolo: {cromossomo, inicio, fim, fita, ensembl_id?}}, "_meta"?},
    lido por perfil_tss.py, mofa_preparar.py e painel_janelas.py. A fita é o ponto fino: os
    scripts testam `fita >= 0`, então 0, true e "1" virariam fita + (TSS no início) — só 1 e -1
    como inteiros passam. bool não vale como inteiro em lugar nenhum."""
    try:
        dados = _ler_painel_json(caminho)
    except _ChaveRepetida:
        return Veredito("painel_genes_json", False, ["chave repetida no JSON: o json do Python fica com a ÚLTIMA, "
                                                     "sem aviso"])
    except NaoFinito:
        return Veredito("painel_genes_json", False, ["número não finito (NaN/Infinity) no painel"])
    except json.JSONDecodeError as e:
        return Veredito("painel_genes_json", False, [f"JSON ilegível: {str(e)[:80]}"])
    except RecursionError:
        return Veredito("painel_genes_json", False, ["JSON aninhado fundo demais para ler"])
    except _Teto as e:
        return Veredito("painel_genes_json", False, [str(e)])
    except UnicodeDecodeError:
        raise
    except ValueError as e:
        return Veredito("painel_genes_json", False, [f"JSON ilegível ({type(e).__name__})"])
    if not isinstance(dados, dict):
        return Veredito("painel_genes_json", False, ["a raiz do JSON não é objeto: os scripts fazem dados['genes']"])
    genes = dados.get("genes")
    if not isinstance(genes, dict) or not genes:
        return Veredito("painel_genes_json", False, ["'genes' ausente, vazio ou não é objeto {símbolo: {...}}"])
    if len(genes) > _MAX_GENES_PAINEL:
        return Veredito("painel_genes_json", False, [f"mais de {_MAX_GENES_PAINEL} genes"])
    ruins = _Ocorrencias()
    cromossomos: dict[str, None] = {}
    n_neg = n_sem_ens = 0
    for simbolo, g in genes.items():
        pos = f"gene {_nome_seguro(simbolo)}"
        if not _SIMBOLO.fullmatch(simbolo):
            ruins.add("símbolo fora de [A-Za-z0-9._()-]{1,64} (vai para a 4ª coluna do BED e para a chave GENE)", pos)
            continue
        if not isinstance(g, dict):
            ruins.add("gene não é objeto", pos)
            continue
        crom = g.get("cromossomo")
        if isinstance(crom, bool) or not isinstance(crom, (str, int)) or not _CROM.fullmatch(str(crom)):
            ruins.add("cromossomo ausente, ou não é texto/inteiro em [A-Za-z0-9_.-]{1,64}", pos)
        else:
            cromossomos[str(crom)] = None
        ini, fim = g.get("inicio"), g.get("fim")
        if not (_eh_int(ini) and _eh_int(fim)):
            ruins.add("inicio/fim ausente ou não inteiro (bool e decimal não valem)", pos)
        elif not 1 <= ini <= fim < 2 ** 31:
            ruins.add("inicio/fim fora de 1 ≤ inicio ≤ fim < 2^31", pos)
        fita = g.get("fita")
        if not _eh_int(fita) or fita not in (1, -1):
            ruins.add("fita fora de {1, -1} como inteiro: 0, true e '1' viram fita + no perfil_tss.py e no "
                      "mofa_preparar.py (que testam fita >= 0)", pos)
        elif fita == -1:
            n_neg += 1
        ens = g.get("ensembl_id", "")
        if not isinstance(ens, str) or not _ENSEMBL.fullmatch(ens):
            ruins.add("ensembl_id fora de [A-Za-z0-9_.-]{0,64}", pos)
        elif not ens:
            n_sem_ens += 1
    falhas = ruins.lista()
    ok = not falhas
    perfil: dict[str, Any] = {"n_genes": len(genes), "n_fita_negativa": n_neg, "n_sem_ensembl": n_sem_ens,
                              "tem_meta": "_meta" in dados}
    if ok:
        perfil["cromossomos"] = list(cromossomos)
    return Veredito("painel_genes_json", ok, falhas, perfil)


@_a_prova_de_leitura
def painel_bed(caminho: Path) -> Veredito:
    """janelas.bed do painel_janelas.py ('<crom>\\t<ini>\\t<fim>\\t<gene>\\t<ensembl>', LF), lido
    pelo intersecao_dmc_painel.py (split('\\t'), int()). Exatamente 5 campos (o 5º pode ser
    vazio); sem cabeçalho nem 'track'; início < fim; gene único; de 1 a 10 000 linhas."""
    ruins = _Ocorrencias()
    genes: set[str] = set()
    cromossomos: dict[str, None] = {}
    n = 0
    for k, linha in _linhas_lf(caminho, _MAX_SIMBOLOS, f"mais de {_MAX_SIMBOLOS} janelas (teto da leitura)"):
        if linha.endswith("\r"):
            ruins.add("quebra de linha CRLF: o painel_janelas grava LF", f"linha {k}")
            continue
        if not linha:
            ruins.add("linha vazia", f"linha {k}")
            continue
        if linha.startswith(("#", "track", "browser")):
            ruins.add("linha de cabeçalho/track: o intersecao_dmc_painel.py faz int() do 2º campo", f"linha {k}")
            continue
        c = linha.split("\t")
        if len(c) != 5:
            ruins.add("nº de campos diferente de 5 (cromossomo, início, fim, gene, ensembl)", f"linha {k}")
            continue
        n += 1
        crom, ini, fim, gene, ens = c
        if not _CROM.fullmatch(crom):
            ruins.add("cromossomo fora de [A-Za-z0-9_.-]{1,64}", f"linha {k}")
        else:
            cromossomos[crom] = None
        if not (_INT_CURTO.fullmatch(ini) and _INT_CURTO.fullmatch(fim)) or int(ini) >= int(fim):
            ruins.add("início/fim não inteiros ou início ≥ fim", f"linha {k}")
        if not _SIMBOLO.fullmatch(gene):
            ruins.add("gene fora de [A-Za-z0-9._()-]{1,64}", f"linha {k}")
        elif gene in genes:
            ruins.add("gene repetido (é a chave da interseção)", f"linha {k}")
        genes.add(gene)
        if not _ENSEMBL.fullmatch(ens):
            ruins.add("ensembl fora de [A-Za-z0-9_.-]{0,64}", f"linha {k}")
    falhas = ruins.lista()
    if not n:
        falhas.append("nenhuma janela")
    ok = not falhas
    perfil: dict[str, Any] = {"n_janelas": n}
    if ok:
        perfil["cromossomos"] = list(cromossomos)
    return Veredito("painel_bed", ok, falhas, perfil)


def _tabela_methylkit(caminho: Path, formato: str, *, dmc: bool) -> Veredito:
    """dmc.tsv e todas_cpgs.tsv: o write.table(quote=FALSE, row.names=FALSE) de
    metilacao_dmc.R:55,61. Cabeçalho exato, 7 campos, start/end inteiros sem notação científica,
    strand em + - *, p/q em [0,1] (com expoente), meth.diff em [-100,100] e chave chr:start única."""
    teto = (_MAX_LINHAS_METHYLKIT, f"mais de {_MAX_LINHAS_METHYLKIT} linhas (teto da leitura)")
    with _abrir(caminho, aparar_cabecalho=False, teto=teto) as t:
        if tuple(t.cab) != _CAB_METHYLKIT:
            return Veredito(formato, False, ["cabeçalho diferente de 'chr start end strand pvalue qvalue meth.diff' "
                                             "(o write.table do methylKit, separado por TAB)"] + t.falha_aspas())
        ruins = _Ocorrencias()
        chaves: set[int] = set()
        n = n_q_na = n_zero = n_hiper = n_hipo = 0
        for k, linha in t.linhas():
            if len(linha) != 7:
                ruins.add("nº de campos diferente de 7 (coluna de row.names? o write.table com row.names=TRUE grava "
                          "8)", f"linha {k}")
                continue
            n += 1
            crom, ini, fim, fita, tp, tq, td = linha
            if not _CROM.fullmatch(crom):
                ruins.add("chr fora de [A-Za-z0-9_.-]{1,64}", f"linha {k}")
            if not (_INT_CURTO.fullmatch(ini) and _INT_CURTO.fullmatch(fim)):
                ruins.add("start/end não são inteiros (notação científica ou NA)", f"linha {k}")
            if fita not in ("+", "-", "*"):
                ruins.add("strand fora de + - *", f"linha {k}")
            for nome, tv in (("pvalue", tp), ("qvalue", tq)):
                if tv == "NA":
                    if dmc:
                        ruins.add(f"{nome} NA numa DMC", f"linha {k}")
                    elif nome == "qvalue":
                        n_q_na += 1
                    continue
                x = _decimal_finito(tv)
                if x is None or not 0.0 <= x <= 1.0:
                    ruins.add(f"{nome} fora de [0,1] ou não decimal", f"linha {k}")
            if td == "NA":
                if dmc:
                    ruins.add("meth.diff NA numa DMC", f"linha {k}")
            else:
                d = _decimal_finito(td)
                if d is None or not -100.0 <= d <= 100.0:
                    ruins.add("meth.diff fora de [-100,100] ou não decimal (inf/NaN)", f"linha {k}")
                elif d == 0:
                    n_zero += 1
                    if dmc:
                        ruins.add("meth.diff = 0 numa DMC: é o sinal de todas_cpgs.tsv no lugar do dmc.tsv (a "
                                  "interseção contaria a linha como hipo)", f"linha {k}")
                elif d > 0:
                    n_hiper += 1
                else:
                    n_hipo += 1
            h = _chave_hash(crom, ini)
            if h in chaves:
                ruins.add("chave chr:start repetida", f"linha {k}")
            chaves.add(h)
        falhas = ruins.lista() + t.falha_aspas()
    if not dmc and not n:
        falhas.append("tabela sem CpGs: o teste do methylKit não devolveu nenhuma linha")
    perfil = ({"n_dmc": n, "n_hiper": n_hiper, "n_hipo": n_hipo} if dmc
              else {"n_cpgs": n, "n_q_na": n_q_na, "n_diff_zero": n_zero})
    return Veredito(formato, not falhas, falhas, perfil)


@_a_prova_de_leitura
def dmc_methylkit(caminho: Path) -> Veredito:
    """dmc.tsv (as DMCs do getMethylDiff): sem NA e sem meth.diff = 0 (o sinal de todas_cpgs.tsv
    no lugar errado; o demo tem 1.781 zeros e a interseção os contaria como hipo). 0 linhas é
    aceito (nenhuma DMC é um resultado)."""
    return _tabela_methylkit(caminho, "dmc_methylkit", dmc=True)


@_a_prova_de_leitura
def cpgs_methylkit(caminho: Path) -> Veredito:
    """todas_cpgs.tsv (o getData do calculateDiffMeth, sem filtro): aceita NA (contado), ≥1
    linha, até 10 M linhas."""
    return _tabela_methylkit(caminho, "cpgs_methylkit", dmc=False)


@_a_prova_de_leitura
def tabela_intersecao_painel(caminho: Path) -> Veredito:
    """intersecao.tsv (intersecao_dmc_painel.py:66-70): cabeçalho exato `GENE N_DMC HIPER
    HIPO`, GENE único, N_DMC ≥ 1, HIPER + HIPO = N_DMC, N_DMC em ordem não crescente. 0 linhas é
    aceito (nenhuma DMC no painel)."""
    teto = (_MAX_SIMBOLOS, f"mais de {_MAX_SIMBOLOS} genes (teto da leitura)")
    with _abrir(caminho, aparar_cabecalho=False, teto=teto) as t:
        if t.cab != ["GENE", "N_DMC", "HIPER", "HIPO"]:
            return Veredito("tabela_intersecao_painel", False, ["cabeçalho diferente de 'GENE N_DMC HIPER HIPO' "
                                                                "(separados por TAB)"] + t.falha_aspas())
        ruins = _Ocorrencias()
        genes: set[str] = set()
        n = soma = 0
        ant = math.inf
        topo = None
        for k, linha in t.linhas():
            if len(linha) != 4:
                ruins.add("nº de campos diferente de 4", f"linha {k}")
                continue
            n += 1
            gene, tn, th, to = linha
            if not _SIMBOLO.fullmatch(gene):
                ruins.add("GENE fora de [A-Za-z0-9._()-]{1,64}", f"linha {k}")
            elif gene in genes:
                ruins.add("GENE repetido", f"linha {k}")
            genes.add(gene)
            if not all(_INT_CURTO.fullmatch(x) for x in (tn, th, to)):
                ruins.add("N_DMC/HIPER/HIPO não são inteiros", f"linha {k}")
                continue
            nd, hi, ho = int(tn), int(th), int(to)
            if nd < 1:
                ruins.add("N_DMC < 1: o script só grava gene com DMC", f"linha {k}")
            if hi + ho != nd:
                ruins.add("HIPER + HIPO ≠ N_DMC", f"linha {k}")
            if nd > ant:
                ruins.add("N_DMC crescente: o script grava em ordem não crescente", f"linha {k}")
            ant = nd
            soma += nd
            topo = topo or (gene if _SIMBOLO.fullmatch(gene) else None)
        falhas = ruins.lista() + t.falha_aspas()
    perfil: dict[str, Any] = {"n_genes": n, "soma_n_dmc": soma}
    if not falhas:
        perfil["gene_topo"] = topo
    return Veredito("tabela_intersecao_painel", not falhas, falhas, perfil)


@_a_prova_de_leitura
def tabela_motifs_homer(caminho: Path) -> Veredito:
    """motifs_top.tsv (motifs_homer.py:82-90): cabeçalho exato `MOTIF P_VALUE Q_VALUE`, de 1 a
    15 linhas (o script para em 15), MOTIF não vazio (sem exigir unicidade: o HOMER repete nome
    de motivo de bases diferentes), P e Q em [0,1], P em ordem não decrescente."""
    with _abrir(caminho, aparar_cabecalho=False, teto=(100, "mais de 100 linhas: o script grava no máximo 15")) as t:
        if t.cab != ["MOTIF", "P_VALUE", "Q_VALUE"]:
            return Veredito("tabela_motifs_homer", False, ["cabeçalho diferente de 'MOTIF P_VALUE Q_VALUE' "
                                                           "(separados por TAB)"] + t.falha_aspas())
        ruins = _Ocorrencias()
        n = n_q = 0
        ant = -math.inf
        topo = None
        for k, linha in t.linhas():
            if len(linha) != 3:
                ruins.add("nº de campos diferente de 3", f"linha {k}")
                continue
            n += 1
            motivo, tp, tq = linha
            if not motivo.strip() or not motivo.isprintable():
                ruins.add("MOTIF vazio ou com caractere de controle", f"linha {k}")
            p, q = _decimal_finito(tp), _decimal_finito(tq)
            if p is None or q is None or not (0.0 <= p <= 1.0 and 0.0 <= q <= 1.0):
                ruins.add("P_VALUE/Q_VALUE fora de [0,1] ou não decimal", f"linha {k}")
                continue
            if p < ant:
                ruins.add("P_VALUE decrescente: o HOMER ranqueia do menor p para o maior", f"linha {k}")
            ant = p
            n_q += q < 0.05
            topo = topo or _nome(motivo, 64)
        falhas = ruins.lista() + t.falha_aspas()
    if not n:
        falhas.append("nenhum motivo")
    if n > 15:
        falhas.append(f"{n} linhas: o script grava no máximo 15")
    perfil: dict[str, Any] = {"n_linhas": n, "n_q_lt_0_05": n_q}
    if not falhas:
        perfil["motif_topo"] = topo
    return Veredito("tabela_motifs_homer", not falhas, falhas, perfil)


@_a_prova_de_leitura
def view_mofa(caminho: Path) -> Veredito:
    """view_promotor/view_corpo.tsv (mofa_preparar.py:107-110), lidas pelo _ler_view do
    mofa_treinar (csv.reader; 'NA' → NaN, o resto float()): 1ª coluna `feature`, ≥4 amostras
    (mofa_preparar.py:75-76) com nomes únicos no padrão de membro, feature única, valor 'NA' ou
    decimal em [0,100], ≥1 linha, nenhuma linha toda NA."""
    with _abrir(caminho, aparar_cabecalho=False, teto=(_MAX_GENES, f"mais de {_MAX_GENES} linhas (teto)")) as t:
        cab = t.cab
        if not cab or cab[0] != "feature":
            return Veredito("view_mofa", False, ["1ª coluna deve ser 'feature'"] + t.falha_aspas())
        amostras = cab[1:]
        falhas: list[str] = []
        if len(amostras) < 4:
            falhas.append(f"{len(amostras)} amostra(s): o MOFA precisa de ≥4 (mofa_preparar.py:75-76)")
        if len(set(amostras)) != len(amostras) or not all(diretorio.NOME_MEMBRO.fullmatch(a) for a in amostras):
            falhas.append("nomes de amostra repetidos ou fora do padrão (letras ASCII, dígitos, '_', '.', '-')")
        largura = len(cab)
        ruins = _Ocorrencias()
        feats: set[str] = set()
        n = n_na = 0
        for k, linha in t.linhas():
            if len(linha) != largura:
                ruins.add(f"nº de campos diferente do cabeçalho ({largura})", f"linha {k}")
                continue
            n += 1
            f = linha[0]
            if not f:
                ruins.add("feature vazia", f"linha {k}")
            elif f in feats:
                ruins.add("feature repetida", f"linha {k}")
            feats.add(f)
            na_linha = 0
            for j in range(1, largura):
                v = linha[j]
                if v == "NA":
                    na_linha += 1
                    continue
                x = _decimal_finito(v)
                if x is None or not 0.0 <= x <= 100.0:
                    ruins.add("valor que não é 'NA' nem decimal em [0,100]", f"linha {k} coluna {j + 1}")
            n_na += na_linha
            if na_linha == largura - 1:
                ruins.add("linha com todas as amostras NA", f"linha {k}")
        falhas += ruins.lista() + t.falha_aspas()
    if not n:
        falhas.append("view sem features (só o cabeçalho)")
    perfil: dict[str, Any] = {"n_features": n, "n_amostras": len(amostras), "n_na": n_na}
    if not falhas:
        perfil["amostras"] = amostras[:50]
    return Veredito("view_mofa", not falhas, falhas, perfil)


@_a_prova_de_leitura
def tabela_variancia_mofa(caminho: Path) -> Veredito:
    """variancia_explicada.tsv (mofa_treinar.py:114-117): cabeçalho exato `fator promotor corpo`,
    linhas Factor1..FactorK em sequência (K ≥ 2), R² em [-100,100] (o script aborta fora disso)."""
    with _abrir(caminho, aparar_cabecalho=False, teto=(1000, "mais de 1000 fatores (teto)")) as t:
        if t.cab != ["fator", "promotor", "corpo"]:
            return Veredito("tabela_variancia_mofa", False, ["cabeçalho diferente de 'fator promotor corpo' "
                                                             "(separados por TAB)"] + t.falha_aspas())
        ruins = _Ocorrencias()
        n = 0
        for k, linha in t.linhas():
            if len(linha) != 3:
                ruins.add("nº de campos diferente de 3", f"linha {k}")
                continue
            n += 1
            if linha[0] != f"Factor{n}":
                ruins.add(f"fator fora de sequência (esperava Factor{n})", f"linha {k}")
            for v in linha[1:]:
                x = _decimal_finito(v)
                if x is None or not -100.0 <= x <= 100.0:
                    ruins.add("R² não é decimal finito em [-100,100] (nan/inf?)", f"linha {k}")
                    break
        falhas = ruins.lista() + t.falha_aspas()
    if n < 2:
        falhas.append(f"{n} fator(es): o MOFA desta plataforma treina K ≥ 2")
    return Veredito("tabela_variancia_mofa", not falhas, falhas, {"n_fatores": n})


@_a_prova_de_leitura
def fatores_mofa(caminho: Path) -> Veredito:
    """fatores_por_amostra.tsv do mofa_treinar_api.py: cabeçalho `amostra condicao
    Factor1..FactorK` (K ≥ 2), amostra única, condição não vazia, fatores decimais finitos, ≥4
    amostras."""
    with _abrir(caminho, aparar_cabecalho=False, teto=(_MAX_AMOSTRAS, f"mais de {_MAX_AMOSTRAS} amostras")) as t:
        cab = t.cab
        k_fat = len(cab) - 2
        if cab[:2] != ["amostra", "condicao"] or k_fat < 2 or cab[2:] != [f"Factor{i}" for i in range(1, k_fat + 1)]:
            return Veredito("fatores_mofa", False, ["cabeçalho diferente de 'amostra condicao Factor1 … FactorK' "
                                                    "(K ≥ 2, em sequência)"] + t.falha_aspas())
        largura = len(cab)
        ruins = _Ocorrencias()
        amostras: set[str] = set()
        por_condicao: Counter[str] = Counter()
        for k, linha in t.linhas():
            if len(linha) != largura:
                ruins.add(f"nº de campos diferente do cabeçalho ({largura})", f"linha {k}")
                continue
            a, c = linha[0], linha[1]
            if not a:
                ruins.add("amostra vazia", f"linha {k}")
            elif a in amostras:
                ruins.add("amostra repetida", f"linha {k}")
            amostras.add(a)
            if not c or c != c.strip():
                ruins.add("condição vazia ou com espaço em volta", f"linha {k}")
            else:
                por_condicao[c] += 1
            if any(_decimal_finito(v) is None for v in linha[2:]):
                ruins.add("fator não é decimal finito (nan/inf?)", f"linha {k}")
        falhas = ruins.lista() + t.falha_aspas()
    if len(amostras) < 4:
        falhas.append(f"{len(amostras)} amostra(s): o MOFA precisa de ≥4")
    perfil: dict[str, Any] = {"n_amostras": len(amostras), "n_fatores": k_fat}
    if not falhas:
        perfil["por_condicao"] = {_nome_seguro(c): n for c, n in sorted(por_condicao.items())}
    return Veredito("fatores_mofa", not falhas, falhas, perfil)


VALIDADORES: dict[str, Callable[[Path], Veredito]] = {
    "contagens_genes": contagens_genes,
    "coldata": coldata,
    "tabela_de": tabela_de,
    "resumo_json": resumo_json,
    "figura_png": figura_png,
    # genômica (F5)
    "painel_simbolos": painel_simbolos,
    "bed_regioes": bed_regioes,
    "vcf_indexado": vcf_indexado,
    "indice_vcf": indice_vcf,
    "vcf_anotado_snpeff": vcf_anotado_snpeff,
    "tabela_frequencias": tabela_frequencias,
    "contagem_efeitos_snpeff": contagem_efeitos_snpeff,
    "tabela_vep": tabela_vep,
    "contagem_consequencias_vep": contagem_consequencias_vep,
    # ML (F6)
    "rotulos_amostras": rotulos_amostras,
    "lista_amostras": lista_amostras,
    "matriz_marcadores": matriz_marcadores,
    "rotulos_grupos": rotulos_grupos,
    "tabela_shap": tabela_shap,
    "tabela_nucleo_estavel": tabela_nucleo_estavel,
    # epigenômica (F6); os dois coverages_* são PASTA (DIRETORIOS)
    "coverages_bismark": coverages_bismark,
    "coverages_por_condicao": coverages_por_condicao,
    "painel_genes_json": painel_genes_json,
    "painel_bed": painel_bed,
    "dmc_methylkit": dmc_methylkit,
    "cpgs_methylkit": cpgs_methylkit,
    "tabela_intersecao_painel": tabela_intersecao_painel,
    "tabela_motifs_homer": tabela_motifs_homer,
    "view_mofa": view_mofa,
    "tabela_variancia_mofa": tabela_variancia_mofa,
    "fatores_mofa": fatores_mofa,
}

# extensão com que uma ENTRADA de run é gravada em raw/runs/<run_id>/ (o script lê pelo
# conteúdo, mas o R e o matplotlib reclamam de extensão estranha em mensagens de erro). Na
# genômica a extensão MUDA a leitura: o bcftools só lê o BED como 0-based com '.bed', e o
# índice é procurado como '<arquivo>.tbi/.csi'. Quando o nome da origem termina num sufixo de
# SUFIXOS, é ele que vale (um BCF continua '.bcf'): ver sufixo_de_destino.
EXTENSOES: dict[str, str] = {
    "contagens_genes": ".tsv", "coldata": ".tsv", "tabela_de": ".tsv",
    "resumo_json": ".json", "figura_png": ".png",
    "painel_simbolos": ".txt", "bed_regioes": ".bed", "vcf_indexado": ".vcf.gz", "indice_vcf": ".tbi",
    "vcf_anotado_snpeff": ".vcf.gz", "tabela_frequencias": ".tsv", "contagem_efeitos_snpeff": ".tsv",
    "contagem_consequencias_vep": ".tsv", "tabela_vep": ".tsv",
    # F6 (os formatos de PASTA não entram: a cópia preserva o nome da pasta e dos membros)
    "rotulos_amostras": ".tsv", "lista_amostras": ".txt", "matriz_marcadores": ".tsv",
    "rotulos_grupos": ".tsv", "tabela_shap": ".tsv", "tabela_nucleo_estavel": ".tsv",
    "painel_genes_json": ".json", "painel_bed": ".bed", "dmc_methylkit": ".tsv", "cpgs_methylkit": ".tsv",
    "tabela_intersecao_painel": ".tsv", "tabela_motifs_homer": ".tsv", "view_mofa": ".tsv",
    "tabela_variancia_mofa": ".tsv", "fatores_mofa": ".tsv",
}

# Sufixos ACEITOS no nome de um formato cujo nome muda a leitura (a regra de nome exige um
# deles). EXTENSOES[f] está sempre em SUFIXOS[f] (teste de sincronia).
SUFIXOS: dict[str, tuple[str, ...]] = {
    "painel_simbolos": (".txt",),
    "bed_regioes": (".bed",),
    "vcf_indexado": (".vcf.gz", ".bcf"),
    "indice_vcf": (".tbi", ".csi"),
    "vcf_anotado_snpeff": (".vcf.gz",),
}

# Arquivos IRMÃOS obrigatórios de um formato: <principal><sufixo>, por sufixo do principal.
# O cliente nunca manda o caminho do índice — o servidor o deriva daqui (G1 §0.2). Ordem =
# a de procura; com mais de um presente o vcf_indexado reprova (ambíguo, crítica #4).
IRMAOS: dict[str, dict[str, tuple[str, ...]]] = {
    "vcf_indexado": {".vcf.gz": (".tbi", ".csi"), ".bcf": (".csi",)},
}

# Formatos que podem entrar POR REFERÊNCIA (arquivo grande já numa raiz de referência, sem
# cópia): só o VCF/BCF indexado — o validador custa milissegundos num BCF de 2 GB.
REFERENCIAVEIS: frozenset[str] = frozenset({"vcf_indexado"})

# Formatos de PASTA (F6, ESPEC §1.A.2): a regra de membros que fica congelada no registro e no
# plano. A leitura é a união do list.files(pattern="\\.cov(\\.gz)?$") do R (metilacao_dmc.R:31)
# com os glob '*.cov.gz' e '*.cov' do Python (perfil_tss.py:75, mofa_preparar.py:74).
# DIRETORIOS ⊆ VALIDADORES; fora de EXTENSOES, de REFERENCIAVEIS e dos alvos do adaptador.
DIRETORIOS: dict[str, dict[str, Any]] = {
    "coverages_bismark": {"leitura": r"\.cov(?:\.gz)?\Z", "fixos": None},
    "coverages_por_condicao": {"leitura": r"\.cov(?:\.gz)?\Z", "fixos": None},
}

COLUNAS_GARANTIDAS.update({
    "tabela_frequencias": tuple(_CAB_FREQ),
    "contagem_efeitos_snpeff": ("EFEITO", "N"),
    "contagem_consequencias_vep": ("CONSEQUENCIA", "N"),
    # F6
    "rotulos_amostras": ("amostra", "grupo"), "rotulos_grupos": ("amostra", "grupo"),
    "matriz_marcadores": (), "tabela_shap": ("VARIANTE", "SHAP_MEDIO_ABS"),
    "tabela_nucleo_estavel": ("MARCADOR", "FREQ_BOOTSTRAP", "RECORRENCIA_FOLDS"),
    "dmc_methylkit": _CAB_METHYLKIT, "cpgs_methylkit": _CAB_METHYLKIT,
    "tabela_intersecao_painel": ("GENE", "N_DMC", "HIPER", "HIPO"),
    "tabela_motifs_homer": ("MOTIF", "P_VALUE", "Q_VALUE"), "view_mofa": ("feature",),
    "tabela_variancia_mofa": ("fator", "promotor", "corpo"), "fatores_mofa": ("amostra", "condicao"),
})
COLUNAS_DE_NIVEL.update({"rotulos_amostras": ("grupo",)})
# tabela_frequencias NÃO entra: a chave dela é composta (CHROM:POS:REF:ALT) e a de
# relatorio.tabelas é uma coluna só (G1 §0.9). Os methylKit também não (chave chr:start).
COLUNAS_CHAVE.update({
    "contagem_efeitos_snpeff": ("EFEITO",),
    "contagem_consequencias_vep": ("CONSEQUENCIA",),
    "rotulos_amostras": ("amostra",), "tabela_shap": ("VARIANTE",),
    "tabela_nucleo_estavel": ("MARCADOR",), "tabela_intersecao_painel": ("GENE",),
    "tabela_variancia_mofa": ("fator",),
})
# Chave de LINHA de uma tabela, para o desempate da recontagem por porta (adendo §A4 X3′): a
# linha de borda (|x| a menos da tolerância do limite) conta se a sua chave está na porta de
# desempate. Os dois lados precisam da MESMA chave; as colunas são garantidas pelo formato.
CHAVE_LINHA: dict[str, tuple[str, ...]] = {
    "cpgs_methylkit": ("chr", "start"),
    "dmc_methylkit": ("chr", "start"),
}


# ------------------------------------------------------------- nomes (fora do conteúdo)
_MSG_NOME_BED = ("nome não termina em .bed: o bcftools -R lê o arquivo como 1-based inclusivo e desloca "
                 "cada região em 1 pb (o '.bed' é o que liga o 0-based)")
_MSG_NOME_PAINEL = ("nome deve ser '<id>.txt' com id só de letras, dígitos, '_' e '-' (sem ponto): o "
                    "coordenadas_painel.py:69/77 nomeia as saídas pelo STEM (painel.adaptado.tsv geraria "
                    "painel.adaptado.bed, e a porta 'painel.bed' sairia como 'arquivo não gerado')")


def regra_de_nome(formato: str, nome: str, *, origem: bool = False) -> list[str]:
    """O que o NOME do arquivo muda na leitura do consumidor ([] = nada a reprovar).

    Separada do validador de conteúdo de propósito: o adaptador valida temporários
    '<nome>.adaptando.<hex>.tsv'. Roda onde o nome é definitivo — no contrato, sobre o
    `arquivo` de cada porta, e no registro da entrada, sobre o nome final. `origem=True` é o
    nome do arquivo de ORIGEM no modo caminho (crítica F5 #5b): na cópia o servidor escolhe o
    nome final ('<entrada><sufixo>'), e a regra só sobre ele nunca reprovava — um BED 1-based
    '.txt' virava 'regioes.bed' e passava a ser lido 0-based. Na origem vale só o SUFIXO
    (o stem da origem não chega ao script)."""
    # o htslib compara o sufixo SEM caixa ('.BED' também é lido 0-based — medido no bcftools
    # 1.19); na origem vale o mesmo, e a cópia sai sempre '<entrada>.bed'. O nome final segue
    # estrito: é a plataforma que o escolhe.
    if origem and formato == "bed_regioes":
        nome = nome.lower()
    if formato == "bed_regioes" and not nome.endswith(".bed"):
        return ([f"arquivo de origem: {_MSG_NOME_BED}; renomeie a origem só se ela já for 0-based"]
                if origem else [_MSG_NOME_BED])
    if formato == "painel_simbolos" and not origem:
        stem = nome[:-4] if nome.endswith(".txt") else None
        if stem is None or not _STEM_SEGURO.fullmatch(stem):
            return [_MSG_NOME_PAINEL]
    sufixos = SUFIXOS.get(formato)
    if sufixos and not nome.endswith(sufixos):
        quem = "arquivo de origem" if origem else "nome"
        return [f"{quem} não termina em {' nem '.join(sufixos)}: o consumidor escolhe o leitor (e procura o "
                "índice) pelo sufixo"]
    return []


def sufixo_de_destino(formato: str, nome_fonte: Optional[str]) -> str:
    """Sufixo da CÓPIA em raw/runs/<id>/: o da origem quando ela termina num de SUFIXOS[formato]
    (o mais longo primeiro: '.vcf.gz' antes de '.gz'); senão EXTENSOES (upload sem nome cai
    aqui). Um BCF copiado continua '.bcf' — renomeá-lo '.vcf.gz' reprovaria no conteúdo."""
    if nome_fonte:
        for s in sorted(SUFIXOS.get(formato, ()), key=len, reverse=True):
            if nome_fonte.endswith(s):
                return s
    return EXTENSOES.get(formato, ".dat")


def candidatos_irmaos(formato: str, nome: str) -> tuple[str, ...]:
    """Sufixos dos irmãos que o consumidor procura ao lado de `nome` (ex.: x.vcf.gz →
    ('.tbi', '.csi'); x.bcf → ('.csi',)); () = formato sem irmão ou sufixo desconhecido."""
    for sufixo, irmaos in sorted(IRMAOS.get(formato, {}).items(), key=lambda kv: len(kv[0]), reverse=True):
        if nome.endswith(sufixo):
            return irmaos
    return ()


def nome_de_referencia_ok(formato: str, nome: str) -> bool:
    """Portão BARATO do modo referência, antes de qualquer stat/sha (crítica F5 #1 e #3): só
    formato referenciável, com sufixo do formato, sem ser arquivo em construção
    ('*.building.*', que o gtonly_1000bg.sh grava e depois move por cima). Fora disso o run
    responde o mesmo 422 de um arquivo inexistente — sem revelar tamanho nem hash de um CSV,
    log ou cache que more na raiz de referência."""
    if formato not in REFERENCIAVEIS or ".building." in nome or regra_de_nome(formato, nome):
        return False
    return formato not in IRMAOS or bool(candidatos_irmaos(formato, nome))


def validar_com_nome(formato: str, caminho: Path, *, nome_origem: Optional[str] = None) -> Veredito:
    """O veredito de uma ENTRADA registrada: conteúdo (validar) + regra de nome do arquivo final
    + (modo caminho) regra de nome da origem, com o prefixo 'nome: '. É o que o registro grava."""
    v = validar(formato, caminho)
    extra = [f"nome: {x}" for x in regra_de_nome(formato, caminho.name)]
    if nome_origem is not None:
        extra += [f"nome: {x}" for x in regra_de_nome(formato, nome_origem, origem=True)]
    if not extra:
        return v
    return Veredito(v.formato, False, extra + v.falhas, v.perfil)


def valores_coluna(caminho: Path, coluna: str, limite: int = 50) -> Optional[list[str]]:
    """Valores DISTINTOS de uma coluna, na ordem em que aparecem e CRUS (sem aparar: o valor
    oferecido é o que o R vai ver) — é o que alimenta os dropdowns das escolhas do autor (só
    valores reais do dado). None = coluna inexistente, arquivo ilegível, ou mais de `limite`
    valores distintos (não é coluna de nível, é de dado) — e aí a leitura PARA no 51º valor,
    em vez de ler o arquivo inteiro a cada consulta. Linhas vazias não param a leitura no 51º
    valor: o teto de linhas físicas a limita (None ao passar dele)."""
    try:
        with _abrir(caminho, teto=(_MAX_GENES, "teto")) as t:
            if coluna not in t.cab:
                return None
            i = t.cab.index(coluna)
            vistos: list[str] = []
            conjunto: set[str] = set()
            for _, linha in t.linhas():
                v = linha[i] if i < len(linha) else ""
                if v and v not in conjunto:
                    conjunto.add(v)
                    vistos.append(v)
                    if len(vistos) > limite:
                        return None
            return vistos
    except (csv.Error, _Ilegivel, UnicodeDecodeError, OSError):
        return None


def _pasta_comum(caminho: Path) -> bool:
    """Diretório que não é symlink nem junção do NTFS (o is_symlink não vê junção)."""
    return not caminho.is_symlink() and caminho.is_dir() and not diretorio.eh_reparse(caminho)


def _tetos_da_pasta() -> tuple:
    """Os tetos entram na chave do cache: um monkeypatch nos testes (ou uma troca de constante)
    não pode receber o veredito calculado com o teto antigo."""
    return (diretorio.MAX_LINHAS_PASTA, diretorio.MAX_DESCOMPRIMIDO_PASTA, diretorio.MAX_RAZAO_GZIP,
            diretorio.RECUSA_HARDLINK, diretorio.MAX_MEMBROS, diretorio.MAX_VARRIDAS,
            _MAX_LINHAS_COV, _MAX_DESCOMPRIMIDO_COV, _MAX_CROMOSSOMOS)


@functools.lru_cache(maxsize=8)
def _validar_pasta_cache(formato: str, caminho: str, chave: tuple) -> Veredito:
    membros = [(nome, ino) for nome, _tam, _mt, _ct, ino in chave[3]]
    return VALIDADORES[formato](Path(caminho), membros)


def _validar_pasta(formato: str, caminho: Path) -> Veredito:
    """Cache pela identidade da pasta e de CADA membro (nome, tamanho, mtime, ctime, inode), no
    padrão do _ler_vcf. Sem ele a mesma pasta seria lida três vezes a cada registro (validação +
    duas checagens) e de novo em todo envelope de passo posterior. A validação lê exatamente os
    membros (nome, inode) da chave: o abrir_membro recusa o que trocou depois da listagem.
    Devolve uma CÓPIA (o veredito do cache é compartilhado). Resíduo declarado, como no
    _ler_vcf: no host Windows o ctime é a criação, e reescrever um membro no lugar com o mesmo
    tamanho e o mtime devolvido não invalida (no container, sim)."""
    regra = DIRETORIOS[formato]
    try:
        with diretorio.abrir_pasta(caminho) as p:
            listados = diretorio.listar_membros(p, regra)
            st = p.st
    except diretorio.ErroMembros as e:
        return Veredito(formato, False, [str(e)], {"teto": dict(e.limite)} if e.teto else {})
    chave = (formato, str(caminho.resolve()), (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_ctime_ns),
             tuple((n, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_ino) for n, s in listados),
             _tetos_da_pasta())
    return copy.deepcopy(_validar_pasta_cache(formato, str(caminho), chave))


def validar(formato: str, caminho: Path) -> Veredito:
    """Ponto único de entrada. Formato desconhecido, arquivo ausente, vazio ou ilegível = falha
    — um portão reprova, nunca levanta (a exceção virava 500 com a entrada já no disco).
    Formato de PASTA (DIRETORIOS): symlink, junção ou não-diretório reprova ("esperava uma
    pasta"); o resto vai ao validador de pasta, com cache. Para formato de arquivo nada muda:
    pasta continua reprovando."""
    if formato not in VALIDADORES:
        return Veredito(formato, False, [f"formato desconhecido: {formato!r} (conhecidos: {sorted(VALIDADORES)})"])
    try:
        if formato in DIRETORIOS:
            if not _pasta_comum(caminho):
                return Veredito(formato, False, [diretorio.MSG_NAO_PASTA])
            return _validar_pasta(formato, caminho)
        if not caminho.exists():
            return Veredito(formato, False, [f"arquivo ausente: {caminho.name}"])
        if caminho.is_dir() or caminho.stat().st_size == 0:
            return Veredito(formato, False, [f"arquivo vazio ou diretório: {caminho.name}"])
        return VALIDADORES[formato](caminho)
    except UnicodeDecodeError:
        return Veredito(formato, False, ["arquivo não é texto UTF-8"])
    except (csv.Error, _Ilegivel) as e:
        return Veredito(formato, False, [f"arquivo ilegível: {str(e)[:80]}"])
    except bgzf.Ilegivel as e:
        return Veredito(formato, False, [f"binário ilegível: {e}"])
    except EOFError:
        return Veredito(formato, False, ["gzip truncado: o arquivo acaba no meio de um membro"])
    except OSError as e:
        return Veredito(formato, False, [f"arquivo ilegível ({type(e).__name__})"])


# ------------------------------------------------------------ checagem cruzada
def contagens_e_coldata(contagens: Path, tabela_amostras: Path) -> Veredito:
    """As duas entradas do DESeq2 precisam conversar: o script escolhe as colunas da
    matriz PELOS NOMES do coldata — amostra do coldata ausente na matriz quebra o R; coluna
    da matriz ausente do coldata é ignorada em silêncio (declarada aqui). Nomes que o R leria
    como número já reprovaram nos dois validadores (seleção por posição)."""
    vc, vd = validar("contagens_genes", contagens), validar("coldata", tabela_amostras)
    falhas = [f"contagens: {f}" for f in vc.falhas] + [f"coldata: {f}" for f in vd.falhas]
    ignoradas: list[str] = []
    if vc.ok and vd.ok:
        colunas = set(vc.perfil["amostras"])
        faltam = [a for a in vd.perfil["amostras"] if a not in colunas]
        if faltam:
            falhas.append(f"{len(faltam)} amostra(s) do coldata ausente(s) da matriz: {_nomes(faltam)}")
        citadas = set(vd.perfil["amostras"])
        ignoradas = [a for a in vc.perfil["amostras"] if a not in citadas]
    return Veredito("contagens_e_coldata", not falhas, falhas,
                    {"colunas_ignoradas": ignoradas[:50], "n_colunas_ignoradas": len(ignoradas)})


# --------------------------------------------------------------- checagens de VCF (F5)
def _checagem(func: Callable[..., Veredito]) -> Callable[..., Veredito]:
    """Uma checagem REPROVA; nunca levanta (crítica F5 #6). O runs._checagens as chama cruas,
    fora do validar(), no meio do registro de uma entrada: uma exceção virava 500 depois da
    cópia e antes de gravar o run.json — e a cópia ficava órfã."""
    nome = func.__name__

    @functools.wraps(func)
    def envolto(*caminhos: Path) -> Veredito:
        try:
            return func(*caminhos)
        except UnicodeDecodeError:
            return Veredito(nome, False, ["arquivo não é texto UTF-8"])
        except (bgzf.Ilegivel, _Ilegivel) as e:
            return Veredito(nome, False, [f"leitura impossível: {e}"])
        except (EOFError, IndexError, ValueError, struct.error, zlib.error) as e:
            return Veredito(nome, False, [f"leitura impossível ({type(e).__name__})"])
        except OSError as e:
            return Veredito(nome, False, [f"arquivo ilegível ({type(e).__name__})"])
    return envolto


def _vcf_aprovado(vcf: Path, nome: str) -> Optional[Veredito]:
    """A checagem só fala de um VCF que o validador aprova: o resto reprova com as falhas dele
    (crítica F5 #5c — um .vcf.gz ilegível dava contigs [] e era APROVADO)."""
    v = validar("vcf_indexado", vcf)
    return None if v.ok else Veredito(nome, False, [f"vcf: {x}" for x in v.falhas])


_ARS_UCD = re.compile(r"(?:[1-9]|1[0-9]|2[0-9]|X|Y|M|MT|NKLS02\d{6}\.1)", re.ASCII)
_REFSEQ_GENBANK = re.compile(r"(?:NC|NW|NT)_\d+|CM\d{6}", re.ASCII)
_ANOTACAO_SEM_PAR = {"M": "o GTF/SnpEff/VEP usam 'MT'", "Y": "o GTF ARS-UCD1.3.113 não tem genes em Y"}


@_checagem
def vcf_tem_info_af(vcf: Path) -> Veredito:
    """O frequencias.py:43-44 roda 'bcftools query %INFO/AF', que aborta sem ##INFO=<ID=AF>."""
    ruim = _vcf_aprovado(vcf, "vcf_tem_info_af")
    if ruim:
        return ruim
    ok = "AF" in _ler_vcf(vcf).get("info", frozenset())
    return Veredito("vcf_tem_info_af", ok, [] if ok else [
        "cabeçalho sem ##INFO=<ID=AF>: o 'bcftools query %INFO/AF' do frequencias.py aborta"], {"tem_info_af": ok})


@_checagem
def vcf_contigs_ars_ucd(vcf: Path) -> Veredito:
    """Nomenclatura ARS-UCD (1..29, X, Y, M/MT, NKLS02*.1) nos contigs COM DADO (pelo índice que
    o htslib usa): 'chr14' ou RefSeq sai sem anotação nenhuma, com exit 0. M e Y passam com
    aviso (o GTF/SnpEff/VEP usam MT e não têm genes em Y)."""
    ruim = _vcf_aprovado(vcf, "vcf_contigs_ars_ucd")
    if ruim:
        return ruim
    r = _ler_vcf(vcf)
    dados = list(r.get("contigs_com_dados") or ())
    nomes = dados or [c for c, _, _ in r.get("contigs_cab", ())]
    falhas = []
    if not nomes:
        falhas.append("nenhum contig com dados no índice nem no cabeçalho")
    chr_ = [c for c in nomes if c.lower().startswith("chr")]
    refseq = [c for c in nomes if _REFSEQ_GENBANK.match(c)]
    outros = [c for c in nomes if not _ARS_UCD.fullmatch(c) and c not in chr_ and c not in refseq]
    if chr_:
        falhas.append(f"{len(chr_)} contig(s) com prefixo 'chr' (ex.: {_nome_seguro(chr_[0])}): o GTF, o SnpEff e "
                      "o VEP usam '14', e tudo sai sem anotação com exit 0")
    if refseq:
        falhas.append(f"{len(refseq)} contig(s) em nomenclatura RefSeq/GenBank (ex.: {_nome_seguro(refseq[0])})")
    if outros:
        falhas.append(f"{len(outros)} contig(s) fora da nomenclatura ARS-UCD (1..29, X, Y, M/MT, NKLS02*.1)")
    sem_par = [c for c in dados if c in _ANOTACAO_SEM_PAR]
    return Veredito("vcf_contigs_ars_ucd", not falhas, falhas, {
        "fonte": "indice" if dados else "cabecalho", "contigs": [_nome_seguro(c) for c in nomes[:50]],
        "contigs_sem_par_na_anotacao": sem_par, "avisos": [f"{c}: {_ANOTACAO_SEM_PAR[c]}" for c in sem_par]})


@_checagem
def vcf_e_bed(vcf: Path, bed: Path) -> Veredito:
    """Cruzada, ANTES de rodar o bcftools: contigs do BED existem no VCF (pega chr14 × 14 e
    MT × M); nenhuma região começa depois do comprimento declarado; e, pelos bins do índice
    que o htslib usa (o .csi quando há os dois), quais regiões certamente não têm registro.
    TODAS vazias = a extração devolveria 0 variantes — reprova. Só cabeçalho e índice: nunca
    conta por fluxo."""
    vb, vv = validar("bed_regioes", bed), validar("vcf_indexado", vcf)
    falhas = [f"bed: {x}" for x in vb.falhas] + [f"vcf: {x}" for x in vv.falhas]
    perfil: dict[str, Any] = {}
    if vb.ok and vv.ok:
        r = _ler_vcf(vcf)
        comp = {c: ln for c, ln, _ in r["contigs_cab"]}
        idx = r["_indice"]
        refs = {}
        for ref in idx["refs"]:
            nm = ref["nome"] if ref["nome"] is not None else r["por_tid"].get(ref["tid"])
            refs[nm] = ref
        conhecidos = set(comp) if comp else set(refs)
        ausentes: set[str] = set()
        alem = vazias = n = 0
        for crom, a, b in _regioes_bed(bed):
            n += 1
            if crom not in conhecidos:
                ausentes.add(crom)
                vazias += 1
                continue
            ln = comp.get(crom)
            if ln is not None and a >= ln:
                alem += 1
            ref = refs.get(crom)
            if ref is None or not ref["bins"]:
                vazias += 1
                continue
            bins = bgzf.reg2bins(a, b, idx["min_shift"], idx["depth"])
            if bins is not None and not (bins & ref["bins"]):
                vazias += 1
        aus = sorted(ausentes)
        if aus:
            falhas.append(f"{len(aus)} contig(s) do BED ausentes do cabeçalho do VCF (ex.: {_nome_seguro(aus[0])}): "
                          "nomenclatura divergente (chr14 × 14, MT × M)")
        if alem:
            falhas.append(f"{alem} região(ões) começam depois do fim do contig declarado no VCF")
        if n and vazias == n:
            falhas.append("pelo índice, NENHUMA região do BED tem registro no VCF: a extração devolveria 0 variantes")
        perfil = {"contigs_bed_ausentes_no_vcf": [_nome_seguro(x) for x in aus[:50]], "n_regioes": n,
                  "n_regioes_certamente_vazias": vazias, "n_regioes_alem_do_contig": alem}
    return Veredito("vcf_e_bed", not falhas, falhas, perfil)


# --------------------------------------------------------------- checagens da F6
def _pares_tabela(caminho: Path, coluna_a: str, coluna_b: str) -> list[tuple[str, str]]:
    """(a, b) de cada linha de uma tabela JÁ aprovada pelo validador (coldata, rotulos_amostras),
    na ordem do arquivo. O cabeçalho é aparado como no validador do coldata."""
    teto = (_MAX_AMOSTRAS, f"mais de {_MAX_AMOSTRAS} linhas (teto da leitura)")
    with _abrir(caminho, teto=teto) as t:
        ia, ib = t.cab.index(coluna_a), t.cab.index(coluna_b)
        return [(l[ia], l[ib]) for _, l in t.linhas() if len(l) == len(t.cab)]


@_checagem
def vcf_e_rotulos(vcf: Path, rotulos: Path) -> Veredito:
    """VCF/BCF × tabela de rótulos, ANTES de rodar o bcftools: o ml_dataset_genotipos descarta
    em silêncio a amostra rotulada que não está no VCF (ml_dataset_genotipos.py:60-63) e o n do
    resumo divergiria da tabela. Só a PRIMEIRA falha aplicável é reportada, nesta ordem (L19):
    (1) VCF sem amostras ou sem FORMAT/GT (o demo da genômica só tem sítios); (2) amostra
    rotulada ausente do cabeçalho. "Grupo com < 10 presentes" não dispara sozinha depois da (2)
    — o rotulos_amostras já exige ≥ 10 — e fica como assert interno."""
    falhas: list[str] = []
    ruim = _vcf_aprovado(vcf, "vcf_e_rotulos")
    if ruim:
        falhas += ruim.falhas
    vr = validar("rotulos_amostras", rotulos)
    falhas += [f"rotulos: {x}" for x in vr.falhas]
    if falhas:
        return Veredito("vcf_e_rotulos", False, falhas)
    r = _ler_vcf(vcf)
    no_vcf = set(r.get("amostras", ()))
    pares = _pares_tabela(rotulos, "amostra", "grupo")
    presentes = [(a, g) for a, g in pares if a in no_vcf]
    niveis = Counter(g for _, g in presentes)
    perfil = {"n_amostras_vcf": len(no_vcf), "n_rotuladas": len(pares),
              "n_vcf_sem_rotulo": len(no_vcf - {a for a, _ in pares}),
              "niveis_no_vcf": dict(sorted(niveis.items())[:50])}
    if not no_vcf or "GT" not in r.get("format", frozenset()):
        return Veredito("vcf_e_rotulos", False, [
            "VCF sem amostras com genótipo (FORMAT/GT): só sítios, nada para a matriz 0/1/2"], perfil)
    ausentes = [a for a, _ in pares if a not in no_vcf]
    if ausentes:
        return Veredito("vcf_e_rotulos", False, [
            f"{len(ausentes)} amostra(s) rotulada(s) ausente(s) do cabeçalho do VCF (ex.: {_nome_seguro(ausentes[0])}): "
            "o ml_dataset_genotipos as descartaria em silêncio e o n do resumo divergiria da tabela"], perfil)
    assert all(n >= _MIN_POR_NIVEL_ML for n in niveis.values()), "rotulos_amostras aprovou grupo com < 10"
    return Veredito("vcf_e_rotulos", True, [], perfil)


@_checagem
def coverages_e_amostras(coverages: Path, amostras: Path) -> Veredito:
    """Pasta de coverages × coldata (amostra → condição), ANTES do plano — o mesmo que o
    montar_coverages confere de novo no job. A `amostra` é o stem do arquivo por igualdade
    exata (decisão 7). Reprovam: dois arquivos da mesma amostra (x.cov e x.cov.gz); amostras
    que só diferem na caixa (crítica M10: no NTFS os nomes finais se sobrescrevem); amostra
    sem arquivo; nome final '<condicao>__<amostra>.bismark.cov.gz' fora de NOME_MEMBRO, longo
    demais ou repetido sem caixa; e a disjunção por SUBSTRING entre os nomes finais
    (metilacao_dmc.R:32-33, perfil_tss.py:76-77)."""
    vc, va = validar("coverages_bismark", coverages), validar("coldata", amostras)
    falhas = [f"coverages: {x}" for x in vc.falhas] + [f"amostras: {x}" for x in va.falhas]
    perfil: dict[str, Any] = {}
    if vc.ok and va.ok:
        nomes = [n for n, _ in diretorio.listar_membros(coverages, DIRETORIOS["coverages_bismark"])]
        por_stem: dict[str, str] = {}
        dobras: dict[str, str] = {}
        for nome in nomes:
            s = stem_coverage(nome)
            if s is None:
                continue
            if s in por_stem:
                falhas.append(f"dois arquivos da mesma amostra ({por_stem[s]} e {nome}): o montar não saberia qual "
                              "copiar")
                continue
            if s.casefold() in dobras:
                falhas.append(f"amostras que só diferem na caixa ({dobras[s.casefold()]} × {s}): no NTFS os nomes "
                              "finais se sobrescreveriam e o DMC rodaria com uma amostra a menos")
                continue
            por_stem[s] = nome
            dobras[s.casefold()] = s
        pares = _pares_tabela(amostras, "amostra", "condicao")
        do_coldata = {a for a, _ in pares}
        sem_arquivo = [a for a, _ in pares if a not in por_stem]
        if sem_arquivo:
            falhas.append(f"{len(sem_arquivo)} amostra(s) do coldata sem arquivo na pasta (ex.: "
                          f"{_nome_seguro(sem_arquivo[0])}): a amostra é o nome do arquivo sem .bismark.cov.gz/.cov.gz/"
                          ".bismark.cov/.cov, por igualdade exata")
        finais: dict[str, str] = {}
        dobras_finais: set[str] = set()
        for a, c in pares:
            if a not in por_stem:
                continue
            final = nome_por_condicao(c, a)
            if not diretorio.NOME_MEMBRO.fullmatch(final):
                falhas.append(f"nome final de {_nome_seguro(a)} fora do padrão de membro (até 128 caracteres; só "
                              "letras ASCII, dígitos, '_', '.', '-')")
                continue
            if final.casefold() in dobras_finais:
                falhas.append(f"nome final repetido sem distinção de caixa ({_nome_seguro(a)}): no NTFS um "
                              "sobrescreveria o outro")
                continue
            dobras_finais.add(final.casefold())
            finais[final] = c
        for nivel in sorted({c for _, c in pares}):
            alheios = sorted(n for n, c in finais.items() if nivel in n and c != nivel)
            if alheios:
                quem = (f"a condição {nivel}" if _NOME_ECOAVEL.fullmatch(nivel) else "uma condição")
                falhas.append(f"{quem} casa por SUBSTRING com {len(alheios)} arquivo(s) de outra condição: o "
                              "metilacao_dmc.R e o perfil_tss.py escolhem o grupo por substring do nome e juntariam "
                              "os grupos; renomeie a condição")
        sem_amostra = sorted(n for s, n in por_stem.items() if s not in do_coldata)
        perfil = {"n_amostras": len(finais), "por_condicao": dict(sorted(Counter(finais.values()).items())),
                  "n_arquivos_sem_amostra": len(sem_amostra), "arquivos_sem_amostra": sem_amostra[:20]}
    return Veredito("coverages_e_amostras", not falhas, falhas, perfil)


@_checagem
def coverages_e_painel(coverages: Path, painel: Path) -> Veredito:
    """Pasta de coverages × painel JSON: nenhum cromossomo em comum reprova (o caso típico é
    'chr1' × '1', em que a interseção daria 0 genes com código 0). Não conta CpG por janela: o
    perfil_tss já aborta sem CpG (perfil_tss.py:89-92)."""
    vc, vp = validar("coverages_bismark", coverages), validar("painel_genes_json", painel)
    falhas = [f"coverages: {x}" for x in vc.falhas] + [f"painel: {x}" for x in vp.falhas]
    perfil: dict[str, Any] = {}
    if vc.ok and vp.ok:
        cov = set(vc.perfil["cromossomos"])
        genes = _ler_painel_json(painel)["genes"]
        crom_genes = [str(g["cromossomo"]) for g in genes.values()]
        comuns = sorted(cov & set(crom_genes))
        ausentes = sum(c not in cov for c in crom_genes)
        if not comuns:
            falhas.append("nenhum cromossomo em comum entre os coverages e o painel (ex.: coverages 'chr1' × painel "
                          "'1'): a interseção e o TSS dariam 0 genes, com código 0")
        avisos = ([f"{ausentes} gene(s) do painel em cromossomo(s) sem coverage: ficam fora da interseção, do TSS e "
                   "das views"] if ausentes and comuns else [])
        perfil = {"cromossomos_comuns": [_nome_seguro(c) for c in comuns[:50]], "n_cromossomos_comuns": len(comuns),
                  "n_genes_em_cromossomo_ausente": ausentes, "avisos": avisos}
    return Veredito("coverages_e_painel", not falhas, falhas, perfil)


_SEM_GENES_HOMER = {"M": "mitocondrial: o preparse do HOMER não tem promotores ali",
                    "MT": "mitocondrial: o preparse do HOMER não tem promotores ali"}


@_checagem
def coverages_contigs_ars_ucd(coverages: Path) -> Veredito:
    """Portão de contig do HOMER (crítica M7), só no epigenomica_dmc: o genoma e o preparse do
    HOMER usam '1'…'29' (ARS-UCD, especies/bos_taurus.yaml `contigs: sem_chr`). Coverages com
    'chr' passariam pelas outras checagens (com um painel em 'chr'), rodariam cinco passos e o
    motifs pararia no último — o relatório exige todos os envelopes. Mesma regex do
    vcf_contigs_ars_ucd. M/MT passam com aviso."""
    v = validar("coverages_bismark", coverages)
    if not v.ok:
        return Veredito("coverages_contigs_ars_ucd", False, [f"coverages: {x}" for x in v.falhas])
    nomes = list(v.perfil.get("cromossomos") or ())
    chr_ = [c for c in nomes if c.lower().startswith("chr")]
    fora = [c for c in nomes if not _ARS_UCD.fullmatch(c) and c not in chr_]
    falhas = []
    if chr_:
        falhas.append(f"{len(chr_)} cromossomo(s) com prefixo 'chr' (ex.: {_nome_seguro(chr_[0])}): o genoma e o "
                      "preparse do HOMER usam '1'…'29'; o passo motifs não extrairia nenhuma sequência")
    if fora:
        falhas.append(f"{len(fora)} cromossomo(s) fora da nomenclatura ARS-UCD (1..29, X, Y, M/MT, NKLS02*.1; ex.: "
                      f"{_nome_seguro(fora[0])})")
    presentes = [c for c in nomes if c in _SEM_GENES_HOMER]
    return Veredito("coverages_contigs_ars_ucd", not falhas, falhas, {
        "cromossomos": [_nome_seguro(c) for c in nomes[:50]], "n_cromossomos": len(nomes),
        "avisos": [f"{c}: {_SEM_GENES_HOMER[c]}" for c in presentes]})


# tipo de checagem → (função, nomes dos campos que apontam para entradas, na ordem dos args)
CHECAGENS: dict[str, tuple[Callable[..., Veredito], tuple[str, ...]]] = {
    "contagens_e_coldata": (contagens_e_coldata, ("contagens", "coldata")),
    "vcf_tem_info_af": (vcf_tem_info_af, ("vcf",)),
    "vcf_contigs_ars_ucd": (vcf_contigs_ars_ucd, ("vcf",)),
    "vcf_e_bed": (vcf_e_bed, ("vcf", "bed")),
    # F6
    "vcf_e_rotulos": (vcf_e_rotulos, ("vcf", "rotulos")),
    "coverages_e_amostras": (coverages_e_amostras, ("coverages", "amostras")),
    "coverages_e_painel": (coverages_e_painel, ("coverages", "painel")),
    "coverages_contigs_ars_ucd": (coverages_contigs_ars_ucd, ("coverages",)),
}
# formato que cada campo de uma checagem exige da entrada apontada (validar_contrato confere:
# uma checagem com os campos trocados reprovaria todo run)
FORMATOS_CHECAGEM: dict[str, dict[str, str]] = {
    "contagens_e_coldata": {"contagens": "contagens_genes", "coldata": "coldata"},
    "vcf_tem_info_af": {"vcf": "vcf_indexado"},
    "vcf_contigs_ars_ucd": {"vcf": "vcf_indexado"},
    "vcf_e_bed": {"vcf": "vcf_indexado", "bed": "bed_regioes"},
    "vcf_e_rotulos": {"vcf": "vcf_indexado", "rotulos": "rotulos_amostras"},
    "coverages_e_amostras": {"coverages": "coverages_bismark", "amostras": "coldata"},
    "coverages_e_painel": {"coverages": "coverages_bismark", "painel": "painel_genes_json"},
    "coverages_contigs_ars_ucd": {"coverages": "coverages_bismark"},
}
