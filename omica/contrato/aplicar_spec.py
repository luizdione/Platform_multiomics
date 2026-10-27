"""aplicar_spec.py — aplica um MAPEAMENTO de colunas a uma tabela, sem criar dado nenhum.

É a metade determinística do agente adaptador (F3): o LLM só PROPÕE operações sobre os
cabeçalhos reais; quem mexe no arquivo é este código, que só sabe:

  renomear   {origem, destino}  — troca o NOME de uma coluna (o conteúdo fica intacto)
  selecionar {colunas}          — mantém só essas colunas, nessa ordem (reordena também)
  transpor   {}                 — linhas viram colunas (amostras × genes → genes × amostras)
  copiar     {origem, destino}  — duplica uma coluna com outro nome, logo depois da origem.
                                  Só os PARES que quem chama autoriza (`copias_permitidas`):
                                  o caso é preencher gene_name com o gene_id quando a matriz
                                  não tem símbolo (o expressao_deseq2.R exige a coluna).
                                  Sem autorização, copiar é recusado — copiar uma coluna de
                                  amostra seria inventar uma réplica.

Leitura tolerante (BOM, separador vírgula/ponto e vírgula/tab, espaço em volta do nome da
coluna) e escrita sempre em TSV UTF-8 sem BOM — isso é normalização, também determinística.

Garantias, conferidas aqui e não só prometidas:
- ANCORAGEM: `origem` e `colunas` têm de ser cabeçalhos que existem NAQUELE ponto da
  sequência, exatamente e uma vez só. O que não ancora é erro — nunca é "corrigido".
- PROIBIÇÃO DE CRIAR: depois de aplicar, todo valor da saída (cabeçalho fora dos nomes
  novos e cada célula) tem de existir na entrada com multiplicidade suficiente. Se não,
  a aplicação é recusada (seria defeito deste arquivo, não do modelo). A única
  multiplicidade a mais admitida é a de um `copiar` autorizado, e exatamente a dele.

Só biblioteca padrão — a imagem web não tem pandas.
"""
from __future__ import annotations

import csv
import io
import re
from collections import Counter
from pathlib import Path
from typing import Any, Optional

_DELIMITADORES = "\t,;"
_NOME_COLUNA = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-]{0,79}", re.ASCII)
_MAX_BYTES = 256 * 1024 * 1024


class SpecInvalida(ValueError):
    """Operação que não ancora, ambígua ou que criaria dado."""


def ler_tabela(caminho: Path) -> tuple[str, list[str], list[list[str]], dict[str, Any]]:
    """(delimitador, cabeçalho, linhas, notas de normalização)."""
    if caminho.stat().st_size > _MAX_BYTES:
        raise SpecInvalida(f"tabela grande demais para adaptar ({caminho.stat().st_size} bytes)")
    bruto = caminho.read_bytes()
    notas: dict[str, Any] = {"bom": bruto.startswith(b"\xef\xbb\xbf")}
    try:
        texto = bruto.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise SpecInvalida("arquivo não é texto UTF-8") from None
    primeira = texto.split("\n", 1)[0]
    contagem = {d: primeira.count(d) for d in _DELIMITADORES}
    delim = max(contagem, key=lambda d: contagem[d])
    if contagem[delim] == 0:
        raise SpecInvalida("não achei separador (tab, vírgula ou ponto e vírgula) no cabeçalho")
    leitor = csv.reader(io.StringIO(texto, newline=""), delimiter=delim)
    # linha só com separadores (resíduo de exportação do Excel): o validador a reprova, porque
    # o R a lê como gene com NA; aqui ela sai, e a nota declara quantas saíram
    linhas: list[list[str]] = []
    descartadas = 0
    try:
        cab = next(leitor, None) or []
        for l in leitor:
            if any(c.strip() for c in l):
                linhas.append(l)
            elif l:  # [] é a linha realmente vazia, que o R também pula: não conta
                descartadas += 1
    except csv.Error as e:
        # campo > 131072 (aspas sem fechamento, ou um arquivo que não é tabela): recusa, não 500
        raise SpecInvalida(f"tabela ilegível ({str(e)[:80]})") from None
    notas["cabecalho_com_espaco"] = [c for c in cab if c != c.strip()]
    cab = [c.strip() for c in cab]
    notas["linhas_so_separadores_descartadas"] = descartadas
    notas["delimitador"] = {"\t": "tab", ",": "virgula", ";": "ponto_e_virgula"}[delim]
    return delim, cab, linhas, notas


