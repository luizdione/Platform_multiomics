"""gerar_golden_v1.py — grava tests/golden/cmd_demo_v1.json (script, NÃO é teste).

Linha de base da v2 multiespécie (especificação §3 A0 e §9.2, invariante I2): o cmd que a
v1 monta para cada spec de `scripts:` e de `scripts_api` dos 4 manifests. Gerado UMA vez com
o código de `c483d9e`, ANTES de qualquer mudança da v2; depois disso o JSON é a régua, e
tests/test_linha_de_base_v2.py::test_golden_cmd_v1 confere que o cmd de `bos_taurus/demo`
continua igual byte a byte.

Receita (fixa — o teste reproduz exatamente estas condições):
  - OMICA_DATA=/data/omica_golden; GENOME_DATA numa pasta temporária; `_ler_dotenv`
    trocado por um stub: o .env real (que guarda o token da API) nunca é lido;
  - `scripts:`     → montar_cmd(spec);
  - `scripts_api`  → valores = {p.name: f"{DP}/raw/runs/{RUN}/arq.tsv"} para cada parâmetro
                     SEM default, saida = f"{DP}/results/{mod}/runs/{RUN}/p1";
  - sentinelas: str(cfg.data_dir) → <DADOS> só em `scripts:`; DP (as_posix) → <DP> só em
    `scripts_api`. Assim o JSON é o mesmo no host Windows (\\data\\omica_golden) e no Linux.
    O valor "omica_golden" não colide com os literais /data/omica/refs/... dos manifests.

Uso (da raiz do repositório):
  python tests/golden/gerar_golden_v1.py            # grava o JSON
  python tests/golden/gerar_golden_v1.py --conferir # só compara com o JSON gravado (exit 1 se divergir)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

SAIDA_JSON = Path(__file__).resolve().parent / "cmd_demo_v1.json"
OMICA_DATA_GOLDEN = "/data/omica_golden"
RUN = "transcriptomica_de-20261028-120000-abcdef"


def _isolar_ambiente(genome_data: str) -> None:
    """Mesmo isolamento do tests/conftest.py: nenhuma OMICA_* do shell e nada do .env."""
    for nome in [n for n in os.environ if n.upper().startswith("OMICA_")]:
        del os.environ[nome]
    os.environ["OMICA_DATA"] = OMICA_DATA_GOLDEN
    os.environ["GENOME_DATA"] = genome_data
    os.environ["OMICA_REDIS_URL"] = ""
    from omica import config as config_mod

    config_mod._ler_dotenv = lambda caminho: {}  # nunca lê o .env real
    config_mod.reset_config_cache()


def gerar() -> dict[str, str]:
    from omica.config import get_config
    from omica.jobs.dispatch import montar_cmd
    from omica.registry import load_registry

    cfg = get_config()
    dados = str(cfg.data_dir)
    dp = cfg.data_dir.as_posix()
    reg = load_registry()
    if reg.erros:
        raise SystemExit(f"manifest inválido, golden não gerado: {reg.erros}")
    def _ctx(spec, mod_id: str) -> dict:
        # Specs com `requer_refs` (v2) só montam com contexto; o demo é o da v1.
        # Import tardio: no c483d9e não há omica.contexto nem requer_refs.
        if not getattr(spec, "requer_refs", None):
            return {}
        from omica import contexto
        return {"ctx": contexto.demo(), "mod_id": mod_id}

    golden: dict[str, str] = {}
    for m in reg.modules:
        for spec in m.scripts:
            golden[f"{m.id}/scripts/{spec.key}"] = montar_cmd(spec, **_ctx(spec, m.id)).replace(dados, "<DADOS>")
        for spec in m.scripts_api:
            valores = {p.name: f"{dp}/raw/runs/{RUN}/arq.tsv" for p in spec.params if not p.default}
            saida = f"{dp}/results/{m.id}/runs/{RUN}/p1"
            golden[f"{m.id}/scripts_api/{spec.key}"] = montar_cmd(
                spec, valores, saida=saida, **_ctx(spec, m.id)).replace(dp, "<DP>")
    return golden


def main(argv: list[str]) -> int:
    with tempfile.TemporaryDirectory(prefix="omica_golden_") as tmp:
        _isolar_ambiente(tmp)
        golden = gerar()
    if "--conferir" in argv:
        gravado = json.loads(SAIDA_JSON.read_text(encoding="utf-8"))
        if gravado != golden:
            difs = sorted(k for k in set(gravado) | set(golden) if gravado.get(k) != golden.get(k))
            print(f"golden DIVERGE em {len(difs)} entrada(s): {difs}")
            return 1
        print(f"golden confere: {len(golden)} entradas")
        return 0
    SAIDA_JSON.write_text(json.dumps(golden, ensure_ascii=False, indent=1) + "\n",
                          encoding="utf-8", newline="\n")
    print(f"{len(golden)} entradas gravadas em {SAIDA_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
