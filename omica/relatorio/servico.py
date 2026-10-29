"""servico.py — o relatório de um run, do lado da ômica (F4).

Fluxo com o n8n (a chave da Anthropic fica SÓ no n8n; aqui nunca passa):
  1. `pedido(run_id)`   — run concluído → ficha de fatos (dos envelopes ENTREGUES, com
     invariantes conferidos) + o pacote pronto para a Messages API: `sistema` (o prompt
     versionado em n8n/prompts/), `mensagem` (ficha legível) e `esquema` (saída estruturada).
     O n8n só embrulha isso no corpo da chamada.
  2. o n8n chama o Claude e devolve o JSON do bloco de texto em `receber(...)`.
  3. `receber(run_id, rascunho, ...)` — revalida o esquema, roda o portão de números e, se
     passar, renderiza em <pasta de resultados do run>/relatorio/ (results/<mod>/runs/<run_id>/
     no demo; results/projetos/<esp>/<slug>/<mod>/runs/<run_id>/ fora dele). Reprovado → devolve as
     falhas para UMA nova tentativa; na segunda reprovação o n8n leva ao autor.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Any, Optional

from pydantic import ValidationError

from omica.config import REPO_ROOT, get_config
from omica.relatorio import fatos as fatos_mod
from omica.relatorio.esquema import ESQUEMA_SAIDA, Rascunho
from omica.relatorio.portao_numeros import conferir, conferir_invariantes
from omica.relatorio.render import formatar, renderizar
from omica.travas import TravaOcupada, trava

PROMPT_SISTEMA = REPO_ROOT / "n8n" / "prompts" / "relatorio_sistema.md"
MAX_TENTATIVAS_AUTOMATICAS = 2


class ErroRelatorio(Exception):
    def __init__(self, status: int, erro: str, /, **detalhe: Any) -> None:
        super().__init__(erro)
        self.status, self.erro, self.detalhe = status, erro, detalhe


def _pastas(run_id: str) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    from omica import runs

    try:
        run = runs.estado(run_id)
    except runs.ErroRun as e:
        raise ErroRelatorio(e.status, e.erro, **e.detalhe) from None
    if run["estado"] != "concluido":
        raise ErroRelatorio(409, "run_nao_concluido", estado=run["estado"])
    pasta = runs._pasta(run_id)
    plano = json.loads((pasta / "plano.json").read_text(encoding="utf-8"))
    return pasta, run, plano


def _envelopes(pasta: Path, plano: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Só envelopes ENTREGUES (gravados quando o portão do passo abriu) — nunca recalculados:
    o sha256 que o relatório cita é o do momento da entrega."""
    envs = {}
    for p in plano["passos"]:
        arq = pasta / "envelopes" / f"{p['id']}.json"
        if not arq.exists():
            raise ErroRelatorio(409, "envelope_ausente", passo=p["id"])
        envs[p["id"]] = json.loads(arq.read_text(encoding="utf-8"))
    return envs


def _mensagem(ficha: dict[str, Any], plano: dict[str, Any], run: dict[str, Any]) -> str:
    """A ficha em forma legível para o redator: ids exatos, rótulo e valor JÁ formatado (ele
    precisa ver os valores para escrever com sentido, mas só os cita por id)."""
    linhas = [f"Pipeline: {plano['pipeline']} (módulo {plano['modulo']}). Run {plano['run_id']}.",
              "Passos executados:"]
    for p in plano["passos"]:
        selo = ficha["selos"].get(p["id"])
        linhas.append(f"- {p['id']}: {p['titulo']} (selo do código: {selo})")
    linhas.append("Escolhas do autor: " + json.dumps(plano.get("escolhas", {}), ensure_ascii=False))
    linhas.append("\nFICHA DE FATOS (cite SÓ por {{f:id}}):")
    for f in ficha["fatos"]:
        linhas.append(f"- {f['id']} = {formatar(f['valor'])}  ({f['rotulo']})")
    if ficha["figuras"]:
        linhas.append("\nFIGURAS (cite por {{fig:id}} e dê legenda a cada uma):")
        for g in ficha["figuras"]:
            linhas.append(f"- {g['id']}")
    else:
        # pipeline sem figura (genômica): um cabeçalho seguido de nada convidava o redator a
        # descrever "a figura" que não existe — texto sem número passa no portão
        linhas.append("\nFIGURAS: nenhuma neste run. Não use {{fig:…}}, não fale de figura e "
                      "devolva `legendas` vazia.")
    return "\n".join(linhas)


