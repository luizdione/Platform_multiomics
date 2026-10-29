"""test_importador.py — importador de subprojetos legados (v2 multiespécie, M5; ESPEC §4 D2).

Catálogo SINTÉTICO em tmp e árvore falsa em tmp GENOME_DATA (nada de E:\\data, nada no
repositório). Cada regra tem controle (passa no caso bom) e canário (reprova no defeito
plantado). Cobre também a crítica v2: bloqueante 2 (o formato REAL da proposta é mapeado para o
modelo; título/pergunta só do autor ou MARCADOR_AUTOR + draft), médio 9 (--json com a mesma
recusa do --saida) e médio 12 (legado com raça lida do nome não fica `valido` sem proveniência).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

import pytest
import yaml
from pydantic import ValidationError

from fixtures_v2 import apontar_catalogo, escrever_especie, escrever_subprojeto
from omica import importador
from omica.__main__ import main as cli
from omica.config import REPO_ROOT, reset_config_cache
from omica.importador import MARCADOR_AUTOR, EntradaCatalogo, gerar_amostras_sub03, mapear_proposta
from omica.subprojetos import Subprojeto, carregar_subprojetos, validar_contra_especie

# distribuição REAL do sub03 (levantamento §3): 34 = 8 + 6 + 6 + 8 + 6
DISTRIB = {
    "Holstein_hypothalamus_heat_stress": 3, "Holstein_hypothalamus_control": 3,
    "Holstein_pituitary_heat_stress": 3, "Holstein_pituitary_control": 3,
    "Angus_granulosa_summer_heat": 4, "Angus_granulosa_winter_control": 4,
    "Crossbred_PBMC_baseline_d0": 2, "Crossbred_PBMC_heat_stress_d7": 2,
    "Tharparkar_PBMC_baseline_d0": 2, "Tharparkar_PBMC_heat_stress_d7": 2,
    "Brangus_whole_blood_tropical_heat": 2, "Holstein_F_whole_blood_tropical_heat": 2,
    "Kedah_Kelantan_whole_blood_tropical_heat": 2,
}
PASTA = {"hypothalamus": "PRJNA885909_hypothalamus", "pituitary": "PRJNA885909_pituitary",
         "granulosa": "GSE235171_granulosa", "PBMC": "PRJNA562925_PBMC", "whole_blood": "PRJNA1424279_whole_blood"}
ESPERADO = {"total": 34,
            "por_tecido": {"granulosa": 8, "hipotalamo": 6, "hipofise": 6, "PBMC": 8, "sangue_total": 6},
            "por_raca": {"Holstein": 14, "Angus": 8, "Crossbred": 4, "Tharparkar": 4, "Brangus": 2,
                         "Kedah-Kelantan": 2}}
PLANILHA = "/data/rnaseq/samplesheets/sub03_samplesheet_nfcore.csv"
MATRIZ = "/data/rnaseq/nfcore_output/sub03/salmon/salmon.merged.gene_counts.tsv"


# ------------------------------------------------------------------------- montagem

def _pasta_de(prefixo: str) -> str:
    return next(v for k, v in PASTA.items() if k in prefixo)


def _tocar(p: Path, conteudo: str = "x") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(conteudo, encoding="utf-8")
    return p


def montar_sub03(genome: Path, *, distrib: Optional[dict[str, int]] = None, tirar_da_planilha: int = 0,
                 extra: tuple[str, ...] = (), forma: str = "/e/data/") -> list[str]:
    """Planilha nf-core + 1ª linha da matriz + FASTQ + quant.sf, tudo falso em `genome`.
    `tirar_da_planilha` tira N nomes só da planilha (a matriz continua com todos); `extra` são
    nomes acrescentados nas DUAS (ex.: um prefixo fora da tabela)."""
    nomes, linhas, srr = [], ["sample,fastq_1,fastq_2,strandedness"], 21000000
    salmon = genome / "rnaseq" / "nfcore_output" / "sub03" / "salmon"
    for prefixo, n in list((distrib or DISTRIB).items()) + [(e, 1) for e in extra]:
        for _ in range(n):
            srr += 1
            nome = f"{prefixo}_SRR{srr}"
            pasta = _pasta_de(prefixo) if not prefixo.startswith("Nelore") else "PRJNA1107366_skin"
            se = "PBMC" in prefixo
            f1 = f"rnaseq/{pasta}/SRR{srr}_1.fastq.gz"
            f2 = "" if se else f"rnaseq/{pasta}/SRR{srr}_2.fastq.gz"
            for f in (f1, f2):
                if f:
                    _tocar(genome / f)
            _tocar(salmon / nome / "quant.sf")
            linhas.append(f"{nome},{forma}{f1},{forma + f2 if f2 else ''},auto")
            nomes.append(nome)
    _tocar(genome / "rnaseq" / "samplesheets" / "sub03_samplesheet_nfcore.csv",
           "\n".join(linhas[:len(linhas) - tirar_da_planilha]) + "\n")
    _tocar(salmon / "salmon.merged.gene_counts.tsv",
           "gene_id\tgene_name\t" + "\t".join(nomes) + "\nENSBTAG1\tX\t" + "\t".join("1" * len(nomes)) + "\n")
    return nomes


def entrada(slug: str, *, status: str = "a_revisar", tipo: str = "pesquisa", privado: bool = True,
            dados: tuple[str, ...] = ("/data/results/a",), importador: Optional[dict] = None,
            **sub_extra: Any) -> dict[str, Any]:
    sub = {"id": f"bos_taurus/{slug}", "especie": "bos_taurus", "assembly": "ARS-UCD1.3",
           "titulo": f"Título de teste {slug}", "pergunta": f"Pergunta de teste do subprojeto {slug}?",
           "tipo": tipo, "status": status, "privado": privado, "dados_legados": list(dados)}
    sub.update(sub_extra)
    return {"subprojeto": sub, "importador": dict(importador or {})}


def amostras_sub03(**extra: Any) -> dict[str, Any]:
    return {"gerador": "sub03_nfcore", "planilha": PLANILHA, "matriz": MATRIZ,
            "esperado": json.loads(json.dumps(ESPERADO)), **extra}


@pytest.fixture()
def arvore(tmp_path, monkeypatch):
    """GENOME_DATA = tmp/genome (o --saida em tmp/out fica FORA dele), espécies e projetos do
    repositório em tmp, e uma árvore de dados legados falsa que faz o catálogo-controle passar."""
    genome = tmp_path / "genome"
    genome.mkdir()
    monkeypatch.setenv("GENOME_DATA", str(genome))
    reset_config_cache()
    esp = tmp_path / "especies"
    escrever_especie(esp, "bos_taurus", modulos=("genomica", "epigenomica", "transcriptomica", "ml"),
                     assembly="ARS-UCD1.3", prefixo="ENSBTAG", ordem=3,
                     recursos={"vcf_populacional": "/data/1000bg_vcf"},
                     nunca_usar=["/data/1000bg_vcf/amostras_racas.csv"])
    escrever_especie(esp, "homo_sapiens", nao_referencia=["/data/genome_reference/hg19/refGene_hg19.txt.gz"])
    repo = tmp_path / "projetos_repo"
    escrever_subprojeto(repo, "bos_taurus", "aula_teste", status="valido", tipo="aula", assembly="ARS-UCD1.3",
                        legado=["genomes:aula"])
    apontar_catalogo(monkeypatch, esp, repo)
    for p in ("1000bg_vcf/amostras_racas.csv", "genome_reference/hg19/refGene_hg19.txt.gz",
              "results/a/x.tsv", "results/b/y.tsv", "results/invalido/z.tsv", "rnaseq/x_33samples.csv"):
        _tocar(genome / p)
    montar_sub03(genome)
    _tocar(tmp_path / "host" / "ficha.md")
    return tmp_path


def catalogo_controle(tmp_path: Path) -> list[dict[str, Any]]:
    return [
        entrada("gen_teste_a", importador={
            "razao": "conferido no disco", "canarios_ausentes": ["/data/vcfs/TAUIND"],
            "invalidos_conhecidos": ["/data/results/invalido"], "host_somente": [str(tmp_path / "host" / "ficha.md")],
            "fonte_titulo": "ficha.md (1º cabeçalho)", "fonte_pergunta": "ficha.md (Objetivo)"}),
        entrada("gen_teste_rnaseq", dados=(PLANILHA, MATRIZ),
                importador={"nunca_usar": ["/data/rnaseq/x_33samples.csv"], "amostras": amostras_sub03()}),
        entrada("gen_teste_velho", status="invalido", dados=("/data/results/b",),
                validade="rótulos de raça inválidos", substituido_por="bos_taurus/gen_teste_a",
                relacoes=["refazer_com:bos_taurus/gen_teste_a"], legado=["genomes:sub01", "genomes:sub01_x"]),
    ]


def escrever_catalogo(tmp_path: Path, cat: Any) -> Path:
    arq = tmp_path / "cat" / "catalogo.yaml"
    arq.parent.mkdir(parents=True, exist_ok=True)
    arq.write_text(yaml.safe_dump(cat, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return arq


def rodar(tmp_path: Path, cat: Any, *extra: str, capsys=None, dry_run: bool = True) -> tuple[int, str]:
    arq = escrever_catalogo(tmp_path, cat)
    argv = ["importar-subprojetos", "--catalogo", str(arq), *extra]
    if dry_run:
        argv.insert(1, "--dry-run")
    codigo = cli(argv)
    saida = ""
    if capsys is not None:
        cap = capsys.readouterr()
        saida = cap.out + cap.err
    return codigo, saida


def listagem(raiz: Path) -> dict[str, tuple]:
    return {str(p.relative_to(raiz)): (p.is_dir(), p.stat().st_size if p.is_file() else 0, p.stat().st_mtime_ns)
            for p in sorted(raiz.rglob("*"))}


# ------------------------------------------------------------------------ controles

def test_controle_tudo_existe_exit_0(arvore, capsys):
    cod, out = rodar(arvore, catalogo_controle(arvore), capsys=capsys)
    assert cod == 0, out
    assert "3 entradas, 0 falhas" in out
    assert "amostras 34" in out and "granulosa 8" in out and "hipotalamo 6" in out


def test_gerador_sintetico_34_da_8_6_6_8_6(arvore):
    linhas, falhas = gerar_amostras_sub03(PLANILHA, MATRIZ)
    assert falhas == []
    cont = importador.contagens(linhas)
    assert cont == {"total": 34, "por_tecido": dict(sorted(ESPERADO["por_tecido"].items())),
                    "por_raca": dict(sorted(ESPERADO["por_raca"].items()))}
    hf = [x for x in linhas if x["raca_rotulo_original"] == "Holstein_F"]
    assert len(hf) == 2 and {x["raca"] for x in hf} == {"Holstein"}          # D-7
    assert all(x["fastq_1"].startswith("/data/rnaseq/") for x in linhas)    # /e/data → /data
    assert {x["layout"] for x in linhas if x["tecido"] == "PBMC"} == {"SE"}
    assert list(linhas[0]) == list(importador.COLUNAS_SUB03)


def test_para_container():
    assert importador.para_container("/e/data/rnaseq/a.fq.gz") == "/data/rnaseq/a.fq.gz"
    assert importador.para_container("E:\\data\\rnaseq\\a.fq.gz") == "/data/rnaseq/a.fq.gz"
    assert importador.para_container("/data/rnaseq/a.fq.gz") == "/data/rnaseq/a.fq.gz"
    for ruim in ("/home/x/a.fq.gz", "/data/../etc/passwd", "C:/Users/a.fq.gz", ""):
        assert importador.para_container(ruim) is None, ruim


# ---------------------------------------------------------------- canários da ESPEC

def test_canario_dados_legados_tauind(arvore, capsys):
    cat = catalogo_controle(arvore)
    cat[0]["subprojeto"]["dados_legados"].append("/data/vcfs/TAUIND")
    cod, out = rodar(arvore, cat, capsys=capsys)
    assert cod == 1
    assert "/data/vcfs/TAUIND" in out and "dados_legados ausente" in out


def test_canario_slug_sub03(arvore, capsys):
    cat = catalogo_controle(arvore)
    cat[0]["subprojeto"]["id"] = "bos_taurus/sub03"
    cod, out = rodar(arvore, cat, capsys=capsys)
    assert cod == 1 and "slug nu" in out


def test_canario_prefixo_nelore_skin(arvore, capsys):
    montar_sub03(arvore / "genome", extra=("Nelore_skin",))
    cod, out = rodar(arvore, catalogo_controle(arvore), capsys=capsys)
    assert cod == 1 and "Nelore_skin" in out and "prefixo fora da tabela fechada" in out


def test_canario_planilha_33_contra_matriz_34(arvore, capsys):
    montar_sub03(arvore / "genome", tirar_da_planilha=1)
    cod, out = rodar(arvore, catalogo_controle(arvore), capsys=capsys)
    assert cod == 1 and "nomes da planilha (33)" in out and "colunas da matriz (34)" in out


@pytest.mark.parametrize("opcao", ["--saida", "--json"])
@pytest.mark.parametrize("onde", ["repo", "genome", "omica_data"])
def test_canario_destino_proibido_exit_2(arvore, capsys, opcao, onde):
    """--saida (ESPEC) e --json (crítica v2, médio 9): dentro do repositório, de GENOME_DATA ou de
    OMICA_DATA → exit 2, ANTES de qualquer E/S."""
    base = {"repo": REPO_ROOT, "genome": arvore / "genome", "omica_data": arvore / "dados"}[onde]
    alvo = base / "_teste_importador_nao_criar" / ("rel.json" if opcao == "--json" else "")
    arq = escrever_catalogo(arvore, catalogo_controle(arvore))
    antes = listagem(arvore)
    cod = cli(["importar-subprojetos", "--dry-run", "--catalogo", str(arq), opcao, str(alvo)])
    cap = capsys.readouterr()
    assert cod == 2, cap.out + cap.err
    assert "recusado" in cap.err and cap.out == ""          # recusa antes de conferir qualquer coisa
    assert not (base / "_teste_importador_nao_criar").exists()
    assert listagem(arvore) == antes


def test_controle_json_fora_das_raizes_grava(arvore, capsys):
    rel = arvore / "out" / "rel.json"
    cod, out = rodar(arvore, catalogo_controle(arvore), "--json", str(rel), capsys=capsys)
    assert cod == 0, out
    corpo = json.loads(rel.read_text(encoding="utf-8"))
    assert corpo["exit"] == 0 and corpo["dry_run"] is True and len(corpo["entradas"]) == 3
    assert corpo["entradas"][1]["amostras"]["total"] == 34
    # a pergunta de artigo nunca vai para o relatório (só para o subprojeto.yaml do --saida)
    assert "Pergunta de teste" not in rel.read_text(encoding="utf-8")


def test_canario_sem_dry_run_exit_2(arvore, capsys):
    cod, out = rodar(arvore, catalogo_controle(arvore), capsys=capsys, dry_run=False)
    assert cod == 2 and importador.MENSAGEM_SO_DRY_RUN in out


def test_sem_saida_a_arvore_tmp_fica_identica(arvore, capsys):
    cat = catalogo_controle(arvore)
    arq = arvore / "cat" / "catalogo.yaml"
    arq.parent.mkdir()
    arq.write_text(yaml.safe_dump(cat, allow_unicode=True), encoding="utf-8")
    antes = listagem(arvore)
    assert cli(["importar-subprojetos", "--dry-run", "--catalogo", str(arq)]) == 0
    assert listagem(arvore) == antes
    assert not (arvore / "dados").exists()          # OMICA_DATA nem criado


def test_controle_gravacao_revalida_pelo_modelo(arvore, capsys):
    out_dir = arvore / "out"
    cod, out = rodar(arvore, catalogo_controle(arvore), "--saida", str(out_dir), capsys=capsys)
    assert cod == 0, out
    gravados = sorted(str(p.relative_to(out_dir)).replace(os.sep, "/") for p in out_dir.rglob("*") if p.is_file())
    assert gravados == ["bos_taurus/gen_teste_a/subprojeto.yaml", "bos_taurus/gen_teste_rnaseq/amostras.tsv",
                        "bos_taurus/gen_teste_rnaseq/subprojeto.yaml", "bos_taurus/gen_teste_velho/subprojeto.yaml"]
    for p in out_dir.rglob("subprojeto.yaml"):
        Subprojeto.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))
    # o loader de verdade aceita a árvore gravada (id = caminho, privado fora do repo, amostras na pasta)
    esp, _ = importador.carregar_especies()
    regs, erros = carregar_subprojetos(esp, fontes=[("dados", out_dir)])
    assert erros == {} and len(regs) == 3
    tsv = (out_dir / "bos_taurus" / "gen_teste_rnaseq" / "amostras.tsv").read_text(encoding="utf-8")
    assert tsv.splitlines()[0].split("\t") == list(importador.COLUNAS_SUB03) and len(tsv.splitlines()) == 35


def test_canario_gravacao_que_nao_reproduz_e_recusada(arvore, capsys, monkeypatch):
    """O YAML gravado é relido pelo modelo: um escritor que perde um campo (aqui `privado`, que
    voltaria False) não passa calado."""
    original = importador._yaml_subprojeto

    def sem_privado(sub, catalogo):
        return "\n".join(x for x in original(sub, catalogo).splitlines() if not x.startswith("privado:")) + "\n"

    monkeypatch.setattr(importador, "_yaml_subprojeto", sem_privado)
    with pytest.raises(RuntimeError, match="não reproduz"):
        rodar(arvore, catalogo_controle(arvore), "--saida", str(arvore / "out"), capsys=capsys)


def test_stdout_cp1252_nao_quebra(arvore, monkeypatch):
    """A falha "nomes da planilha (33) ≠ colunas..." tem '≠', que não existe em cp1252: o
    relatório num console Windows sem PYTHONUTF8 não pode virar UnicodeEncodeError."""
    import io

    montar_sub03(arvore / "genome", tirar_da_planilha=1)
    arq = escrever_catalogo(arvore, catalogo_controle(arvore))
    buf_out, buf_err = io.BytesIO(), io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(buf_out, encoding="cp1252"))
    monkeypatch.setattr(sys, "stderr", io.TextIOWrapper(buf_err, encoding="cp1252"))
    cod = cli(["importar-subprojetos", "--dry-run", "--catalogo", str(arq)])
    sys.stdout.flush()
    assert cod == 1
    assert b"\\u2260" in buf_out.getvalue()


def test_host_somente_ausente_e_so_aviso(arvore, capsys):
    cat = catalogo_controle(arvore)
    cat[0]["importador"]["host_somente"] = [str(arvore / "host" / "nao_existe.md")]
    cod, out = rodar(arvore, cat, capsys=capsys)
    assert cod == 0 and "host_somente ausente neste host (informativo)" in out


def test_canario_pipeline_que_nao_declara_a_especie(arvore, capsys):
    cat = catalogo_controle(arvore)
    cat.append({"subprojeto": {"id": "homo_sapiens/teste_humano", "especie": "homo_sapiens", "assembly": "GRCh38",
                               "titulo": "Teste", "pergunta": "Pergunta de teste humana?", "tipo": "pesquisa",
                               "status": "draft", "privado": True, "dados_legados": ["/data/results/a"],
                               "pipelines": ["genomica_painel"]}})
    assert "não declara a espécie homo_sapiens" in _falha(arvore, capsys, cat)


def test_canario_parametro_inexistente(arvore, capsys):
    cat = catalogo_controle(arvore)
    cat[0]["subprojeto"]["parametros"] = {"transcriptomica.deseq2_demo.nao_existe": "1"}
    assert "não tem o parâmetro" in _falha(arvore, capsys, cat)


def test_com_falha_saida_nao_grava(arvore, capsys):
    cat = catalogo_controle(arvore)
    cat[0]["subprojeto"]["dados_legados"].append("/data/vcfs/TAUIND")
    cod, out = rodar(arvore, cat, "--saida", str(arvore / "out"), capsys=capsys)
    assert cod == 1 and "nada gravado" in out and not (arvore / "out").exists()


def _link_de_pasta(link: Path, alvo: Path) -> None:
    alvo.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(alvo, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        if sys.platform != "win32":
            pytest.skip("sem symlink de pasta neste host")
        import _winapi

        _winapi.CreateJunction(str(alvo), str(link))


def test_canario_saida_por_link_dentro_de_genome_data(arvore, capsys):
    """Uma pasta-link em <saida> que aponta para GENOME_DATA não fura a recusa (resolve)."""
    out_dir = arvore / "out"
    out_dir.mkdir()
    _link_de_pasta(out_dir / "bos_taurus", arvore / "genome" / "alvo_link")
    cod, out = rodar(arvore, catalogo_controle(arvore), "--saida", str(out_dir), capsys=capsys)
    assert cod == 2 and "recusado" in out
    assert list((arvore / "genome" / "alvo_link").iterdir()) == []


def test_canario_catalogo_dentro_do_repositorio(arvore, capsys):
    cod = cli(["importar-subprojetos", "--dry-run", "--catalogo",
               str(REPO_ROOT / "projetos" / "bos_taurus" / "demo" / "subprojeto.yaml")])
    out = capsys.readouterr().err
    assert cod == 2 and "regra 7" in out


# ----------------------------------------------------------- canários das regras novas

def _falha(arvore, capsys, cat) -> str:
    cod, out = rodar(arvore, cat, capsys=capsys)
    assert cod == 1, out
    return out


def test_canario_canario_ausente_que_existe(arvore, capsys):
    cat = catalogo_controle(arvore)
    cat[0]["importador"]["canarios_ausentes"].append("/data/results/a")
    assert "canário EXISTE" in _falha(arvore, capsys, cat)


@pytest.mark.parametrize("campo", ["invalidos_conhecidos", "excluir_do_indice", "nunca_usar"])
def test_canario_caminho_que_tem_de_existir(arvore, capsys, campo):
    cat = catalogo_controle(arvore)
    cat[0]["importador"][campo] = ["/data/results/nao_existe"]
    assert f"{campo} ausente: /data/results/nao_existe" in _falha(arvore, capsys, cat)


def test_canario_painel_fonte_ausente(arvore, capsys):
    cat = catalogo_controle(arvore)
    cat[0]["importador"]["painel_fonte"] = "/data/genes_alvo/nao_existe.tsv"
    assert "painel_fonte ausente" in _falha(arvore, capsys, cat)


def test_canario_painel_na_pasta(arvore, capsys):
    cat = catalogo_controle(arvore)
    cat[0]["subprojeto"]["painel"] = "painel.tsv"
    assert "importador.painel_fonte" in _falha(arvore, capsys, cat)


@pytest.mark.parametrize("como", ["id_do_repo", "alias_do_repo", "id_repetido", "alias_casefold"])
def test_canario_colisao(arvore, capsys, como):
    cat = catalogo_controle(arvore)
    if como == "id_do_repo":
        cat[0]["subprojeto"]["id"] = "bos_taurus/aula_teste"
    elif como == "alias_do_repo":
        cat[0]["subprojeto"]["legado"] = ["GENOMES:AULA"]
    elif como == "id_repetido":
        cat[1]["subprojeto"]["id"] = cat[0]["subprojeto"]["id"]
    else:   # "sub01" (genomes) × "SUB01" (epigenoma) só se distinguem pelo prefixo da origem
        cat[0]["subprojeto"]["legado"] = ["Genomes:SUB01"]
    assert "colide com" in _falha(arvore, capsys, cat)


def test_controle_alias_com_prefixos_diferentes(arvore, capsys):
    cat = catalogo_controle(arvore)
    cat[0]["subprojeto"]["legado"] = ["epigenoma:SUB01"]     # o velho tem genomes:sub01
    cod, out = rodar(arvore, cat, capsys=capsys)
    assert cod == 0, out


def test_canario_demo_fora_do_repositorio(arvore, capsys):
    cat = catalogo_controle(arvore)
    cat[0]["subprojeto"].update(tipo="demo", privado=False)
    assert "só existem no repositório" in _falha(arvore, capsys, cat)


def test_canario_pesquisa_sem_privado(arvore, capsys):
    cat = catalogo_controle(arvore)
    cat[0]["subprojeto"]["privado"] = False
    assert "regra 7" in _falha(arvore, capsys, cat)


@pytest.mark.parametrize("onde", ["titulo", "pergunta"])
def test_canario_marcador_exige_draft(arvore, capsys, onde):
    cat = catalogo_controle(arvore)
    cat[0]["subprojeto"][onde] = MARCADOR_AUTOR
    assert "exige status draft" in _falha(arvore, capsys, cat)


def test_controle_marcador_em_draft_e_pendencia(arvore, capsys):
    cat = catalogo_controle(arvore)
    cat[0]["subprojeto"].update(titulo=MARCADOR_AUTOR, pergunta=MARCADOR_AUTOR, status="draft")
    cat[0]["importador"]["status_proposto"] = "valido"
    cod, out = rodar(arvore, cat, capsys=capsys)
    assert cod == 0, out
    assert "titulo e pergunta a escrever pelo autor" in out and "status proposto valido" in out


def test_canario_medio12_raca_do_nome_valido_sem_proveniencia(arvore, capsys):
    cat = catalogo_controle(arvore)
    cat[1]["subprojeto"]["status"] = "valido"
    assert "legado frágil marcado valido" in _falha(arvore, capsys, cat)


def test_controle_medio12_valido_com_proveniencia_e_aviso(arvore, capsys):
    cat = catalogo_controle(arvore)
    cat[1]["subprojeto"].update(status="valido", proveniencia_rotulos="nome da amostra, não conferido no BioSample",
                                avisos=["raça lida do nome"])
    cod, out = rodar(arvore, cat, capsys=capsys)
    assert cod == 0, out


@pytest.mark.parametrize("defeito, trecho", [
    ({"substituido_por": "bos_taurus/nao_existe"}, "substituido_por aponta para id inexistente"),
    ({"relacoes": ["refazer_com:bos_taurus/nao_existe"]}, "relação aponta para id inexistente"),
    ({"relacoes": ["copia_de:bos_taurus/gen_teste_a"]}, "relação fora do padrão"),
    ({"relacoes": ["refazer_com:bos_taurus/gen_teste_velho"]}, "relação consigo mesmo"),
])
def test_canario_relacoes(arvore, capsys, defeito, trecho):
    cat = catalogo_controle(arvore)
    cat[2]["subprojeto"].update(defeito)
    assert trecho in _falha(arvore, capsys, cat)


@pytest.mark.parametrize("como", ["nunca_usar_da_especie", "planilha_em_nunca_usar", "dentro_de_invalido"])
def test_canario_caminho_proibido(arvore, capsys, como):
    cat = catalogo_controle(arvore)
    if como == "nunca_usar_da_especie":
        cat[0]["subprojeto"]["dados_legados"].append("/data/1000bg_vcf/amostras_racas.csv")
    elif como == "planilha_em_nunca_usar":
        cat[1]["importador"]["nunca_usar"].append(PLANILHA)
    else:
        cat[0]["subprojeto"]["dados_legados"].append("/data/results/invalido/z.tsv")
    assert "caminho proibido" in _falha(arvore, capsys, cat)


def test_canario_recurso_da_especie_ausente(arvore, capsys):
    (arvore / "genome" / "genome_reference" / "hg19" / "refGene_hg19.txt.gz").unlink()
    assert "espécie homo_sapiens: nao_referencia ausente" in _falha(arvore, capsys, catalogo_controle(arvore))


@pytest.mark.parametrize("chave, valor, trecho", [
    ("total", 33, "total de amostras 34"),
    ("por_tecido", {"granulosa": 8, "hipotalamo": 6, "hipofise": 6, "PBMC": 8, "pele": 6}, "por tecido"),
    ("por_raca", {"Holstein": 12, "Holstein_F": 2, "Angus": 8, "Crossbred": 4, "Tharparkar": 4, "Brangus": 2,
                  "Kedah-Kelantan": 2}, "por raça"),
])
def test_canario_contagens_divergem(arvore, capsys, chave, valor, trecho):
    cat = catalogo_controle(arvore)
    cat[1]["importador"]["amostras"]["esperado"][chave] = valor
    assert trecho in _falha(arvore, capsys, cat)


@pytest.mark.parametrize("defeito", ["pasta_sem_dataset", "fastq_fora_de_data", "quant_ausente", "fastq_ausente"])
def test_canario_gerador_arquivos(arvore, capsys, defeito):
    genome = arvore / "genome"
    planilha = genome / "rnaseq" / "samplesheets" / "sub03_samplesheet_nfcore.csv"
    linhas = planilha.read_text(encoding="utf-8").splitlines()
    nome, f1, f2, st = linhas[1].split(",")
    if defeito == "pasta_sem_dataset":
        novo = f1.replace("PRJNA885909_hypothalamus", "PRJNA000_skin")
        _tocar(genome / novo.replace("/e/data/", ""))
        linhas[1] = ",".join([nome, novo, f2, st])
        trecho = "pasta de FASTQ sem dataset"
    elif defeito == "fastq_fora_de_data":
        linhas[1] = ",".join([nome, "/home/autor/x_1.fastq.gz", f2, st])
        trecho = "fastq_1 fora de /data"
    elif defeito == "quant_ausente":
        (genome / "rnaseq" / "nfcore_output" / "sub03" / "salmon" / nome / "quant.sf").unlink()
        trecho = "quant.sf ausente"
    else:
        (genome / f2.replace("/e/data/", "")).unlink()
        trecho = "fastq_2 ausente"
    planilha.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    assert trecho in _falha(arvore, capsys, catalogo_controle(arvore))


@pytest.mark.parametrize("mutar, trecho", [
    (lambda c: c[0]["subprojeto"].__setitem__("razao", "x"), "subprojeto:"),          # forbid
    (lambda c: c[0]["importador"].__setitem__("canario_inexistente", []), "formato da entrada"),
    (lambda c: c[0].__setitem__("extra", 1), "formato da entrada"),
    (lambda c: c[0]["importador"].__setitem__("nunca_usar", ["E:\\data\\x"]), "formato da entrada"),
    (lambda c: c[1]["importador"]["amostras"].__setitem__("gerador", "outro"), "formato da entrada"),
    (lambda c: c[0]["subprojeto"].__setitem__("assembly", "ARS-UCD1.2"), "não é da espécie"),
    (lambda c: c[0]["subprojeto"].__setitem__("modulos", ["proteomica"]), "módulos fora da espécie"),
])
def test_canario_formato(arvore, capsys, mutar, trecho):
    cat = catalogo_controle(arvore)
    mutar(cat)
    assert trecho in _falha(arvore, capsys, cat)


@pytest.mark.parametrize("cat, trecho", [({"subprojeto": {}}, "LISTA"), ([], "catálogo vazio")])
def test_canario_catalogo_mal_formado(arvore, capsys, cat, trecho):
    assert trecho in _falha(arvore, capsys, cat)


def test_pipelines_do_subprojeto(arvore, capsys):
    cat = catalogo_controle(arvore)
    cat[0]["subprojeto"].update(pipelines=["transcriptomica_de"], modulos=["transcriptomica"])
    cod, out = rodar(arvore, cat, capsys=capsys)
    assert cod == 0, out                                              # controle
    cat[0]["subprojeto"]["pipelines"] = ["nao_existe"]
    assert "pipeline inexistente" in _falha(arvore, capsys, cat)
    cat[0]["subprojeto"]["pipelines"] = ["genomica_painel"]           # declara só bos_taurus, mas é de genômica
    assert "fora de ['transcriptomica']" in _falha(arvore, capsys, cat)


# ------------------------------------------- bloqueante 2: o formato REAL da proposta

# forma exata de uma entrada de subprojetos_propostos.yaml (gen_sub04): sem especie, assembly,
# titulo nem pergunta; `razao` e `canario_inexistente` fora do modelo; `relacoes` como dict
PROPOSTA_REAL = {
    "id": "bos_taurus/gen_sub04_reverse_aims",
    "legado": ["sub04", "sub04_reverse_aims"],
    "tipo": "pesquisa",
    "status": "invalido",
    "razao": "lado AIM vem de um Fst feito com rótulos inválidos; refazer contra planob_aims",
    "privado": True,
    "relacoes": {"depende_de": ["bos_taurus/gen_sub03_rnaseq_calor", "bos_taurus/gen_sub02_aims_girolando"],
                 "refazer_com": "bos_taurus/planob_aims"},
    "dados_legados": ["/data/results/a", "/data/results/b/y.tsv"],
    "canario_inexistente": ["/data/vcfs/TAUIND", "/data/results/sub04"],
}
TEXTO = dict(titulo="Título copiado da ficha de teste", pergunta="Hipótese copiada da ficha de teste.",
             assembly="ARS-UCD1.3", fonte_titulo="ficha.md (1º cabeçalho)", fonte_pergunta="ficha.md (Hipótese)")


def test_proposta_real_nao_valida_sem_mapear():
    with pytest.raises(ValidationError) as e:
        Subprojeto.model_validate(PROPOSTA_REAL)
    faltando = {err["loc"][0] for err in e.value.errors()}
    assert {"especie", "assembly", "titulo", "pergunta", "razao", "canario_inexistente", "relacoes"} <= faltando


def test_mapear_proposta_real():
    ent = mapear_proposta(PROPOSTA_REAL, origem_legado="genomes", **TEXTO)
    sub, imp = ent["subprojeto"], ent["importador"]
    assert sub["validade"] == PROPOSTA_REAL["razao"] and "razao" not in imp       # invalido: razao → validade
    assert sub["relacoes"] == ["depende_de:bos_taurus/gen_sub03_rnaseq_calor",
                               "depende_de:bos_taurus/gen_sub02_aims_girolando",
                               "refazer_com:bos_taurus/planob_aims"]
    assert sub["legado"] == ["genomes:sub04", "genomes:sub04_reverse_aims"]
    assert imp["canarios_ausentes"] == PROPOSTA_REAL["canario_inexistente"]
    assert sub["especie"] == "bos_taurus" and sub["titulo"] == TEXTO["titulo"]
    EntradaCatalogo.model_validate(ent)
    Subprojeto.model_validate(sub)


def test_mapear_razao_fora_de_invalido_vai_para_o_importador():
    p = dict(PROPOSTA_REAL, status="a_revisar")
    p.pop("relacoes")
    ent = mapear_proposta(p, origem_legado="genomes", **TEXTO)
    assert ent["importador"]["razao"] == p["razao"] and "validade" not in ent["subprojeto"]


def test_mapear_marcador_vira_draft_e_guarda_o_status_proposto():
    p = {k: v for k, v in PROPOSTA_REAL.items() if k != "relacoes"} | {"status": "valido"}
    ent = mapear_proposta(p, origem_legado="genomes", titulo=MARCADOR_AUTOR, pergunta=MARCADOR_AUTOR,
                          assembly="ARS-UCD1.3")
    assert ent["subprojeto"]["status"] == "draft" and ent["importador"]["status_proposto"] == "valido"


def test_mapear_canario_invalido_com_marcador_e_recusado():
    """Draft é executável: uma proposta inválida não pode virar draft por falta de texto."""
    with pytest.raises(ValueError, match="draft é executável"):
        mapear_proposta(PROPOSTA_REAL, origem_legado="genomes", titulo="Título", pergunta=MARCADOR_AUTOR,
                        assembly="ARS-UCD1.3")


@pytest.mark.parametrize("extra", [{"nova_chave": 1}, {"titulo": "inventado pela proposta"},
                                   {"pergunta": "inventada pela proposta?"}])
def test_mapear_canario_chave_sem_mapeamento(extra):
    with pytest.raises(ValueError, match="sem mapeamento"):
        mapear_proposta(PROPOSTA_REAL | extra, origem_legado="genomes", **TEXTO)


def test_mapear_painel_de_data_vai_para_painel_fonte():
    p = {k: v for k, v in PROPOSTA_REAL.items() if k != "relacoes"} | {
        "status": "a_revisar", "painel": "/data/genes_alvo/painel.tsv"}
    ent = mapear_proposta(p, origem_legado="epigenoma", **TEXTO)
    assert ent["importador"]["painel_fonte"] == "/data/genes_alvo/painel.tsv"
    assert "painel" not in ent["subprojeto"] and ent["subprojeto"]["legado"][0] == "epigenoma:sub04"


def test_proposta_real_mapeada_passa_no_importador(arvore, capsys):
    """De ponta a ponta: o gen_sub04 no formato real + os 3 alvos das relações, mapeados, saem com 0."""
    alvos = [
        {"id": "bos_taurus/gen_sub03_rnaseq_calor", "tipo": "pesquisa", "status": "a_revisar", "privado": True,
         "razao": "34 = 34", "dados_legados": [PLANILHA]},
        {"id": "bos_taurus/gen_sub02_aims_girolando", "tipo": "pesquisa", "status": "invalido", "privado": True,
         "substituido_por": "bos_taurus/planob_aims", "razao": "rótulos inválidos",
         "dados_legados": ["/data/results/b"]},
        {"id": "bos_taurus/planob_aims", "tipo": "pesquisa", "status": "valido", "privado": True,
         "razao": "revalidado", "dados_legados": ["/data/results/a"]},
    ]
    cat = [mapear_proposta(PROPOSTA_REAL, origem_legado="genomes", **TEXTO)]
    cat += [mapear_proposta(a, origem_legado="genomes", titulo=MARCADOR_AUTOR if a["status"] != "invalido"
                            else "Título da ficha", pergunta=MARCADOR_AUTOR if a["status"] != "invalido"
                            else "Objetivo copiado da ficha.", assembly="ARS-UCD1.3") for a in alvos]
    cod, out = rodar(arvore, cat, capsys=capsys)
    assert cod == 0, out
    assert out.count("a escrever pelo autor") == 2


# ------------------------------------ os arquivos REAIS (só onde existem: host do autor)

INSUMOS = Path(r"C:\Users\luizd\OneDrive\Data_Science\Projetos\omica\.project-memory\docs\v2_insumos_2026-10-28")
PROPOSTOS = INSUMOS / "subprojetos" / "subprojetos_propostos.yaml"
CATALOGO_REAL = INSUMOS / "catalogo_bovino.yaml"


@pytest.mark.skipif(not PROPOSTOS.is_file(), reason="propostas só existem no host do autor")
def test_todas_as_propostas_reais_mapeiam():
    """As 8 propostas bovinas (sem demo e ns5a), no formato real, mapeiam e validam (sem disco)."""
    propostas = yaml.safe_load(PROPOSTOS.read_text(encoding="utf-8"))["subprojetos"]
    n = 0
    for p in propostas:
        if p["id"] in ("bos_taurus/demo", "homo_sapiens/ns5a_hcv_huh7"):
            continue
        origem = "epigenoma" if p["id"].split("/")[1].startswith("epi_") else "genomes"
        ent = mapear_proposta(p, origem_legado=origem, **TEXTO)
        EntradaCatalogo.model_validate(ent)
        n += 1
    assert n == 8


@pytest.mark.skipif(not CATALOGO_REAL.is_file(), reason="o catálogo só existe no host do autor")
def test_catalogo_real_valida_contra_os_modelos_e_as_especies_do_repo():
    """Sem disco: formato, modelo, espécie do repositório, marcador → draft, privado, nenhum
    legado `valido` com raça lida do nome, ids/aliases únicos. O --dry-run real é o E2E-6."""
    bruto = yaml.safe_load(CATALOGO_REAL.read_text(encoding="utf-8"))
    especies, erros = importador.carregar_especies()
    assert erros == {}
    vistos: set[str] = set()
    for item in bruto:
        ent = EntradaCatalogo.model_validate(item)
        sub = Subprojeto.model_validate(ent.subprojeto)
        assert validar_contra_especie(sub, especies) == [], sub.id
        assert sub.privado and sub.tipo == "pesquisa" and not sub.painel, sub.id
        if MARCADOR_AUTOR in sub.titulo or MARCADOR_AUTOR in sub.pergunta:
            assert sub.status == "draft", sub.id
        else:
            assert ent.importador.fonte_titulo and ent.importador.fonte_pergunta, sub.id
        if ent.importador.amostras is not None:
            assert sub.status != "valido" or (sub.proveniencia_rotulos and sub.avisos), sub.id
        for c in (sub.id, *sub.legado):
            assert c.casefold() not in vistos, c
            vistos.add(c.casefold())
    assert len(bruto) == 8
