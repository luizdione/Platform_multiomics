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
    "nav_professor": {"pt": "Professor Ômico", "en": "Omics Professor"},
    "fluxograma": {"pt": "Fluxograma do pipeline (gerado do manifest)", "en": "Pipeline flowchart (from manifest)"},
    "workflows_modulo": {"pt": "Workflows (passos encadeados)", "en": "Workflows (chained steps)"},
    "passo": {"pt": "Passo", "en": "Step"},
    "passos": {"pt": "passos", "en": "steps"},
    "testado_em": {"pt": "testado de ponta a ponta em", "en": "end-to-end tested on"},
    "atualiza_sozinho": {"pt": "esta página atualiza sozinha", "en": "this page auto-refreshes"},
    "workflow_didatica": {
        "pt": "Cada passo consome o arquivo de saída do passo anterior — a validação do encadeamento acontece no manifest, antes de qualquer execução.",
        "en": "Each step consumes the previous step's output file — chaining is validated in the manifest, before anything runs.",
    },
    "professor_titulo": {"pt": "Professor Ômico", "en": "Omics Professor"},
    "professor_sub": {
        "pt": "Escreva o que você quer analisar, em português. O Professor consulta o catálogo da plataforma e propõe o workflow ou análise certa — nada roda sem o seu clique, e ele só escolhe do catálogo (nunca inventa comando).",
        "en": "Describe what you want to analyze. The Professor consults the platform catalogue and proposes the right workflow — nothing runs without your click, and it only picks from the catalogue.",
    },
    "motor_ok": {"pt": "motor local no ar", "en": "local engine up"},
    "motor_off": {"pt": "motor local fora do ar — inicie com", "en": "local engine down — start with"},
    "professor_pergunta": {"pt": "O que você quer analisar?", "en": "What do you want to analyze?"},
    "professor_placeholder": {
        "pt": "ex.: quero extrair as variantes do painel demo e ver quais têm efeito funcional",
        "en": "e.g.: extract the demo panel variants and see functional effects",
    },
    "professor_arquivo": {"pt": "Arquivo de entrada", "en": "Input file"},
    "opcional": {"pt": "opcional", "en": "optional"},
    "professor_perguntar": {"pt": "🎓 Perguntar ao Professor", "en": "🎓 Ask the Professor"},
    "professor_plano": {"pt": "Plano proposto", "en": "Proposed plan"},
    "professor_executar": {"pt": "Executar", "en": "Run"},
    "professor_confianca": {
        "pt": "O plano acima foi validado contra o catálogo; a execução usa as mesmas rotas determinísticas dos módulos.",
        "en": "The plan was validated against the catalogue; execution uses the modules' deterministic routes.",
    },
    "professor_invalido": {"pt": "O modelo propôs algo fora do catálogo — plano rejeitado", "en": "The model proposed something outside the catalogue — plan rejected"},
    "professor_vitrine": {"pt": "Workflows prontos e testados nesta máquina", "en": "Ready workflows, tested on this machine"},
    "professor_sem_testados": {"pt": "Nenhum workflow testado ainda.", "en": "No tested workflows yet."},
    "modelo": {"pt": "modelo", "en": "model"},
    "abrir_relatorio": {"pt": "Abrir relatório completo (nova aba)", "en": "Open full report (new tab)"},
    "scripts_modulo": {"pt": "Análises deste módulo", "en": "Module analyses"},
    "resultados_modulo": {"pt": "Resultados (lidos do disco agora)", "en": "Results (read from disk now)"},
    "rodar": {"pt": "▶ Rodar", "en": "▶ Run"},
    "exibindo": {"pt": "Exibindo", "en": "Showing"},
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
