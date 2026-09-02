"""Parser do índice de skills contra fixture com o formato real do
INDICE_CONSOLIDADO.md (seções `## dominio (N skills)` + tabela de 5 colunas)."""
from omica.skills.build_index import parse_indice

FIXTURE = """# Índice Consolidado

Texto introdutório qualquer.

## reads-align (2 skills)

Regras do domínio em prosa.

| Skill | Origem | Descricao | Stack | Pasta |
|---|---|---|---|---|
| fastp-qc | sciagent | QC e trimming de FASTQ com fastp. | fastp, conda | `reads-align/fastp-qc` |
| bwa-mem2-align | bioskills | Alinhamento curto com bwa-mem2. | bwa-mem2, samtools | `reads-align/bwa-mem2-align` |

## stats-ml (1 skills)

| Skill | Origem | Descricao | Stack | Pasta |
|---|---|---|---|---|
| nested-cv | clawbio | CV aninhada p/ seleção honesta. | scikit-learn | `stats-ml/nested-cv` |
"""


def test_parse_extrai_skills_e_dominios():
    indice = parse_indice(FIXTURE)
    assert indice["total"] == 3
    assert indice["dominios"] == {"reads-align": 2, "stats-ml": 1}
    primeiro = indice["skills"][0]
    assert primeiro == {
        "id": "fastp-qc",
        "dominio": "reads-align",
        "origem": "sciagent",
        "descricao": "QC e trimming de FASTQ com fastp.",
        "stack": ["fastp", "conda"],
        "pasta": "reads-align/fastp-qc",
    }


def test_parse_ignora_cabecalho_e_separador_da_tabela():
    indice = parse_indice(FIXTURE)
    ids = [s["id"] for s in indice["skills"]]
    assert "Skill" not in ids and "---" not in ids


def test_parse_ignora_tabela_fora_de_secao_de_dominio():
    texto = "| a | b | c | d | e |\n" + FIXTURE
    indice = parse_indice(texto)
    assert indice["total"] == 3