def perfil_tabela(caminho: Path, n_amostra: int = 5, max_niveis: int = 20) -> dict[str, Any]:
    """O que o adaptador mostra ao modelo: NOMES reais e uma amostra curta — nunca a tabela
    inteira. Colunas com poucos valores distintos vêm com os valores (são níveis)."""
    _, cab, linhas, notas = ler_tabela(caminho)
    niveis: dict[str, list[str]] = {}
    tipos: dict[str, str] = {}
    for i, nome in enumerate(cab):
        valores = [l[i].strip() for l in linhas if i < len(l)]
        distintos = list(dict.fromkeys(v for v in valores if v))
        if 0 < len(distintos) <= max_niveis:
            niveis[nome] = distintos
        tipos[nome] = "numerica" if valores and all(_numerico(v) for v in valores[:200]) else "texto"
    return {
        **notas, "n_linhas": len(linhas), "n_colunas": len(cab), "cabecalho": cab,
        "tipos": tipos, "niveis": niveis,
        "amostra": [[c[:40] for c in l[:12]] for l in linhas[:n_amostra]],
    }


def _numerico(v: str) -> bool:
    try:
        float(v)
        return True
    except ValueError:
        return v in ("NA", "")


def _indice(cab: list[str], nome: Any, onde: str) -> int:
    if not isinstance(nome, str) or not nome:
        raise SpecInvalida(f"{onde}: nome de coluna ausente")
    n = cab.count(nome)
    if n == 0:
        raise SpecInvalida(f"{onde}: coluna {nome!r} não existe (cabeçalho atual: {cab[:15]})")
    if n > 1:
        raise SpecInvalida(f"{onde}: coluna {nome!r} aparece {n} vezes — ambígua")
    return cab.index(nome)


def simular_cabecalho(cab: list[str], ops: list[dict[str, Any]],
                      linhas_primeira_coluna: Optional[list[str]] = None) -> list[str]:
    """Aplica as operações SÓ ao cabeçalho, para conferir ancoragem antes de tocar no dado.
    `transpor` precisa da 1ª coluna (os nomes que viram cabeçalho)."""
    cab = list(cab)
    for i, op in enumerate(ops):
        onde = f"op {i + 1} ({op.get('op')})"
        tipo = op.get("op")
        if tipo == "renomear":
            j = _indice(cab, op.get("origem"), onde)
            destino = op.get("destino")
            if not isinstance(destino, str) or not _NOME_COLUNA.fullmatch(destino):
                raise SpecInvalida(f"{onde}: destino inválido {destino!r}")
            if destino in cab and cab.index(destino) != j:
                raise SpecInvalida(f"{onde}: destino {destino!r} já é outra coluna — ambíguo")
            cab[j] = destino
        elif tipo == "selecionar":
            colunas = op.get("colunas")
            if not isinstance(colunas, list) or not colunas:
                raise SpecInvalida(f"{onde}: lista de colunas vazia")
            if len(set(colunas)) != len(colunas):
                raise SpecInvalida(f"{onde}: coluna repetida na seleção")
            for c in colunas:
                _indice(cab, c, onde)
            cab = list(colunas)
        elif tipo == "copiar":
            j = _indice(cab, op.get("origem"), onde)
            destino = op.get("destino")
            if not isinstance(destino, str) or not _NOME_COLUNA.fullmatch(destino):
                raise SpecInvalida(f"{onde}: destino inválido {destino!r}")
            if destino in cab:
                raise SpecInvalida(f"{onde}: destino {destino!r} já existe — copiar não sobrescreve")
            cab.insert(j + 1, destino)
        elif tipo == "transpor":
            if linhas_primeira_coluna is None:
                raise SpecInvalida(f"{onde}: transpor exige os dados")
            cab = [cab[0]] + list(linhas_primeira_coluna)
            linhas_primeira_coluna = None  # depois de transpor, a 1ª coluna muda
        else:
            raise SpecInvalida(f"{onde}: operação desconhecida {tipo!r}")
        extras = set(op) - {"op", "origem", "destino", "colunas"}
        if extras:
            raise SpecInvalida(f"{onde}: campos desconhecidos {sorted(extras)}")
    return cab


