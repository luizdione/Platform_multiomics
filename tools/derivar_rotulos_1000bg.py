"""derivar_rotulos_1000bg.py — gera a tabela de exemplo do pipeline ml_aims_vcf (roda no host).

Saída: modules/ml/data/rotulos_1000bg_racas.tsv (formato rotulos_amostras) e, ao lado,
rotulos_1000bg_racas.proveniencia.json. A tabela é DERIVADA, nunca digitada: vem da
tabela-verdade de raças do Run 8 que a espécie declara (especies/bos_taurus.yaml,
`recursos.rotulos_raca`), lida pelo mesmo caminho declarado que o resto da plataforma usa
(`especies.no_disco`).

Regra (ESPEC F6 §3.1.2 + adendo §A8.2):
  - raça = coluna `breed_biosample` (a raça DECLARADA no BioSample). Holstein fica com 187,
    não com os 301 da categoria reconciliada;
  - entram as raças com n ≥ 10 (o ml_dataset_genotipos exige 10 por grupo);
  - grupo = re.sub(r"[^A-Za-z0-9_.\\-]", "_", raça): o grupo vira argumento do script
    (N'Dama → N_Dama, Red Angus → Red_Angus). Colisão de nomes depois do saneamento, ou nível
    que formatos._problema_nivel recusa, ABORTA (nada é gravado);
  - colunas `amostra grupo breed_biosample`, ordenadas por (grupo, amostra), com LF. Sem
    `genetic_pole`, que é análise do autor (crítica L10);
  - a tabela pronta passa pelo validador real (`formatos.validar("rotulos_amostras")`) antes
    de ser gravada.

Um `--verdade` que caia num caminho de `nunca_usar` da espécie (rótulos antigos ou arquivados
como inválidos) aborta.

Uso (na raiz do repositório):
  python tools/derivar_rotulos_1000bg.py              # grava a tabela e a proveniência
  python tools/derivar_rotulos_1000bg.py --conferir   # só confere: o versionado bate?
"""
from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from omica import especies  # noqa: E402
from omica.contrato import formatos  # noqa: E402

ESPECIE = "bos_taurus"
RECURSO = "rotulos_raca"
FONTE = f"especies/{ESPECIE}.yaml#recursos.{RECURSO}"
SAIDA_PADRAO = REPO / "modules" / "ml" / "data" / "rotulos_1000bg_racas.tsv"
COLUNA_RACA = "breed_biosample"
COLUNA_AMOSTRA = "sample"
MINIMO = 10
CABECALHO = ("amostra", "grupo", "breed_biosample")
_FORA_DO_NOME = re.compile(r"[^A-Za-z0-9_.\-]")
REGRA = (f"raças com n ≥ {MINIMO} em {COLUNA_RACA} (vazio fica fora); grupo = "
         r"re.sub(r'[^A-Za-z0-9_.\-]', '_', raça); colisão de nomes ou nível recusado por "
         "formatos._problema_nivel aborta; linhas ordenadas por (grupo, amostra), LF; colunas "
         "amostra, grupo, breed_biosample (genetic_pole fica fora: é análise do autor)")


def _especie() -> especies.Especie:
    validas, erros = especies.carregar_especies()
    if ESPECIE not in validas:
        raise SystemExit(f"espécie {ESPECIE} inválida ou ausente: {erros.get(ESPECIE + '.yaml', '?')}")
    return validas[ESPECIE]


def declarada() -> tuple[str, Path]:
    """(caminho declarado em recursos.rotulos_raca, onde ele está aqui)."""
    valor = _especie().recursos.get(RECURSO)
    if not valor:
        raise SystemExit(f"{FONTE} não declarado")
    return valor, especies.no_disco(valor)


def conferir_permitida(verdade: Path) -> None:
    """Aborta se `verdade` é (ou está dentro de) um caminho de `nunca_usar` da espécie."""
    alvo = verdade.resolve()
    for valor in _especie().nunca_usar:
        proibido = especies.no_disco(valor).resolve()
        if alvo == proibido or proibido in alvo.parents:
            raise SystemExit(f"--verdade cai em nunca_usar da espécie {ESPECIE} ({valor}): "
                             f"rótulos antigos ou arquivados como inválidos; a fonte vigente é {FONTE}")


def grupo_de(raca: str) -> str:
    return _FORA_DO_NOME.sub("_", raca)


