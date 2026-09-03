"""doctor.py — diagnóstico da plataforma (v0, Fase 1).

Herda a semântica do reset.py da epigenome_platform (status/logs/soft) e o
diagnóstico da aba Scripts, sem repetir o bug de relançar variante morta:
o doctor NÃO escolhe app — existe um único entry point.

Uso: python -m omica doctor [--fix-soft]
Saída: uma linha por checagem (OK/AVISO/FALHA) + código de saída 1 se houver FALHA.
"""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass

from omica import __version__
from omica.config import REPO_ROOT, get_config
from omica.jobs.guarda_ram import uso_ram_pct
from omica.registry import load_registry

OK, AVISO, FALHA = "OK", "AVISO", "FALHA"


@dataclass
class Checagem:
    nome: str
    nivel: str
    detalhe: str


def _docker_vivo() -> Checagem:
    if not shutil.which("docker"):
        return Checagem("Docker", AVISO, "binário docker não encontrado (ok dentro do container)")
    try:
        r = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                           capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            return Checagem("Docker", OK, f"server {r.stdout.strip()}")
        return Checagem("Docker", FALHA, r.stderr.strip()[:120] or "docker info falhou")
    except subprocess.TimeoutExpired:
        return Checagem("Docker", FALHA, "docker info não respondeu em 15s")


def _dados() -> list[Checagem]:
    cfg = get_config()
    itens: list[Checagem] = []
    try:
        cfg.ensure_dirs()
        teste = cfg.work_dir / ".doctor_write_test"
        teste.write_text(str(time.time()), encoding="utf-8")
        teste.unlink()
        itens.append(Checagem("Dados (OMICA_DATA)", OK, f"{cfg.data_dir} gravável"))
    except OSError as e:
        itens.append(Checagem("Dados (OMICA_DATA)", FALHA, f"{cfg.data_dir}: {e}"))
    if cfg.genome_data.exists():
        itens.append(Checagem("Dados pesados (GENOME_DATA)", OK, str(cfg.genome_data)))
    else:
        itens.append(Checagem("Dados pesados (GENOME_DATA)", FALHA, f"{cfg.genome_data} não montado"))
    return itens


def _ram() -> Checagem:
    cfg = get_config()
    pct = uso_ram_pct()
    nivel = OK if pct < cfg.ram_max_pct else AVISO
    return Checagem("RAM do sistema", nivel, f"{pct:.0f}% em uso (limite p/ novos jobs: {cfg.ram_max_pct}%)")


def _manifests() -> Checagem:
    reg = load_registry()
    if reg.erros:
        detalhes = "; ".join(f"{k}: {v}" for k, v in reg.erros.items())
        return Checagem("Manifests dos módulos", FALHA, detalhes[:200])
    return Checagem("Manifests dos módulos", OK, f"{len(reg.modules)} módulos válidos")


def _redis() -> Checagem:
    cfg = get_config()
    if not cfg.redis_url:
        return Checagem("Fila (Redis)", AVISO, "OMICA_REDIS_URL vazio — fallback de thread local")
    try:
        from redis import Redis

        Redis.from_url(cfg.redis_url, socket_connect_timeout=2).ping()
        return Checagem("Fila (Redis)", OK, cfg.redis_url)
    except Exception as e:
        return Checagem("Fila (Redis)", AVISO, f"sem resposta ({e.__class__.__name__}) — fallback de thread")


def _skills() -> Checagem:
    cfg = get_config()
    indice = REPO_ROOT / "data" / "skills_index.json"
    fonte = cfg.skills_dir / "INDICE_CONSOLIDADO.md"
    if not cfg.skills_dir.exists():
        return Checagem("Biblioteca de skills", AVISO, f"{cfg.skills_dir} indisponível")
    if not indice.exists():
        return Checagem("Biblioteca de skills", AVISO, "skills_index.json ainda não gerado (python -m omica reindex)")
    try:
        n = len(json.loads(indice.read_text(encoding="utf-8"))["skills"])
    except Exception as e:
        return Checagem("Biblioteca de skills", FALHA, f"skills_index.json ilegível: {e}")
    if fonte.exists() and fonte.stat().st_mtime > indice.stat().st_mtime:
        return Checagem("Biblioteca de skills", AVISO, f"{n} skills, mas o índice-fonte é mais novo — rode reindex")
    return Checagem("Biblioteca de skills", OK, f"{n} skills indexadas")


