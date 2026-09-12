"""
warehouse.py
Warehouse do catálogo FIPE — instância de `BigQueryWarehouse`
(`src/bigquery_warehouse.py`) apontando pro dataset `analytics_ford`
(mesmo do notebook `tabela_marcas.ipynb`, mesmo projeto GCP do warehouse do
YouTube — dataset diferente).

Cliente resolvido de forma lazy pela própria classe — nunca no import
deste módulo (mesmo cuidado de `src/analytics/warehouse.py`).
"""
from __future__ import annotations

from typing import Any

from google.cloud import bigquery

from src.bigquery_warehouse import BigQueryWarehouse
from src.config import settings
from src.govmode import schemas

_warehouse = BigQueryWarehouse(
    dataset=settings.govmode_bigquery_dataset,
    project_id=settings.bigquery_project_id,
)


def status() -> dict[str, Any]:
    return _warehouse.status(configurado=settings.bigquery_configurado)


def table_id(nome: str) -> str:
    return _warehouse.table_id(nome)


def garantir_tabelas() -> None:
    """
    Cria (idempotente) as 4 tabelas do catálogo caso ainda não existam,
    mesmo sem nenhuma linha para gravar ainda.

    `upsert()` já cria a tabela na primeira gravação (`preparar_tabela`),
    mas uma consulta de leitura (ex.: notebook de população, `GET
    /gov/resumo`) pode rodar antes de qualquer gravação ter acontecido —
    sem isso, `query()` levanta `NotFound: Table ... was not found`. Chamar
    esta função antes de consultar evita esse erro sem precisar rodar a
    extração primeiro.
    """
    for nome, schema in schemas.TABELAS.items():
        _warehouse.preparar_tabela(nome, schema)


def upsert(nome: str, linhas: list[dict]) -> None:
    """Grava `linhas` na tabela `nome`, usando o schema e o
    `write_disposition` corretos definidos em `src/govmode/schemas.py`."""
    _warehouse.upsert(
        nome,
        linhas,
        schema=schemas.TABELAS[nome],
        write_disposition=schemas.WRITE_DISPOSITION.get(nome, "WRITE_APPEND"),
    )


def query(sql: str, params: list[bigquery.ScalarQueryParameter] | None = None):
    return _warehouse.query(sql, params)
