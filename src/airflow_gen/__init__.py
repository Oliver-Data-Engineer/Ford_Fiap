"""
src.airflow_gen — Geração de DAGs do Airflow a partir das pipelines salvas.

O código gerado é um invólucro fino: as tasks chamam `src.pipelines.executor`,
o mesmo motor usado pelo Playground.
"""
from src.airflow_gen.generator import (
    descrever_schedule,
    gerar_codigo,
    gerar_dag,
    montar_cron,
    montar_dag_id,
    slug,
)

__all__ = [
    "gerar_dag",
    "gerar_codigo",
    "montar_cron",
    "montar_dag_id",
    "descrever_schedule",
    "slug",
]