def derivar(verdade: Path) -> tuple[str, dict[str, int]]:
    """(texto da tabela, n por grupo). Aborta (SystemExit) em colisão, nível recusado, amostra
    repetida ou tabela-verdade sem as colunas."""
    with verdade.open(encoding="utf-8", newline="") as f:
        leitor = csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE)
        faltam = [c for c in (COLUNA_AMOSTRA, COLUNA_RACA) if c not in (leitor.fieldnames or [])]
        if faltam:
            raise SystemExit(f"tabela-verdade sem a(s) coluna(s) {faltam}")
        linhas = [(l[COLUNA_AMOSTRA], l[COLUNA_RACA]) for l in leitor]
    n_raca = Counter(r for _, r in linhas if r.strip())
    racas = sorted(r for r, n in n_raca.items() if n >= MINIMO)
    por_grupo: dict[str, str] = {}
    for raca in racas:
        g = grupo_de(raca)
        if g in por_grupo:
            raise SystemExit(f"colisão de nomes: {por_grupo[g]!r} e {raca!r} viram o mesmo grupo {g!r}")
        problema = formatos._problema_nivel(g)
        if problema:
            raise SystemExit(f"nível {g!r} (raça {raca!r}) recusado: {problema}")
        por_grupo[g] = raca
    grupo_da_raca = {r: g for g, r in por_grupo.items()}
    saida: list[tuple[str, str, str]] = []
    vistas: set[str] = set()
    for amostra, raca in linhas:
        if raca not in grupo_da_raca:
            continue
        if amostra in vistas:
            raise SystemExit(f"amostra repetida na tabela-verdade: {amostra!r}")
        vistas.add(amostra)
        saida.append((amostra, grupo_da_raca[raca], raca))
    saida.sort(key=lambda t: (t[1], t[0]))
    texto = "".join("\t".join(campos) + "\n" for campos in (CABECALHO, *saida))
    n = Counter(g for _, g, _ in saida)
    return texto, dict(sorted(n.items()))


def proveniencia(fonte: str, fonte_caminho: str, md5_fonte: str, texto: str,
                 n_por_grupo: dict[str, int], data: str) -> dict[str, Any]:
    return {
        "gerador": "tools/derivar_rotulos_1000bg.py",
        "fonte": fonte,
        "fonte_caminho": fonte_caminho,
        "fonte_md5": md5_fonte,
        "regra": REGRA,
        "n_amostras": sum(n_por_grupo.values()),
        "n_grupos": len(n_por_grupo),
        "n_por_grupo": n_por_grupo,
        "saida_sha256": hashlib.sha256(texto.encode("utf-8")).hexdigest(),
        "data": data,
    }


def _json(dados: dict[str, Any]) -> str:
    return json.dumps(dados, indent=2, ensure_ascii=False) + "\n"


def arquivo_proveniencia(saida: Path) -> Path:
    return saida.with_name(saida.name.removesuffix(".tsv") + ".proveniencia.json")


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Tabela de exemplo do ml_aims_vcf (raças do Run 8)")
    p.add_argument("--verdade", help=f"tabela-verdade (padrão: {FONTE})")
    p.add_argument("--saida", default=str(SAIDA_PADRAO))
    p.add_argument("--conferir", action="store_true",
                   help="não grava: confere se a tabela e a proveniência versionadas batem")
    args = p.parse_args(argv)

    valor, no_disco = declarada()
    if args.verdade:
        verdade = Path(args.verdade)
        eh_declarada = verdade.resolve() == no_disco.resolve()
        fonte, fonte_caminho = (FONTE, valor) if eh_declarada else ("argumento --verdade", args.verdade)
    else:
        verdade, fonte, fonte_caminho = no_disco, FONTE, valor
    conferir_permitida(verdade)
    if not verdade.is_file():
        raise SystemExit(f"tabela-verdade não encontrada: {verdade}")
    md5 = hashlib.md5(verdade.read_bytes()).hexdigest()
    texto, n = derivar(verdade)

    saida = Path(args.saida)
    prov_arq = arquivo_proveniencia(saida)
    if args.conferir:
        gravada = json.loads(prov_arq.read_text(encoding="utf-8")) if prov_arq.is_file() else {}
        esperada = proveniencia(fonte, fonte_caminho, md5, texto, n, gravada.get("data", ""))
        # o arquivo é gravado com LF; o checkout com autocrlf do Windows pode trocar por CRLF
        # (o validador e o ml_contraste leem os dois iguais). O saida_sha256 é do texto com LF.
        iguais = (saida.is_file() and saida.read_bytes().replace(b"\r\n", b"\n") == texto.encode("utf-8")
                  and gravada == esperada)
        print(("ok: " if iguais else "DIVERGE: ") + f"{saida.name} e {prov_arq.name} "
              f"{'batem com' if iguais else 'diferem de'} uma derivação nova ({len(n)} grupos, "
              f"{sum(n.values())} amostras)")
        return 0 if iguais else 1

    # o portão real antes de gravar: uma tabela que o validador reprova nunca vira exemplo
    tmp = saida.with_name(saida.stem + ".tmp" + saida.suffix)
    saida.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(texto.encode("utf-8"))
    try:
        v = formatos.validar("rotulos_amostras", tmp)
        if not v.ok:
            raise SystemExit(f"a tabela derivada reprova em rotulos_amostras: {v.falhas[:3]}")
        tmp.replace(saida)
    finally:
        tmp.unlink(missing_ok=True)
    hoje = datetime.date.today().isoformat()
    prov_arq.write_bytes(_json(proveniencia(fonte, fonte_caminho, md5, texto, n, hoje)).encode("utf-8"))
    print(f"{saida}: {sum(n.values())} amostras em {len(n)} grupos (fonte md5 {md5})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