def _porta() -> Checagem:
    cfg = get_config()
    try:
        with socket.create_connection(("127.0.0.1", cfg.porta), timeout=1):
            return Checagem(f"Porta {cfg.porta}", OK, "respondendo (plataforma no ar)")
    except OSError:
        return Checagem(f"Porta {cfg.porta}", AVISO, "nada escutando (plataforma parada?)")


def _null_bytes() -> Checagem:
    """Herança do watchdog do epigenoma: OneDrive já corrompeu código com null-bytes."""
    suspeitos = []
    for py in (REPO_ROOT / "omica").rglob("*.py"):
        if b"\x00" in py.read_bytes():
            suspeitos.append(py.name)
    if suspeitos:
        return Checagem("Integridade (null-bytes)", FALHA, ", ".join(suspeitos[:5]))
    return Checagem("Integridade (null-bytes)", OK, "nenhum arquivo .py corrompido")


def _workers() -> Checagem:
    """Workers RQ vivos por fila (via Redis). Sem Redis a checagem vira aviso."""
    cfg = get_config()
    if not cfg.redis_url:
        return Checagem("Workers RQ", AVISO, "sem Redis — fallback de thread (fila default)")
    try:
        from redis import Redis
        from rq import Worker

        conexao = Redis.from_url(cfg.redis_url, socket_connect_timeout=2)
        filas: dict[str, int] = {}
        for w in Worker.all(connection=conexao):
            for q in w.queue_names():
                filas[q] = filas.get(q, 0) + 1
        esperadas = {"omica", "bioinfo", "vep", "methyl", "ml"}
        faltando = sorted(esperadas - set(filas))
        detalhe = ", ".join(f"{q}×{n}" for q, n in sorted(filas.items())) or "nenhum"
        if faltando:
            return Checagem("Workers RQ", AVISO, f"{detalhe} — sem worker: {', '.join(faltando)}")
        return Checagem("Workers RQ", OK, detalhe)
    except Exception as e:
        return Checagem("Workers RQ", AVISO, f"não consultável ({e.__class__.__name__})")


def _referencias() -> list[Checagem]:
    """Referências pesadas que as análises pressupõem (fora do repo, em E:\\)."""
    cfg = get_config()
    itens = []
    alvos = [
        ("Cache VEP (release 110, ARS-UCD1.2)", cfg.data_dir / "refs" / "vep" / "bos_taurus"),
        ("Índice Salmon bovino", cfg.data_dir / "refs" / "salmon_bostau"),
        ("Banco SnpEff ARS-UCD1.3.113", cfg.genome_data / "snpeff_data" / "ARS-UCD1.3.113"),
        ("VCFs 1000 Bull (Run 8)", cfg.genome_data / "1000bg_vcf"),
        ("Listas de raça (BioSamples)", cfg.genome_data / "1000bg_metadata"),
    ]
    for nome, caminho in alvos:
        if caminho.exists():
            itens.append(Checagem(nome, OK, str(caminho)))
        else:
            itens.append(Checagem(nome, AVISO, f"{caminho} ausente — análises que o usam falharão"))
    return itens


def _glossario() -> Checagem:
    caminho = REPO_ROOT / "didatica" / "glossario.json"
    if not caminho.exists():
        return Checagem("Glossário", AVISO, "didatica/glossario.json ausente")
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        return Checagem("Glossário", OK, f"{len(dados['termos'])} termos")
    except Exception as e:
        return Checagem("Glossário", FALHA, f"ilegível: {e}")


def rodar(checar_docker: bool = True) -> list[Checagem]:
    checagens: list[Checagem] = [Checagem("Versão", OK, f"omica {__version__}")]
    if checar_docker:
        checagens.append(_docker_vivo())
    checagens.extend(_dados())
    checagens.append(_ram())
    checagens.append(_manifests())
    checagens.append(_redis())
    checagens.append(_workers())
    checagens.extend(_referencias())
    checagens.append(_skills())
    checagens.append(_glossario())
    checagens.append(_porta())
    checagens.append(_null_bytes())
    return checagens


def main(fix_soft: bool = False) -> int:
    if fix_soft:
        print("fix-soft: docker compose restart + reindex das skills")
        subprocess.run(["docker", "compose", "restart"], cwd=REPO_ROOT, check=False)
        from omica.skills.build_index import main as reindex

        reindex()
    checagens = rodar()
    largura = max(len(c.nome) for c in checagens) + 2
    houve_falha = False
    for c in checagens:
        marca = {"OK": "✅", "AVISO": "⚠️ ", "FALHA": "❌"}[c.nivel]
        print(f"{marca} {c.nome.ljust(largura)} {c.detalhe}")
        houve_falha = houve_falha or c.nivel == FALHA
    return 1 if houve_falha else 0
