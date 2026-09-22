"""selos.py — o selo "testado de ponta a ponta" com lastro verificável.

Auditoria de 2026-09-14: `testado_em`/`testado_por` eram texto livre no manifest
e sobreviviam à reescrita do próprio script que certificam — um workflow podia
exibir ✅ e um número na vitrine do Professor enquanto o código que produziu
aquele número já não existia mais.

Agora cada workflow selado carrega `hash_scripts`, a impressão digital dos
scripts (mais `_comum.py` e os parâmetros do manifest) no dia do E2E. Este
módulo compara, e — só quando o autor mandar, depois de re-executar de verdade —
regrava.

Uso:
  python -m omica selos            # confere e lista os vencidos (sai 1 se houver)
  python -m omica selos --gravar   # recarimba com o estado ATUAL dos scripts
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from omica.registry import MODULES_DIR, hash_dos_passos, load_registry


def conferir(modules_dir: Path = MODULES_DIR) -> list[dict]:
    """Estado de cada workflow selado: id, hash gravado, hash atual, vencido."""
    estado = []
    for m in load_registry(modules_dir).modules:
        for wf in m.workflows:
            if not wf.testado_em:
                continue
            atual = hash_dos_passos(m, wf, modules_dir)
            estado.append({
                "modulo": m.id,
                "workflow": wf.id,
                "testado_em": wf.testado_em,
                "gravado": wf.hash_scripts or "(nenhum)",
                "atual": atual,
                "vencido": (not wf.hash_scripts) or wf.hash_scripts != atual,
            })
    return estado


def regravar(modules_dir: Path = MODULES_DIR) -> int:
    """Reescreve hash_scripts nos manifests com o estado ATUAL dos scripts.

    Edita o YAML como TEXTO (linha a linha) de propósito: um round-trip pelo
    yaml.safe_dump destruiria os comentários e os blocos `>-` que carregam a
    nota didática de cada workflow.
    """
    trocados = 0
    for m in load_registry(modules_dir).modules:
        selados = [wf for wf in m.workflows if wf.testado_em]
        if not selados:
            continue
        caminho = modules_dir / m.id / "manifest.yaml"
        linhas = caminho.read_text(encoding="utf-8").splitlines()
        fila = [hash_dos_passos(m, wf, modules_dir) for wf in selados]
        ids = [wf.id for wf in selados]
        saida, i = [], 0
        for linha in linhas:
            if re.match(r"^\s*hash_scripts:", linha) and i < len(fila):
                recuo = re.match(r"^\s*", linha).group(0)
                saida.append(f'{recuo}hash_scripts: "{fila[i]}"')
                print(f"  {m.id}/{ids[i]} → {fila[i]}")
                i += 1
                trocados += 1
                continue
            saida.append(linha)
            if re.match(r"^\s*testado_por:", linha) and i < len(fila) and not any(
                    re.match(r"^\s*hash_scripts:", x) for x in linhas):
                recuo = re.match(r"^\s*", linha).group(0)
                saida.append(f'{recuo}hash_scripts: "{fila[i]}"')
                print(f"  {m.id}/{ids[i]} → {fila[i]} (novo)")
                i += 1
                trocados += 1
        caminho.write_text("\n".join(saida) + "\n", encoding="utf-8")
    return trocados


def main(gravar: bool = False) -> int:
    if gravar:
        print("Regravando hash_scripts com o estado ATUAL dos scripts.")
        print("ATENÇÃO: o selo afirma que o workflow rodou de ponta a ponta. Só")
        print("recarimbe DEPOIS de re-executar de verdade — senão você está")
        print("apenas silenciando o aviso.\n")
        n = regravar()
        print(f"\n{n} selo(s) recarimbado(s).")
        return 0

    estado = conferir()
    if not estado:
        print("Nenhum workflow selado.")
        return 0
    largura = max(len(f"{e['modulo']}/{e['workflow']}") for e in estado) + 2
    vencidos = 0
    for e in estado:
        nome = f"{e['modulo']}/{e['workflow']}".ljust(largura)
        if e["vencido"]:
            vencidos += 1
            print(f"❌ {nome} vencido — testado em {e['testado_em']}: "
                  f"{e['gravado']} → {e['atual']}")
        else:
            print(f"✅ {nome} válido — {e['atual']} (testado em {e['testado_em']})")
    if vencidos:
        print(f"\n{vencidos} selo(s) vencido(s): os números publicados por eles são")
        print("de uma versão anterior dos scripts. Re-execute o workflow na")
        print("plataforma e então rode: python -m omica selos --gravar")
        return 1
    return 0
