"""
store.py
Persistência de prompts, pipelines e execuções.

Backend: arquivos JSON em `data/` — suficiente para o volume do projeto e sem
adicionar dependência de banco.  Toda a leitura/escrita passa por aqui, então
migrar para SQLite/Postgres depois é trocar esta camada e nada mais.

Escrita é atômica (arquivo temporário + os.replace) e serializada por um lock,
para que uma execução do Airflow e uma do Playground não corrompam o arquivo.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from src.config import settings
from src.pipelines.models import (
    Pipeline,
    PipelineEntrada,
    PipelineRun,
    Prompt,
    PromptEntrada,
    StatusPipeline,
    agora_iso,
)

logger = logging.getLogger(__name__)

M = TypeVar("M", bound=BaseModel)

_LOCK = threading.RLock()

ARQUIVO_PROMPTS = "prompts.json"
ARQUIVO_PIPELINES = "pipelines.json"
ARQUIVO_RUNS = "runs.json"

# Execuções guardadas no histórico — as mais antigas são descartadas
MAX_RUNS_HISTORICO = 200


# ─────────────────────────────────────────────────────────────────────────────
# I/O de baixo nível
# ─────────────────────────────────────────────────────────────────────────────

def _caminho(arquivo: str) -> Path:
    return settings.data_dir / arquivo


def _ler(arquivo: str) -> list[dict]:
    caminho = _caminho(arquivo)
    if not caminho.is_file():
        return []

    try:
        conteudo = json.loads(caminho.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("[store] %s ilegível (%s) — tratando como vazio.", arquivo, exc)
        return []

    return conteudo if isinstance(conteudo, list) else []


def _escrever(arquivo: str, registros: list[dict]) -> None:
    """Grava via arquivo temporário + replace: nunca deixa um JSON truncado."""
    caminho = _caminho(arquivo)
    temporario = caminho.with_suffix(".tmp")

    temporario.write_text(
        json.dumps(registros, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.replace(temporario, caminho)


def _carregar(arquivo: str, modelo: type[M]) -> list[M]:
    """Lê e valida; registros inválidos são ignorados em vez de derrubar a API."""
    itens: list[M] = []
    for bruto in _ler(arquivo):
        try:
            itens.append(modelo.model_validate(bruto))
        except Exception as exc:
            logger.warning(
                "[store] registro inválido em %s ignorado: %s", arquivo, exc
            )
    return itens


def _salvar(arquivo: str, itens: list[BaseModel]) -> None:
    _escrever(arquivo, [i.model_dump(mode="json") for i in itens])


# ─────────────────────────────────────────────────────────────────────────────
# Prompts
# ─────────────────────────────────────────────────────────────────────────────

def listar_prompts() -> list[Prompt]:
    with _LOCK:
        prompts = _carregar(ARQUIVO_PROMPTS, Prompt)
    prompts.sort(key=lambda p: p.updated_at, reverse=True)
    return prompts


def obter_prompt(prompt_id: str) -> Prompt | None:
    return next((p for p in listar_prompts() if p.id == prompt_id), None)


def criar_prompt(entrada: PromptEntrada) -> Prompt:
    prompt = Prompt(**entrada.model_dump())
    prompt.versions = [prompt.snapshot()]

    with _LOCK:
        prompts = _carregar(ARQUIVO_PROMPTS, Prompt)
        prompts.append(prompt)
        _salvar(ARQUIVO_PROMPTS, prompts)

    logger.info("[store] prompt criado: %s (%s)", prompt.name, prompt.id)
    return prompt


def atualizar_prompt(prompt_id: str, entrada: PromptEntrada) -> Prompt | None:
    """
    Atualiza um prompt.

    Se o conteúdo (texto, tipo de entrada, formato ou schema) mudou, cria uma
    versão nova e arquiva a anterior; alterações só de nome/descrição não
    consomem versão.
    """
    with _LOCK:
        prompts = _carregar(ARQUIVO_PROMPTS, Prompt)
        indice = next((i for i, p in enumerate(prompts) if p.id == prompt_id), None)
        if indice is None:
            return None

        atual = prompts[indice]

        mudou_conteudo = (
            atual.prompt != entrada.prompt
            or atual.input_type != entrada.input_type
            or atual.output_format != entrada.output_format
            or atual.output_schema != entrada.output_schema
        )

        if mudou_conteudo:
            # Arquiva a versão vigente antes de sobrescrever
            if not any(v.version == atual.version for v in atual.versions):
                atual.versions.append(atual.snapshot())
            atual.version += 1
            atual.prompt = entrada.prompt
            atual.input_type = entrada.input_type
            atual.output_format = entrada.output_format
            atual.output_schema = entrada.output_schema
            atual.versions.append(atual.snapshot())

        atual.name = entrada.name
        atual.description = entrada.description
        atual.updated_at = agora_iso()

        prompts[indice] = atual
        _salvar(ARQUIVO_PROMPTS, prompts)

    return atual


def remover_prompt(prompt_id: str) -> bool:
    with _LOCK:
        prompts = _carregar(ARQUIVO_PROMPTS, Prompt)
        restantes = [p for p in prompts if p.id != prompt_id]
        if len(restantes) == len(prompts):
            return False
        _salvar(ARQUIVO_PROMPTS, restantes)
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Pipelines
# ─────────────────────────────────────────────────────────────────────────────

def listar_pipelines() -> list[Pipeline]:
    with _LOCK:
        pipelines = _carregar(ARQUIVO_PIPELINES, Pipeline)
    pipelines.sort(key=lambda p: p.updated_at, reverse=True)
    return pipelines


def obter_pipeline(pipeline_id: str) -> Pipeline | None:
    return next((p for p in listar_pipelines() if p.id == pipeline_id), None)


def criar_pipeline(entrada: PipelineEntrada) -> Pipeline:
    pipeline = Pipeline(**entrada.model_dump())
    pipeline.versions = [pipeline.snapshot()]

    with _LOCK:
        pipelines = _carregar(ARQUIVO_PIPELINES, Pipeline)
        pipelines.append(pipeline)
        _salvar(ARQUIVO_PIPELINES, pipelines)

    logger.info("[store] pipeline criada: %s (%s)", pipeline.name, pipeline.id)
    return pipeline


def atualizar_pipeline(pipeline_id: str, entrada: PipelineEntrada) -> Pipeline | None:
    """
    Atualiza uma pipeline; mudança em source/steps/llm/schedule gera versão nova.
    """
    with _LOCK:
        pipelines = _carregar(ARQUIVO_PIPELINES, Pipeline)
        indice = next((i for i, p in enumerate(pipelines) if p.id == pipeline_id), None)
        if indice is None:
            return None

        atual = pipelines[indice]

        mudou_config = (
            atual.source != entrada.source
            or atual.steps != entrada.steps
            or atual.llm != entrada.llm
            or atual.schedule != entrada.schedule
        )

        if mudou_config:
            if not any(v.version == atual.version for v in atual.versions):
                atual.versions.append(atual.snapshot())
            atual.version += 1
            atual.source = entrada.source
            atual.steps = entrada.steps
            atual.llm = entrada.llm
            atual.schedule = entrada.schedule
            atual.versions.append(atual.snapshot())

        atual.name = entrada.name
        atual.description = entrada.description
        atual.status = entrada.status
        atual.updated_at = agora_iso()

        pipelines[indice] = atual
        _salvar(ARQUIVO_PIPELINES, pipelines)

    return atual


def salvar_pipeline(pipeline: Pipeline) -> Pipeline:
    """Persiste um objeto Pipeline já montado (usado pelo gerador de DAG)."""
    with _LOCK:
        pipelines = _carregar(ARQUIVO_PIPELINES, Pipeline)
        indice = next((i for i, p in enumerate(pipelines) if p.id == pipeline.id), None)
        pipeline.updated_at = agora_iso()

        if indice is None:
            pipelines.append(pipeline)
        else:
            pipelines[indice] = pipeline

        _salvar(ARQUIVO_PIPELINES, pipelines)

    return pipeline


def alterar_status_pipeline(pipeline_id: str, status: StatusPipeline) -> Pipeline | None:
    pipeline = obter_pipeline(pipeline_id)
    if pipeline is None:
        return None
    pipeline.status = status
    return salvar_pipeline(pipeline)


def remover_pipeline(pipeline_id: str) -> bool:
    with _LOCK:
        pipelines = _carregar(ARQUIVO_PIPELINES, Pipeline)
        restantes = [p for p in pipelines if p.id != pipeline_id]
        if len(restantes) == len(pipelines):
            return False
        _salvar(ARQUIVO_PIPELINES, restantes)
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Execuções
# ─────────────────────────────────────────────────────────────────────────────

def salvar_run(run: PipelineRun) -> PipelineRun:
    """
    Persiste (ou atualiza) uma execução e mantém o histórico limitado.

    Nota: as três camadas de dado são guardadas por inteiro — é o que permite
    auditar de onde veio cada insight sem reexecutar a coleta.
    """
    with _LOCK:
        runs = _carregar(ARQUIVO_RUNS, PipelineRun)
        indice = next((i for i, r in enumerate(runs) if r.id == run.id), None)

        if indice is None:
            runs.append(run)
        else:
            runs[indice] = run

        runs.sort(key=lambda r: r.started_at, reverse=True)
        _salvar(ARQUIVO_RUNS, runs[:MAX_RUNS_HISTORICO])

    return run


def listar_runs(pipeline_id: str | None = None, limite: int = 50) -> list[PipelineRun]:
    with _LOCK:
        runs = _carregar(ARQUIVO_RUNS, PipelineRun)

    if pipeline_id:
        runs = [r for r in runs if r.pipeline_id == pipeline_id]

    runs.sort(key=lambda r: r.started_at, reverse=True)
    return runs[:limite]


def obter_run(run_id: str) -> PipelineRun | None:
    with _LOCK:
        runs = _carregar(ARQUIVO_RUNS, PipelineRun)
    return next((r for r in runs if r.id == run_id), None)


def registrar_execucao_na_pipeline(pipeline_id: str, status: str, quando: str) -> None:
    """Atualiza os campos de última execução exibidos na aba Pipelines."""
    pipeline = obter_pipeline(pipeline_id)
    if pipeline is None:
        return
    pipeline.last_run_at = quando
    pipeline.last_run_status = status
    salvar_pipeline(pipeline)
