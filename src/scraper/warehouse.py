"""
warehouse.py
Warehouse do Scraper Mode — instância de `BigQueryWarehouse`
(`src/bigquery_warehouse.py`) apontando pro dataset
`settings.scraper_bigquery_dataset` (mesmo padrão de `src/govmode/warehouse.py`).

Cliente resolvido de forma lazy pela própria classe — nunca no import
deste módulo.
"""
from __future__ import annotations

from typing import Any

from google.cloud import bigquery

from src.bigquery_warehouse import BigQueryWarehouse
from src.config import settings
from src.scraper import schemas

_warehouse = BigQueryWarehouse(
    dataset=settings.scraper_bigquery_dataset,
    project_id=settings.bigquery_project_id,
)


def status() -> dict[str, Any]:
    return _warehouse.status(configurado=settings.bigquery_configurado)


def table_id(nome: str) -> str:
    return _warehouse.table_id(nome)


def garantir_tabelas() -> None:
    """Cria (idempotente) as 3 tabelas caso ainda não existam — evita
    'table not found' numa leitura antes de qualquer execução ter gravado
    nada (mesmo cuidado de `src/govmode/warehouse.py::garantir_tabelas`)."""
    for nome, schema in schemas.TABELAS.items():
        _warehouse.preparar_tabela(nome, schema)


def upsert(nome: str, linhas: list[dict]) -> None:
    _warehouse.upsert(
        nome,
        linhas,
        schema=schemas.TABELAS[nome],
        write_disposition=schemas.WRITE_DISPOSITION.get(nome, "WRITE_APPEND"),
    )


def query(sql: str, params: list[bigquery.ScalarQueryParameter] | None = None):
    return _warehouse.query(sql, params)
