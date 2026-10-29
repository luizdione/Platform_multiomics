"""Linha de base da v2 multiespécie (especificação §3 A0 e §9; invariantes I1 e I2).

Escrito e verde NO `c483d9e` intocado, ANTES de qualquer mudança da v2: é a régua que prova
que as frentes seguintes não mexeram no que sustenta o demo selado.

  I1 — os 5 hashes de selo continuam os literais de hoje (o hash lê só `scripts:`,
       `_comum.py` e os bytes dos scripts; campo novo de manifest não pode entrar nele);
  I2 — o cmd que a v1 monta para `bos_taurus/demo` (21 specs de `scripts:` + 7 de
       `scripts_api`) sai igual byte a byte ao golden gravado por
       tests/golden/gerar_golden_v1.py, que roda com a MESMA receita destes testes.

Cada régua tem o seu canário: um selo que não morde uma edição de script e um golden que
não acusa um default trocado seriam decorativos.

Desde o passo B5 (frente B) o arquivo é da frente B: `painel_bed` e `snpeff` (scripts_api da
genômica) passaram a ler o GTF e o banco do SnpEff do CONTEXTO (`{ref_gtf}`,
`{ref_snpeff_db}`, `{ref_snpeff_datadir}`). O golden continua o MESMO JSON gravado no
`c483d9e`: os 28 cmds montados com o contexto do demo têm de sair iguais a ele; os 26 que não
pedem referência, também sem contexto; e os 2 que pedem, sem contexto, são recusados.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from fixtures_v2 import apontar_catalogo, escrever_subprojeto
from omica import contexto
from omica.config import REPO_ROOT, get_config, reset_config_cache
from omica.jobs import dispatch
from omica.registry import MODULES_DIR, hash_dos_passos, load_registry

# §9.1 — os literais dos manifests em c483d9e (genomica:286, epigenomica:167/180,
# transcriptomica:197, ml:123). Mudar um destes exige reselo (M6), fora desta etapa.
SELOS_V1 = {
    ("genomica", "wf_painel_demo"): "46124beee606ccc6",
    ("epigenomica", "wf_mofa_demo"): "89df0a07439b5f96",
    ("epigenomica", "wf_metilacao_demo"): "ef8e5355bc92acc7",
    ("transcriptomica", "wf_de_granulosa"): "ef51927233d11997",
    ("ml", "wf_ml_aims"): "9563a9c0e7dbc8de",
}

GOLDEN = Path(__file__).resolve().parent / "golden" / "cmd_demo_v1.json"
OMICA_DATA_GOLDEN = "/data/omica_golden"
RUN = "transcriptomica_de-20261028-120000-abcdef"

# §9.2 — recorte fixado também como literal no CÓDIGO: se alguém regravar o JSON com um cmd
# diferente (golden "atualizado" para fazer o teste passar), estes reprovam.
RECORTE_9_2 = {
    "transcriptomica/scripts/deseq2_demo":
        "Rscript modules/transcriptomica/scripts/expressao_deseq2.R --counts modules/transcriptomica/demo_data/"
        "counts_granulosa.tsv --coldata modules/transcriptomica/demo_data/coldata_granulosa.tsv --referencia "
        "winter_control --padj 0.05 --lfc 1 --saida <DADOS>/results/transcriptomica/demo/de",
    "transcriptomica/scripts/salmon_quant_demo":
        "python3 modules/transcriptomica/scripts/salmon_quant.py --fastq1 modules/transcriptomica/demo_data/"
        "SRR24953465_1.fastq.gz --fastq2 modules/transcriptomica/demo_data/SRR24953465_2.fastq.gz --indice "
        "/data/omica/refs/salmon_bostau --construir-indice --saida <DADOS>/results/transcriptomica/demo/quant",
    "genomica/scripts/coordenadas_painel":
        "python3 modules/genomica/scripts/coordenadas_painel.py --painel modules/genomica/data/paineis/"
        "termotolerancia.txt --pad 15000 --gtf /data/snpeff_data/ARS-UCD1.3.113/genes.gtf.gz --saida "
        "<DADOS>/results/genomica/paineis",
    "genomica/scripts/fst_racas":
        "python3 modules/genomica/scripts/fst_racas.py --vcf /data/1000bg_vcf/Chr14-Run8-TAUIND-public.bcf "
        "--regiao 14:1-2500000 --grupo-a Angus=/data/1000bg_metadata/samples_angus.txt --grupo-b "
        "Brahman=/data/1000bg_metadata/samples_brahman.txt --saida <DADOS>/results/genomica/fst",
    "epigenomica/scripts/dmc_demo":
        "Rscript modules/epigenomica/scripts/metilacao_dmc.R --dir modules/epigenomica/demo_data --grupo-a "
        "Nelore --grupo-b HF --min-cov 5 --diff 15 --qvalue 0.05 --saida <DADOS>/results/epigenomica/demo/dmc",
    "ml/scripts/ml_dataset_demo":
        "python3 modules/ml/scripts/ml_dataset_genotipos.py --vcf /data/1000bg_vcf/Chr14-Run8-TAUIND-public.bcf "
        "--regiao 14:1-2500000 --grupo-a Angus=/data/1000bg_metadata/samples_angus.txt --grupo-b "
        "Brahman=/data/1000bg_metadata/samples_brahman.txt --maf 0.2 --passo 15 --saida "
        "<DADOS>/results/ml/demo/dataset",
    "genomica/scripts_api/painel_bed":
        "python3 modules/genomica/scripts/coordenadas_painel.py --painel <DP>/raw/runs/<RUN>/arq.tsv --pad 15000 "
        "--gtf /data/snpeff_data/ARS-UCD1.3.113/genes.gtf.gz --saida <DP>/results/genomica/runs/<RUN>/p1",
    "genomica/scripts_api/snpeff":
        "python3 modules/genomica/scripts/anotacao_snpeff.py --entrada <DP>/raw/runs/<RUN>/arq.tsv --db "
        "ARS-UCD1.3.113 --datadir /data/snpeff_data --xmx 4g --saida <DP>/results/genomica/runs/<RUN>/p1",
    "transcriptomica/scripts_api/deseq2":
        "Rscript modules/transcriptomica/scripts/expressao_deseq2.R --counts <DP>/raw/runs/<RUN>/arq.tsv "
        "--coldata <DP>/raw/runs/<RUN>/arq.tsv --referencia <DP>/raw/runs/<RUN>/arq.tsv --padj 0.05 --lfc 1 "
        "--saida <DP>/results/transcriptomica/runs/<RUN>/p1",
}


# ------------------------------------------------------------------------------- I1: selos

def test_cinco_selos_literais():
    reg = load_registry()
    assert reg.erros == {}
    for (mod, wf_id), literal in SELOS_V1.items():
        m = reg.por_id(mod)
        assert m is not None, mod
        wf = next((w for w in m.workflows if w.id == wf_id), None)
        assert wf is not None, f"{mod}/{wf_id}"
        assert wf.hash_scripts == literal, f"{mod}/{wf_id}: linha hash_scripts editada"
        assert hash_dos_passos(m, wf) == literal, f"{mod}/{wf_id}: o hash dos passos mudou"


def _copiar_modules(destino: Path) -> Path:
    # demo_data não entra no hash nem no load (só scripts, _comum.py e manifests)
    shutil.copytree(MODULES_DIR, destino / "modules",
                    ignore=shutil.ignore_patterns("demo_data", "__pycache__"))
    return destino / "modules"


def test_canario_selo_morde(tmp_path):
    copia = _copiar_modules(tmp_path)
    m = load_registry(copia).por_id("genomica")
    wf = next(w for w in m.workflows if w.id == "wf_painel_demo")
    # controle: a cópia intocada dá o literal (senão o canário reprovaria por outro motivo)
    assert hash_dos_passos(m, wf, copia) == "46124beee606ccc6"
    with open(copia / "genomica" / "scripts" / "frequencias.py", "a", encoding="utf-8") as f:
        f.write("\n# x")
    assert hash_dos_passos(m, wf, copia) != "46124beee606ccc6"


# ------------------------------------------------------------------------------ I2: golden

@pytest.fixture()
def ambiente_golden(monkeypatch):
    """A receita do gerador: OMICA_DATA=/data/omica_golden (GENOME_DATA fica no tmp do conftest)."""
    monkeypatch.setenv("OMICA_DATA", OMICA_DATA_GOLDEN)
    reset_config_cache()
    cfg = get_config()
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    dados, dp = str(cfg.data_dir), cfg.data_dir.as_posix()
    esperado = {k: v.replace("<DADOS>", dados).replace("<DP>", dp) for k, v in golden.items()}
    yield esperado, dp
    reset_config_cache()


def _variantes(mod_id: str, spec, valores=None, saida=None) -> list[tuple[str, str]]:
    """O cmd com o contexto do demo e, se o spec não pede referência, também sem contexto."""
    cmds = [("ctx=demo", dispatch.montar_cmd(spec, valores, saida=saida, ctx=contexto.demo(), mod_id=mod_id))]
    if not spec.requer_refs:
        cmds.append(("ctx=None", dispatch.montar_cmd(spec, valores, saida=saida)))
    return cmds


def _os_28(dp: str):
    """(chave do golden, mod_id, spec, valores, saida) dos specs da v1, pela receita do gerador."""
    reg = load_registry()
    assert reg.erros == {}
    for m in reg.modules:
        for spec in m.scripts:
            yield f"{m.id}/scripts/{spec.key}", m.id, spec, None, None
        for spec in m.scripts_api:
            yield (f"{m.id}/scripts_api/{spec.key}", m.id, spec, _valores_api(spec, dp),
                   f"{dp}/results/{m.id}/runs/{RUN}/p1")


def _valores_api(spec, dp: str) -> dict[str, str]:
    """A receita do gerador: todo parâmetro SEM default recebe um caminho de raw/runs/<RUN>."""
    return {p.name: f"{dp}/raw/runs/{RUN}/arq.tsv" for p in spec.params if not p.default}


def test_golden_cmd_v1(ambiente_golden):
    """I2 com o contexto do demo: os 28 cmds iguais ao golden gravado no c483d9e."""
    esperado, dp = ambiente_golden
    assert len(esperado) == 28
    ctx = contexto.demo()
    conferidos = set()
    for chave, mod_id, spec, valores, saida in _os_28(dp):
        if chave not in esperado:
            continue  # spec nova depois da v1: fora do I2
        cmd = dispatch.montar_cmd(spec, valores, saida=saida, ctx=ctx, mod_id=mod_id)
        assert cmd == esperado[chave], chave
        conferidos.add(chave)
    # nenhuma das 28 sumiu (um spec renomeado ou apagado escaparia do I2 em silêncio)
    assert conferidos == set(esperado)


def test_golden_sem_contexto(ambiente_golden):
    """Os 26 que não pedem referência saem iguais também SEM contexto (a UI antiga e o
    Professor montam assim); os 2 que pedem (B5) recusam em vez de montar com um literal."""
    esperado, dp = ambiente_golden
    sem_ref, com_ref = set(), set()
    for chave, mod_id, spec, valores, saida in _os_28(dp):
        if chave not in esperado:
            continue
        if spec.requer_refs:
            with pytest.raises(ValueError, match="nenhum contexto foi dado"):
                dispatch.montar_cmd(spec, valores, saida=saida)
            com_ref.add(chave)
        else:
            assert dispatch.montar_cmd(spec, valores, saida=saida) == esperado[chave], chave
            sem_ref.add(chave)
    assert com_ref == {"genomica/scripts_api/painel_bed", "genomica/scripts_api/snpeff"}
    assert len(sem_ref) == 26


def test_golden_b5_le_a_referencia_do_contexto(ambiente_golden):
    """painel_bed/snpeff não têm mais o literal no cmd: o valor vem do contexto. Canário: um
    contexto com OUTRO GTF muda o cmd (o golden não está passando por causa de um literal)."""
    esperado, dp = ambiente_golden
    m = load_registry().por_id("genomica")
    painel = next(s for s in m.scripts_api if s.key == "painel_bed")
    snpeff = next(s for s in m.scripts_api if s.key == "snpeff")
    assert "{ref_gtf}" in painel.cmd and "/data/snpeff_data" not in painel.cmd
    assert "{ref_snpeff_db}" in snpeff.cmd and "ARS-UCD1.3.113" not in snpeff.cmd
    demo = contexto.demo()
    outro = contexto.Contexto(demo.especie, demo.slug, demo.assembly,
                              {**demo.valores, "ref_gtf": "/data/outro/genes.gtf.gz"}, {}, {}, "x")
    saida = f"{dp}/results/genomica/runs/{RUN}/p1"
    cmd = dispatch.montar_cmd(painel, _valores_api(painel, dp), saida=saida, ctx=outro, mod_id="genomica")
    assert cmd != esperado["genomica/scripts_api/painel_bed"] and "--gtf /data/outro/genes.gtf.gz" in cmd


def test_golden_fora_do_demo(ambiente_golden, tmp_path, monkeypatch):
    """§9.2, controle fora do demo: o mesmo deseq2 em homo_sapiens/teste_rna aceita SÓ a saída
    no layout do subprojeto, com o resto do argv idêntico."""
    esperado, dp = ambiente_golden
    proj = tmp_path / "projetos"
    shutil.copytree(REPO_ROOT / "projetos", proj)
    escrever_subprojeto(proj, "homo_sapiens", "teste_rna")
    apontar_catalogo(monkeypatch, REPO_ROOT / "especies", proj)
    ctx = contexto.resolver("homo_sapiens", "teste_rna")
    spec = next(s for s in load_registry().por_id("transcriptomica").scripts_api if s.key == "deseq2")
    valores = _valores_api(spec, dp)
    legado = f"{dp}/results/transcriptomica/runs/{RUN}/p1"
    novo = f"{dp}/results/projetos/homo_sapiens/teste_rna/transcriptomica/runs/{RUN}/p1"
    cmd = dispatch.montar_cmd(spec, valores, saida=novo, ctx=ctx, mod_id="transcriptomica")
    assert cmd == esperado["transcriptomica/scripts_api/deseq2"].replace(legado, novo)
    with pytest.raises(ValueError, match="saida precisa ser"):
        dispatch.montar_cmd(spec, valores, saida=legado, ctx=ctx, mod_id="transcriptomica")


def test_golden_recorte_literal_9_2():
    """O JSON gravado contém os 9 literais da §9.2 (régua fora do próprio JSON)."""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    for chave, literal in RECORTE_9_2.items():
        assert golden[chave] == literal.replace("<RUN>", RUN), chave


def test_golden_canario_default(ambiente_golden):
    esperado, dp = ambiente_golden
    m = load_registry().por_id("genomica")
    spec = next(s for s in m.scripts_api if s.key == "painel_bed")
    valores = _valores_api(spec, dp)
    saida = f"{dp}/results/genomica/runs/{RUN}/p1"
    # controle: o spec real bate com o golden (em todas as variantes de contexto disponíveis)
    for rotulo, cmd in _variantes("genomica", spec, valores, saida):
        assert cmd == esperado["genomica/scripts_api/painel_bed"], rotulo
    mutante = spec.model_copy(deep=True)
    pad = next(p for p in mutante.params if p.name == "pad")
    assert pad.default == "15000"
    pad.default = "15001"
    for rotulo, cmd in _variantes("genomica", mutante, valores, saida):
        assert cmd != esperado["genomica/scripts_api/painel_bed"], rotulo

