"""Índices IRMÃOS (.tbi/.csi) das entradas de run (G1 §1.5 e §1.7, canários I1–I9), a cópia
atômica do registro (crítica #8 e 2ª parte da #6) e a regra de nome da ORIGEM pela rota
(crítica #5b).

O cliente nunca manda o caminho do índice: o servidor o deriva do nome do principal
(formatos.candidatos_irmaos), o registra (caminho, bytes, sha256, mtime), o copia junto na
cópia, o congela no plano e confere o CONJUNTO presente no aprovar e no executar. Cada
canário tem o seu controle (o mesmo arranjo sem a mexida). VCF/BCF sintéticos de
test_formatos_genomica; helpers e a raiz de referência de test_runs_referencia.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from omica import runs
from omica.config import REPO_ROOT
from test_formatos_genomica import _demo, _gravar_indice, escrever_bcf, escrever_csi, sintetico
from test_runs import api, lancados  # noqa: F401 (fixtures)
from test_runs_referencia import (BED_BCF, BED_DEMO, CRU, _aprovado_ref, _cache_vcf_limpo,  # noqa: F401
                                  _erro, _executar, _plano, _aprovar, _por_caminho, _run, _sha, _sobe_bed, raiz)

DEMO_VCF = "modules/genomica/demo_data/demo_bta14_inicio.vcf.gz"


@pytest.fixture()
def caixa(cfg) -> Path:
    c = cfg.data_dir / "entradas"
    c.mkdir(parents=True, exist_ok=True)
    return c


def _registro(api, rid: str, caminho) -> dict:
    r = _por_caminho(api, rid, "vcf", caminho)
    assert r.status_code == 200, r.text
    return r.json()["entradas"]["vcf"]


def _bcf(pasta: Path, nome: str = "y.bcf", indice: str | None = ".csi") -> Path:
    bcf = pasta / nome
    regs = escrever_bcf(bcf, ["14"], [(0, 100), (0, 200)])
    if indice == ".csi":
        escrever_csi(Path(f"{bcf}.csi"), regs, n_ref=1, mtime_de=bcf)
    elif indice == ".tbi":  # o tabix não indexa BCF: só para o canário
        Path(f"{bcf}.tbi").write_bytes(Path(__file__).read_bytes()[:200])
    return bcf


# ================================================================== I1–I3 veredito
def test_i1_vcf_sem_indice_reprova_e_bloqueia_o_plano(api, caixa):
    rid = _run(api)
    _sobe_bed(api, rid)
    reg = _registro(api, rid, sintetico(caixa, "x.vcf.gz", indice=None))
    assert not reg["veredito"]["ok"] and any("sem índice ao lado" in f for f in reg["veredito"]["falhas"])
    assert reg["irmaos"] == {} and reg["candidatos_irmaos"] == [".tbi", ".csi"]
    r = api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {}})
    assert r.status_code == 409 and _erro(r) == "entradas_reprovadas"


@pytest.mark.parametrize("indice,ok", [(".tbi", False), (".csi", True)])
def test_i2_bcf_so_aceita_csi(api, raiz, indice, ok):
    rid = _run(api)
    _sobe_bed(api, rid, BED_BCF)
    reg = _registro(api, rid, _bcf(raiz, indice=indice))
    assert reg["veredito"]["ok"] is ok, reg["veredito"]
    if not ok:
        assert any("BCF com .tbi" in f for f in reg["veredito"]["falhas"])
        assert reg["irmaos"] == {}  # o .tbi nem é candidato de um BCF: não entra no registro


@pytest.mark.parametrize("estrago", ["mais_velho", "magic_errado"])
def test_i3_indice_velho_ou_estranho_reprova(api, raiz, estrago):
    vcf = sintetico(raiz, "x.vcf.gz")
    tbi = Path(f"{vcf}.tbi")
    if estrago == "mais_velho":
        st = vcf.stat()
        os.utime(tbi, ns=(st.st_atime_ns, st.st_mtime_ns - 60 * 10**9))
        trecho = "mais velho que o dado"
    else:
        _gravar_indice(tbi, b"XYZ\x01" + b"\0" * 60, mtime_de=vcf)
        trecho = ".tbi"
    rid = _run(api)
    reg = _registro(api, rid, vcf)
    assert not reg["veredito"]["ok"] and any(trecho in f for f in reg["veredito"]["falhas"]), reg["veredito"]


def test_i4_indice_symlink_para_fora_da_raiz_e_recusado(api, raiz, cfg):
    vcf = sintetico(raiz, "x.vcf.gz", indice=None)
    fora = cfg.genome_data / "outro"
    fora.mkdir()
    alvo = sintetico(fora, "x.vcf.gz")
    try:
        Path(f"{vcf}.tbi").symlink_to(Path(f"{alvo}.tbi"))
    except OSError:
        pytest.skip("sem permissão para criar symlink neste sistema")
    rid = _run(api)
    r = _por_caminho(api, rid, "vcf", vcf)
    falta = _por_caminho(api, rid, "vcf", raiz / "nao_existe.vcf.gz")
    assert r.status_code == 422 and r.json() == falta.json()
    assert "vcf" not in runs._carregar(rid)["entradas"]


# ====================================================================== I5 cópia
def test_i5_copia_leva_o_indice_com_o_nome_certo_e_os_mtimes(api, caixa, cfg):
    origem = sintetico(caixa, "lote_7.vcf.gz")
    rid = _run(api)
    reg = _registro(api, rid, origem)
    pasta = cfg.raw_dir / "runs" / rid
    final, indice = pasta / "vcf.vcf.gz", pasta / "vcf.vcf.gz.tbi"
    assert reg["modo"] == "copia" and reg["caminho"] == final.as_posix() and reg["veredito"]["ok"]
    assert reg["irmaos"][".tbi"]["caminho"] == indice.as_posix()
    assert reg["irmaos"][".tbi"]["sha256"] == _sha(Path(f"{origem}.tbi")) == _sha(indice)
    # decisão 14: a cópia herda os mtimes da origem (um índice velho na origem não é mascarado
    # pela ordem da cópia)
    assert final.stat().st_mtime_ns == origem.stat().st_mtime_ns
    assert indice.stat().st_mtime_ns == Path(f"{origem}.tbi").stat().st_mtime_ns
    assert sorted(p.name for p in pasta.iterdir()) == ["vcf.vcf.gz", "vcf.vcf.gz.tbi"]


def test_i5_teto_soma_principal_e_indice(api, caixa, cfg, monkeypatch):
    """Principal de 60 B + índice de 50 B com teto 100: 413, e a pasta do run fica vazia
    (nem a pasta temporária do registro sobra). Controle: com teto 110, entra."""
    (caixa / "p.vcf.gz").write_bytes(b"P" * 60)
    (caixa / "p.vcf.gz.tbi").write_bytes(b"T" * 50)
    monkeypatch.setattr(runs, "_MAX_ENTRADA", 100)
    rid = _run(api)
    r = _por_caminho(api, rid, "vcf", caixa / "p.vcf.gz")
    assert r.status_code == 413 and _erro(r) == "entrada_grande_demais", r.text
    assert list((cfg.raw_dir / "runs" / rid).iterdir()) == []
    monkeypatch.setattr(runs, "_MAX_ENTRADA", 110)
    assert _por_caminho(api, rid, "vcf", caixa / "p.vcf.gz").status_code == 200  # (veredito reprovado: lixo)


# ============================================================ I6 depois da aprovação
@pytest.mark.parametrize("mexida", ["tbi_trocado", "csi_novo", "tbi_removido"])
def test_i6_indice_mexido_depois_da_aprovacao_nao_lanca(api, lancados, raiz, mexida):
    """Um .csi que aparece ao lado de um VCF aprovado só com .tbi muda o que o htslib lê; um
    .tbi trocado ou removido também. Controle: test_r14_controle_sem_mexida_executa."""
    rid, _, vcf = _aprovado_ref(api, raiz)
    tbi = Path(f"{vcf}.tbi")
    if mexida == "tbi_trocado":
        st = tbi.stat()
        dados = bytearray(tbi.read_bytes())
        dados[20] ^= 0xFF
        tbi.write_bytes(bytes(dados))
        os.utime(tbi, ns=(st.st_atime_ns, st.st_mtime_ns))
    elif mexida == "csi_novo":
        Path(f"{vcf}.csi").write_bytes(tbi.read_bytes())
    else:
        tbi.unlink()
    r = _executar(api, rid)
    assert r.status_code == 409 and _erro(r) == "entrada_alterada", r.text
    assert r.json()["detail"]["arquivo"] == "indice"
    assert lancados == []


def test_i6_indice_da_copia_mexido_depois_da_aprovacao(api, lancados, caixa, cfg):
    rid = _run(api)
    _sobe_bed(api, rid)
    _registro(api, rid, sintetico(caixa, "x.vcf.gz"))
    plano = _plano(api, rid)
    assert _aprovar(api, rid, plano).status_code == 200
    tbi = cfg.raw_dir / "runs" / rid / "vcf.vcf.gz.tbi"
    tbi.write_bytes(tbi.read_bytes() + b"\0")
    r = _executar(api, rid)
    assert r.status_code == 409 and r.json()["detail"] == {"erro": "entrada_alterada", "entrada": "vcf",
                                                           "arquivo": "indice"}
    assert lancados == []


def test_i6_indice_mexido_entre_plano_e_aprovacao(api, raiz):
    """O aprovar confere os índices do plano PROPOSTO (L3), não só o executar."""
    vcf_rid = _run(api)
    _sobe_bed(api, vcf_rid)
    vcf = sintetico(raiz, "x.vcf.gz")
    _registro(api, vcf_rid, vcf)
    plano = _plano(api, vcf_rid)
    Path(f"{vcf}.csi").write_bytes(b"x")
    r = _aprovar(api, vcf_rid, plano)
    assert r.status_code == 409 and _erro(r) == "entrada_alterada", r.text


# ================================================================ I8 re-registro
@pytest.mark.parametrize("segundo", ["vcf_com_csi", "bcf"])
def test_i8_reregistro_nao_deixa_o_indice_antigo_ao_lado(api, caixa, cfg, segundo):
    rid = _run(api)
    _registro(api, rid, sintetico(caixa, "a.vcf.gz"))
    if segundo == "bcf":
        reg = _registro(api, rid, _bcf(caixa, "b.bcf"))
        esperado = ["vcf.bcf", "vcf.bcf.csi"]
    else:
        reg = _registro(api, rid, sintetico(caixa, "b.vcf.gz", indice=".csi"))
        esperado = ["vcf.vcf.gz", "vcf.vcf.gz.csi"]
    assert set(reg["irmaos"]) == {".csi"} and reg["veredito"]["ok"], reg["veredito"]
    assert sorted(p.name for p in (cfg.raw_dir / "runs" / rid).iterdir()) == esperado


def test_i8_indice_perdido_ao_lado_do_destino_e_apagado_na_troca(api, caixa, cfg):
    """Um .csi que nenhum registro aponta (sobra de uma troca interrompida) ao lado do destino
    seria o índice que o htslib carrega. A troca apaga todo candidato que a cópia nova não
    traz — não só os do registro anterior."""
    rid = _run(api)
    pasta = cfg.raw_dir / "runs" / rid
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "vcf.vcf.gz.csi").write_bytes(b"sobra")
    reg = _registro(api, rid, sintetico(caixa, "a.vcf.gz"))
    assert set(reg["irmaos"]) == {".tbi"} and reg["veredito"]["ok"]
    assert sorted(p.name for p in pasta.iterdir()) == ["vcf.vcf.gz", "vcf.vcf.gz.tbi"]


# ================================================================== I9 controle
def test_i9_demo_do_repositorio_pela_caixa_aprova(api, cfg):
    rid = _run(api)
    _sobe_bed(api, rid)
    reg = _registro(api, rid, DEMO_VCF)
    origem = REPO_ROOT / DEMO_VCF
    assert reg["modo"] == "copia" and reg["veredito"]["ok"], reg["veredito"]
    assert reg["veredito"]["perfil"]["n_registros"] == 122372 and reg["sha256"] == _sha(origem)
    assert set(reg["irmaos"]) == {".tbi"} and reg["origem"] == f"caminho:{DEMO_VCF}"
    plano = _plano(api, rid)
    assert plano["entradas"]["vcf"]["irmaos"][".tbi"]["sha256"] == _sha(Path(f"{origem}.tbi"))
    assert _aprovar(api, rid, plano).status_code == 200


# ================================================= crítica #8 / #6 cópia atômica
def test_reregistro_que_estoura_o_teto_nao_perde_a_entrada_valida(api, caixa, cfg, monkeypatch):
    """Crítica #8: o re-registro apagava o índice ANTES de copiar; um 413 na cópia nova
    deixava o run.json com o registro antigo e o .tbi já sumido. Aqui o principal novo cabe e
    o índice dele não (o 413 sai no MEIO da cópia): nada muda, e a entrada antiga aprova."""
    peq = sintetico(caixa, "peq.vcf.gz", reg=list(_demo()[1][:200]))
    grande = sintetico(caixa, "grande.vcf.gz")
    rid = _run(api)
    _sobe_bed(api, rid)
    _registro(api, rid, peq)
    antes = runs._carregar(rid)["entradas"]["vcf"]
    pasta = cfg.raw_dir / "runs" / rid
    arquivos = {p.name: _sha(p) for p in pasta.iterdir()}
    monkeypatch.setattr(runs, "_MAX_ENTRADA", grande.stat().st_size + 50)  # o .tbi tem mais de 50 B
    assert Path(f"{grande}.tbi").stat().st_size > 50
    r = _por_caminho(api, rid, "vcf", grande)
    assert r.status_code == 413 and _erro(r) == "entrada_grande_demais", r.text
    assert runs._carregar(rid)["entradas"]["vcf"] == antes
    assert {p.name: _sha(p) for p in pasta.iterdir()} == arquivos
    assert _aprovar(api, rid, _plano(api, rid)).status_code == 200


def test_reregistro_com_checagem_que_levanta_nao_deixa_copia_orfa(api, caixa, cfg, monkeypatch):
    """2ª parte da crítica #6: uma exceção depois da cópia (aqui, numa checagem) virava 500
    com a cópia nova já no lugar e o run.json antigo. Agora nada muda: nem o registro, nem os
    arquivos, nem sobra a pasta temporária."""
    rid = _run(api)
    _sobe_bed(api, rid)
    _registro(api, rid, sintetico(caixa, "a.vcf.gz"))
    antes = runs._carregar(rid)
    pasta = cfg.raw_dir / "runs" / rid
    arquivos = {p.name: _sha(p) for p in pasta.iterdir()}

    def explode(p, run):
        raise RuntimeError("bug numa checagem")

    monkeypatch.setattr(runs, "_checagens", explode)
    outro = sintetico(caixa, "b.vcf.gz", reg=list(_demo()[1][:100]))
    with pytest.raises(RuntimeError, match="bug numa checagem"):
        runs.registrar_entrada(rid, "vcf", caminho=outro.as_posix())
    assert runs._carregar(rid)["entradas"] == antes["entradas"]
    assert {p.name: _sha(p) for p in pasta.iterdir()} == arquivos


# ================================================== crítica #5b nome da origem
@pytest.mark.parametrize("nome,ok", [("regioes.txt", False), ("minhas_regioes.bed", True)])
def test_bed_de_origem_sem_sufixo_bed_reprova_pela_rota(api, caixa, cfg, nome, ok):
    """Um BED 1-based salvo como '.txt' virava 'regioes.bed' na cópia e passava a ser lido
    0-based em silêncio (cada região desloca 1 pb). A regra de nome roda também sobre a
    ORIGEM no modo caminho. Controle: a mesma origem com '.bed'."""
    origem = caixa / nome
    origem.write_bytes(BED_DEMO.read_bytes())
    rid = _run(api)
    r = _por_caminho(api, rid, "regioes", origem)
    reg = r.json()["entradas"]["regioes"]
    assert r.status_code == 200 and reg["caminho"].endswith("/regioes.bed")
    assert reg["veredito"]["ok"] is ok, reg["veredito"]
    if not ok:
        assert any(f.startswith("nome: arquivo de origem") for f in reg["veredito"]["falhas"])


# ============================================ crítica #1 nos irmãos: alvo do índice
def _indice_aponta_para(monkeypatch, indice: Path, alvo: Path) -> None:
    """`indice` passa a resolver para `alvo`: symlink de verdade quando o sistema deixa (no
    container); no host Windows sem o privilégio, o mesmo efeito simulado no Path.resolve —
    o portão só enxerga o caminho resolvido."""
    try:
        indice.symlink_to(alvo)
        return
    except OSError:
        pass
    indice.write_bytes(b"\x00" * 8)  # o lexists do irmão tem de ver algo ali
    original = Path.resolve

    def resolve(self, strict=False):
        if os.path.normcase(str(self)) == os.path.normcase(str(indice)):
            return original(alvo, strict=strict)
        return original(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve)


@pytest.mark.parametrize("alvo,aceito", [("amostras_racas.csv", False),
                                         ("Chr14-Run8-TAUIND-public.building.vcf.gz.tbi", False),
                                         ("real.vcf.gz.tbi", True)],
                         ids=["csv_da_raiz", "indice_em_construcao", "controle_outro_indice"])
def test_indice_que_resolve_para_arquivo_que_nao_e_indice_e_recusado(api, raiz, monkeypatch, alvo, aceito):
    """Crítica #1, "nos irmãos, só os sufixos de IRMAOS": o nome do índice é derivado do
    principal, mas o arquivo RESOLVIDO podia ser qualquer um da raiz — um `x.vcf.gz.tbi` que
    aponta para o CSV de amostras gravava o sha256 e o tamanho do CSV no run.json (e no GET).
    Agora o alvo resolvido também tem de ter o sufixo do índice e não estar em construção: o
    mesmo 422 de um arquivo inexistente. Controle: apontar para outro índice da raiz passa
    no portão de caminho (quem julga o conteúdo é o validador)."""
    vcf = sintetico(raiz, "x.vcf.gz", indice=None)
    destino = raiz / alvo
    if destino.name.endswith(".tbi"):
        real = sintetico(raiz, destino.name[:-len(".tbi")])
        assert Path(f"{real}.tbi") == destino
    else:
        destino.write_bytes(b"amostra,raca\n" + b"S1,Angus\n" * 40)
    _indice_aponta_para(monkeypatch, Path(f"{vcf}.tbi"), destino)
    rid = _run(api)
    r = _por_caminho(api, rid, "vcf", vcf)
    if aceito:
        # (o sha do registro é o de quem lê `x.vcf.gz.tbi`; na simulação, o do marcador)
        assert r.status_code == 200 and ".tbi" in runs._carregar(rid)["entradas"]["vcf"]["irmaos"], r.text
        return
    falta = _por_caminho(api, rid, "vcf", raiz / "nao_existe.vcf.gz")
    assert r.status_code == 422 and r.json() == falta.json(), r.text
    assert _sha(destino) not in r.text and str(destino.stat().st_size) not in r.text
    assert "vcf" not in runs._carregar(rid)["entradas"]


# ============================== crítica #6 pela rota: binário truncado não vira 500
@pytest.mark.parametrize("modo", ["copia", "referencia"])
@pytest.mark.parametrize("corte", [40, 200, 5000, None], ids=["40B", "200B", "5000B", "controle_inteiro"])
def test_vcf_truncado_reprova_pela_rota_sem_500(api, caixa, raiz, modo, corte):
    """Crítica #6: uma checagem (ou o validador) que levantasse exceção sobre um binário
    truncado virava 500 no meio do registro. Pela rota, nos dois modos, o truncado dá 200
    com o veredito e as 3 checagens REPROVADOS, e o plano 409 entradas_reprovadas — nunca
    exceção. Controle: o arquivo inteiro aprova tudo."""
    pasta = caixa if modo == "copia" else raiz
    base = sintetico(pasta, "base.vcf.gz")
    if corte is None:
        vcf = base
    else:
        vcf = pasta / "t.vcf.gz"
        vcf.write_bytes(base.read_bytes()[:corte])
        tbi = Path(f"{base}.tbi").read_bytes()
        Path(f"{vcf}.tbi").write_bytes(tbi[:min(corte // 4, len(tbi) - 30)])
    rid = _run(api)
    _sobe_bed(api, rid)
    r = _por_caminho(api, rid, "vcf", vcf)
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["entradas"]["vcf"]["modo"] == modo
    checagens = {c["formato"]: c["ok"] for c in runs._carregar(rid)["checagens"]}
    assert set(checagens) == {"vcf_tem_info_af", "vcf_contigs_ars_ucd", "vcf_e_bed"}, checagens
    if corte is None:
        assert corpo["entradas"]["vcf"]["veredito"]["ok"] and all(checagens.values()), checagens
        assert api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {}}).status_code == 200
        return
    assert not corpo["entradas"]["vcf"]["veredito"]["ok"] and not any(checagens.values()), checagens
    p = api.post(f"/api/v1/runs/{rid}/plano", json={"escolhas": {}})
    assert p.status_code == 409 and _erro(p) == "entradas_reprovadas"
