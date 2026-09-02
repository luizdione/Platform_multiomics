"""i18n.py — internacionalização PT/EN, herdada da genomes_platform (ui/i18n.py)
e adaptada de Streamlit para FastAPI/Jinja2 (server-side, sem estado de sessão).

Default PT-BR (público: autor + alunos IFRJ). t(chave, lang) com fallback:
chave sem tradução no idioma pedido cai para PT; chave inexistente devolve a
própria chave entre colchetes (visível de propósito — nunca falha silenciosa).
"""
from __future__ import annotations

DEFAULT_LANG = "pt"
IDIOMAS = ("pt", "en")

TRANSLATIONS: dict[str, dict[str, str]] = {
    "titulo_plataforma": {"pt": "ômica — plataforma de ômicas", "en": "ômica — omics platform"},
    "subtitulo": {
        "pt": "Genômica, epigenômica e transcriptômica num só lugar — localhost, sem nuvem, com camada didática.",
        "en": "Genomics, epigenomics and transcriptomics in one place — localhost, no cloud, teaching-first.",
    },
    "nav_inicio": {"pt": "Início", "en": "Home"},
    "nav_jobs": {"pt": "Jobs", "en": "Jobs"},
    "nav_ajuda": {"pt": "Ajuda", "en": "Help"},
    "modulos": {"pt": "Módulos de ômica", "en": "Omics modules"},
    "em_construcao": {"pt": "Em construção", "en": "Under construction"},
    "fases": {"pt": "fases", "en": "phases"},
    "etapas": {"pt": "etapas", "en": "steps"},
    "saude": {"pt": "Saúde da plataforma", "en": "Platform health"},
    "versao": {"pt": "Versão", "en": "Version"},
    "ram_sistema": {"pt": "RAM do sistema", "en": "System RAM"},
    "fila_backend": {"pt": "Backend da fila", "en": "Queue backend"},
    "manifests_ok": {"pt": "manifests válidos", "en": "valid manifests"},
    "erros_manifests": {"pt": "Erros de manifest", "en": "Manifest errors"},
    "jobs_titulo": {"pt": "Jobs da plataforma", "en": "Platform jobs"},
    "rodar_demo": {"pt": "▶ Rodar job demo", "en": "▶ Run demo job"},
    "demo_explica": {
        "pt": "O job demo atravessa a infraestrutura inteira (fila → execução → log → resultado) sem tocar em dados reais. Use-o para conferir que a plataforma está saudável.",
        "en": "The demo job crosses the whole infrastructure (queue → run → log → result) without touching real data. Use it to check platform health.",
    },
    "sem_jobs": {"pt": "Nenhum job executado ainda.", "en": "No jobs run yet."},
    "job": {"pt": "Job", "en": "Job"},
    "status": {"pt": "Status", "en": "Status"},
    "atualizado": {"pt": "Atualizado", "en": "Updated"},
    "console": {"pt": "Console (ao vivo)", "en": "Console (live)"},
    "resultado": {"pt": "Resultado", "en": "Result"},
    "sem_resultado": {
        "pt": "Sem arquivo de resultado — a UI nunca inventa número.",
        "en": "No result file — the UI never makes numbers up.",
    },
    "recusado_ram": {"pt": "Job recusado pela guarda de RAM", "en": "Job refused by the RAM guard"},
    "voltar": {"pt": "← Voltar", "en": "← Back"},
    "ajuda_titulo": {"pt": "Ajuda e tutoriais", "en": "Help & tutorials"},
    "status_queued": {"pt": "Na fila", "en": "Queued"},
    "status_running": {"pt": "Rodando", "en": "Running"},
    "status_done": {"pt": "Concluído", "en": "Done"},
    "status_error": {"pt": "Erro", "en": "Error"},
    "rodape": {
        "pt": "Plataforma local (zero nuvem, zero LLM no caminho crítico). Dados pesados em E:\\data\\omica.",
        "en": "Local platform (no cloud, no LLM in the critical path). Heavy data on E:\\data\\omica.",
    },
}


def t(chave: str, lang: str = DEFAULT_LANG) -> str:
    entrada = TRANSLATIONS.get(chave)
    if not entrada:
        return f"[{chave}]"
    return entrada.get(lang) or entrada.get(DEFAULT_LANG) or f"[{chave}]"


def normalizar_lang(valor: str | None) -> str:
    return valor if valor in IDIOMAS else DEFAULT_LANG
