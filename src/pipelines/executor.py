"""
executor.py
Orquestração das execuções — a fronteira entre "quem pede" e o `PipelineEngine`.

Três formas de disparar a mesma pipeline, todas sobre o mesmo engine:

    executar_agora()        aguarda o fim (usado por chamadas curtas)
    iniciar_em_background() devolve o run_id na hora (Playground e /run da API)
    executar_pipeline_sync() ponto de entrada síncrono para as DAGs do Airflow

O andamento fica em memória durante a execução e é persistido no fim; o
frontend consulta o status por `run_id` sem bloquear a interface (Regra 35).
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from src.pipelines import db
from src.pipelines.engine import PipelineEngine, montar_steps
from src.pipelines.models import (
    Pipeline,
    PipelineRun,
    PipelineVersao,
    StatusRun,
    agora_iso,
)
from src.pipelines.store import (
    obter_pipeline,
    obter_run,
    registrar_execucao_na_pipeline,
    salvar_run,
)

logger = logging.getLogger(__name__)

# Execuções em andamento, por run_id. Um run só sai daqui depois de persistido.
_EM_ANDAMENTO: dict[str, PipelineRun] = {}
_LOCK = threading.RLock()

# Mantém referência às tasks para que o GC não as colete no meio da execução
_TAREFAS: set[asyncio.Task] = set()


# ─────────────────────────────────────────────────────────────────────────────
# Registro em memória
# ─────────────────────────────────────────────────────────────────────────────

def _registrar(run: PipelineRun) -> None:
    with _LOCK:
        _EM_ANDAMENTO[run.id] = run


def _desregistrar(run_id: str) -> None:
    with _LOCK:
        _EM_ANDAMENTO.pop(run_id, None)


def _persistir_tabelas(run: PipelineRun) -> None:
    """
    Grava RAW→REFINED→TRUST no banco local (Regra 10: tudo rastreável).

    Isolado em try/except: uma falha aqui não deve derrubar a resposta ao
    usuário nem apagar o resultado já calculado da execução.
    """
    try:
        db.salvar_tabelas_execucao(run)
    except Exception:
        logger.exception("[executor] falha ao persistir tabelas do run %s", run.id)


def obter_status(run_id: str) -> PipelineRun | None:
    """
    Estado de uma execução — do registro em memória se estiver rodando,
    do histórico persistido caso já tenha terminado.
    """
    with _LOCK:
        em_memoria = _EM_ANDAMENTO.get(run_id)
    return em_memoria or obter_run(run_id)


# ─────────────────────────────────────────────────────────────────────────────
# Execução
# ─────────────────────────────────────────────────────────────────────────────

async def executar_agora(
    config: PipelineVersao,
    *,
    pipeline_id: str | None = None,
    pipeline_name: str = "Playground",
    pipeline_version: int | None = None,
    origem: str = "playground",
    persistir: bool = True,
) -> PipelineRun:
    """Executa a configuração e devolve o run completo, já persistido."""
    run = PipelineRun(
        pipeline_id=pipeline_id,
        pipeline_name=pipeline_name,
        pipeline_version=pipeline_version,
        origem=origem,
    )
    _registrar(run)

    engine = PipelineEngine()
    try:
        await engine.executar(config, run)
    finally:
        if persistir:
            salvar_run(run)
            _persistir_tabelas(run)
            if pipeline_id:
                registrar_execucao_na_pipeline(pipeline_id, run.status.value, agora_iso())
        _desregistrar(run.id)

    logger.info(
        "[executor] run %s (%s) terminou com status '%s'",
        run.id, pipeline_name, run.status.value,
    )
    return run


def iniciar_em_background(
    config: PipelineVersao,
    *,
    pipeline_id: str | None = None,
    pipeline_name: str = "Playground",
    pipeline_version: int | None = None,
    origem: str = "playground",
) -> PipelineRun:
    """
    Dispara a execução e devolve imediatamente o run em estado `pending`.

    O chamador acompanha por `GET /pipelines/runs/{run_id}`.
    """
    run = PipelineRun(
        pipeline_id=pipeline_id,
        pipeline_name=pipeline_name,
        pipeline_version=pipeline_version,
        origem=origem,
        # Régua completa já na resposta imediata — o frontend desenha a
        # pipeline antes da primeira etapa começar.
        steps=montar_steps(config),
    )
    _registrar(run)

    async def trabalho() -> None:
        engine = PipelineEngine()
        try:
            await engine.executar(config, run)
        except Exception as exc:
            # O engine já trata falhas por etapa; isto cobre o improvável
            logger.exception("[executor] run %s abortou", run.id)
            run.status = StatusRun.FALHOU
            run.finished_at = agora_iso()
            run.trust.setdefault("erro_execucao", str(exc))
        finally:
            salvar_run(run)
            _persistir_tabelas(run)
            if pipeline_id:
                registrar_execucao_na_pipeline(pipeline_id, run.status.value, agora_iso())
            # O run permanece em memória por um instante para que o último
            # polling do frontend leia o estado final antes do fallback em disco.
            await asyncio.sleep(2)
            _desregistrar(run.id)

    tarefa = asyncio.create_task(trabalho())
    _TAREFAS.add(tarefa)
    tarefa.add_done_callback(_TAREFAS.discard)

    return run


# ─────────────────────────────────────────────────────────────────────────────
# Execução de uma pipeline persistida
# ─────────────────────────────────────────────────────────────────────────────

def _config_da_pipeline(pipeline: Pipeline, version: int | None) -> PipelineVersao:
    return pipeline.na_versao(version)


def disparar_pipeline(
    pipeline_id: str,
    *,
    version: int | None = None,
    origem: str = "manual",
) -> PipelineRun:
    """Executa uma pipeline persistida em background (chamada pela API)."""
    pipeline = obter_pipeline(pipeline_id)
    if pipeline is None:
        raise ValueError(f"Pipeline '{pipeline_id}' não encontrada.")

    config = _config_da_pipeline(pipeline, version)
    return iniciar_em_background(
        config,
        pipeline_id=pipeline.id,
        pipeline_name=pipeline.name,
        pipeline_version=config.version,
        origem=origem,
    )


def executar_pipeline_sync(
    pipeline_id: str,
    *,
    version: int | None = None,
    origem: str = "airflow",
) -> dict[str, Any]:
    """
    Ponto de entrada **síncrono** — é o que a DAG do Airflow chama.

    Roda exatamente o mesmo `PipelineEngine` do Playground (Regra 4: não
    existem duas implementações da regra de negócio) e devolve um resumo
    serializável, adequado para XCom.

    Levanta RuntimeError se a execução falhar por completo, para que a task
    apareça como falha no Airflow.
    """
    pipeline = obter_pipeline(pipeline_id)
    if pipeline is None:
        raise ValueError(f"Pipeline '{pipeline_id}' não encontrada.")

    config = _config_da_pipeline(pipeline, version)

    run = asyncio.run(
        executar_agora(
            config,
            pipeline_id=pipeline.id,
            pipeline_name=pipeline.name,
            pipeline_version=config.version,
            origem=origem,
        )
    )

    resumo = {
        "run_id": run.id,
        "pipeline_id": run.pipeline_id,
        "pipeline_version": run.pipeline_version,
        "status": run.status.value,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "steps": [
            {"type": s.type.value, "status": s.status.value, "resumo": s.resumo,
             "error": s.error.model_dump() if s.error else None}
            for s in run.steps
        ],
        "total_videos": run.refined.get("estatisticas", {}).get("total_videos", 0),
        "total_insights": len(run.trust.get("insights", [])),
        "usage": run.usage,
    }

    if run.status == StatusRun.FALHOU:
        falhas = [s for s in run.steps if s.error]
        detalhe = falhas[0].error.message if falhas else "motivo não registrado"
        raise RuntimeError(f"Execução {run.id} falhou: {detalhe}")

    return resumo
