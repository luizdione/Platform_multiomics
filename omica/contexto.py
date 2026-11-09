"""contexto.py — (espécie, subprojeto) → os valores que o SERVIDOR põe no cmd (v2 multiespécie, M1).

O cliente diz só QUAL contexto quer (`homo_sapiens`, `teste_rna`); tudo o que vai ao argv — a
referência da assembly (`ref_*`), os arquivos do subprojeto (`sub_*`), a própria assembly
(`esp_*`) e as sobrescritas de parâmetro — sai dos YAMLs do repositório (especies/,
projetos/) e de <OMICA_DATA>/projetos, resolvido aqui. Nada disso vem do formulário.

O que este arquivo garante:
  - uma recusa tem UM motivo, o mesmo em todas as camadas (UI, dispatch, runs, API): a
    precedência de `execucao_permitida` é fixa e os códigos HTTP saem de STATUS_DA_RECUSA;
  - o `sha256` do contexto entra no plano do run (e, portanto, no `plano_sha256` que o autor
    aprova). Trocar uma ref, o amostras.tsv ou o subprojeto entre o plano e a execução dá 409
    `contexto_mudou`, nunca um run que roda com outra referência sem o plano acusar;
  - o sha NÃO cobre o que não muda a execução (crítica v2, médio 13): da espécie entra só a
    assembly USADA (refs, equivalências, contigs), os módulos e os parâmetros; dos YAMLs entra
    o conteúdo canônico (`model_dump`), não os bytes — comentário editado não derruba run
    aprovado. Os arquivos de dados (amostras, painel, MANIFEST das refs) entram pelos bytes,
    com CRLF→LF (o checkout do Windows com autocrlf × o container com LF);
  - o caminho de resultados de um run sai de (espécie, subprojeto, módulo, run_id), nunca de
    um campo gravado que alguém possa editar (crítica v2, médio 6; ver runs.pasta_resultados).

Fora de bos_taurus/demo só PIPELINES executam (ESPEC §10): `scripts:` e os workflows selados
gravam em caminho fixo do demo e sobrescreveriam o que sustenta os selos.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Mapping, Optional

from omica import especies as especies_mod
from omica import subprojetos as subprojetos_mod
from omica.chaves_contexto import (CHAVES_CONTEXTO, ESPECIE_LEGADA, ID_DEMO, ID_ESPECIE, SLUG,
                                   SLUG_DEMO)
from omica.config import get_config
from omica.especies import Especie
from omica.pipelines import Pipeline, carregar_pipelines
from omica.registry import WorkflowSpec, load_registry
from omica.subprojetos import Registro

# motivo de recusa de execução → código HTTP sugerido (o núcleo não conhece HTTP; quem traduz
# é a API/a UI). Precedência em execucao_permitida.
STATUS_DA_RECUSA: Mapping[str, int] = MappingProxyType({
    "especie_inexistente": 404,
    "subprojeto_inexistente": 404,
    "subprojeto_nao_executavel": 409,
    "so_pipelines_fora_do_demo": 409,
    "workflow_nao_validado_neste_subprojeto": 409,
    "pipeline_fora_da_especie": 422,
    "pipeline_fora_do_subprojeto": 422,
    "modulo_fora_do_subprojeto": 422,
})


class ContextoRecusado(Exception):
    """Recusa de contexto com código HTTP sugerido. Mesmo formato de runs.ErroRun (status e
    erro só-posicionais: um detalhe chamado `status` não colide com o código)."""

    def __init__(self, status: int, erro: str, /, **detalhe: Any) -> None:
        super().__init__(erro)
        self.status, self.erro, self.detalhe = status, erro, detalhe


@dataclass(frozen=True)
class Contexto:
    especie: str
    slug: str
    assembly: str
    valores: Mapping[str, str]                 # só chaves de CHAVES_CONTEXTO
    parametros: Mapping[str, str]              # "<mod>.<script>.<param>" → valor (sub > espécie)
    arquivos: Mapping[str, Optional[str]]      # rótulo → sha256 (None = arquivo ausente)
    sha256: str
    # F6 (adendo §A4 X5′, crítica M4): os `avisos` do subprojeto, para o plano, a mensagem ao
    # redator e o HTML do relatório. Já estão cobertos pelo sha do subprojeto (_sha_subprojeto):
    # o sha do contexto não muda, só o conteúdo dos planos novos.
    avisos: tuple[str, ...] = ()

    @property
    def id(self) -> str:
        return f"{self.especie}/{self.slug}"

    @property
    def eh_demo(self) -> bool:
        return self.id == ID_DEMO

    @property
    def eh_especie_legada(self) -> bool:
        return self.especie == ESPECIE_LEGADA

    def padroes(self, mod_id: str, script_key: str) -> dict[str, str]:
        """Sobrescritas de default deste script: {nome do parâmetro: valor}."""
        pre = f"{mod_id}.{script_key}."
        return {k[len(pre):]: v for k, v in self.parametros.items() if k.startswith(pre)}

    def para_plano(self) -> dict[str, Any]:
        """O que vai ao plano do run (e, com ele, ao plano_sha256 que o autor aprova)."""
        return {"especie": self.especie, "subprojeto": self.slug, "assembly": self.assembly,
                "valores": dict(self.valores), "parametros": dict(self.parametros),
                "arquivos": dict(self.arquivos), "sha256": self.sha256, "avisos": list(self.avisos)}


@dataclass(frozen=True)
class Catalogo:
    especies: dict[str, Especie]
    erros_especies: dict[str, str]
    subprojetos: dict[str, Registro]
    erros_subprojetos: dict[str, str]
    # ids dos módulos que o registry carregou (None = carregar quando precisar)
    modulos_registro: Optional[tuple[str, ...]] = None


def catalogo() -> Catalogo:
    """Espécies e subprojetos lidos AGORA (sem cache: um YAML editado vale no próximo pedido).
    O registry é carregado uma vez e serve aos dois loaders (conferência das chaves de
    `parametros`, crítica v2 médio 14: chave inexistente vai para `erros_*`, nunca vira default
    bovino em silêncio) e a `modulos_habilitados`."""
    reg = load_registry()
    especies, erros_esp = especies_mod.carregar_especies(reg=reg)
    subs, erros_sub = subprojetos_mod.carregar_subprojetos(especies, reg=reg)
    return Catalogo(especies, erros_esp, subs, erros_sub, tuple(m.id for m in reg.modules))


def _cat(cat: Optional[Catalogo]) -> Catalogo:
    return cat if cat is not None else catalogo()


# --------------------------------------------------------------------------- sha
def _sha_canonico(obj: Any) -> str:
    """A MESMA receita de runs._sha_canonico (o sha do contexto vive dentro do plano)."""
    texto = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _sha_texto(p: Path) -> Optional[str]:
    """sha256 do arquivo com CRLF→LF, lido em blocos (o mesmo que `replace(b"\\r\\n", b"\\n")`
    sobre o conteúdo inteiro, sem carregá-lo). None se não for arquivo."""
    if not p.is_file():
        return None
    h = hashlib.sha256()
    pendente = b""
    with p.open("rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            bloco = pendente + bloco
            # um '\r' no fim do bloco pode ser a metade de um '\r\n' partido entre dois blocos
            pendente, bloco = (b"\r", bloco[:-1]) if bloco.endswith(b"\r") else (b"", bloco)
            h.update(bloco.replace(b"\r\n", b"\n"))
    h.update(pendente)
    return h.hexdigest()


def _sha_especie(esp: Especie, assembly: str) -> str:
    """Só o que a execução usa da espécie (médio 13): a assembly escolhida pelo subprojeto,
    os módulos (decidem o que pode rodar) e os parâmetros. Outra assembly, nomes de exibição,
    recursos e comentários do YAML ficam fora."""
    asm = esp.assemblies[assembly]
    return _sha_canonico({"id": esp.id, "assembly": assembly,
                          "dados_assembly": asm.model_dump(mode="json", exclude_defaults=True),
                          "modulos": list(esp.modulos), "parametros": dict(esp.parametros)})


def _sha_subprojeto(reg: Registro) -> str:
    """O conteúdo canônico do subprojeto (sem comentários; `exclude_defaults` para um campo
    novo do modelo, com default, não invalidar os runs aprovados antes dele)."""
    return _sha_canonico(reg.sub.model_dump(mode="json", exclude_defaults=True))


# ---------------------------------------------------------------------- resolver
def _caminho_no_cmd(reg: Registro, nome: str) -> str:
    """Como o WORKER vê um arquivo do subprojeto. Repositório: relativo à raiz (cwd dos jobs,
    montado em /app em todos os containers); <dados>: sob OMICA_DATA, que no container já é
    /data/omica. Montado dos ids (validados no load), nunca do caminho resolvido."""
    sub = reg.sub
    if reg.origem == "repo":
        return f"projetos/{sub.especie}/{sub.slug}/{nome}"
    return f"{get_config().data_dir.as_posix().rstrip('/')}/projetos/{sub.especie}/{sub.slug}/{nome}"


def resolver(especie: str, slug: str, *, cat: Optional[Catalogo] = None) -> Contexto:
    cat = _cat(cat)
    esp = cat.especies.get(especie) if isinstance(especie, str) else None
    if esp is None:
        raise ContextoRecusado(404, "especie_inexistente", especie=especie)
    reg = cat.subprojetos.get(f"{especie}/{slug}") if isinstance(slug, str) else None
    if reg is None:
        raise ContextoRecusado(404, "subprojeto_inexistente", subprojeto=f"{especie}/{slug}")
    sub = reg.sub
    if not sub.executavel:
        # `status_subprojeto`, não `status`: não confunde com o código HTTP
        raise ContextoRecusado(409, "subprojeto_nao_executavel", subprojeto=sub.id,
                               status_subprojeto=sub.status, validade=sub.validade)
    asm = esp.assemblies[sub.assembly]  # o loader já garantiu (validar_contra_especie)

    valores: dict[str, str] = {f"ref_{k}": v for k, v in asm.refs.items()}
    valores["esp_assembly"] = sub.assembly
    amostras = reg.pasta / sub.amostras
    if amostras.is_file():
        valores["sub_amostras"] = _caminho_no_cmd(reg, sub.amostras)
    painel = reg.pasta / sub.painel if sub.painel else None
    if painel is not None and painel.is_file():
        # declarado e ausente fica sem valor: o cmd que o pedir recebe "referência ausente"
        # no plano, e não um script que quebra no meio do run
        valores["sub_painel_bed"] = _caminho_no_cmd(reg, sub.painel)
    fora = sorted(set(valores) - set(CHAVES_CONTEXTO))
    if fora:  # inalcançável com o loader atual (refs fechadas): trava contra regressão dele
        raise ContextoRecusado(409, "contexto_invalido", subprojeto=sub.id, chaves=fora)

    parametros = {**esp.parametros, **sub.parametros}
    arquivos: dict[str, Optional[str]] = {
        "especie": _sha_especie(esp, sub.assembly),
        "subprojeto": _sha_subprojeto(reg),
        "amostras": _sha_texto(amostras),
        # criar este MANIFEST (refs baixadas/reconstruídas) muda o sha de propósito
        "refs_manifest": _sha_texto(get_config().refs_dir / especie / sub.assembly / "MANIFEST.json"),
    }
    if painel is not None:
        arquivos["painel"] = _sha_texto(painel)
    corpo = {"especie": especie, "subprojeto": slug, "assembly": sub.assembly,
             "valores": valores, "parametros": parametros, "arquivos": arquivos}
    return Contexto(especie, slug, sub.assembly, MappingProxyType(valores),
                    MappingProxyType(parametros), MappingProxyType(arquivos), _sha_canonico(corpo),
                    tuple(str(a) for a in sub.avisos))


def demo(*, cat: Optional[Catalogo] = None) -> Contexto:
    return resolver(ESPECIE_LEGADA, SLUG_DEMO, cat=cat)


def do_plano(plano: dict, *, cat: Optional[Catalogo] = None) -> Contexto:
    """O contexto de um plano congelado. Sem a chave `contexto` (plano aprovado antes da v2):
    o demo, sem conferência de sha — o cmd remontado com ele é o da v1, e a trava que vale é
    o `cmd != item["cmd"]` do executar. Com a chave: resolve de novo e exige o MESMO sha."""
    if "contexto" not in plano:
        return demo(cat=cat)
    c = plano["contexto"]
    if not (isinstance(c, dict) and isinstance(c.get("especie"), str)
            and isinstance(c.get("subprojeto"), str) and isinstance(c.get("sha256"), str)):
        # o plano é protegido pelo plano_sha256: isto só acontece com plano forjado
        raise ContextoRecusado(409, "plano_adulterado", campo="contexto")
    ctx = resolver(c["especie"], c["subprojeto"], cat=cat)
    if ctx.sha256 != c["sha256"]:
        raise ContextoRecusado(409, "contexto_mudou", esperado=c["sha256"], atual=ctx.sha256)
    return ctx


# -------------------------------------------------------------------- resultados
def rel_resultados_de(especie: str, slug: str, modulo: str, run_id: str) -> str:
    """Pasta de resultados de um run, relativa a results/: a raiz legada no demo (os runs da
    v1 e do n8n continuam onde estavam) e results/projetos/<esp>/<slug>/ fora dele."""
    if f"{especie}/{slug}" == ID_DEMO:
        return f"{modulo}/runs/{run_id}"
    return f"projetos/{especie}/{slug}/{modulo}/runs/{run_id}"


def rel_resultados(ctx: Contexto, modulo: str, run_id: str) -> str:
    return rel_resultados_de(ctx.especie, ctx.slug, modulo, run_id)


# ---------------------------------------------------------------------- consultas
def listar_especies(cat: Optional[Catalogo] = None) -> list[Especie]:
    return sorted(_cat(cat).especies.values(), key=lambda e: (e.ordem, e.id))


def listar_subprojetos(especie: str, *, incluir_arquivados: bool = False,
                       cat: Optional[Catalogo] = None) -> list[Registro]:
    """Os subprojetos da espécie: o demo primeiro, depois por título."""
    regs = [r for r in _cat(cat).subprojetos.values()
            if r.sub.especie == especie and (incluir_arquivados or r.sub.status != "arquivado")]
    return sorted(regs, key=lambda r: (r.sub.id != ID_DEMO, r.sub.titulo.casefold(), r.sub.id))


def obter_subprojeto(especie: str, slug: str, cat: Optional[Catalogo] = None) -> Optional[Registro]:
    if not (isinstance(especie, str) and isinstance(slug, str)
            and ID_ESPECIE.fullmatch(especie) and SLUG.fullmatch(slug)):
        return None
    return _cat(cat).subprojetos.get(f"{especie}/{slug}")


def modulos_habilitados(reg: Registro, cat: Optional[Catalogo] = None) -> list[str]:
    """esp.modulos ∩ (sub.modulos, se não vazio) ∩ módulos que o registry carregou, na ordem
    da espécie. Módulo com manifest inválido nunca aparece (nada dele executa)."""
    cat = _cat(cat)
    esp = cat.especies.get(reg.sub.especie)
    if esp is None:
        return []
    ids = cat.modulos_registro
    if ids is None:
        ids = tuple(m.id for m in load_registry().modules)
    do_sub = set(reg.sub.modulos) if reg.sub.modulos else None
    return [m for m in esp.modulos if m in ids and (do_sub is None or m in do_sub)]


def pipelines_do_subprojeto(reg: Registro, modulo: Optional[str] = None, *,
                            cat: Optional[Catalogo] = None) -> list[Pipeline]:
    """Pipelines que este subprojeto pode rodar (a mesma regra de execucao_permitida): da
    espécie, listados no subprojeto (se ele lista algum) e de módulo habilitado. Na ordem do
    subprojeto quando ele lista; senão por id."""
    validos, _ = carregar_pipelines()
    habilitados = set(modulos_habilitados(reg, cat))
    sub = reg.sub
    escolhidos = [p for p in validos.values()
                  if sub.especie in p.especies and p.modulo in habilitados
                  and (not sub.pipelines or p.id in sub.pipelines)
                  and (modulo is None or p.modulo == modulo)]
    ordem = {pid: i for i, pid in enumerate(sub.pipelines)}
    return sorted(escolhidos, key=lambda p: (ordem.get(p.id, len(ordem)), p.id))


def execucao_permitida(especie: str, slug: str, tipo: Literal["script", "workflow", "pipeline"], *,
                       workflow: Optional[WorkflowSpec] = None, pipeline: Optional[Pipeline] = None,
                       cat: Optional[Catalogo] = None) -> tuple[bool, str]:
    """(True, "") ou (False, motivo ∈ STATUS_DA_RECUSA). Precedência FIXA (ESPEC §5 B1), para
    o mesmo pedido receber o mesmo motivo na UI, no dispatch e na API."""
    if tipo not in ("script", "workflow", "pipeline"):
        raise ValueError(f"tipo de execução desconhecido: {tipo!r}")
    if tipo == "pipeline" and pipeline is None:
        raise ValueError("execucao_permitida(tipo='pipeline') exige o pipeline")
    cat = _cat(cat)
    if not isinstance(especie, str) or especie not in cat.especies:
        return False, "especie_inexistente"
    reg = cat.subprojetos.get(f"{especie}/{slug}") if isinstance(slug, str) else None
    if reg is None:
        return False, "subprojeto_inexistente"
    if not reg.sub.executavel:
        return False, "subprojeto_nao_executavel"
    if tipo in ("script", "workflow") and reg.sub.id != ID_DEMO:
        return False, "so_pipelines_fora_do_demo"
    if tipo == "workflow" and workflow is not None and reg.sub.id not in workflow.validado_em:
        return False, "workflow_nao_validado_neste_subprojeto"
    if tipo == "pipeline":
        assert pipeline is not None
        if especie not in pipeline.especies:
            return False, "pipeline_fora_da_especie"
        if reg.sub.pipelines and pipeline.id not in reg.sub.pipelines:
            return False, "pipeline_fora_do_subprojeto"
        if pipeline.modulo not in modulos_habilitados(reg, cat):
            return False, "modulo_fora_do_subprojeto"
    return True, ""
