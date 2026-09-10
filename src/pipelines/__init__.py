"""
src.pipelines — Camada de processamento: RAW → REFINED → TRUST.

Não conhece provider de LLM (usa `Agente`) nem a UI (é chamada pelos routers).

    from src.pipelines import PipelineEngine, executor, store
"""
from src.pipelines import executor, refine, store
from src.pipelines.engine import PipelineEngine
from src.pipelines.models import (
    ConfigLLM,
    ConfigSchedule,
    FonteYouTube,
    PassoPipeline,
    Pipeline,
    PipelineEntrada,
    PipelineRun,
    PipelineVersao,
    Prompt,
    PromptEntrada,
    StatusPipeline,
    StatusRun,
    StatusStep,
    TipoEntrada,
    TipoStep,
)

__all__ = [
    "PipelineEngine",
    "executor",
    "refine",
    "store",
    "Pipeline",
    "PipelineEntrada",
    "PipelineVersao",
    "PipelineRun",
    "PassoPipeline",
    "Prompt",
    "PromptEntrada",
    "FonteYouTube",
    "ConfigLLM",
    "ConfigSchedule",
    "StatusPipeline",
    "StatusRun",
    "StatusStep",
    "TipoEntrada",
    "TipoStep",
]
