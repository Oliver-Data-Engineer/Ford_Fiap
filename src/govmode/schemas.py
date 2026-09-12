"""
schemas.py
Schemas BigQuery do catálogo FIPE — mesmo padrão do notebook
`tabela_marcas.ipynb`, uma constante por tabela.

`modelos_com_fipe` difere do notebook original numa coisa deliberada: lá é
um registro por MODELO (só o ano mais recente); aqui é um registro por
`(modelo, ano)` — decisão do usuário, pra dar pra visualizar/consultar
todos os anos de cada modelo, não só o atual.
"""
from __future__ import annotations

from google.cloud import bigquery

MARCAS_VEICULOS = "marcas_veiculos"
MODELOS_VEICULOS = "modelos_veiculos"
MODELOS_COM_FIPE = "modelos_com_fipe"
REFERENCIA_MESES_FIPE = "referencia_meses_fipe"

# Igual ao notebook — recarregada por inteiro a cada execução (a lista de
# marcas da FIPE não é grande e não faz sentido acumular versões antigas).
SCHEMA_MARCAS_VEICULOS: list[bigquery.SchemaField] = [
    bigquery.SchemaField("tipo_veiculo", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("marca_nome", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("marca_codigo", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("atualizado_em", "TIMESTAMP"),
]

# Igual ao notebook, mas gravada com deduplicação por anti-join antes do
# upsert (src/govmode/engine.py) — no notebook, rodar a mesma marca duas
# vezes duplicava linhas; aqui não, mesmo sendo WRITE_APPEND.
SCHEMA_MODELOS_VEICULOS: list[bigquery.SchemaField] = [
    bigquery.SchemaField("tipo_veiculo", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("marca_codigo", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("modelo_codigo", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("modelo_nome", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("atualizado_em", "TIMESTAMP"),
]

# Um registro por (modelo, ano) — não por modelo (decisão do usuário: ver
# docstring do módulo). `ano_codigo` é o código usado na URL
# `.../modelos/{modelo}/anos/{ano_codigo}`; `ano_modelo` é o rótulo legível
# que a própria API devolve (ex.: "2026 Gasolina"). Resumível via anti-join
# chaveado em (marca_codigo, modelo_codigo, ano_codigo) — src/govmode/engine.py.
SCHEMA_MODELOS_COM_FIPE: list[bigquery.SchemaField] = [
    bigquery.SchemaField("tipo_veiculo", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("marca_codigo", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("modelo_codigo", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("modelo_nome", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("ano_codigo", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("ano_modelo", "STRING"),
    bigquery.SchemaField("codigo_fipe", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("atualizado_em", "TIMESTAMP"),
]

# Igual ao notebook — WRITE_TRUNCATE (tabela de referência oficial,
# atualiza todo mês, nunca deve acumular códigos duplicados).
SCHEMA_REFERENCIA_MESES_FIPE: list[bigquery.SchemaField] = [
    bigquery.SchemaField("codigo", "INT64", mode="REQUIRED", description="Código oficial da Tabela Fipe"),
    bigquery.SchemaField("descricao_original", "STRING", mode="REQUIRED", description="Ex: abril/2026"),
    bigquery.SchemaField("ano", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("mes", "STRING", mode="REQUIRED", description="Mês em formato numérico (01 a 12)"),
    bigquery.SchemaField("mes_abrev", "STRING", mode="REQUIRED", description="Abreviação do mês (jan, fev...)"),
]

TABELAS: dict[str, list[bigquery.SchemaField]] = {
    MARCAS_VEICULOS: SCHEMA_MARCAS_VEICULOS,
    MODELOS_VEICULOS: SCHEMA_MODELOS_VEICULOS,
    MODELOS_COM_FIPE: SCHEMA_MODELOS_COM_FIPE,
    REFERENCIA_MESES_FIPE: SCHEMA_REFERENCIA_MESES_FIPE,
}

# Write disposition por tabela — a maioria dos catálogos de referência é
# recarregada por inteiro; modelos_veiculos/modelos_com_fipe crescem
# incrementalmente (dedup feita antes do upsert, não pelo write_disposition).
WRITE_DISPOSITION: dict[str, str] = {
    MARCAS_VEICULOS: "WRITE_TRUNCATE",
    MODELOS_VEICULOS: "WRITE_APPEND",
    MODELOS_COM_FIPE: "WRITE_APPEND",
    REFERENCIA_MESES_FIPE: "WRITE_TRUNCATE",
}
