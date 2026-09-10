"""
generator.py
Gera o arquivo de DAG do Airflow a partir de uma Pipeline persistida.

Princípio (Regra 4 da spec): a DAG **não** reimplementa nada.  Cada task chama
`src.pipelines.executor` / `src.pipelines.engine` — os mesmos objetos que o
Playground usa.  O código gerado é fino de propósito.

Princípio (Regra 33): nenhuma credencial entra no arquivo.  A DAG lê as chaves
do ambiente do worker, exatamente como a aplicação web.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path

from src.config import settings
from src.pipelines.models import ConfigSchedule, Pipeline

logger = logging.getLogger(__name__)

# Mapa mode → expressão cron; "cron" usa a expressão informada pelo usuário
_DIAS_CRON = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6}


def slug(texto: str) -> str:
    """
    Converte um nome livre em identificador válido de dag_id/arquivo.

    "Análise Toyota Corolla" → "analise_toyota_corolla"
    """
    base = unicodedata.normalize("NFKD", texto or "")
    base = "".join(c for c in base if not unicodedata.combining(c))
    base = base.lower()
    base = re.sub(r"[^a-z0-9]+", "_", base).strip("_")
    return base or "pipeline"


def montar_cron(schedule: ConfigSchedule) -> str | None:
    """
    Traduz a agenda da UI para uma expressão cron.

    `None` significa execução apenas manual (schedule=None no Airflow).
    """
    modo = (schedule.mode or "manual").lower()

    if modo == "manual":
        return None
    if modo == "cron":
        return (schedule.cron or "").strip() or None
    if modo == "hourly":
        return "0 * * * *"

    # daily/weekly/monthly compartilham o horário HH:MM
    try:
        hora, minuto = (int(p) for p in (schedule.hora or "08:00").split(":")[:2])
    except (ValueError, TypeError):
        hora, minuto = 8, 0

    hora = max(0, min(23, hora))
    minuto = max(0, min(59, minuto))

    if modo == "daily":
        return f"{minuto} {hora} * * *"
    if modo == "weekly":
        dia = _DIAS_CRON.get(schedule.dia_semana, 1)
        return f"{minuto} {hora} * * {dia}"
    if modo == "monthly":
        dia = max(1, min(28, schedule.dia_mes or 1))  # 28 evita meses curtos
        return f"{minuto} {hora} {dia} * *"

    return None


def descrever_schedule(schedule: ConfigSchedule) -> str:
    """Descrição legível da agenda, exibida na aba Pipelines."""
    modo = (schedule.mode or "manual").lower()
    dias = ["domingo", "segunda", "terça", "quarta", "quinta", "sexta", "sábado"]

    if modo == "manual":
        return "Manual"
    if modo == "hourly":
        return "A cada hora"
    if modo == "daily":
        return f"Diário às {schedule.hora}"
    if modo == "weekly":
        return f"Semanal — {dias[schedule.dia_semana % 7]} às {schedule.hora}"
    if modo == "monthly":
        return f"Mensal — dia {schedule.dia_mes} às {schedule.hora}"
    if modo == "cron":
        return f"Cron: {schedule.cron or '(não definido)'}"
    return modo


# ─────────────────────────────────────────────────────────────────────────────
# Template
# ─────────────────────────────────────────────────────────────────────────────

TEMPLATE = '''"""
{dag_id}.py — DAG gerada automaticamente pelo YouTube Pipeline Playground.

Pipeline : {nome}
ID       : {pipeline_id}
Versão   : {versao}
Agenda   : {agenda_legivel}
Gerada em: {gerado_em}

NÃO EDITE À MÃO — regenere pela aba Pipelines da aplicação.

Esta DAG não reimplementa a regra de negócio: cada task chama o mesmo
`PipelineEngine` usado pelo Playground.  Credenciais (YOUTUBE_API_KEY,
{env_llm}) são lidas do ambiente do worker — nunca deste arquivo.
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
# Tasks — cada uma delega para src.pipelines
# ─────────────────────────────────────────────────────────────────────────────

def _config():
    """Configuração congelada da versão que esta DAG representa."""
    from src.pipelines.store import obter_pipeline

    pipeline = obter_pipeline(PIPELINE_ID)
    if pipeline is None:
        raise ValueError(f"Pipeline {{PIPELINE_ID}} não encontrada no store.")
    return pipeline, pipeline.na_versao(PIPELINE_VERSION)


def validar_configuracao(**_):
    """Falha cedo se a pipeline sumiu ou se falta credencial no worker."""
    pipeline, config = _config()

    if not os.environ.get("YOUTUBE_API_KEY"):
        raise RuntimeError("YOUTUBE_API_KEY não configurada no ambiente do worker.")

    provider = config.llm.provider
    prompts = [p for p in config.steps if p.type == "agent"]
    if prompts and provider != "ollama" and not os.environ.get({env_llm!r}):
        raise RuntimeError(
            f"{env_llm} não configurada no worker — necessária para o provider '{{provider}}'."
        )

    return {{"pipeline": pipeline.name, "version": config.version, "steps": len(config.steps)}}


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
    resumo = contexto["ti"].xcom_pull(task_ids="executar_pipeline") or {{}}

    print(f"run_id .......... {{resumo.get('run_id')}}")
    print(f"status .......... {{resumo.get('status')}}")
    print(f"vídeos .......... {{resumo.get('total_videos')}}")
    print(f"insights ........ {{resumo.get('total_insights')}}")
    print(f"tokens .......... {{resumo.get('usage', {{}}).get('total_tokens')}}")

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
'''


# ─────────────────────────────────────────────────────────────────────────────
# Geração
# ─────────────────────────────────────────────────────────────────────────────

def montar_dag_id(pipeline: Pipeline) -> str:
    return f"youtube_{slug(pipeline.name)}"


def gerar_codigo(pipeline: Pipeline) -> str:
    """Devolve o código-fonte da DAG como string (sem gravar em disco)."""
    from src.config import BASE_DIR, ENV_VAR_POR_PROVIDER
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
        descricao=pipeline.description or f"Análise automatizada: {pipeline.name}",
        agenda_legivel=descrever_schedule(config.schedule),
        gerado_em=agora,
        raiz=str(BASE_DIR),
        schedule=montar_cron(config.schedule),
        catchup=config.schedule.catchup,
        retries=config.schedule.retries,
        retry_delay=config.schedule.retry_delay_minutos,
        env_llm=ENV_VAR_POR_PROVIDER.get(config.llm.provider, "LLM_API_KEY"),
        ano=int(data[0]),
        mes=int(data[1]),
        dia=int(data[2]),
    )


def gerar_dag(pipeline: Pipeline) -> dict[str, str]:
    """
    Gera e grava o arquivo da DAG em `airflow/dags/`.

    Devolve {dag_id, caminho, codigo} — a UI usa os três (nome, download, view).
    """
    codigo = gerar_codigo(pipeline)
    dag_id = montar_dag_id(pipeline)

    destino: Path = settings.dags_dir / f"{dag_id}.py"
    destino.write_text(codigo, encoding="utf-8")

    logger.info("[airflow] DAG '%s' gravada em %s", dag_id, destino)

    return {
        "dag_id": dag_id,
        "caminho": str(destino),
        "caminho_relativo": f"airflow/dags/{dag_id}.py",
        "codigo": codigo,
    }
