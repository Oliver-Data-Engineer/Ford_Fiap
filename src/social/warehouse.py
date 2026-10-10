"""
warehouse.py (Redes Sociais)
Warehouse próprio da aba Redes Sociais — instância de `BigQueryWarehouse`
(`src/bigquery_warehouse.py`) apontando pro dataset
`settings.social_bigquery_dataset` (mesmo projeto GCP dos demais warehouses,
dataset separado — mesmo princípio de não-unificação de docs/SPEC.md
§13.7/14.2).

Cliente resolvido de forma lazy pela própria classe — nunca no import deste
módulo.
"""
from __future__ import annotations

import logging
from typing import Any

from google.cloud import bigquery

from src.bigquery_warehouse import BigQueryWarehouse
from src.config import settings
from src.social import schemas
from src.social.models import SocialRun

logger = logging.getLogger(__name__)

_warehouse = BigQueryWarehouse(
    dataset=settings.social_bigquery_dataset,
    project_id=settings.bigquery_project_id,
)


def status() -> dict[str, Any]:
    return _warehouse.status(configurado=settings.bigquery_configurado)


def table_id(nome: str) -> str:
    return _warehouse.table_id(nome)


def garantir_tabelas() -> None:
    """Cria (idempotente) as tabelas mesmo sem nenhuma linha ainda — uma
    consulta de leitura pode rodar antes de qualquer execução da pipeline."""
    for nome, schema in schemas.TABELAS.items():
        _warehouse.preparar_tabela(nome, schema)


def query(sql: str, params: list[bigquery.ScalarQueryParameter] | None = None):
    return _warehouse.query(sql, params)


def registrar_execucao(run: SocialRun) -> None:
    """Grava os posts sintéticos e a análise de IA do run no BigQuery — só
    roda se `settings.bigquery_configurado`; sem isso, silenciosamente pulado
    (recurso aditivo, mesmo comportamento de `src/analytics/warehouse.py`)."""
    if not settings.bigquery_configurado:
        return

    posts: list[dict] = run.refined.get("posts") or []
    analises: list[dict] = run.trust.get("analise") or []

    linhas_posts = [
        {
            "post_id": p.get("post_id"),
            "run_id": run.id,
            "rede": p.get("rede"),
            "autor": p.get("autor"),
            "texto": p.get("texto"),
            "curtidas": p.get("curtidas"),
            "respostas": p.get("respostas"),
            "publicado_em": p.get("publicado_em"),
            "coletado_em": run.started_at,
        }
        for p in posts
    ]

    posts_por_id = {p.get("post_id"): p for p in posts}
    llm = run.trust.get("llm") or {}
    linhas_analise = [
        {
            "post_id": a.get("post_id"),
            "run_id": run.id,
            "rede": (posts_por_id.get(a.get("post_id")) or {}).get("rede"),
            "prompt_id": a.get("prompt_id"),
            "prompt_version": a.get("prompt_version"),
            "provider": llm.get("provider"),
            "model": llm.get("model"),
            "success": a.get("success"),
            "output_struct": a.get("struct"),
            "coletado_em": run.started_at,
        }
        for a in analises
    ]

    try:
        _warehouse.upsert(schemas.POSTS_SOCIAIS, linhas_posts, schema=schemas.SCHEMA_POSTS_SOCIAIS)
    except Exception:
        logger.exception("[social.warehouse] falha ao gravar %s do run %s", schemas.POSTS_SOCIAIS, run.id)

    try:
        _warehouse.upsert(schemas.POSTS_ANALISE, linhas_analise, schema=schemas.SCHEMA_POSTS_ANALISE)
    except Exception:
        logger.exception("[social.warehouse] falha ao gravar %s do run %s", schemas.POSTS_ANALISE, run.id)
