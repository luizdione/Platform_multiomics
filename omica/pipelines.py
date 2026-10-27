"""pipelines.py — pipelines orquestráveis (n8n): modelo, carga e validação de CONTRATO.

Um pipeline (pipelines/<id>.yaml) encadeia scripts da seção `scripts_api` de um manifest.
A ponte entre passos é declarativa (`liga`) e tipada: cada porta produzida tem um formato,
cada parâmetro consumido também, e o load REPROVA o pipeline se os dois não baterem. É isto
que permite dizer que, entre passos internos, "deixar o output no formato do input seguinte"
é garantido por contrato — sem LLM no meio do run.

Referências aceitas em `liga`:
  entrada.<nome>   — arquivo enviado pelo usuário (formato declarado em `entradas`)
  escolha.<nome>   — decisão científica do autor, só com valores reais do dado
  <passo>.<porta>  — saída de um passo ANTERIOR
Em `params`, "=<passo>.<param>" repete o valor usado num passo anterior (inclusive o default).

Como no registry, um pipeline inválido não derruba os outros: vai para `erros`.

Revisão adversarial da F2 (2026-10-19): o contrato deixava passar pipelines que carregavam
"válidos" e nunca rodavam — escolha de coluna inexistente (todo plano 422), checagem com as
entradas trocadas (todo run 409), entrada que ninguém consome mas o upload exige, e uma
conferência de relatorio.tabelas tautológica (comparava o formato da porta com ele mesmo,
nunca reprovava). Agora cada referência é conferida contra o que o VALIDADOR do formato
garante (formatos.COLUNAS_GARANTIDAS / COLUNAS_DE_NIVEL / COLUNAS_CHAVE / FORMATOS_CHECAGEM):
fonte única. A `chave` de relatorio.tabelas tem de ser coluna ÚNICA (COLUNAS_CHAVE), não só
garantida.

Genômica (F5, especificação G1 §0/§1.2 e crítica):
  - `Entrada.modos` é a LISTA BRANCA de como o arquivo pode entrar no run ("copia" para
    raw/runs/<id>/ ou "referencia", sem cópia, a partir de uma raiz de referência). Quem
    escolhe o modo é o servidor, pela raiz em que o caminho resolvido cai; o cliente manda só
    o caminho. `envio_formulario` diz ao n8n se o campo é de arquivo ou de caminho — fonte
    única para a API (GET /pipelines) e para o builder.
  - Regras novas no load, cada uma com canário em tests/test_contrato.py: modos vazio ou
    repetido (C1); referência só em formato referenciável (C2) e nunca adaptável — a
    adaptação grava um arquivo novo ao lado do original, que estaria na raiz de referência
    (C3); o `arquivo` de toda porta é um nome simples que passa na regra de nome do formato
    (C4: uma porta bed_regioes 'regioes.txt' seria lida 1-based pelo bcftools).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field

from omica.config import REPO_ROOT
from omica.contrato.formatos import (CHECAGENS, COLUNAS_CHAVE, COLUNAS_DE_NIVEL,
                                     COLUNAS_GARANTIDAS, FORMATOS_CHECAGEM, IRMAOS,
                                     REFERENCIAVEIS, VALIDADORES, regra_de_nome)
from omica.registry import Manifest, ScriptSpec, load_registry

PIPELINES_DIR = REPO_ROOT / "pipelines"
_ID = re.compile(r"^[a-z][a-z0-9_]*$")
# `arquivo` de uma porta: nome simples dentro de {saida} — nada de '/', '..' ou absoluto
_ARQUIVO_PORTA = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-]*", re.ASCII)

ModoEntrada = Literal["copia", "referencia"]


class Entrada(BaseModel):
    model_config = ConfigDict(extra="forbid")
    formato: str
    rotulo: str
    adaptavel: bool = False   # True = o agente adaptador (F3) pode propor um mapeamento
    exemplo: str = ""         # arquivo demo do repo que satisfaz o formato
    # Lista BRANCA dos modos (G1 §0.1): "copia" = o arquivo é copiado para raw/runs/<id>/;
    # "referencia" = fica onde está, numa raiz de referência configurada, atestado por
    # impressão + sha. O servidor decide pela raiz do caminho; fora da lista, 422.
    modos: list[ModoEntrada] = Field(default_factory=lambda: ["copia"])


def envio_formulario(e: Entrada) -> Literal["arquivo", "caminho"]:
    """Como o formulário do n8n pede a entrada: "caminho" (texto com um caminho no servidor)
    quando o formato tem arquivo irmão obrigatório (o índice não vai por upload: 422
    entrada_exige_indice) ou quando a entrada aceita referência; "arquivo" (upload) no resto.
    Fonte única: a API expõe e o builder do n8n usa esta mesma função."""
    return "caminho" if (e.formato in IRMAOS or "referencia" in e.modos) else "arquivo"


class Checagem(BaseModel):
    model_config = ConfigDict(extra="allow")  # os campos dependem do tipo
    # em sincronia com formatos.CHECAGENS (teste em tests/test_contrato.py)
    tipo: Literal["contagens_e_coldata", "vcf_tem_info_af", "vcf_contigs_ars_ucd", "vcf_e_bed"]


class Escolha(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rotulo: str
    valores_de: str  # "<entrada>.<coluna>": o autor só escolhe entre valores reais


class Passo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    script: str  # chave em `scripts_api` do manifest do módulo
    liga: dict[str, str] = Field(default_factory=dict)
    params: dict[str, str] = Field(default_factory=dict)


class TabelaRelatorio(BaseModel):
    model_config = ConfigDict(extra="forbid")
    passo: str
    porta: str
    chave: str
    colunas: list[str]
    top: dict = Field(default_factory=dict)


class Filtro(BaseModel):
    model_config = ConfigDict(extra="forbid")
    coluna: str
    menor_que: Optional[str] = None           # id de fato com o corte (ex.: de.de_resumo.criterios.padj)
    abs_maior_ou_igual: Optional[str] = None


class Invariante(BaseModel):
    """Conferência aritmética entre fatos (R4 do portão de números). Declarativa de
    propósito: nada de expressão avaliada (`eval`) vinda de YAML."""
    model_config = ConfigDict(extra="forbid")
    tipo: Literal["soma", "igual", "recontagem"]
    partes: list[str] = Field(default_factory=list)
    total: str = ""
    a: str = ""
    b: str = ""
    tabela: str = ""
    filtros: list[Filtro] = Field(default_factory=list)


class Relatorio(BaseModel):
    model_config = ConfigDict(extra="forbid")
    figuras: list[str] = Field(default_factory=list)
    resumos: list[str] = Field(default_factory=list)
    tabelas: list[TabelaRelatorio] = Field(default_factory=list)
    invariantes: list[Invariante] = Field(default_factory=list)


class SeloPipeline(BaseModel):
    model_config = ConfigDict(extra="forbid")
    testado_em: str = ""
    hash: str = ""


class Pipeline(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    modulo: str
    titulo: str
    descricao: str = ""
    entradas: dict[str, Entrada]
    checagens_entrada: list[Checagem] = Field(default_factory=list)
    escolhas_autor: dict[str, Escolha] = Field(default_factory=dict)
    passos: list[Passo]
    relatorio: Relatorio = Field(default_factory=Relatorio)
    selo_pipeline: SeloPipeline = Field(default_factory=SeloPipeline)


def spec_api(m: Manifest, chave: str) -> Optional[ScriptSpec]:
    return next((s for s in m.scripts_api if s.key == chave), None)


def validar_contrato(p: Pipeline, m: Manifest) -> list[str]:
    """Lista de problemas (vazia = contrato fechado). Determinístico, sem LLM."""
    from omica.agentes.adaptador import ALVOS  # leve: só formatos, aplicar_spec e pydantic

    erros: list[str] = []
    for nome, e in p.entradas.items():
        if e.formato not in VALIDADORES:
            erros.append(f"entrada {nome}: formato desconhecido {e.formato!r}")
        elif e.adaptavel and e.formato not in ALVOS:
            # o n8n chamaria o adaptador, que só devolveria "não é adaptável": promessa vazia
            erros.append(f"entrada {nome}: adaptavel, mas o adaptador não conhece o formato {e.formato!r}")
        # C1: sem modo nenhum a entrada nunca seria aceita; repetido é erro de digitação
        if not e.modos or len(set(e.modos)) != len(e.modos):
            erros.append(f"entrada {nome}: modos vazio ou repetido {e.modos!r}")
        if "referencia" in e.modos:
            # C2: só formato cujo validador é barato num arquivo de GB e que o script lê no lugar
            if e.formato not in REFERENCIAVEIS:
                erros.append(f"entrada {nome}: modo referencia, mas o formato {e.formato!r} não é referenciável "
                             f"(referenciáveis: {sorted(REFERENCIAVEIS)})")
            # C3: a adaptação grava '<nome>.adaptado.tsv' ao lado do ORIGINAL (runs.adaptar_entrada):
            # numa referência, isso escreveria dentro da raiz de referência
            if e.adaptavel:
                erros.append(f"entrada {nome}: adaptavel não combina com modo referencia (a adaptação grava um "
                             "arquivo novo ao lado do original)")
    for nome, esc in p.escolhas_autor.items():
        ent, sep, coluna = esc.valores_de.partition(".")
        if ent not in p.entradas:
            erros.append(f"escolha {nome}: valores_de aponta para entrada inexistente {esc.valores_de!r}")
            continue
        # a escolha vira argumento do script: só coluna de NÍVEL cujo valor o validador do
        # formato já garante (existe, poucos valores, aceito pelo sanitizador do argv)
        niveis = COLUNAS_DE_NIVEL.get(p.entradas[ent].formato, ())
        if not sep or coluna not in niveis:
            erros.append(f"escolha {nome}: valores_de {esc.valores_de!r} não é coluna de nível garantida "
                         f"pelo formato {p.entradas[ent].formato!r} (aceitas: {list(niveis)})")
    for c in p.checagens_entrada:
        campos = c.model_dump(exclude={"tipo"})
        exigidos = set(CHECAGENS[c.tipo][1])
        if set(campos) != exigidos:
            erros.append(f"checagem {c.tipo}: campos {sorted(campos)} (exige {sorted(exigidos)})")
        for campo, alvo in campos.items():
            if not isinstance(alvo, str) or alvo not in p.entradas:
                erros.append(f"checagem {c.tipo}: {campo} -> entrada inexistente {alvo!r}")
                continue
            esperado = FORMATOS_CHECAGEM.get(c.tipo, {}).get(campo)
            if esperado and p.entradas[alvo].formato != esperado:
                erros.append(f"checagem {c.tipo}: {campo} exige formato {esperado!r}, mas a entrada "
                             f"{alvo!r} é {p.entradas[alvo].formato!r}")

    vistos: dict[str, tuple[Passo, ScriptSpec]] = {}
    for passo in p.passos:
        rotulo = f"passo {passo.id}"
        if not _ID.match(passo.id):
            erros.append(f"{rotulo}: id inválido (use [a-z][a-z0-9_]*)")
        if passo.id in vistos or passo.id in ("entrada", "escolha"):
            erros.append(f"{rotulo}: id repetido ou reservado")
            continue
        spec = spec_api(m, passo.script)
        if spec is None:
            erros.append(f"{rotulo}: script {passo.script!r} não existe em scripts_api de {m.id}")
            continue
        if "{saida}" not in spec.cmd:
            erros.append(f"{rotulo}: cmd de {spec.key} não grava em {{saida}} (sobrescreveria o demo)")
        nomes = {par.name for par in spec.params}
        for param, ref in passo.liga.items():
            if param not in nomes:
                erros.append(f"{rotulo}: liga {param!r} não é parâmetro de {spec.key}")
                continue
            origem, _, campo = ref.partition(".")
            if origem == "escolha":
                if campo not in p.escolhas_autor:
                    erros.append(f"{rotulo}: escolha inexistente {ref!r}")
                elif param in spec.consome:
                    # uma escolha é um VALOR (nível de condição), nunca um arquivo: ligada a
                    # um parâmetro que consome formato, passaria sem conferência de formato.
                    erros.append(f"{rotulo}: {param} consome arquivo {spec.consome[param]!r}; "
                                 f"escolha do autor não pode alimentá-lo")
                continue
            if origem == "entrada":
                formato = p.entradas[campo].formato if campo in p.entradas else None
                if formato is None:
                    erros.append(f"{rotulo}: entrada inexistente {ref!r}")
                    continue
            elif origem in vistos:
                spec_anterior = vistos[origem][1]
                porta = spec_anterior.produz.get(campo)
                if porta is None:
                    erros.append(f"{rotulo}: {origem} não produz a porta {campo!r}")
                    continue
                formato = porta.formato
            else:
                erros.append(f"{rotulo}: {ref!r} aponta para passo inexistente ou POSTERIOR")
                continue
            esperado = spec.consome.get(param)
            if esperado is None:
                erros.append(f"{rotulo}: {spec.key} não declara o formato que consome em {param!r}")
            elif esperado != formato:
                erros.append(f"{rotulo}: {param} consome {esperado!r} mas {ref} produz {formato!r}")
        for param, valor in passo.params.items():
            if param not in nomes:
                erros.append(f"{rotulo}: params.{param} não é parâmetro de {spec.key}")
            elif param in passo.liga:
                erros.append(f"{rotulo}: {param} está em liga E em params")
            elif param in spec.consome:
                # caminho literal num parâmetro que consome formato fura o contrato: o
                # arquivo nunca passaria pelo validador do formato.
                erros.append(f"{rotulo}: {param} consome {spec.consome[param]!r} e só pode vir por liga")
            elif valor.startswith("="):
                # o propor_plano resolve qualquer parâmetro do passo anterior — ligado, fixado em
                # params ou pelo default do spec (antes, o default era recusado à toa)
                origem, _, outro = valor[1:].partition(".")
                anterior = vistos.get(origem)
                if anterior is None or outro not in {par.name for par in anterior[1].params}:
                    erros.append(f"{rotulo}: params.{param}={valor!r} não aponta para parâmetro de passo anterior")
        for par in spec.params:
            if par.name in spec.consome and par.name not in passo.liga:
                erros.append(f"{rotulo}: {par.name} consome {spec.consome[par.name]!r} e não foi ligado")
            elif par.name not in passo.liga and par.name not in passo.params and not par.default:
                erros.append(f"{rotulo}: parâmetro {par.name!r} de {spec.key} ficou sem valor")
        for nome_porta, porta in spec.produz.items():
            if porta.formato not in VALIDADORES:
                erros.append(f"{rotulo}: {spec.key} produz formato desconhecido {porta.formato!r}")
            # C4: o nome da porta é fixo no manifest — a regra de nome roda aqui, estática, e não
            # no envelope. Nome simples (nada de '/', '..' ou absoluto) e que o consumidor do
            # formato lê do jeito certo (BED com '.bed', índice com '.tbi/.csi'...)
            if not _ARQUIVO_PORTA.fullmatch(porta.arquivo) or ".." in porta.arquivo:
                erros.append(f"{rotulo}: porta {nome_porta} de {spec.key} com arquivo {porta.arquivo!r} que não é "
                             "um nome simples dentro de {saida}")
            else:
                erros += [f"{rotulo}: porta {nome_porta} de {spec.key}: {x}"
                          for x in regra_de_nome(porta.formato, porta.arquivo)]
        vistos[passo.id] = (passo, spec)

    def _porta(ref: str, formato: str, onde: str) -> None:
        origem, _, campo = ref.partition(".")
        if origem not in vistos or campo not in vistos[origem][1].produz:
            erros.append(f"relatorio.{onde}: {ref!r} não é porta de nenhum passo")
        elif vistos[origem][1].produz[campo].formato != formato:
            erros.append(f"relatorio.{onde}: {ref!r} não é {formato}")

    for ref in p.relatorio.figuras:
        _porta(ref, "figura_png", "figuras")
    for ref in p.relatorio.resumos:
        _porta(ref, "resumo_json", "resumos")
    for t in p.relatorio.tabelas:
        # (a versão anterior passava como "formato esperado" o formato da própria porta: o ramo
        # "não é {formato}" era inalcançável — um portão que nunca podia ser visto reprovando)
        onde = f"relatorio.tabelas {t.passo}.{t.porta}"
        if t.passo not in vistos or t.porta not in vistos[t.passo][1].produz:
            erros.append(f"{onde}: não é porta de nenhum passo")
            continue
        formato = vistos[t.passo][1].produz[t.porta].formato
        garantidas = COLUNAS_GARANTIDAS.get(formato)
        if garantidas is None:
            erros.append(f"{onde}: formato {formato!r} não é tabela")
            continue
        extras = sorted(set(t.top) - {"n", "ordenar"})
        if extras:
            erros.append(f"{onde}: top com campos desconhecidos {extras}")
        n = t.top.get("n", 10)
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            erros.append(f"{onde}: top.n deve ser inteiro ≥1 (veio {n!r})")
        ordenar = t.top.get("ordenar")
        citadas = [t.chave, *t.colunas, *([ordenar] if ordenar is not None else [])]
        fora = [c for c in citadas if c not in garantidas]
        if fora:
            erros.append(f"{onde}: colunas {fora} não são garantidas pelo validador de {formato!r} "
                         f"(garantidas: {list(garantidas)})")
        # a chave identifica UMA linha (o portão de números exige achá-la exatamente 1 vez):
        # garantida não basta — gene_name existe sempre, mas o demo tem 39 repetidos, e o run
        # inteiro rodaria para reprovar só no relatório (revisão F2 #20, resíduo)
        unicas = COLUNAS_CHAVE.get(formato, ())
        if t.chave not in unicas:
            erros.append(f"{onde}: chave {t.chave!r} não é coluna ÚNICA garantida pelo validador de "
                         f"{formato!r} (únicas: {list(unicas)})")

    # entrada que nenhum passo nem checagem consome seria exigida no upload à toa; escolha que
    # nenhum passo usa seria exigida no plano à toa
    usadas = {ref.partition(".")[2] for x in p.passos for ref in x.liga.values() if ref.startswith("entrada.")}
    usadas |= {a for c in p.checagens_entrada for a in c.model_dump(exclude={"tipo"}).values()
               if isinstance(a, str)}
    for nome in p.entradas:
        if nome not in usadas:
            erros.append(f"entrada {nome}: nenhum passo nem checagem a consome")
    escolhidas = {ref.partition(".")[2] for x in p.passos for ref in x.liga.values() if ref.startswith("escolha.")}
    for nome in p.escolhas_autor:
        if nome not in escolhidas:
            erros.append(f"escolha {nome}: nenhum passo a usa")

    # invariantes (R4): cada referência tem de ser campo de um RESUMO declarado no relatório
    # (os fatos de tabela são top-N e não servem de total); recontagem só em tabela declarada
    resumos = set(p.relatorio.resumos)
    tabelas = {f"{t.passo}.{t.porta}" for t in p.relatorio.tabelas}

    def _ref(ref: str, onde: str) -> None:
        partes = ref.split(".")
        if len(partes) < 3 or ".".join(partes[:2]) not in resumos:
            erros.append(f"relatorio.invariantes {onde}: {ref!r} não é campo de um resumo declarado")

    for i, inv in enumerate(p.relatorio.invariantes, start=1):
        onde = f"#{i} ({inv.tipo})"
        exigidos = {"soma": [inv.partes, inv.total], "igual": [inv.a, inv.b],
                    "recontagem": [inv.tabela, inv.filtros, inv.total]}[inv.tipo]
        if not all(exigidos):
            erros.append(f"relatorio.invariantes {onde}: campos obrigatórios do tipo ausentes")
            continue
        for ref in [*inv.partes, inv.total, inv.a, inv.b]:
            if ref:
                _ref(ref, onde)
        for flt in inv.filtros:
            limites = [x for x in (flt.menor_que, flt.abs_maior_ou_igual) if x]
            if not limites:
                erros.append(f"relatorio.invariantes {onde}: filtro em {flt.coluna!r} sem limite")
            for ref in limites:
                _ref(ref, onde)
        if inv.tipo == "recontagem" and inv.tabela not in tabelas:
            erros.append(f"relatorio.invariantes {onde}: tabela {inv.tabela!r} não está em relatorio.tabelas")
    return erros


def carregar_pipelines(pasta: Path = PIPELINES_DIR,
                       modules_dir: Optional[Path] = None) -> tuple[dict[str, Pipeline], dict[str, str]]:
    """(pipelines válidos por id, erros por arquivo)."""
    reg = load_registry(modules_dir) if modules_dir else load_registry()
    validos: dict[str, Pipeline] = {}
    erros: dict[str, str] = {}
    if not pasta.exists():
        return validos, erros
    for arq in sorted(pasta.glob("*.yaml")):
        try:
            p = Pipeline.model_validate(yaml.safe_load(arq.read_text(encoding="utf-8")))
            if p.id != arq.stem:
                raise ValueError(f"id {p.id!r} difere do nome do arquivo {arq.stem!r}")
            m = reg.por_id(p.modulo)
            if m is None:
                raise ValueError(f"módulo inexistente ou inválido: {p.modulo!r}")
            problemas = validar_contrato(p, m)
            if problemas:
                raise ValueError("contrato quebrado: " + "; ".join(problemas))
            validos[p.id] = p
        except Exception as e:  # um pipeline ruim nunca derruba os outros
            erros[arq.name] = str(e)
    return validos, erros
