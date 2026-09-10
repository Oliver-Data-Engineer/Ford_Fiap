"""
youtube_analise_ford_territory.py — DAG gerada automaticamente pelo YouTube Pipeline Playground.

Pipeline : Analise Ford Territory
ID       : pipeline_741b11466ba3
Versão   : 1
Agenda   : Diário às 08:00
Gerada em: 2026-09-10T05:03:45.370245+00:00

NÃO EDITE À MÃO — regenere pela aba Pipelines da aplicação.

Esta DAG não reimplementa a regra de negócio: cada task chama o mesmo
`PipelineEngine` usado pelo Playground.  Credenciais (YOUTUBE_API_KEY,
GROQ_API_KEY) são lidas do ambiente do worker — nunca deste arquivo.
"""
import os
import sys
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator

# Torna o pacote `src` importável quando a DAG roda a partir de airflow/dags/
RAIZ_PROJETO = os.environ.get("FORD_FIAP_HOME", 'C:\\Users\\guilh\\Documents\\Project\\Git\\Ford_Fiap')
if RAIZ_PROJETO not in sys.path:
    sys.path.insert(0, RAIZ_PROJETO)

PIPELINE_ID = 'pipeline_741b11466ba3'
PIPELINE_VERSION = 1

default_args = {
    "owner": "ford-fiap",
    "depends_on_past": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}


# ─────────────────────────────────────────────────────────────────────────────
# Tasks — cada uma delega para src.pipelines
# ─────────────────────────────────────────────────────────────────────────────

def _config():
    """Configuração congelada da versão que esta DAG representa."""
    from src.pipelines.store import obter_pipeline

    pipeline = obter_pipeline(PIPELINE_ID)
    if pipeline is None:
        raise ValueError(f"Pipeline {PIPELINE_ID} não encontrada no store.")
    return pipeline, pipeline.na_versao(PIPELINE_VERSION)


def validar_configuracao(**_):
    """Falha cedo se a pipeline sumiu ou se falta credencial no worker."""
    pipeline, config = _config()

    if not os.environ.get("YOUTUBE_API_KEY"):
        raise RuntimeError("YOUTUBE_API_KEY não configurada no ambiente do worker.")

    provider = config.llm.provider
    prompts = [p for p in config.steps if p.type == "agent"]
    if prompts and provider != "ollama" and not os.environ.get('GROQ_API_KEY'):
        raise RuntimeError(
            f"GROQ_API_KEY não configurada no worker — necessária para o provider '{provider}'."
        )

    return {"pipeline": pipeline.name, "version": config.version, "steps": len(config.steps)}


def executar_pipeline(**contexto):
    """
    Executa a pipeline inteira (SOURCE → RAW → REFINED → AGENTE → TRUST).

    Reaproveita `executor.executar_pipeline_sync`, o mesmo caminho do botão
    "Executar" do Playground.
    """
    from src.pipelines.executor import executar_pipeline_sync

    return executar_pipeline_sync(
        PIPELINE_ID, version=PIPELINE_VERSION, origem="airflow"
    )


def publicar_resultado(**contexto):
    """Lê o resumo da execução e registra o desfecho no log da task."""
    resumo = contexto["ti"].xcom_pull(task_ids="executar_pipeline") or {}

    print(f"run_id .......... {resumo.get('run_id')}")
    print(f"status .......... {resumo.get('status')}")
    print(f"vídeos .......... {resumo.get('total_videos')}")
    print(f"insights ........ {resumo.get('total_insights')}")
    print(f"tokens .......... {resumo.get('usage', {}).get('total_tokens')}")

    for step in resumo.get("steps", []):
        marca = "OK " if step["status"] == "success" else step["status"].upper()
        print(f"  [{marca}] {step['type']} — {step.get('resumo') or ''}")
        if step.get("error"):
            print(f"        erro: {step['error'].get('message')}")

    return resumo


# ─────────────────────────────────────────────────────────────────────────────
# DAG
# ─────────────────────────────────────────────────────────────────────────────

with DAG(
    dag_id='youtube_analise_ford_territory',
    description='Percepcoes do publico sobre o Territory 2026',
    default_args=default_args,
    schedule='0 8 * * *',
    start_date=datetime(2026, 9, 10),
    catchup=False,
    max_active_runs=1,
    tags=["youtube", "pipeline", "llm"],
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
