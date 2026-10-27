"""Relatório de um run da genômica (F5, especificação G1 §3–§4): pipeline SEM figura, fatos de
tabelas de contagem (efeitos do SnpEff, consequências do VEP), resultado em LISTA no resumo
(o gene do painel que o GTF não tem) e versões com dígito que só entram por fato.

O run é sintético e montado direto do YAML real do `genomica_painel` (resumos com os números do
E2E-2): o relatório não depende do registro de entradas por caminho, que é de outra frente.
`servico._pastas` é trocado por um que devolve a pasta, o run e o plano montados aqui.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from omica.pipelines import carregar_pipelines
from omica.relatorio import fatos as fatos_mod
from omica.relatorio import servico
from omica.relatorio.portao_numeros import NUMERO_SOLTO, conferir, conferir_fato, conferir_invariantes

EFEITOS = ("EFEITO\tN\nintergenic_region\t900\nintron_variant\t700\nupstream_gene_variant\t300\n"
           "missense_variant&splice_region_variant\t5\n5_prime_UTR_variant\t4\n")
EFEITOS_VEP = ("CONSEQUENCIA\tN\nintron_variant\t2100\ndownstream_gene_variant\t900\n"
               "5_prime_UTR_variant\t12\nmissense_variant\t9\n")
RUN = "genomica_painel-20261026-000000-abcdef"
DADOS = f"/data/omica/results/genomica/runs/{RUN}"
# números do E2E-2 (termotolerancia.txt × demo): G1 §5.2
RESUMOS = {
    "painel_bed": ("painel_resumo", {
        "gerado_em": "2026-10-26T00:00:00", "painel": "painel.txt",
        "fonte": "GTF local genes.gtf.gz (ARS-UCD1.3; autossomos = ARS-UCD1.2 dos VCFs Run 8)",
        "aviso_ensembl": "Ensembl REST atual serve ARS-UCD2.0 — NÃO usar para estes VCFs",
        "pad_pb": 15000, "n_simbolos": 14, "n_localizados": 13, "nao_encontrados": ["HSPA1A"],
        "bed": f"{DADOS}/painel_bed/painel.bed"}),
    "extracao": ("extracao_resumo", {
        "gerado_em": "x", "entrada": "vcf.vcf.gz", "painel_bed": "painel.bed", "n_regioes": 13,
        "n_variantes_extraidas": 2365, "vcf_extraido": f"{DADOS}/extracao/extraido.vcf.gz"}),
    "frequencias": ("frequencias_resumo", {
        "gerado_em": "x", "entrada": "extraido.vcf.gz", "n_variantes_com_af": 2287, "n_sem_af": 78,
        "n_multialelicos": 130, "n_alelos_spanning_descartados": 307,
        "definicao_maf": "frequência do 2º alelo mais comum entre REF(1-ΣAF) e ALT1..ALTn",
        "maf_corte": 0.05, "n_maf_acima_do_corte": 166, "maf_media": 0.03112,
        "tabela_tsv": f"{DADOS}/frequencias/frequencias.tsv"}),
    "snpeff": ("anotacao_resumo", {
        "gerado_em": "x", "entrada": "extraido.vcf.gz", "banco": "ARS-UCD1.3.113", "n_variantes_anotadas": 2365,
        "n_efeitos_distintos": 20, "efeito_mais_comum": "intergenic_region",
        "vcf_anotado": f"{DADOS}/snpeff/anotado.vcf.gz", "tabela_tsv": f"{DADOS}/snpeff/efeitos.tsv"}),
    "vep": ("vep_resumo", {
        "gerado_em": "x", "entrada": "extraido.vcf.gz",
        "release": "110 (ARS-UCD1.2 — pinado de propósito; ver docstring)", "n_variantes_anotadas": 2287,
        "n_consequencias_distintas": 18, "consequencia_mais_comum": "intron_variant",
        "tabela_vep": f"{DADOS}/vep/vep.tsv", "tabela_efeitos": f"{DADOS}/vep/efeitos_vep.tsv"}),
}
SEM_SELO = {"painel_bed", "vep"}  # G1 §3.2: só extracao/frequencias/snpeff têm o selo do wf_painel_demo


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _montar(tmp_path: Path, mexer=None) -> tuple[Path, dict, dict]:
    """Pasta do run (envelopes) + plano com o `relatorio` do YAML real. `mexer(resumos)` altera
    uma cópia dos resumos antes de gravar."""
    p = carregar_pipelines()[0]["genomica_painel"]
    resumos = json.loads(json.dumps(RESUMOS))
    if mexer:
        mexer(resumos)
    pasta = tmp_path / "run"
    (pasta / "envelopes").mkdir(parents=True)
    saida = tmp_path / "saidas"
    portas: dict[str, dict] = {}
    for passo, (porta, dados) in resumos.items():
        arq = saida / passo / f"{porta}.json"
        arq.parent.mkdir(parents=True, exist_ok=True)
        arq.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
        portas.setdefault(passo, {})[porta] = arq
    for passo, porta, texto in (("snpeff", "efeitos", EFEITOS), ("vep", "efeitos_vep", EFEITOS_VEP)):
        arq = saida / passo / f"{porta}.tsv"
        arq.write_text(texto, encoding="utf-8")
        portas[passo][porta] = arq
    for passo, ps in portas.items():
        env = {"portas": {k: {"caminho": v.as_posix(), "sha256": _sha(v)} for k, v in ps.items()}}
        (pasta / "envelopes" / f"{passo}.json").write_text(json.dumps(env), encoding="utf-8")
    plano = {"run_id": RUN, "plano_sha256": "f" * 64, "pipeline": p.id, "modulo": p.modulo, "escolhas": {},
             "passos": [{"id": x.id, "titulo": f"passo {x.id}",
                         "selos": [] if x.id in SEM_SELO else [{"workflow": "wf_painel_demo", "vigente": True}]}
                        for x in p.passos],
             "relatorio": p.relatorio.model_dump()}
    return pasta, {"run_id": RUN, "estado": "concluido"}, plano


@pytest.fixture()
def run_genomica(tmp_path, monkeypatch, cfg):
    pasta, run, plano = _montar(tmp_path)
    monkeypatch.setattr(servico, "_pastas", lambda run_id: (pasta, run, plano))
    return pasta, plano


def _ficha(tmp_path: Path, mexer=None) -> tuple[dict, dict]:
    pasta, _, plano = _montar(tmp_path, mexer)
    return plano, fatos_mod.gerar(plano, servico._envelopes(pasta, plano))


BOM = {"secoes": [
    {"id": "resumo", "titulo": "Resumo",
     "paragrafos": ["Dos {{f:painel_bed.painel_resumo.n_simbolos}} genes do painel, "
                    "{{f:painel_bed.painel_resumo.n_localizados}} foram localizados no GTF "
                    "({{f:painel_bed.painel_resumo.fonte}}); {{f:painel_bed.painel_resumo.nao_encontrados.0}} "
                    "não foi achado e não terá variantes."]},
    {"id": "resultados", "titulo": "Resultados",
     "paragrafos": ["Nas janelas de BTA14 há {{f:extracao.extracao_resumo.n_variantes_extraidas}} variantes; "
                    "o efeito mais frequente no SnpEff ({{f:snpeff.anotacao_resumo.banco}}) foi "
                    "{{f:snpeff.efeitos.top1.EFEITO}} ({{f:snpeff.efeitos.top1.N}} variantes), e há "
                    "{{f:snpeff.efeitos.top4.N}} variantes com {{f:snpeff.efeitos.top4.EFEITO}}.",
                    "No VEP ({{f:vep.vep_resumo.release}}), a consequência mais anotada foi "
                    "{{f:vep.efeitos_vep.top1.CONSEQUENCIA}} ({{f:vep.efeitos_vep.top1.N}} anotações)."]}],
    "legendas": []}


# ---------------------------------------------------------------- ficha
def test_ficha_da_genomica_sem_figura_com_tabelas_e_lista(tmp_path):
    _, ficha = _ficha(tmp_path)
    por_id = {f["id"]: f for f in ficha["fatos"]}
    assert ficha["figuras"] == []
    # tabelas de contagem: chave textual, N inteiro, na ordem do arquivo (N decrescente)
    assert por_id["snpeff.efeitos.top1.EFEITO"]["valor"] == "intergenic_region"
    assert por_id["snpeff.efeitos.top1.N"]["valor"] == 900 and por_id["snpeff.efeitos.top1.N"]["tipo"] == "numero"
    assert por_id["snpeff.efeitos.top4.EFEITO"]["valor"] == "missense_variant&splice_region_variant"
    assert por_id["vep.efeitos_vep.top3.CONSEQUENCIA"]["valor"] == "5_prime_UTR_variant"
    assert por_id["vep.efeitos_vep.top1.N"]["fonte"] == {
        **por_id["vep.efeitos_vep.top1.N"]["fonte"], "tipo": "celula", "chave_coluna": "CONSEQUENCIA",
        "chave": "intron_variant", "coluna": "N", "bruto": "2100"}
    # o resultado em lista vira fato, com o índice real e o rótulo dizendo de quantos
    lista = por_id["painel_bed.painel_resumo.nao_encontrados.0"]
    assert lista["valor"] == "HSPA1A" and lista["fonte"]["campo"] == "nao_encontrados.0"
    assert lista["rotulo"] == "nao_encontrados: item 1 de 1"
    # versões só como texto de fato; caminhos e carimbo de hora fora
    assert por_id["snpeff.anotacao_resumo.banco"]["valor"] == "ARS-UCD1.3.113"
    assert por_id["vep.vep_resumo.release"]["tipo"] == "texto"
    assert not [i for i in por_id if i.endswith((".bed", ".vcf_extraido", ".tabela_tsv", ".gerado_em",
                                                  ".vcf_anotado", ".tabela_vep", ".tabela_efeitos"))]
    assert ficha["selos"] == {"painel_bed": "sem_selo", "extracao": "vigente", "frequencias": "vigente",
                              "snpeff": "vigente", "vep": "sem_selo"}
    assert conferir_invariantes(ficha) == []  # controle com os números do E2E-2


def test_lista_vazia_nao_vira_fato_e_teto_de_itens(tmp_path):
    """Lista vazia = nenhum fato (não há o que citar); lista longa = só os primeiros
    MAX_ITENS_LISTA, com o índice REAL mesmo quando um item do meio não é valor simples."""
    def mexer(r):
        r["painel_bed"][1]["nao_encontrados"] = []
        r["extracao"][1]["lista"] = ["A", None, {"x": 1}, True, "B"] + [f"G{i}" for i in range(100)]
    _, ficha = _ficha(tmp_path, mexer)
    ids = {f["id"]: f["valor"] for f in ficha["fatos"]}
    assert not [i for i in ids if ".nao_encontrados." in i]
    da_lista = {i: v for i, v in ids.items() if i.startswith("extracao.extracao_resumo.lista.")}
    assert da_lista["extracao.extracao_resumo.lista.0"] == "A"
    assert da_lista["extracao.extracao_resumo.lista.4"] == "B"  # não .1: o índice é o da fonte
    assert len(da_lista) == fatos_mod.MAX_ITENS_LISTA - 3  # None, objeto e booleano ficam fora
    assert max(int(i.rsplit(".", 1)[1]) for i in da_lista) == fatos_mod.MAX_ITENS_LISTA - 1


# ------------------------------------------------- item de lista no portão (R3)
def test_item_de_lista_e_conferido_na_fonte(tmp_path):
    plano, ficha = _ficha(tmp_path)
    fato = next(f for f in ficha["fatos"] if f["id"] == "painel_bed.painel_resumo.nao_encontrados.0")
    assert conferir_fato(fato, plano) is None  # controle
    # canário: a ficha diz outro gene que o arquivo não tem
    assert "difere" in conferir_fato({**fato, "valor": "DGAT1"}, plano)
    # canários de caminho forjado: fora da lista, não numérico, dígito não ASCII — "ausente",
    # nunca IndexError/ValueError (que escapariam do R3 como 500)
    for campo in ("nao_encontrados.1", "nao_encontrados.x", "nao_encontrados.²", "nao_encontrados.-1",
                  "nao_encontrados.0.0", "n_simbolos.0"):
        motivo = conferir_fato({**fato, "fonte": {**fato["fonte"], "campo": campo}}, plano)
        assert motivo and "ausente" in motivo, (campo, motivo)


# ------------------------------------------------------------ pedido
def test_pedido_sem_figura_avisa_o_redator(run_genomica):
    p = servico.pedido(RUN)
    assert p["n_figuras"] == 0
    assert "FIGURAS: nenhuma neste run" in p["mensagem"] and "FIGURAS (cite" not in p["mensagem"]
    assert "painel_bed.painel_resumo.nao_encontrados.0 = HSPA1A" in p["mensagem"]
    assert "snpeff.efeitos.top1.N = 900" in p["mensagem"]
    assert "vep: passo vep (selo do código: sem_selo)" in p["mensagem"]
    # o prompt que vai junto já fala de pipeline sem figura e do N do VEP
    assert "Se a lista de figuras vier vazia" in p["sistema"] and "Nunca chame um N do VEP" in p["sistema"]


def test_invariante_da_genomica_reprovado_bloqueia_o_pedido(tmp_path, monkeypatch, cfg):
    """Pela rota inteira do serviço (o canário por invariante, um a um, está em test_contrato):
    13 regiões extraídas contra 12 genes localizados não vira texto."""
    pasta, run, plano = _montar(tmp_path, lambda r: r["painel_bed"][1].update(n_localizados=12))
    monkeypatch.setattr(servico, "_pastas", lambda run_id: (pasta, run, plano))
    with pytest.raises(servico.ErroRelatorio) as e:
        servico.pedido(RUN)
    assert e.value.erro == "invariantes_reprovados"
    assert e.value.detalhe["falhas"][0].startswith("invariante 1 (igual)")


# ------------------------------------------------------------ rascunho
def test_rascunho_sem_figura_vira_relatorio(run_genomica, cfg):
    servico.pedido(RUN)
    r = servico.receber(RUN, BOM, modelo="claude-opus-5-5", stop_reason="end_turn")
    assert r["ok"], r
    # fatos do painel_bed e do vep saem marcados (código sem selo vigente)
    assert set(r["marcados_sem_selo"]) == {
        "painel_bed.painel_resumo.n_simbolos", "painel_bed.painel_resumo.n_localizados",
        "painel_bed.painel_resumo.fonte", "painel_bed.painel_resumo.nao_encontrados.0",
        "vep.vep_resumo.release", "vep.efeitos_vep.top1.CONSEQUENCIA", "vep.efeitos_vep.top1.N"}
    pasta = cfg.results_dir / "genomica" / "runs" / RUN / "relatorio"
    html = (pasta / "relatorio.html").read_text(encoding="utf-8")
    assert "HSPA1A" in html and "ARS-UCD1.3.113" in html and "2.365" in html
    assert "missense_variant&amp;splice_region_variant" in html
    assert "<figure" not in html and list((pasta / "figuras").iterdir()) == []
    prov = json.loads((pasta / "proveniencia.json").read_text(encoding="utf-8"))["numeros"]
    onde = {x["fato"]: x["onde"] for x in prov}
    assert onde["snpeff.efeitos.top1.N"] == "linha EFEITO=intergenic_region, coluna N"
    assert onde["painel_bed.painel_resumo.nao_encontrados.0"] == "campo nao_encontrados.0"


@pytest.mark.parametrize("trecho, solto", [
    ("o banco ARS-UCD1.3.113 do SnpEff", "3.113"),
    ("a montagem ARS-UCD1.2", "2"),
    ("o cache 110 do VEP", "110"),
    ("as variantes do cromossomo 14", "14"),
    ("o painel do Run 8", "8"),
    ("a região 5' UTR", "5"),
])
def test_versao_e_cromossomo_escritos_a_mao_reprovam(run_genomica, trecho, solto):
    """O que a regra nova do prompt proíbe é o que o portão reprova (G1 §4.7): cada exemplo
    tem um dígito solto. Controle no teste seguinte."""
    servico.pedido(RUN)
    frase = "Foram {{f:extracao.extracao_resumo.n_variantes_extraidas}} variantes em " + trecho + "."
    ruim = {"secoes": [{"id": "resumo", "titulo": "Resumo", "paragrafos": [frase]}], "legendas": []}
    r = servico.receber(RUN, ruim, stop_reason="end_turn")
    assert not r["ok"] and {f["regra"] for f in r["falhas"]} == {"R1"}
    assert any(repr(solto) in f["detalhe"] for f in r["falhas"]), r["falhas"]


def test_nomes_com_digito_que_nao_sao_numero_passam():
    """Controle do anterior: BTA14, DESeq2, HSPA1A e os termos de efeito com dígito colado
    a '_' não casam NUMERO_SOLTO — o redator pode escrevê-los."""
    for nome in ("BTA14", "DESeq2", "HSPA1A", "5_prime_UTR_variant", "3_prime_UTR_variant", "log2FC"):
        assert not NUMERO_SOLTO.search(nome), nome


def test_portao_inteiro_sobre_a_ficha_da_genomica(tmp_path):
    """O BOM passa no portão também fora do serviço (controle); trocar a célula de N no
    arquivo depois do envelope reprova por R3 (sha) — a tabela de contagem é reaberta."""
    plano, ficha = _ficha(tmp_path)
    assert conferir(BOM, ficha, plano)["ok"]
    arq = Path(next(f for f in ficha["fatos"] if f["id"] == "snpeff.efeitos.top1.N")["fonte"]["arquivo"])
    arq.write_text(EFEITOS.replace("intergenic_region\t900", "intergenic_region\t901"), encoding="utf-8")
    falhas = conferir(BOM, ficha, plano)["falhas"]
    assert {(f["regra"], f["onde"]) for f in falhas} >= {("R3", "snpeff.efeitos.top1.N")}
