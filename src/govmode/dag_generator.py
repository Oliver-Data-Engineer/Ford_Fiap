"""
dag_generator.py
Gera o arquivo de DAG do Airflow a partir de uma `GovPipeline` persistida —
mesmo princípio de `src/airflow_gen/generator.py` (a DAG não reimplementa
nada, só chama `src.govmode.executor`), mas com um template próprio: sem
LLM, checando `FIPE_API_TOKEN` em vez de `YOUTUBE_API_KEY`, publicando o
progresso da extração em vez de contagem de vídeos/insights.

Reaproveita `montar_cron`/`descrever_schedule`/`slug` de
`src.airflow_gen.generator` por serem genéricos (matemática de cron e
descrição de agenda, sem nada de YouTube) — não duplica essa lógica.
"""
from __future__ import annotations

import logging
from pathlib import Path

from src.airflow_gen.generator import descrever_schedule, montar_cron, slug
from src.config import settings
from src.govmode.models import GovPipeline

logger = logging.getLogger(__name__)

TEMPLATE = '''"""
{dag_id}.py — DAG gerada automaticamente pelo Gov Mode (catálogo FIPE).

Pipeline : {nome}
ID       : {pipeline_id}
Versão   : {versao}
Agenda   : {agenda_legivel}
Gerada em: {gerado_em}

NÃO EDITE À MÃO — regenere pela aba Gov Mode da aplicação.

Esta DAG não reimplementa a regra de negócio: cada task chama a mesma
`GovEngine` usada no painel. Credenciais (GOOGLE_APPLICATION_CREDENTIALS,
FIPE_API_TOKEN) são lidas do ambiente do worker — nunca deste arquivo.
"""
import os
import sys
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator

# Torna o pacote `src` importável quando a DAG roda a partir de airflow/dags/
RAIZ_PROJETO = os.environ.get("FORD_FIAP_HOME", {raiz!r})
if RAIZ_PROJETO not in sys.path:
    sys.path.insert(0, RAIZ_PROJETO)

PIPELINE_ID = {pipeline_id!r}
PIPELINE_VERSION = {versao}

default_args = {{
    "owner": "ford-fiap",
    "depends_on_past": False,
    "retries": {retries},
    "retry_delay": timedelta(minutes={retry_delay}),
}}


# ─────────────────────────────────────────────────────────────────────────────
# Tasks — cada uma delega para src.govmode
# ─────────────────────────────────────────────────────────────────────────────

def _config():
    """Configuração congelada da versão que esta DAG representa."""
    from src.govmode.store import obter_pipeline

    pipeline = obter_pipeline(PIPELINE_ID)
    if pipeline is None:
        raise ValueError(f"Pipeline Gov {{PIPELINE_ID}} não encontrada no store.")
    return pipeline, pipeline.na_versao(PIPELINE_VERSION)


def validar_configuracao(**_):
    """Falha cedo se a pipeline sumiu ou se falta credencial no worker."""
    pipeline, config = _config()

    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS não configurada no ambiente do worker.")

    tipos_step = [p.type for p in config.steps]
    if "fipe" in tipos_step and not os.environ.get("FIPE_API_TOKEN"):
        raise RuntimeError("FIPE_API_TOKEN não configurado no worker — necessário para o passo de código FIPE.")

    return {{"pipeline": pipeline.name, "version": config.version, "marcas_alvo": config.source.marcas_alvo}}


def executar_pipeline(**contexto):
    """
    Executa a pipeline inteira (marcas → modelos → fipe → meses de referência).

    Reaproveita `executor.executar_pipeline_sync`, o mesmo caminho do botão
    "Executar agora" do painel Gov Mode.
    """
    from src.govmode.executor import executar_pipeline_sync

    return executar_pipeline_sync(PIPELINE_ID, version=PIPELINE_VERSION, origem="airflow")


def publicar_resultado(**contexto):
    """Lê o resumo da execução e registra o desfecho no log da task."""
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


# ─────────────────────────────────────────────────────────────────────────────
# DAG
# ─────────────────────────────────────────────────────────────────────────────

with DAG(
    dag_id={dag_id!r},
    description={descricao!r},
    default_args=default_args,
    schedule={schedule!r},
    start_date=datetime({ano}, {mes}, {dia}),
    catchup={catchup},
    max_active_runs=1,
    tags=["gov", "fipe", "bigquery"],
) as dag:

    validar = PythonOperator(
        task_id="validar_configuracao",
        python_callable=validar_configuracao,
    )

    executar = PythonOperator(
        task_id="executar_pipeline",
        python_callable=executar_pipeline,
    )

    publicar = PythonOperator(
        task_id="publicar_resultado",
        python_callable=publicar_resultado,
    )

    validar >> executar >> publicar
'''


def montar_dag_id(pipeline: GovPipeline) -> str:
    return f"gov_fipe_{slug(pipeline.name)}"


def gerar_codigo(pipeline: GovPipeline) -> str:
    """Devolve o código-fonte da DAG como string (sem gravar em disco)."""
    from src.config import BASE_DIR
    from src.pipelines.models import agora_iso

    config = pipeline.snapshot()
    agora = agora_iso()

    # start_date fixo na data de geração — evita backfill acidental
    data = agora[:10].split("-")

    return TEMPLATE.format(
        dag_id=montar_dag_id(pipeline),
        nome=pipeline.name,
        pipeline_id=pipeline.id,
        versao=pipeline.version,
        descricao=pipeline.description or f"Catálogo FIPE: {pipeline.name}",
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


def gerar_dag(pipeline: GovPipeline) -> dict[str, str]:
    """
    Gera e grava o arquivo da DAG em `airflow/dags/`.

    Devolve `{dag_id, caminho, codigo}` — a UI usa os três (nome, download, view).
    """
    codigo = gerar_codigo(pipeline)
    dag_id = montar_dag_id(pipeline)

    destino: Path = settings.dags_dir / f"{dag_id}.py"
    destino.write_text(codigo, encoding="utf-8")

    logger.info("[govmode] DAG '%s' gravada em %s", dag_id, destino)

    return {
        "dag_id": dag_id,
        "caminho": str(destino),
        "caminho_relativo": f"airflow/dags/{dag_id}.py",
        "codigo": codigo,
    }