def nada_criado(cab_antes: list[str], linhas_antes: list[list[str]], cab: list[str],
                linhas: list[list[str]], nomes_novos: list[str],
                copias: Optional[Counter] = None) -> None:
    """PROIBIÇÃO DE CRIAR: toda célula da saída e todo nome de coluna existia na entrada, com
    multiplicidade suficiente. Admitidos a mais: cada nome novo de `renomear`/`copiar` UMA vez
    (esteja no cabeçalho ou, depois de um `transpor`, numa célula) e os valores que um `copiar`
    autorizado duplicou — exatamente esses, nada além."""
    antes = (Counter(c for l in linhas_antes for c in l) + Counter(cab_antes)
             + Counter(nomes_novos) + (copias or Counter()))
    depois = Counter(c for l in linhas for c in l) + Counter(cab)
    sobra = depois - antes
    if sobra:
        raise SpecInvalida(f"a aplicação criaria valores inexistentes na entrada: {list(sobra)[:5]}")


def aplicar(entrada: Path, ops: list[dict[str, Any]], saida: Path,
            copias_permitidas: frozenset[tuple[str, str]] = frozenset()) -> dict[str, Any]:
    """Lê `entrada`, aplica `ops` em ordem e grava TSV em `saida`. Devolve o relatório.
    `copias_permitidas` = pares (origem, destino) que um `copiar` pode usar; vazio = nenhum."""
    _, cab, linhas, notas = ler_tabela(entrada)
    largura = len(cab)
    curtas = [i + 2 for i, l in enumerate(linhas) if len(l) != largura]
    if curtas:
        raise SpecInvalida(f"linhas com nº de campos diferente do cabeçalho: {curtas[:5]}")
    cab_orig, linhas_orig = list(cab), [list(l) for l in linhas]
    novos_nomes: list[str] = []
    copias: Counter = Counter()
    for i, op in enumerate(ops):
        onde = f"op {i + 1} ({op.get('op')})"
        if op.get("op") == "renomear":
            simular_cabecalho(cab, [op])  # ancoragem e ambiguidade
            cab[cab.index(op["origem"])] = op["destino"]
            novos_nomes.append(op["destino"])
        elif op.get("op") == "selecionar":
            simular_cabecalho(cab, [op])
            idx = [cab.index(c) for c in op["colunas"]]
            cab = [cab[j] for j in idx]
            linhas = [[l[j] for j in idx] for l in linhas]
        elif op.get("op") == "copiar":
            simular_cabecalho(cab, [op])  # ancoragem, nome e colisão
            if (op["origem"], op["destino"]) not in copias_permitidas:
                raise SpecInvalida(f"{onde}: copiar {op['origem']!r} → {op['destino']!r} não é permitido "
                                   f"(permitidos: {sorted(copias_permitidas)})")
            j = cab.index(op["origem"])
            cab.insert(j + 1, op["destino"])
            copias.update(l[j] for l in linhas)
            linhas = [l[:j + 1] + [l[j]] + l[j + 1:] for l in linhas]
            novos_nomes.append(op["destino"])
        elif op.get("op") == "transpor":
            simular_cabecalho(cab, [op], [l[0] for l in linhas])
            if len(set(l[0] for l in linhas)) != len(linhas):
                raise SpecInvalida(f"{onde}: 1ª coluna com nomes repetidos não vira cabeçalho")
            matriz = [cab] + linhas
            transposta = [list(col) for col in zip(*matriz)]
            cab, linhas = transposta[0], transposta[1:]
        else:
            simular_cabecalho(cab, [op])  # levanta com a mensagem certa

    nada_criado(cab_orig, linhas_orig, cab, linhas, novos_nomes, copias)
    saida.parent.mkdir(parents=True, exist_ok=True)
    with saida.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(cab)
        w.writerows(linhas)
    return {**notas, "ops_aplicadas": len(ops), "colunas_antes": cab_orig[:50],
            "colunas_depois": cab[:50], "n_linhas": len(linhas)}
