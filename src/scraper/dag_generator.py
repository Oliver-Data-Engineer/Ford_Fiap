"""
dag_generator.py
Gera o arquivo de DAG do Airflow a partir de um `SiteScraper` persistido —
mesmo princípio de `src/govmode/dag_generator.py`: a DAG não reimplementa
nada, só chama `src.scraper.executor`.

Reaproveita `montar_cron`/`descrever_schedule`/`slug` de
`src.airflow_gen.generator` (genéricos, sem nada de YouTube/FIPE).
"""
from __future__ import annotations

import logging
from pathlib import Path

from src.airflow_gen.generator import descrever_schedule, montar_cron, slug
from src.config import settings
from src.scraper.models import SiteScraper

logger = logging.getLogger(__name__)

TEMPLATE = '''"""
{dag_id}.py — DAG gerada automaticamente pelo Scraper Mode.

Site     : {nome}
ID       : {site_id}
Versão   : {versao}
Agenda   : {agenda_legivel}
Gerada em: {gerado_em}

NÃO EDITE À MÃO — regenere pela aba Scraper Mode da aplicação.

Esta DAG não reimplementa a regra de negócio: a task chama a mesma
`ScraperEngine` usada no painel. Credenciais (GOOGLE_APPLICATION_CREDENTIALS,
credenciais de LLM/proxy) são lidas do ambiente do worker — nunca deste
arquivo. Requer Playwright + Chromium instalados no worker
(`playwright install chromium`).
"""
import os
import sys
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator

RAIZ_PROJETO = os.environ.get("FORD_FIAP_HOME", {raiz!r})
if RAIZ_PROJETO not in sys.path:
    sys.path.insert(0, RAIZ_PROJETO)

SITE_ID = {site_id!r}
SITE_VERSION = {versao}

default_args = {{
    "owner": "ford-fiap",
    "depends_on_past": False,
    "retries": {retries},
    "retry_delay": timedelta(minutes={retry_delay}),
}}


def _site():
    from src.scraper.store import obter_site

    site = obter_site(SITE_ID)
    if site is None:
        raise ValueError(f"Site do scraper {{SITE_ID}} não encontrado no store.")
    return site, site.na_versao(SITE_VERSION)


def validar_configuracao(**_):
    """Falha cedo se o site sumiu, não foi registrado, ou falta credencial."""
    site, config = _site()

    if not site.registrado:
        raise RuntimeError(f"Site '{{site.name}}' ainda não foi registrado (tela de teste pendente).")

    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS não configurada no ambiente do worker.")

    return {{"site": site.name, "version": config.version, "objetivos": [o.value for o in config.objetivos]}}


def executar_pipeline(**contexto):
    """Reaproveita `executor.executar_pipeline_sync`, o mesmo caminho do
    botão "Executar agora" do painel Scraper Mode."""
    from src.scraper.executor import executar_pipeline_sync

    return executar_pipeline_sync(SITE_ID, version=SITE_VERSION, origem="airflow")


def publicar_resultado(**contexto):
    resumo = contexto["ti"].xcom_pull(task_ids="executar_pipeline") or {{}}

    print(f"run_id .......... {{resumo.get('run_id')}}")
    print(f"status .......... {{resumo.get('status')}}")
    print(f"progresso ....... {{resumo.get('progresso')}}")

    for step in resumo.get("steps", []):
        marca = "OK " if step["status"] == "success" else step["status"].upper()
        print(f"  [{{marca}}] {{step['type']}} — {{step.get('resumo') or ''}}")
        if step.get("error"):
            print(f"        erro: {{step['error'].get('message')}}")

    return resumo


with DAG(
    dag_id={dag_id!r},
    description={descricao!r},
    default_args=default_args,
    schedule={schedule!r},
    start_date=datetime({ano}, {mes}, {dia}),
    catchup={catchup},
    max_active_runs=1,
    tags=["scraper", "carros", "bigquery"],
) as dag:

    validar = PythonOperator(task_id="validar_configuracao", python_callable=validar_configuracao)
    executar = PythonOperator(task_id="executar_pipeline", python_callable=executar_pipeline)
    publicar = PythonOperator(task_id="publicar_resultado", python_callable=publicar_resultado)

    validar >> executar >> publicar
'''


def montar_dag_id(site: SiteScraper) -> str:
    return f"scraper_{slug(site.name)}"


def gerar_codigo(site: SiteScraper) -> str:
    """Devolve o código-fonte da DAG como string (sem gravar em disco)."""
    from src.config import BASE_DIR
    from src.pipelines.models import agora_iso

    config = site.snapshot()
    agora = agora_iso()
    data = agora[:10].split("-")

    return TEMPLATE.format(
        dag_id=montar_dag_id(site),
        nome=site.name,
        site_id=site.id,
        versao=site.version,
        descricao=site.description or f"Scraper Mode: {site.name}",
        agenda_legivel=descrever_schedule(config.schedule),
        gerado_em=agora,
        raiz=str(BASE_DIR),
        schedule=montar_cron(config.schedule),
        catchup=config.schedule.catchup,
        retries=config.schedule.retries,
        retry_delay=config.schedule.retry_delay_minutos,
        ano=int(data[0]),
        mes=int(data[1]),
        dia=int(data[2]),
    )


def gerar_dag(site: SiteScraper) -> dict[str, str]:
    """Gera e grava o arquivo da DAG em `airflow/dags/`."""
    codigo = gerar_codigo(site)
    dag_id = montar_dag_id(site)

    destino: Path = settings.dags_dir / f"{dag_id}.py"
    destino.write_text(codigo, encoding="utf-8")

    logger.info("[scraper] DAG '%s' gravada em %s", dag_id, destino)

    return {
        "dag_id": dag_id,
        "caminho": str(destino),
        "caminho_relativo": f"airflow/dags/{dag_id}.py",
        "codigo": codigo,
    }
