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
    "nav_skills": {"pt": "Skills", "en": "Skills"},
    "skills_titulo": {"pt": "Biblioteca de skills de bioinformática", "en": "Bioinformatics skills library"},
    "skills_sub": {
        "pt": "As 815 receitas da biblioteca consolidada (3 repositórios públicos, deduplicados), pesquisáveis offline em português ou inglês. Clique numa skill para ler o passo a passo completo.",
        "en": "The 815 recipes from the consolidated library (3 public repos, deduplicated), searchable offline in Portuguese or English. Click a skill for the full walkthrough.",
    },
    "skills_busca": {
        "pt": "Buscar em português ou inglês: uma palavra, a ferramenta ou a frase inteira (ex.: metilação, expressão diferencial, deseq2)…",
        "en": "Search in Portuguese or English: a word, a tool or a whole sentence (e.g. methylation, differential expression, deseq2)…",
    },
    "skills_n": {"pt": "skill(s)", "en": "skill(s)"},
    "skills_mostrando": {"pt": "mostrando", "en": "showing"},
    "skills_traducao": {"pt": "glossário PT→EN", "en": "PT→EN glossary"},
    "skills_ignorado": {"pt": "ignorado (palavra de ligação)", "en": "ignored (filler word)"},
    "skills_sem_casar": {"pt": "sem nenhuma skill", "en": "matched nothing"},
    "skills_truncado": {"pt": "frase longa: só os primeiros termos entraram na busca",
                        "en": "long query: only the first terms were searched"},
    "skills_parcial": {
        "pt": "Nenhuma skill casa TODOS os termos — abaixo, as que casam os termos mais específicos",
        "en": "No skill matches ALL terms — below, those matching the most specific ones",
    },
    "skills_casou": {"pt": "casou", "en": "matched"},
    "skills_erro": {"pt": "busca indisponível", "en": "search unavailable"},
    "skills_sem_glossario": {
        "pt": "Glossário de busca PT→EN ausente: por enquanto a busca só casa em inglês. No host: python -m omica reindex.",
        "en": "PT→EN search glossary missing: for now the search only matches English. On the host: python -m omica reindex.",
    },
    "skills_plugin": {
        "pt": "Estas são receitas para LER. Para executar uma delas com roteamento automático, o plugin Bioinfo (assistente de código do autor) usa este mesmo índice: /bioinfo <o que você quer fazer>.",
        "en": "These are recipes to READ. To run one with automatic routing, the Bioinfo plugin (the author's coding assistant) uses this same index: /bioinfo <what you want to do>.",
    },
    "card_quando": {"pt": "Quando usar", "en": "When to use"},
    "card_regras": {"pt": "Regras", "en": "Rules"},
    "card_guias": {"pt": "guia(s) deste domínio", "en": "guide(s) for this domain"},
    "origem": {"pt": "origem", "en": "source"},
    "skill_modulo": {
        "pt": "A plataforma já tem análise pronta neste domínio — antes de seguir a receita, veja o módulo",
        "en": "The platform already has a ready-made analysis in this domain — before following the recipe, see module",
    },
    "skills_relacionadas": {"pt": "Skills relacionadas na biblioteca", "en": "Related skills in the library"},
    "skills_relacionadas_sub": {
        "pt": "Receitas da biblioteca nos domínios deste módulo — para o que ele ainda não faz, ou para entender o método por trás do que ele faz.",
        "en": "Library recipes in this module's domains — for what it does not do yet, or to understand the method behind what it does.",
    },
    "professor_biblioteca": {
        "pt": "Fora do catálogo — a biblioteca de skills tem receita",
        "en": "Outside the catalogue — the skills library has a recipe",
    },
    "professor_biblioteca_sub": {
        "pt": "Busca determinística pela sua frase (o modelo não participa). São receitas para ler: a plataforma não as executa.",
        "en": "Deterministic search on your sentence (the model takes no part). These are recipes to read: the platform does not run them.",
    },
    "professor_ver_todas": {"pt": "ver na biblioteca", "en": "see in the library"},
    "professor_dica_skills": {
        "pt": "Não está no catálogo? A biblioteca de skills responde mesmo com o motor desligado:",
        "en": "Not in the catalogue? The skills library answers even with the engine off:",
    },
    "dominios": {"pt": "domínios", "en": "domains"},
    "dominio": {"pt": "Domínio", "en": "Domain"},
    "todos_dominios": {"pt": "Todos os domínios", "en": "All domains"},
    "descricao": {"pt": "Descrição", "en": "Description"},
    "gerado_em": {"pt": "índice gerado em", "en": "index generated on"},
    "glossario": {"pt": "Glossário", "en": "Glossary"},
    "termos": {"pt": "termos", "en": "terms"},
    "origens": {"pt": "origens", "en": "sources"},
    "diagnostico": {"pt": "Diagnóstico", "en": "Diagnostics"},
    "diagnostico_sub": {"pt": "As mesmas checagens do CLI", "en": "Same checks as the CLI"},
    "checagem": {"pt": "Checagem", "en": "Check"},
    "detalhe": {"pt": "Detalhe", "en": "Detail"},
    "executar_passo": {"pt": "▶ Executar este passo", "en": "▶ Run this step"},
    "modo_aula": {"pt": "Modo aula — passos executáveis", "en": "Class mode — runnable steps"},
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
