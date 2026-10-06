"""professor.py — Professor Ômico: agente didático local da plataforma.

Motor: modelo LOCAL via Ollama (o mesmo motor do LILO; ver OMICA_OLLAMA_URL /
OMICA_OLLAMA_MODEL). Contrato de segurança e honestidade:
  - o LLM recebe o CATÁLOGO da plataforma (workflows e scripts dos manifests)
    e devolve um PLANO ESTRUTURADO escolhendo do catálogo — ele nunca escreve
    comando de shell e nada roda sem o clique de Executar do usuário;
  - o plano é validado contra o registry (alvo inexistente = plano rejeitado);
  - workflows exibem o selo "testado" apenas quando testado_em/testado_por
    estão preenchidos no manifest (validação real de ponta a ponta).
Sem Ollama no ar, a aba continua útil: mostra os workflows testados e explica
como ligar o motor. Zero LLM no caminho crítico da plataforma.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

import httpx
from pydantic import BaseModel, ValidationError

from omica.config import get_config
from omica.registry import load_registry

TIMEOUT_S = 180  # primeira carga de um modelo 14b pode levar ~1 min

SISTEMA = """Você é o Professor Ômico, o agente didático da plataforma ômica
(bioinformática bovina; público: pesquisador e alunos do IFRJ; responda em PT-BR).
Você recebe uma frase humana (e às vezes um caminho de arquivo) e escolhe, DO
CATÁLOGO ABAIXO, o workflow ou script que melhor responde ao pedido.

REGRAS INEGOCIÁVEIS:
1. Escolha SOMENTE itens do catálogo (ids exatos). Nunca invente análise,
   ferramenta ou comando.
