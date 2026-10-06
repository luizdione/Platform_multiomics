"""Motor de workflows: validação de encadeamento no manifest, cadeia executando
em ordem no fallback, e recusa clara quando exige filas especializadas."""
import time

import pytest
import yaml

from omica.jobs import queue
from omica.registry import hash_dos_passos, load_registry


def test_wf_painel_demo_valido_e_selado():
    reg = load_registry()
    gen = reg.por_id("genomica")
    wf = next(w for w in gen.workflows if w.id == "wf_painel_demo")
    assert wf.passos == ["extracao_demo", "frequencias_demo", "anotacao_demo"]
    # selo só existe porque rodou de verdade: tem data e o hash dos scripts bate com o
    # atual (a data em si muda a cada re-execução — 05/10/2026 recarimbou os selos)
    assert wf.testado_em and wf.hash_scripts == hash_dos_passos(gen, wf)


def test_workflow_sem_encadeamento_e_rejeitado(tmp_path):
    # O layout do fixture espelha o REAL (<raiz>/modules/<id>/scripts/...), porque
    # os cmd dos manifests são relativos à raiz do repo e agora a validação de
    # referências resolve por aí (antes ela era código morto e nada resolvia).
    modules = tmp_path / "modules"
    pasta = modules / "solto"
    (pasta / "scripts").mkdir(parents=True)
    (pasta / "scripts" / "a.py").write_text("", encoding="utf-8")
    manifesto = {
        "id": "solto", "nome_pt": "S", "nome_en": "S",
        "scripts": [
            {"key": "a", "titulo": "a", "runtime": "image:omica-worker",
             "cmd": "python3 modules/solto/scripts/a.py",
             "outputs": ["results/solto/a.json"]},
            {"key": "b", "titulo": "b", "runtime": "image:omica-worker",
             "cmd": "python3 modules/solto/scripts/a.py",
             "inputs": ["results/OUTRA/coisa.json"]},
        ],
        "workflows": [{"id": "w", "titulo": "w", "passos": ["a", "b"]}],
    }
    (pasta / "manifest.yaml").write_text(yaml.safe_dump(manifesto), encoding="utf-8")
    reg = load_registry(modules)
    assert "solto" in reg.erros
    assert "não consome nenhum output" in reg.erros["solto"]


def test_cadeia_fallback_roda_em_ordem(cfg):
    itens = [
        {"func_path": "omica.jobs.demo.executar", "prefixo": "wf-passo1", "mem_gb": 0.1},
        {"func_path": "omica.jobs.demo.executar", "prefixo": "wf-passo2", "mem_gb": 0.1},
    ]
    run_id, jobs, backend = queue.enqueue_cadeia(itens, wf_meta={"prefixo": "wf-teste"})
    assert backend == "thread" and len(jobs) == 2
    fim = time.time() + 30
    run = None
    while time.time() < fim:
        run = queue.ler_workflow_run(run_id)
        if run and run["status"] in ("done", "error"):
            break
        time.sleep(0.3)
    assert run is not None and run["status"] == "done", f"run: {run}"
    fins = [p["updated"] for p in run["passos"]]
    assert fins == sorted(fins)  # passo 2 nunca termina antes do 1


def test_cadeia_com_fila_especializada_sem_redis_recusa(cfg):
    itens = [
        {"func_path": "omica.jobs.demo.executar", "prefixo": "x", "fila": "bioinfo"},
        {"func_path": "omica.jobs.demo.executar", "prefixo": "y", "fila": "bioinfo"},
    ]
    with pytest.raises(queue.FilaIndisponivel):
        queue.enqueue_cadeia(itens, wf_meta={"prefixo": "wf-x"})
