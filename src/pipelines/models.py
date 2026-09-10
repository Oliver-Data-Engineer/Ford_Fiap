"""
models.py
Modelos de dados da camada de pipelines.

Cobre quatro objetos persistidos:
    Prompt       — instrução de IA, versionada e reutilizável
    Pipeline     — configuração de uma análise, versionada
    PipelineRun  — registro de uma execução (auditoria/reprodução)
    StepResult   — status de cada etapa dentro de uma execução

E os três níveis de dado que a pipeline produz: RAW, REFINED e TRUST.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def agora_iso() -> str:
    """Timestamp UTC em ISO-8601 — formato único em toda a persistência."""
    return datetime.now(timezone.utc).isoformat()


def novo_id(prefixo: str) -> str:
    return f"{prefixo}_{uuid.uuid4().hex[:12]}"


# ─────────────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────────────

class TipoEntrada(str, Enum):
    """Sobre qual recorte do dado REFINED um prompt roda."""
    TRANSCRIPT = "transcript"        # uma execução por vídeo, sobre a transcrição
    COMMENTS = "comments"            # uma execução por vídeo, sobre os comentários
    VIDEO = "video"                  # uma execução por vídeo, transcrição + comentários
    DATASET = "dataset"              # uma única execução sobre o conjunto inteiro


class TipoStep(str, Enum):
    YOUTUBE_SEARCH = "youtube_search"
    TRANSCRIPT = "transcript"
    COMMENTS = "comments"
    RAW = "raw"
    REFINED = "refined"
    AGENT = "agent"
    TRUST = "trust"
    OUTPUT = "output"


class StatusStep(str, Enum):
    PENDENTE = "pending"
    EXECUTANDO = "running"
    OK = "success"
    FALHOU = "failed"
    PULADO = "skipped"


class StatusRun(str, Enum):
    PENDENTE = "pending"
    EXECUTANDO = "running"
    OK = "success"
    PARCIAL = "partial"    # etapas anteriores concluíram, uma etapa falhou
    FALHOU = "failed"


class StatusPipeline(str, Enum):
    ATIVA = "active"
    PAUSADA = "paused"


# ─────────────────────────────────────────────────────────────────────────────
# Prompt
# ─────────────────────────────────────────────────────────────────────────────

class PromptVersao(BaseModel):
    """Conteúdo congelado de uma versão — nunca é alterado depois de criado."""
    version: int
    prompt: str
    input_type: TipoEntrada = TipoEntrada.TRANSCRIPT
    output_format: str = "json"
    output_schema: dict[str, Any] | None = None
    created_at: str = Field(default_factory=agora_iso)


class Prompt(BaseModel):
    """
    Prompt reutilizável.

    Uma pipeline referencia (prompt_id, prompt_version) — nunca copia o texto
    (Regra 13 da spec).  Editar o prompt cria uma versão nova; execuções
    antigas continuam apontando para a versão que realmente rodou.
    """
    id: str = Field(default_factory=lambda: novo_id("prompt"))
    name: str
    description: str = ""
    version: int = 1
    prompt: str
    input_type: TipoEntrada = TipoEntrada.TRANSCRIPT
    output_format: str = "json"
    output_schema: dict[str, Any] | None = None
    versions: list[PromptVersao] = Field(default_factory=list)
    created_at: str = Field(default_factory=agora_iso)
    updated_at: str = Field(default_factory=agora_iso)

    def snapshot(self) -> PromptVersao:
        """Versão atual congelada, para o histórico."""
        return PromptVersao(
            version=self.version,
            prompt=self.prompt,
            input_type=self.input_type,
            output_format=self.output_format,
            output_schema=self.output_schema,
        )

    def na_versao(self, version: int | None) -> PromptVersao:
        """
        Conteúdo de uma versão específica; `None` devolve a atual.
        Levanta ValueError se a versão pedida não existir.
        """
        if version is None or version == self.version:
            return self.snapshot()

        for v in self.versions:
            if v.version == version:
                return v

        raise ValueError(f"Prompt '{self.id}' não possui versão {version}.")


class PromptEntrada(BaseModel):
    """Corpo aceito na criação/edição de um prompt via API."""
    name: str
    description: str = ""
    prompt: str
    input_type: TipoEntrada = TipoEntrada.TRANSCRIPT
    output_format: str = "json"
    output_schema: dict[str, Any] | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline
# ─────────────────────────────────────────────────────────────────────────────

class FonteYouTube(BaseModel):
    """
    Origem dos vídeos.

    `query` + filtros alimentam a busca; `video_ids` fixa uma lista escolhida
    à mão no Playground.  Os dois podem coexistir — a coleta une os conjuntos.
    """
    type: str = "youtube"
    query: str = ""
    video_ids: list[str] = Field(default_factory=list)

    # True  → a pipeline sempre reprocessa exatamente `video_ids`, ignorando
    #         `query` mesmo que esteja preenchida (schedule com lista fixa).
    # False → comportamento padrão: busca com `query` quando presente, e
    #         mescla com `video_ids` fixados manualmente (Modo B do Playground).
    fonte_fixa: bool = False

    # filtros de busca
    max_results: int = 10
    order: str = "relevance"          # relevance | date | viewCount | rating | title
    meses_atras: int | None = 6
    published_after: str | None = None
    published_before: str | None = None
    channel_id: str | None = None
    relevance_language: str | None = "pt"
    region_code: str | None = None
    video_duration: str | None = None  # any | short | medium | long

    # filtros pós-busca (aplicados sobre as estatísticas já coletadas)
    min_views: int | None = None
    min_likes: int | None = None
    min_comments: int | None = None
    duracao_min_segundos: int | None = None
    duracao_max_segundos: int | None = None

    # limites de coleta
    max_comentarios_por_video: int = 100
    idiomas_transcricao: list[str] = Field(default_factory=lambda: ["pt", "pt-BR", "en"])


class PassoPipeline(BaseModel):
    """
    Uma etapa declarada da pipeline.

    Etapas do tipo `agent` referenciam um prompt por id + versão; as demais
    não têm configuração adicional.
    """
    type: TipoStep
    prompt_id: str | None = None
    prompt_version: int | None = None
    name: str | None = None
    enabled: bool = True


class ConfigLLM(BaseModel):
    """
    Configuração do agente — provider e modelo apenas.
    Credenciais nunca trafegam aqui: são resolvidas no backend pelo ambiente.
    """
    provider: str = "groq"
    model: str | None = None
    temperature: float = 0.0
    max_tokens: int | None = None


class ConfigSchedule(BaseModel):
    """Agenda de execução automática."""
    mode: str = "manual"              # manual | hourly | daily | weekly | monthly | cron
    cron: str | None = None           # preenchido quando mode == "cron"
    hora: str = "08:00"               # usado por daily/weekly/monthly
    dia_semana: int = 1               # 0=domingo … 6=sábado (weekly)
    dia_mes: int = 1                  # (monthly)
    timezone: str = "America/Sao_Paulo"
    catchup: bool = False
    retries: int = 1
    retry_delay_minutos: int = 5


class PipelineVersao(BaseModel):
    """Configuração congelada de uma versão de pipeline."""
    version: int
    source: FonteYouTube
    steps: list[PassoPipeline]
    llm: ConfigLLM
    schedule: ConfigSchedule
    created_at: str = Field(default_factory=agora_iso)


class Pipeline(BaseModel):
    id: str = Field(default_factory=lambda: novo_id("pipeline"))
    name: str
    description: str = ""
    version: int = 1
    status: StatusPipeline = StatusPipeline.ATIVA
    source: FonteYouTube = Field(default_factory=FonteYouTube)
    steps: list[PassoPipeline] = Field(default_factory=list)
    llm: ConfigLLM = Field(default_factory=ConfigLLM)
    schedule: ConfigSchedule = Field(default_factory=ConfigSchedule)
    versions: list[PipelineVersao] = Field(default_factory=list)
    dag_id: str | None = None
    dag_path: str | None = None
    dag_version: int | None = None
    last_run_at: str | None = None
    last_run_status: str | None = None
    created_at: str = Field(default_factory=agora_iso)
    updated_at: str = Field(default_factory=agora_iso)

    def snapshot(self) -> PipelineVersao:
        return PipelineVersao(
            version=self.version,
            source=self.source,
            steps=self.steps,
            llm=self.llm,
            schedule=self.schedule,
        )

    def na_versao(self, version: int | None) -> PipelineVersao:
        if version is None or version == self.version:
            return self.snapshot()

        for v in self.versions:
            if v.version == version:
                return v

        raise ValueError(f"Pipeline '{self.id}' não possui versão {version}.")


class PipelineEntrada(BaseModel):
    """Corpo aceito na criação/edição de uma pipeline via API."""
    name: str
    description: str = ""
    source: FonteYouTube = Field(default_factory=FonteYouTube)
    steps: list[PassoPipeline] = Field(default_factory=list)
    llm: ConfigLLM = Field(default_factory=ConfigLLM)
    schedule: ConfigSchedule = Field(default_factory=ConfigSchedule)
    status: StatusPipeline = StatusPipeline.ATIVA


# ─────────────────────────────────────────────────────────────────────────────
# Execução
# ─────────────────────────────────────────────────────────────────────────────

class ErroStep(BaseModel):
    error_type: str
    message: str
    retryable: bool = False


class StepResult(BaseModel):
    """Status de uma etapa — o Playground desenha a régua de progresso com isto."""
    type: TipoStep
    name: str
    status: StatusStep = StatusStep.PENDENTE
    started_at: str | None = None
    finished_at: str | None = None
    resumo: str = ""
    error: ErroStep | None = None

    def iniciar(self) -> None:
        self.status = StatusStep.EXECUTANDO
        self.started_at = agora_iso()

    def concluir(self, resumo: str = "") -> None:
        self.status = StatusStep.OK
        self.resumo = resumo
        self.finished_at = agora_iso()

    def falhar(self, tipo: str, mensagem: str, retryable: bool = False) -> None:
        self.status = StatusStep.FALHOU
        self.error = ErroStep(error_type=tipo, message=mensagem, retryable=retryable)
        self.finished_at = agora_iso()

    def pular(self, motivo: str) -> None:
        self.status = StatusStep.PULADO
        self.resumo = motivo
        self.finished_at = agora_iso()


class PipelineRun(BaseModel):
    """
    Registro completo de uma execução.

    Guarda versões de pipeline e de prompt, provider, modelo e as três camadas
    de dado — o suficiente para auditar ou reproduzir a execução (Regra 29).
    """
    id: str = Field(default_factory=lambda: novo_id("run"))
    pipeline_id: str | None = None
    pipeline_name: str = "Playground"
    pipeline_version: int | None = None
    origem: str = "playground"           # playground | manual | airflow
    status: StatusRun = StatusRun.PENDENTE
    steps: list[StepResult] = Field(default_factory=list)

    raw: dict[str, Any] = Field(default_factory=dict)
    refined: dict[str, Any] = Field(default_factory=dict)
    trust: dict[str, Any] = Field(default_factory=dict)

    agente: dict[str, Any] = Field(default_factory=dict)
    usage: dict[str, Any] = Field(default_factory=dict)
    started_at: str = Field(default_factory=agora_iso)
    finished_at: str | None = None

    def step(self, tipo: TipoStep) -> StepResult | None:
        return next((s for s in self.steps if s.type == tipo), None)
