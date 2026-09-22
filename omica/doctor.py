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
    """Alguém escutando na porta NÃO é a mesma coisa que a ômica no ar.

    A versão anterior dava OK para qualquer processo na 8710 — inclusive outro
    serviço do autor que tivesse tomado a porta —, e o usuário ficava procurando
    a plataforma numa página que não era a dela. Agora o diagnóstico exige a
    assinatura do /saude da própria ômica.
    """
    import time
    import urllib.error
    import urllib.request

    cfg = get_config()
    try:
        with socket.create_connection(("127.0.0.1", cfg.porta), timeout=1):
            pass
    except OSError:
        return Checagem(f"Porta {cfg.porta}", AVISO, "nada escutando (plataforma parada?)")
    # O Docker publica a porta ANTES de o uvicorn aceitar conexão: durante o
    # startup, "alguém escuta mas /saude não responde" é normal, não invasão de
    # porta. Só é FALHA quando algo RESPONDE e a resposta não é da ômica.
    dados = None
    erro = None
    for tentativa in range(2):
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{cfg.porta}/saude", timeout=5) as resposta:
                dados = json.loads(resposta.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            return Checagem(f"Porta {cfg.porta}", FALHA,
                            f"respondeu HTTP {e.code} em /saude — outro serviço nesta porta")
        except Exception as e:  # conexão recusada, timeout, JSON incompleto
            erro = e
            if tentativa == 0:
                time.sleep(2)
    if dados is None:
        return Checagem(f"Porta {cfg.porta}", AVISO,
                        f"ocupada, mas /saude não respondeu em 2 tentativas "
                        f"({erro.__class__.__name__}) — plataforma subindo?")
    if "versao" not in dados or "modulos_validos" not in dados:
        return Checagem(f"Porta {cfg.porta}", FALHA, "ocupada por outro serviço (/saude alheio)")
    return Checagem(f"Porta {cfg.porta}", OK,
                    f"ômica {dados['versao']} respondendo ({dados['modulos_validos']} módulos)")


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

        from datetime import datetime, timezone

        conexao = Redis.from_url(cfg.redis_url, socket_connect_timeout=2)
        filas: dict[str, int] = {}
        zumbis: list[str] = []
        agora = datetime.now(timezone.utc)
        for w in Worker.all(connection=conexao):
            # Registro no Redis NÃO prova worker vivo: um container morto na marra
            # deixa o registro para trás e o diagnóstico dizia "OK" com fila parada.
            batida = getattr(w, "last_heartbeat", None)
            if batida is not None:
                if batida.tzinfo is None:
                    batida = batida.replace(tzinfo=timezone.utc)
                if (agora - batida).total_seconds() > 120:
                    zumbis.append(w.name)
                    continue
            for q in w.queue_names():
                filas[q] = filas.get(q, 0) + 1
        esperadas = {"omica", "bioinfo", "vep", "methyl", "ml"}
        faltando = sorted(esperadas - set(filas))
        detalhe = ", ".join(f"{q}×{n}" for q, n in sorted(filas.items())) or "nenhum"
        if zumbis:
            detalhe += f" (ignorados {len(zumbis)} sem batida há >2 min)"
        if faltando:
            return Checagem("Workers RQ", FALHA,
                            f"{detalhe} — SEM worker vivo: {', '.join(faltando)}")
        return Checagem("Workers RQ", OK, detalhe)
    except Exception as e:
        return Checagem("Workers RQ", AVISO, f"não consultável ({e.__class__.__name__})")


def _referencias() -> list[Checagem]:
    """Referências pesadas que as análises pressupõem (fora do repo, em E:\\).

    Conferir só a existência do DIRETÓRIO era falso-verde: pasta vazia (download
    interrompido, volume montado sem conteúdo, cache de outra release) passava
    como OK e a falha só aparecia no meio da análise. Cada alvo agora exige um
    artefato INTERNO que prova que a referência está utilizável — inclusive o pin
    de época do cache VEP (110 = ARS-UCD1.2; o 111+ já é ARS-UCD2.0).
    """
    cfg = get_config()
    alvos = [
        ("Cache VEP (release 110, ARS-UCD1.2)",
         cfg.data_dir / "refs" / "vep" / "bos_taurus",
         lambda p: any(d.is_dir() and d.name.startswith("110_") for d in p.iterdir()),
         "sem subpasta 110_* (cache de outra release = outro assembly)"),
        ("Índice Salmon bovino", cfg.data_dir / "refs" / "salmon_bostau",
         lambda p: (p / "info.json").exists(),
         "sem info.json (índice incompleto)"),
        ("Banco SnpEff ARS-UCD1.3.113",
         cfg.genome_data / "snpeff_data" / "ARS-UCD1.3.113",
         lambda p: (p / "snpEffectPredictor.bin").exists(),
         "sem snpEffectPredictor.bin (banco não construído)"),
        ("VCFs 1000 Bull (Run 8)", cfg.genome_data / "1000bg_vcf",
         lambda p: any(p.glob("*.bcf")) or any(p.glob("*.vcf.gz")),
         "diretório sem nenhum VCF/BCF"),
        ("Listas de raça (BioSamples)", cfg.genome_data / "1000bg_metadata",
         lambda p: any(p.glob("samples_*.txt")),
         "sem listas samples_*.txt"),
    ]
    itens = []
    for nome, caminho, conteudo_ok, queixa in alvos:
        if not caminho.exists():
            itens.append(Checagem(nome, AVISO, f"{caminho} ausente — análises que o usam falharão"))
            continue
        try:
            completo = conteudo_ok(caminho)
        except OSError as e:
            itens.append(Checagem(nome, FALHA, f"{caminho} ilegível ({e.__class__.__name__})"))
            continue
        itens.append(Checagem(nome, OK, str(caminho)) if completo
                     else Checagem(nome, FALHA, f"{caminho} existe mas {queixa}"))
    return itens


def _resultados_velhos() -> Checagem:
    """Resultado publicado mais velho que o script que o produziu.

    O selo de workflow só cobre WORKFLOWS. Scripts avulsos — perfil_tss,
    motifs_homer, fst_racas — publicam em results_spec sem passar por selo
    nenhum: depois da auditoria de 2026-09-14, a figura do metaperfil TSS
    continuava sendo a espelhada (fita − invertida) e nada avisava. Comparar
    mtime do artefato com o do script (e com o do _comum.py, que todos importam)
    pega essa classe inteira de desatualização.
    """
    from omica.registry import MODULES_DIR, load_registry

    cfg = get_config()
    velhos: set[str] = set()
    total = 0
    for m in load_registry().modules:
        comum = MODULES_DIR / m.id / "scripts" / "_comum.py"
        base = comum.stat().st_mtime if comum.exists() else 0.0
        for spec in m.scripts:
            alvo = next((t for t in spec.cmd.split() if t.endswith((".py", ".R", ".sh"))), None)
            script = (REPO_ROOT / alvo) if alvo else None
            if script is None or not script.exists():
                continue
            corte = max(script.stat().st_mtime, base)
            for saida in spec.outputs:
                rel = saida[len("results/"):] if saida.startswith("results/") else saida
                caminho = cfg.results_dir / rel
                if not caminho.exists():
                    continue
                total += 1
                if caminho.stat().st_mtime < corte:
                    velhos.add(f"{m.id}/{spec.key}")
    if not total:
        return Checagem("Frescor dos resultados", AVISO, "nenhum resultado publicado ainda")
    if velhos:
        amostra = ", ".join(sorted(velhos)[:4])
        return Checagem("Frescor dos resultados", AVISO,
                        f"{len(velhos)} script(s) alterados depois do resultado publicado "
                        f"({amostra}) — re-executar")
    return Checagem("Frescor dos resultados", OK, f"{total} artefato(s) mais novos que o código")


def _selos() -> Checagem:
    """Workflow selado cujo script mudou depois do teste = número publicado sem
    lastro. Antes disso, o selo era texto livre no manifest e nada o invalidava."""
    from omica.registry import selos_vencidos

    try:
        vencidos = selos_vencidos()
    except Exception as e:
        return Checagem("Selos de workflow", FALHA, f"não verificável: {e}")
    if not vencidos:
        return Checagem("Selos de workflow", OK, "todos batem com os scripts atuais")
    detalhe = "; ".join(f"{mod}/{wf}: {motivo}" for mod, wf, motivo in vencidos[:4])
    return Checagem("Selos de workflow", AVISO, f"{len(vencidos)} vencido(s) — {detalhe}")


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
    checagens.append(_selos())
    checagens.append(_resultados_velhos())
    checagens.append(_porta())
    checagens.append(_null_bytes())
    return checagens


def _jobs_em_andamento(max_idade_h: float = 6.0) -> tuple[list[str], list[str]]:
    """(ativos, órfãos). Um status.json travado em running/queued por um worker
    que morreu ficaria bloqueando o --fix-soft para sempre — o próprio conserto
    criaria um impasse sem saída. Acima de `max_idade_h` sem escrita, o job é
    tratado como órfão: não bloqueia, mas é reportado para o usuário decidir."""
    import time as _t

    cfg = get_config()
    ativos: list[str] = []
    orfaos: list[str] = []
    if not cfg.jobs_dir.exists():
        return ativos, orfaos
    agora = _t.time()
    for status in cfg.jobs_dir.glob("*/status.json"):
        try:
            dados = json.loads(status.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if dados.get("status") not in ("running", "queued"):
            continue
        nome = dados.get("job_id", status.parent.name)
        idade_h = (agora - status.stat().st_mtime) / 3600
        (orfaos if idade_h > max_idade_h else ativos).append(nome)
    return ativos, orfaos


def main(fix_soft: bool = False) -> int:
    if fix_soft:
        # `docker compose restart` MATA o que estiver rodando. A versão anterior
        # reiniciava sem olhar e ainda ignorava o código de retorno: uma análise de
        # horas (VEP, HOMER, nf-core) morria calada por um comando de "conserto".
        ativos, orfaos = _jobs_em_andamento()
        if ativos:
            print(f"❌ fix-soft ABORTADO: {len(ativos)} job(s) em andamento "
                  f"({', '.join(ativos[:5])}). Espere terminar ou cancele antes.")
            return 2
        if orfaos:
            print(f"⚠️  {len(orfaos)} job(s) marcados running/queued há mais de 6 h "
                  f"({', '.join(orfaos[:5])}) — tratados como órfãos e ignorados. "
                  "Apague a pasta em work/jobs/ se não forem mais necessários.")
        print("fix-soft: docker compose restart + reindex das skills")
        r = subprocess.run(["docker", "compose", "restart"], cwd=REPO_ROOT, check=False)
        if r.returncode != 0:
            print(f"⚠️  docker compose restart falhou (código {r.returncode})")
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
