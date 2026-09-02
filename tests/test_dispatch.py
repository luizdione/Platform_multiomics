"""Dispatch: runtime → fila certa, templates de cmd, e recusas explícitas
(fila especializada sem Redis; runtimes de fases futuras)."""
import pytest

from omica.jobs import dispatch
from omica.jobs.queue import FilaIndisponivel


def test_fila_por_runtime():
    assert dispatch.fila_para_runtime("image:omica-bioinfo") == "bioinfo"
    assert dispatch.fila_para_runtime("image:omica-worker") == "omica"


def test_runtime_de_fase_futura_recusa_com_mensagem_clara():
    with pytest.raises(dispatch.RuntimeIndisponivel, match="fase 5"):
        dispatch.fila_para_runtime("conda:datascience")
    with pytest.raises(dispatch.RuntimeIndisponivel, match="fase 4"):
        dispatch.fila_para_runtime("profile:nfcore")
    with pytest.raises(dispatch.RuntimeIndisponivel, match="sem fila"):
        dispatch.fila_para_runtime("image:imagem-fantasma")


def test_montar_cmd_substitui_dados(cfg):
    _, spec = dispatch._resolver("genomica", "extracao_demo")
    cmd = dispatch.montar_cmd(spec)
    assert str(cfg.data_dir) in cmd
    assert "{dados}" not in cmd


def test_script_bioinfo_sem_redis_recusa(cfg):
    # Ambiente de teste não tem Redis: fila "bioinfo" deve recusar com
    # instrução acionável, nunca rodar bcftools no container errado.
    with pytest.raises(FilaIndisponivel, match="docker compose up"):
        dispatch.lancar_script("genomica", "extracao_demo")


def test_script_desconhecido():
    with pytest.raises(KeyError):
        dispatch.lancar_script("genomica", "nao_existe")
