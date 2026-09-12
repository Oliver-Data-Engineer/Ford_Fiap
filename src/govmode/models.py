"""
models.py
Modelos do sistema de pipelines do Gov Mode (catálogo FIPE) — paralelo ao
do YouTube (`src/pipelines/models.py`), não uma extensão dele.

Reaproveitados diretamente de `src.pipelines.models`, por serem genéricos
(zero acoplamento a YouTube): `ConfigSchedule` ("quando rodar"),
`StatusRun`/`StatusStep`/`StatusPipeline`, `ErroStep`, `agora_iso`/`novo_id`.

**Não** reaproveitados: `StepResult` (seu campo `type` é tipado
concretamente como `TipoStep`, o enum de steps do YouTube — não dá pra
encaixar um step de FIPE ali sem generalizar um modelo que já está em
produção) e `Pipeline`/`PipelineVersao` (`source` é tipado concretamente
como `FonteYouTube`, mesmo problema). Por isso `TipoStepGov`/
`StepResultGov`/`GovPipeline*` existem aqui como equivalentes próprios, no
mesmo formato — ver docs/SPEC.md seção 13.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from src.pipelines.models import (
    ConfigSchedule,
    ErroStep,
    StatusPipeline,
    StatusRun,
    StatusStep,
    agora_iso,
    novo_id,
)


class TipoStepGov(str, Enum):
    MARCAS = "marcas"
    MODELOS = "modelos"
    FIPE = "fipe"
    REFERENCIA_MESES = "referencia_meses"


ROTULOS_STEP_GOV: dict[TipoStepGov, str] = {
    TipoStepGov.MARCAS: "Marcas",
    TipoStepGov.MODELOS: "Modelos",
    TipoStepGov.FIPE: "Código FIPE",
    TipoStepGov.REFERENCIA_MESES: "Meses de referência",
}

# Ordem estrutural — marcas antes de modelos antes de fipe (cada um lê o
# resultado do anterior no BigQuery); meses de referência é independente.
STEPS_PADRAO_GOV: list[TipoStepGov] = [
    TipoStepGov.MARCAS,
    TipoStepGov.MODELOS,
    TipoStepGov.FIPE,
    TipoStepGov.REFERENCIA_MESES,
]


class StepResultGov(BaseModel):
    """Equivalente a `StepResult` (src/pipelines/models.py) — só troca
    `type: TipoStep` por `type: TipoStepGov`. Mesmos mutadores."""
    type: TipoStepGov
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

    def pular(self, motivo: str = "") -> None:
        self.status = StatusStep.PULADO
        self.resumo = motivo
        self.finished_at = agora_iso()


class FonteGovFipe(BaseModel):
    """
    Configuração de origem de uma pipeline do Gov Mode — equivalente a
    `FonteYouTube`, mas pro domínio FIPE.

    `marcas_alvo` é obrigatoriamente escopada pela UI (não dá pra rodar
    "todas as marcas do Brasil" de uma vez — o passo de código FIPE busca
    todos os anos de todo modelo, o volume de chamadas explode; ver
    src/govmode/engine.py).
    """
    tipo_veiculo: str = "carros"          # carros | motos | caminhoes
    marcas_alvo: list[str] = Field(default_factory=list)  # nomes de marca (ex.: ["Ford"])


class PassoGovPipeline(BaseModel):
    type: TipoStepGov
    name: str | None = None
    enabled: bool = True


class GovPipelineVersao(BaseModel):
    """Conteúdo congelado de uma versão — nunca alterado depois de criado."""
    version: int
    source: FonteGovFipe
    steps: list[PassoGovPipeline]
    schedule: ConfigSchedule
    created_at: str = Field(default_factory=agora_iso)


class GovPipeline(BaseModel):
    """Pipeline nomeada e versionada do Gov Mode — mesmo padrão de
    `Pipeline` (src/pipelines/models.py), armazenada à parte
    (data/gov_pipelines.json, src/govmode/store.py)."""
    id: str = Field(default_factory=lambda: novo_id("gov_pipeline"))
    name: str
    description: str = ""
    version: int = 1
    status: StatusPipeline = StatusPipeline.ATIVA
    source: FonteGovFipe = Field(default_factory=FonteGovFipe)
    steps: list[PassoGovPipeline] = Field(default_factory=list)
    schedule: ConfigSchedule = Field(default_factory=ConfigSchedule)
    versions: list[GovPipelineVersao] = Field(default_factory=list)
    dag_id: str | None = None
    dag_path: str | None = None
    dag_version: int | None = None
    last_run_at: str | None = None
    last_run_status: str | None = None
    created_at: str = Field(default_factory=agora_iso)
    updated_at: str = Field(default_factory=agora_iso)

    def snapshot(self) -> GovPipelineVersao:
        return GovPipelineVersao(
            version=self.version, source=self.source, steps=self.steps, schedule=self.schedule,
        )

    def na_versao(self, version: int | None) -> GovPipelineVersao:
        if version is None or version == self.version:
            return self.snapshot()
        for v in self.versions:
            if v.version == version:
                return v
        raise ValueError(f"Pipeline Gov '{self.id}' não possui versão {version}.")


class GovPipelineEntrada(BaseModel):
    """Corpo aceito na criação/edição de uma pipeline via API."""
    name: str
    description: str = ""
    status: StatusPipeline = StatusPipeline.ATIVA
    source: FonteGovFipe = Field(default_factory=FonteGovFipe)
    steps: list[PassoGovPipeline] = Field(default_factory=list)
    schedule: ConfigSchedule = Field(default_factory=ConfigSchedule)


class GovPipelineRun(BaseModel):
    """Registro de uma execução — mesmo papel de `PipelineRun`, mas sem
    RAW/REFINED/TRUST (o BigQuery já é o armazenamento primário aqui, não
    existe camada intermediária local pra guardar)."""
    id: str = Field(default_factory=lambda: novo_id("gov_run"))
    pipeline_id: str | None = None
    pipeline_name: str = "Gov Mode"
    pipeline_version: int | None = None
    origem: str = "manual"     # manual | airflow
    status: StatusRun = StatusRun.PENDENTE
    steps: list[StepResultGov] = Field(default_factory=list)
    progresso: dict[str, Any] = Field(default_factory=dict)
    resultado: dict[str, int] = Field(default_factory=dict)
    started_at: str = Field(default_factory=agora_iso)
    finished_at: str | None = None

    def step(self, tipo: TipoStepGov) -> StepResultGov | None:
        return next((s for s in self.steps if s.type == tipo), None)