2. Responda APENAS um objeto JSON válido, sem texto fora dele e sem <think>:
   {"acao": "rodar_workflow" | "rodar_script" | "explicar",
    "modulo": "<id do módulo ou vazio>",
    "alvo": "<id do workflow/script ou vazio>",
    "explicacao": "<2 a 5 frases didáticas em PT-BR: o que será feito, qual
                   output de cada passo alimenta o passo seguinte, e o que o
                   resultado significa biologicamente>"}
3. Se o pedido não casa com nada do catálogo, use acao="explicar" e diga o que
   a plataforma ainda não cobre e qual item chega mais perto.
4. Se o usuário citar um arquivo, mencione na explicação como ele entraria —
   mas escolha mesmo assim um item do catálogo.

CATÁLOGO:
{catalogo}
"""


class Plano(BaseModel):
    acao: str
    modulo: str = ""
    alvo: str = ""
    explicacao: str

    def valido(self) -> bool:
        return self.acao in ("rodar_workflow", "rodar_script", "explicar")


def catalogo() -> dict[str, Any]:
    """Catálogo compacto (JSON) dos manifests — o que o Professor pode propor."""
    reg = load_registry()
    modulos = []
    for m in reg.modules:
        modulos.append({
            "modulo": m.id,
            "nome": m.nome_pt,
            "workflows": [
                {"id": w.id, "titulo": w.titulo, "descricao": w.descricao,
                 "passos": w.passos, "testado_em": w.testado_em}
                for w in m.workflows
            ],
            "scripts": [
                {"id": s.key, "titulo": s.titulo, "descricao": s.descricao,
                 "entradas": s.inputs, "saidas": s.outputs}
                for s in m.scripts
            ],
        })
    return {"modulos": modulos}


def workflows_testados() -> list[dict[str, Any]]:
    """Workflows com selo de validação real — a vitrine da aba do Professor.

    O selo só vale enquanto os scripts forem os mesmos que rodaram no E2E: cada
    item traz `vencido` para a UI poder dizer que aquele ✅ está desatualizado,
    em vez de exibir número antigo como se fosse resultado corrente.
    """
    from omica.registry import hash_dos_passos

    reg = load_registry()
    vitrine = []
    for m in reg.modules:
        for w in m.workflows:
            if not w.testado_em:
                continue
            atual = hash_dos_passos(m, w)
            vencido = (not w.hash_scripts) or w.hash_scripts != atual
            vitrine.append({"modulo": m.id, "modulo_nome": m.nome_pt,
                            "icone": m.icone, "wf": w, "vencido": vencido,
                            "motivo_vencido": ("sem hash de referência" if not w.hash_scripts
                                               else "script alterado desde o teste")})
    return vitrine


def fora_do_catalogo(frase: str, limite: int = 5) -> Optional[dict[str, Any]]:
    """Pedido fora do catálogo não precisa terminar em "a plataforma não cobre":
    a biblioteca de skills quase sempre tem a receita.

    Busca DETERMINÍSTICA, a mesma da página /skills — o LLM não participa, e por
    isso ela vale mesmo quando ele erra o plano ou nem responde. São receitas para
    LER: nada aqui vira botão de executar. Devolve None se nada casar.
    """
    from omica.skills import busca

    achado = busca.buscar(frase, limite=limite)
    if not achado["termos"] or not achado["total"]:
        return None
    return {"frase": frase, "total": achado["total"], "modo": achado["modo"],
            "traducao": achado["traducao"], "skills": achado["resultados"]}


def motor_disponivel() -> tuple[bool, str]:
    cfg = get_config()
    try:
        r = httpx.get(f"{cfg.ollama_url}/api/tags", timeout=4)
        r.raise_for_status()
        nomes = [m["name"] for m in r.json().get("models", [])]
        if cfg.ollama_model in nomes:
            return True, cfg.ollama_model
        return bool(nomes), (nomes[0] if nomes else "")
    except Exception:
        return False, ""


def _extrair_json(texto: str) -> dict[str, Any]:
    texto = re.sub(r"<think>.*?</think>", "", texto, flags=re.DOTALL).strip()
    inicio, fim = texto.find("{"), texto.rfind("}")
    if inicio == -1 or fim == -1:
        raise ValueError(f"resposta do modelo sem JSON: {texto[:200]!r}")
    return json.loads(texto[inicio:fim + 1])


def _validar_contra_catalogo(plano: Plano) -> Optional[str]:
    """Devolve mensagem de erro se o plano apontar para algo fora do catálogo."""
    if not plano.valido():
        return f"ação desconhecida: {plano.acao!r}"
    if plano.acao == "explicar":
        return None
    reg = load_registry()
    m = reg.por_id(plano.modulo)
    if m is None:
        return f"módulo fora do catálogo: {plano.modulo!r}"
    if plano.acao == "rodar_workflow" and not any(w.id == plano.alvo for w in m.workflows):
        return f"workflow fora do catálogo: {plano.alvo!r}"
    if plano.acao == "rodar_script" and not any(s.key == plano.alvo for s in m.scripts):
        return f"script fora do catálogo: {plano.alvo!r}"
    return None


def perguntar(frase: str, arquivo: str = "", modelo: str = "") -> dict[str, Any]:
    """Frase humana → plano validado. Nunca executa nada: quem executa é o
    botão da UI, pelas MESMAS rotas determinísticas de módulo."""
    cfg = get_config()
    ok, modelo_ativo = motor_disponivel()
    if not ok:
        return {"status": "motor_indisponivel",
                "detalhe": f"Ollama não respondeu em {cfg.ollama_url} — inicie com `ollama serve`.",
                "biblioteca": fora_do_catalogo(frase)}
    modelo = modelo or (cfg.ollama_model if modelo_ativo == cfg.ollama_model else modelo_ativo)
    pergunta = frase.strip()
    if arquivo.strip():
        pergunta += f"\n(arquivo informado pelo usuário: {arquivo.strip()})"
    corpo = {
        "model": modelo,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.2},
        "messages": [
            {"role": "system",
             "content": SISTEMA.replace("{catalogo}", json.dumps(catalogo(), ensure_ascii=False))},
            {"role": "user", "content": pergunta},
        ],
    }
    try:
        r = httpx.post(f"{cfg.ollama_url}/api/chat", json=corpo, timeout=TIMEOUT_S)
        r.raise_for_status()
        conteudo = r.json()["message"]["content"]
        plano = Plano.model_validate(_extrair_json(conteudo))
    except (httpx.HTTPError, KeyError, ValueError, ValidationError) as e:
        return {"status": "erro_do_motor", "detalhe": f"{e.__class__.__name__}: {e}",
                "biblioteca": fora_do_catalogo(frase)}
    erro = _validar_contra_catalogo(plano)
    if erro:
        return {"status": "plano_invalido", "detalhe": erro,
                "explicacao": plano.explicacao, "modelo": modelo,
                "biblioteca": fora_do_catalogo(frase)}
    # "explicar" = o catálogo não cobre o pedido. É exatamente quando a biblioteca serve.
    biblioteca = fora_do_catalogo(frase) if plano.acao == "explicar" else None
    return {"status": "ok", "plano": plano.model_dump(), "modelo": modelo,
            "biblioteca": biblioteca}
