"""
schemas.py
Schemas BigQuery do Scraper Mode — mesmo padrão de `src/govmode/schemas.py`,
uma constante por tabela.

As 3 tabelas são logs históricos (append), não catálogos recarregados: cada
execução carimba `coletado_em` e soma linhas. `scraper_ficha_tecnica` é
append puro (valores de atributo podem mudar ao longo do tempo e o
histórico é útil); `scraper_anuncios`/`scraper_comentarios` são
deduplicadas por anti-join antes do upsert (`engine.py`) pra não duplicar
o mesmo anúncio/comentário a cada execução repetida.
"""
from __future__ import annotations

from google.cloud import bigquery

FICHA_TECNICA = "scraper_ficha_tecnica"
ANUNCIOS = "scraper_anuncios"
COMENTARIOS = "scraper_comentarios"

SCHEMA_FICHA_TECNICA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("site_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("site_nome", "STRING"),
    bigquery.SchemaField("marca", "STRING"),
    bigquery.SchemaField("modelo", "STRING"),
    bigquery.SchemaField("versao", "STRING"),
    bigquery.SchemaField("ano", "STRING"),
    bigquery.SchemaField("atributo", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("valor", "STRING"),
    bigquery.SchemaField("url_origem", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("coletado_em", "TIMESTAMP", mode="REQUIRED"),
]

SCHEMA_ANUNCIOS: list[bigquery.SchemaField] = [
    bigquery.SchemaField("site_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("site_nome", "STRING"),
    bigquery.SchemaField("marca", "STRING"),
    bigquery.SchemaField("modelo", "STRING"),
    bigquery.SchemaField("versao", "STRING"),
    bigquery.SchemaField("ano", "STRING"),
    bigquery.SchemaField("preco_texto", "STRING"),
    bigquery.SchemaField("preco_numerico", "FLOAT64"),
    bigquery.SchemaField("url_anuncio", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("localizacao", "STRING"),
    bigquery.SchemaField("vendedor", "STRING"),
    bigquery.SchemaField("publicado_em", "STRING"),
    bigquery.SchemaField("coletado_em", "TIMESTAMP", mode="REQUIRED"),
]

SCHEMA_COMENTARIOS: list[bigquery.SchemaField] = [
    bigquery.SchemaField("site_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("site_nome", "STRING"),
    bigquery.SchemaField("marca", "STRING"),
    bigquery.SchemaField("modelo", "STRING"),
    bigquery.SchemaField("versao", "STRING"),
    bigquery.SchemaField("ano", "STRING"),
    bigquery.SchemaField("autor", "STRING"),
    bigquery.SchemaField("texto", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("nota", "FLOAT64"),
    bigquery.SchemaField("publicado_em", "STRING"),
    bigquery.SchemaField("url_origem", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("coletado_em", "TIMESTAMP", mode="REQUIRED"),
]

TABELAS: dict[str, list[bigquery.SchemaField]] = {
    FICHA_TECNICA: SCHEMA_FICHA_TECNICA,
    ANUNCIOS: SCHEMA_ANUNCIOS,
    COMENTARIOS: SCHEMA_COMENTARIOS,
}

# Todas append — a dedupe de anuncios/comentarios acontece por anti-join
# antes do upsert (engine.py), não pelo write_disposition.
WRITE_DISPOSITION: dict[str, str] = {
    FICHA_TECNICA: "WRITE_APPEND",
    ANUNCIOS: "WRITE_APPEND",
    COMENTARIOS: "WRITE_APPEND",
}
