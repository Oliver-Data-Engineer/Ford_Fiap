"""
executor.py
Orquestração das execuções do Gov Mode — mesmo padrão de
`src/pipelines/executor.py` (registro em memória + task assíncrona de
background + histórico persistido + polling por `run_id`), mas para
`GovEngine`/`GovPipelineRun`.

Diferença central: aqui não existe uma etapa separada de "persistir
tabelas" no fim — cada passo do `GovEngine` já grava no BigQuery conforme
avança (`src/govmode/engine.py`, via `src/govmode/warehouse.py`). O que
este módulo persiste no fim é só o **registro da execução em si**
(`GovPipelineRun`, para histórico/auditoria), não os dados extraídos.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from src.govmode import store
from src.govmode.engine import GovEngine, montar_steps
from src.govmode.models import GovPipeline, GovPipelineRun, GovPipelineVersao, StatusRun
from src.pipelines.models import agora_iso

logger = logging.getLogger(__name__)

_EM_ANDAMENTO: dict[str, GovPipelineRun] = {}
_LOCK = threading.RLock()
_TAREFAS: set[asyncio.Task] = set()


def _registrar(run: GovPipelineRun) -> None:
    with _LOCK:
        _EM_ANDAMENTO[run.id] = run


def _desregistrar(run_id: str) -> None:
    with _LOCK:
        _EM_ANDAMENTO.pop(run_id, None)


def obter_status(run_id: str) -> GovPipelineRun | None:
    """Estado de uma execução — do registro em memória se estiver
    rodando, do histórico persistido caso já tenha terminado."""
    with _LOCK:
        em_memoria = _EM_ANDAMENTO.get(run_id)
    return em_memoria or store.obter_run(run_id)


# ─────────────────────────────────────────────────────────────────────────────
# Execução
# ─────────────────────────────────────────────────────────────────────────────

async def executar_agora(
    config: GovPipelineVersao,
    *,
    pipeline_id: str | None = None,
    pipeline_name: str = "Gov Mode",
    pipeline_version: int | None = None,
    origem: str = "manual",
) -> GovPipelineRun:
    """Executa a configuração e devolve o run completo, já persistido."""
    run = GovPipelineRun(pipeline_id=pipeline_id, pipeline_name=pipeline_name, pipeline_version=pipeline_version, origem=origem)
    _registrar(run)

    motor = GovEngine()
    try:
        await motor.executar(config, run)
    finally:
        store.salvar_run(run)
        if pipeline_id:
            store.registrar_execucao_na_pipeline(pipeline_id, run.status.value, run.finished_at or "")
        _desregistrar(run.id)

    logger.info("[govmode] run %s (%s) terminou com status '%s'", run.id, pipeline_name, run.status.value)
    return run


def iniciar_em_background(
    config: GovPipelineVersao,
    *,
    pipeline_id: str | None = None,
    pipeline_name: str = "Gov Mode",
    pipeline_version: int | None = None,
    origem: str = "manual",
) -> GovPipelineRun:
    """Dispara a execução e devolve imediatamente o run em estado
    `pending`. O chamador acompanha por `GET /gov/runs/{run_id}`."""
    run = GovPipelineRun(
        pipeline_id=pipeline_id, pipeline_name=pipeline_name, pipeline_version=pipeline_version,
        origem=origem, steps=montar_steps(config),
    )
    _registrar(run)

    async def trabalho() -> None:
        motor = GovEngine()
        try:
            await motor.executar(config, run)
        except Exception as exc:
            logger.exception("[govmode] run %s abortou", run.id)
            run.status = StatusRun.FALHOU
            run.finished_at = agora_iso()
            run.resultado.setdefault("erro_execucao", str(exc))
        finally:
            store.salvar_run(run)
            if pipeline_id:
                store.registrar_execucao_na_pipeline(pipeline_id, run.status.value, run.finished_at or "")
            await asyncio.sleep(2)
            _desregistrar(run.id)

    tarefa = asyncio.create_task(trabalho())
    _TAREFAS.add(tarefa)
    tarefa.add_done_callback(_TAREFAS.discard)

    return run


# ─────────────────────────────────────────────────────────────────────────────
# Execução de uma pipeline persistida
# ─────────────────────────────────────────────────────────────────────────────

def _config_da_pipeline(pipeline: GovPipeline, version: int | None) -> GovPipelineVersao:
    return pipeline.na_versao(version)


def disparar_pipeline(pipeline_id: str, *, version: int | None = None, origem: str = "manual") -> GovPipelineRun:
    """Executa uma pipeline persistida em background (chamada pela API)."""
    pipeline = store.obter_pipeline(pipeline_id)
    if pipeline is None:
        raise ValueError(f"Pipeline Gov '{pipeline_id}' não encontrada.")

    config = _config_da_pipeline(pipeline, version)
    return iniciar_em_background(
        config, pipeline_id=pipeline.id, pipeline_name=pipeline.name,
        pipeline_version=config.version, origem=origem,
    )


def executar_pipeline_sync(pipeline_id: str, *, version: int | None = None, origem: str = "airflow") -> dict[str, Any]:
    """
    Ponto de entrada **síncrono** — o que a DAG do Airflow chama
    (`src/govmode/dag_generator.py`). Roda a mesma `GovEngine` do painel
    (Regra 4 do SPEC generalizada: não existem duas implementações da
    regra de negócio) e devolve um resumo serializável, adequado para XCom.
    """
    pipeline = store.obter_pipeline(pipeline_id)
    if pipeline is None:
        raise ValueError(f"Pipeline Gov '{pipeline_id}' não encontrada.")

    config = _config_da_pipeline(pipeline, version)
    run = asyncio.run(
        executar_agora(
            config, pipeline_id=pipeline.id, pipeline_name=pipeline.name,
            pipeline_version=config.version, origem=origem,
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
        "progresso": run.progresso,
    }

    if run.status == StatusRun.FALHOU:
        falhas = [s for s in run.steps if s.error]
        detalhe = falhas[0].error.message if falhas else "motivo não registrado"
        raise RuntimeError(f"Execução {run.id} falhou: {detalhe}")

    return resumo
