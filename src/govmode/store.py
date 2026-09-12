"""
store.py
Persistência das pipelines e execuções do Gov Mode — mesmo padrão de
`src/pipelines/store.py` (JSON em `data/`, escrita atômica), mas em
arquivos próprios (`gov_pipelines.json`, `gov_runs.json`) e com um lock
independente — não compartilha estado com o store do YouTube.

Reaproveita os helpers de I/O de baixo nível de `src.pipelines.store`
(`_ler`/`_escrever`/`_carregar`/`_salvar`) por serem genéricos — leem/
escrevem um arquivo JSON de uma lista de qualquer `BaseModel`, sem nada
específico de YouTube.
"""
from __future__ import annotations

import logging
import threading

from src.govmode.models import GovPipeline, GovPipelineEntrada, GovPipelineRun
from src.pipelines.models import StatusPipeline, agora_iso
from src.pipelines.store import _carregar, _salvar

logger = logging.getLogger(__name__)

_LOCK = threading.RLock()

ARQUIVO_PIPELINES = "gov_pipelines.json"
ARQUIVO_RUNS = "gov_runs.json"

MAX_RUNS_HISTORICO = 200


# ─────────────────────────────────────────────────────────────────────────────
# Pipelines
# ─────────────────────────────────────────────────────────────────────────────

def listar_pipelines() -> list[GovPipeline]:
    with _LOCK:
        pipelines = _carregar(ARQUIVO_PIPELINES, GovPipeline)
    pipelines.sort(key=lambda p: p.updated_at, reverse=True)
    return pipelines


def obter_pipeline(pipeline_id: str) -> GovPipeline | None:
    return next((p for p in listar_pipelines() if p.id == pipeline_id), None)


def criar_pipeline(entrada: GovPipelineEntrada) -> GovPipeline:
    pipeline = GovPipeline(**entrada.model_dump())
    pipeline.versions = [pipeline.snapshot()]

    with _LOCK:
        pipelines = _carregar(ARQUIVO_PIPELINES, GovPipeline)
        pipelines.append(pipeline)
        _salvar(ARQUIVO_PIPELINES, pipelines)

    logger.info("[govmode] pipeline criada: %s (%s)", pipeline.name, pipeline.id)
    return pipeline


def atualizar_pipeline(pipeline_id: str, entrada: GovPipelineEntrada) -> GovPipeline | None:
    """Mudança em `source`/`steps`/`schedule` gera versão nova — mesmo
    critério de `src.pipelines.store.atualizar_pipeline`."""
    with _LOCK:
        pipelines = _carregar(ARQUIVO_PIPELINES, GovPipeline)
        indice = next((i for i, p in enumerate(pipelines) if p.id == pipeline_id), None)
        if indice is None:
            return None

        atual = pipelines[indice]

        mudou_config = (
            atual.source != entrada.source
            or atual.steps != entrada.steps
            or atual.schedule != entrada.schedule
        )

        if mudou_config:
            if not any(v.version == atual.version for v in atual.versions):
                atual.versions.append(atual.snapshot())
            atual.version += 1
            atual.source = entrada.source
            atual.steps = entrada.steps
            atual.schedule = entrada.schedule
            atual.versions.append(atual.snapshot())

        atual.name = entrada.name
        atual.description = entrada.description
        atual.status = entrada.status
        atual.updated_at = agora_iso()

        pipelines[indice] = atual
        _salvar(ARQUIVO_PIPELINES, pipelines)

    return atual


def salvar_pipeline(pipeline: GovPipeline) -> GovPipeline:
    """Persiste um objeto `GovPipeline` já montado (usado pelo gerador de DAG)."""
    with _LOCK:
        pipelines = _carregar(ARQUIVO_PIPELINES, GovPipeline)
        indice = next((i for i, p in enumerate(pipelines) if p.id == pipeline.id), None)
        pipeline.updated_at = agora_iso()

        if indice is None:
            pipelines.append(pipeline)
        else:
            pipelines[indice] = pipeline

        _salvar(ARQUIVO_PIPELINES, pipelines)

    return pipeline


def alterar_status_pipeline(pipeline_id: str, status: StatusPipeline) -> GovPipeline | None:
    pipeline = obter_pipeline(pipeline_id)
    if pipeline is None:
        return None
    pipeline.status = status
    return salvar_pipeline(pipeline)


def remover_pipeline(pipeline_id: str) -> bool:
    with _LOCK:
        pipelines = _carregar(ARQUIVO_PIPELINES, GovPipeline)
        restantes = [p for p in pipelines if p.id != pipeline_id]
        if len(restantes) == len(pipelines):
            return False
        _salvar(ARQUIVO_PIPELINES, restantes)
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Execuções
# ─────────────────────────────────────────────────────────────────────────────

def salvar_run(run: GovPipelineRun) -> GovPipelineRun:
    with _LOCK:
        runs = _carregar(ARQUIVO_RUNS, GovPipelineRun)
        indice = next((i for i, r in enumerate(runs) if r.id == run.id), None)

        if indice is None:
            runs.append(run)
        else:
            runs[indice] = run

        runs.sort(key=lambda r: r.started_at, reverse=True)
        _salvar(ARQUIVO_RUNS, runs[:MAX_RUNS_HISTORICO])

    return run


def listar_runs(pipeline_id: str | None = None, limite: int = 50) -> list[GovPipelineRun]:
    with _LOCK:
        runs = _carregar(ARQUIVO_RUNS, GovPipelineRun)

    if pipeline_id:
        runs = [r for r in runs if r.pipeline_id == pipeline_id]

    runs.sort(key=lambda r: r.started_at, reverse=True)
    return runs[:limite]


def obter_run(run_id: str) -> GovPipelineRun | None:
    with _LOCK:
        runs = _carregar(ARQUIVO_RUNS, GovPipelineRun)
    return next((r for r in runs if r.id == run_id), None)


def registrar_execucao_na_pipeline(pipeline_id: str, status: str, quando: str) -> None:
    pipeline = obter_pipeline(pipeline_id)
    if pipeline is None:
        return
    pipeline.last_run_at = quando
    pipeline.last_run_status = status
    salvar_pipeline(pipeline)
