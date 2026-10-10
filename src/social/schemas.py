"""
schemas.py (Redes Sociais)
Schemas BigQuery do warehouse de Redes Sociais — mesmo padrão de
`src/analytics/schemas.py`, dataset próprio (`settings.social_bigquery_dataset`).
"""
from __future__ import annotations

from google.cloud import bigquery

POSTS_SOCIAIS = "posts_sociais"
POSTS_ANALISE = "posts_analise"

SCHEMA_POSTS_SOCIAIS: list[bigquery.SchemaField] = [
    bigquery.SchemaField("post_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("run_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("rede", "STRING"),
    bigquery.SchemaField("autor", "STRING"),
    bigquery.SchemaField("texto", "STRING"),
    bigquery.SchemaField("curtidas", "INT64"),
    bigquery.SchemaField("respostas", "INT64"),
    bigquery.SchemaField("publicado_em", "STRING"),
    bigquery.SchemaField("coletado_em", "TIMESTAMP"),
]

# `output_struct` é um STRUCT nativo do BigQuery (RECORD) — mesma ideia de
# `comentarios_analise` (src/analytics/schemas.py): a resposta da LLM sempre
# no mesmo formato fixo, independente do post ou da execução.
SCHEMA_POSTS_ANALISE: list[bigquery.SchemaField] = [
    bigquery.SchemaField("post_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("run_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("rede", "STRING"),
    bigquery.SchemaField("prompt_id", "STRING"),
    bigquery.SchemaField("prompt_version", "INT64"),
    bigquery.SchemaField("provider", "STRING"),
    bigquery.SchemaField("model", "STRING"),
    bigquery.SchemaField("success", "BOOL"),
    bigquery.SchemaField("output_struct", "RECORD", fields=[
        bigquery.SchemaField("sentimento", "STRING"),
        bigquery.SchemaField("motivo", "STRING"),
        bigquery.SchemaField("adjetivos", "STRING", mode="REPEATED"),
        bigquery.SchemaField("objeto", "STRING"),
    ]),
    bigquery.SchemaField("coletado_em", "TIMESTAMP"),
]

TABELAS: dict[str, list[bigquery.SchemaField]] = {
    POSTS_SOCIAIS: SCHEMA_POSTS_SOCIAIS,
    POSTS_ANALISE: SCHEMA_POSTS_ANALISE,
}