def pedido(run_id: str) -> dict[str, Any]:
    pasta, run, plano = _pastas(run_id)
    try:
        ficha = fatos_mod.gerar(plano, _envelopes(pasta, plano))
    except (KeyError, ValueError, OSError) as e:
        raise ErroRelatorio(409, "ficha_impossivel", detalhe=str(e)[:300]) from None
    falhas = conferir_invariantes(ficha)
    if falhas:
        # resultados incoerentes entre si: nenhum texto deve ser escrito sobre eles
        raise ErroRelatorio(409, "invariantes_reprovados", falhas=falhas)
    destino = pasta / "relatorio"
    destino.mkdir(parents=True, exist_ok=True)
    sistema = PROMPT_SISTEMA.read_text(encoding="utf-8")
    # o prompt é lido a cada pedido e vale para TODOS os módulos: o sha fica com a ficha, para
    # cada tentativa e o relatório dizerem com qual versão do prompt foram escritos
    ficha["_prompt_sha256"] = hashlib.sha256(sistema.encode("utf-8")).hexdigest()
    (destino / "fatos.json").write_text(json.dumps(ficha, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"run_id": run_id, "n_fatos": len(ficha["fatos"]), "n_figuras": len(ficha["figuras"]),
            "sistema": sistema, "prompt_sha256": ficha["_prompt_sha256"],
            "mensagem": _mensagem(ficha, plano, run), "esquema": ESQUEMA_SAIDA}


def _pagina(titulo: str, corpo: str) -> str:
    return f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'self'; style-src 'unsafe-inline'">
<title>{titulo}</title><style>
body{{max-width:860px;margin:2rem auto;padding:0 1rem;font:16px/1.6 system-ui,sans-serif;color:#1d2733;background:#fff}}
h1{{font-size:1.6rem}} h2{{font-size:1.2rem;margin-top:2rem;border-bottom:1px solid #d9e0e6}}
figure{{margin:1.5rem 0}} img{{max-width:100%}} figcaption{{font-size:.9rem;color:#4a5865}}
table{{border-collapse:collapse;font-size:.8rem;width:100%}} td,th{{border:1px solid #d9e0e6;padding:.25rem .4rem;text-align:left}}
abbr{{text-decoration:underline dotted;cursor:help}} .fato{{font-variant-numeric:tabular-nums}}
@media (prefers-color-scheme: dark){{body{{background:#121820;color:#e3e9ef}} td,th{{border-color:#33404d}}}}
</style></head><body>{corpo}</body></html>"""


def receber(run_id: str, bruto: Any, modelo: str = "", stop_reason: str = "",
            autor: bool = False) -> dict[str, Any]:
    """Rascunho do redator (ou edição do autor) → portão → relatório. Nunca renderiza o que
    não passou. Serializado por run: dois rascunhos simultâneos (um retry do n8n) contariam
    a mesma tentativa duas vezes e furariam a cota automática."""
    pasta, run, plano = _pastas(run_id)
    try:
        with trava(pasta / ".trava_relatorio", 30.0):
            return _receber(run_id, pasta, plano, run, bruto, modelo, stop_reason, autor)
    except TravaOcupada:
        raise ErroRelatorio(409, "relatorio_ocupado", run_id=run_id) from None


def _receber(run_id: str, pasta: Path, plano: dict[str, Any], run: dict[str, Any], bruto: Any,
             modelo: str, stop_reason: str, autor: bool) -> dict[str, Any]:
    from omica import runs

    try:
        # a pasta do run (results/<mod>/runs/... no demo; results/projetos/<esp>/<slug>/... fora
        # dele), RECALCULADA do contexto do run e conferida com o do plano aprovado — nunca um
        # campo do run.json que alguém edite.
        # Antes de registrar a tentativa: run corrompido não gasta a cota automática.
        # o módulo do plano aprovado completa um registro que não o traga (a visão do run
        # sempre traz; um run.json com OUTRO módulo é recusado pela conferência com o plano)
        ref = {"modulo": plano.get("modulo"), **run, "run_id": run_id}
        saida = runs.pasta_resultados(ref, plano) / "relatorio"
    except runs.ErroRun as e:
        raise ErroRelatorio(e.status, e.erro, **e.detalhe) from None
    destino = pasta / "relatorio"
    fichario = destino / "fatos.json"
    if not fichario.exists():
        raise ErroRelatorio(409, "ficha_ausente", detalhe="chame /relatorio/pedido antes")
    ficha = json.loads(fichario.read_text(encoding="utf-8"))
    registro_arq = destino / "tentativas.json"
    tentativas = json.loads(registro_arq.read_text(encoding="utf-8")) if registro_arq.exists() else []
    automaticas = sum(1 for t in tentativas if not t["autor"])
    if not autor and automaticas >= MAX_TENTATIVAS_AUTOMATICAS:
        raise ErroRelatorio(409, "tentativas_esgotadas", detalhe="leve ao autor (revisão humana)")

    registro: dict[str, Any] = {"em": time.time(), "autor": autor, "modelo": modelo, "stop_reason": stop_reason,
                                "prompt_sha256": ficha.get("_prompt_sha256")}
    if stop_reason and stop_reason not in ("end_turn", "stop_sequence"):
        # refusal ou max_tokens: o conteúdo não é um rascunho completo
        registro.update(ok=False, falhas=[{"regra": "API", "onde": "stop_reason", "detalhe": stop_reason}])
    else:
        try:
            rascunho = Rascunho.model_validate(bruto).model_dump()
        except ValidationError as e:
            rascunho = None
            registro.update(ok=False, falhas=[{"regra": "esquema", "onde": "rascunho",
                                               "detalhe": str(e)[:500]}])
        if rascunho is not None:
            resultado = conferir(rascunho, ficha, plano)
            registro.update(ok=resultado["ok"], falhas=resultado["falhas"])
    tentativas.append(registro)
    destino.mkdir(parents=True, exist_ok=True)
    registro_arq.write_text(json.dumps(tentativas, ensure_ascii=False, indent=2), encoding="utf-8")
    if not registro["ok"]:
        return {"ok": False, "falhas": registro["falhas"], "tentativa": len(tentativas),
                "restam_automaticas": max(0, MAX_TENTATIVAS_AUTOMATICAS - automaticas - (0 if autor else 1))}

    cfg = get_config()
    (saida / "figuras").mkdir(parents=True, exist_ok=True)
    for fig in ficha["figuras"]:
        shutil.copyfile(fig["arquivo"], saida / "figuras" / Path(fig["arquivo"]).name)
    raiz = cfg.data_dir.as_posix().rstrip("/") + "/"
    titulo = f"Relatório — {plano['pipeline']} ({run_id})"
    r = renderizar(rascunho, ficha, resultado, plano["modulo"], titulo,
                   arquivos_rel=lambda c: c[len(raiz):] if c.startswith(raiz) else c)
    (saida / "relatorio.html").write_text(_pagina(titulo, r["html"]), encoding="utf-8")
    (saida / "relatorio.md").write_text(r["markdown"], encoding="utf-8")
    (saida / "proveniencia.json").write_text(json.dumps(
        {"run_id": run_id, "plano_sha256": plano["plano_sha256"], "modelo_redator": modelo,
         "prompt_sha256": ficha.get("_prompt_sha256"),
         "editado_pelo_autor": autor, "numeros": r["proveniencia"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "tentativa": len(tentativas), "url": f"/relatorios/{run_id}/",
            "n_numeros": len(r["proveniencia"]), "marcados_sem_selo": resultado["marcados"]}
