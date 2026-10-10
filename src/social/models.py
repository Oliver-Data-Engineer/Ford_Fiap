"""
models.py (Redes Sociais)
Modelos da aba Redes Sociais — domínio paralelo ao YouTube (mesmo princípio
de docs/SPEC.md §13.2/14.2: sistema próprio, não uma extensão do pipeline do
YouTube), reaproveitando só o que é genérico de verdade
(`StatusStep`/`StatusRun`/`agora_iso`/`novo_id` de `src/pipelines/models.py`,
`Prompt`/CRUD de `src/pipelines/store.py`, `Agente`, `BigQueryWarehouse`).

Não existe integração real com Reddit/X/Instagram (exigiria credenciais
próprias de cada rede) — o step de coleta gera posts sintéticos
(`src/social/geradores.py`) e o step de enriquecimento roda uma classificação
de sentimento de verdade sobre eles, via o mesmo `Agente`/LLM configurado no
`.env` (Regra 7 do SPEC: só o engine chama o LLM).
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from src.pipelines.models import StatusRun, StatusStep, agora_iso, novo_id

REDES = ("reddit", "x", "instagram")


class TipoStepSocial(str, Enum):
    COLETA = "coleta"
    RAW = "raw"
    REFINED = "refined"
    ENRIQUECIMENTO = "enriquecimento"
    TRUST = "trust"


ROTULOS_STEP_SOCIAL: dict[TipoStepSocial, str] = {
    TipoStepSocial.COLETA: "Coleta",
    TipoStepSocial.RAW: "RAW",
    TipoStepSocial.REFINED: "REFINED",
    TipoStepSocial.ENRIQUECIMENTO: "Enriquecimento de IA",
    TipoStepSocial.TRUST: "TRUST",
}

STEPS_PADRAO_SOCIAL: list[TipoStepSocial] = [
    TipoStepSocial.COLETA, TipoStepSocial.RAW, TipoStepSocial.REFINED,
    TipoStepSocial.ENRIQUECIMENTO, TipoStepSocial.TRUST,
]


class PostSocial(BaseModel):
    """Um post sintético — mesma forma independente da rede de origem."""
    post_id: str = Field(default_factory=lambda: novo_id("post"))
    rede: str
    autor: str
    texto: str
    curtidas: int = 0
    respostas: int = 0
    publicado_em: str = Field(default_factory=agora_iso)
    url: str = ""


class AnaliseSocialStruct(BaseModel):
    """Mesma forma de `src.analytics.models.AnaliseComentarioStruct` —
    sentimento/motivo/adjetivos/objeto, extraído pela IA."""
    sentimento: str
    motivo: str
    adjetivos: list[str] = Field(default_factory=list)
    objeto: str

    @field_validator("adjetivos", mode="before")
    @classmethod
    def _normalizar_adjetivos(cls, valor):
        if valor is None:
            return []
        if isinstance(valor, str):
            return [valor]
        return list(valor)

    @field_validator("sentimento", "motivo", "objeto", mode="before")
    @classmethod
    def _exigir_texto(cls, valor):
        if not isinstance(valor, str) or not valor.strip():
            raise ValueError("campo obrigatório e não pode ser vazio")
        return valor.strip()


class ErroStepSocial(BaseModel):
    error_type: str
    message: str
    retryable: bool = False


class StepResultSocial(BaseModel):
    """Mesmos métodos de `src.pipelines.models.StepResult`, só tipado para
    `TipoStepSocial` — não dá pra reaproveitar a classe original porque seu
    campo `type` é fixo em `TipoStep` (enum do YouTube)."""
    type: TipoStepSocial
    name: str
    status: StatusStep = StatusStep.PENDENTE
    started_at: str | None = None
    finished_at: str | None = None
    resumo: str = ""
    error: ErroStepSocial | None = None

    def iniciar(self) -> None:
        self.status = StatusStep.EXECUTANDO
        self.started_at = agora_iso()

    def concluir(self, resumo: str = "") -> None:
        self.status = StatusStep.OK
        self.resumo = resumo
        self.finished_at = agora_iso()

    def falhar(self, tipo: str, mensagem: str, retryable: bool = False) -> None:
        self.status = StatusStep.FALHOU
        self.error = ErroStepSocial(error_type=tipo, message=mensagem, retryable=retryable)
        self.finished_at = agora_iso()

    def pular(self, motivo: str) -> None:
        self.status = StatusStep.PULADO
        self.resumo = motivo
        self.finished_at = agora_iso()


def montar_steps_social() -> list[StepResultSocial]:
    return [
        StepResultSocial(type=tipo, name=ROTULOS_STEP_SOCIAL[tipo])
        for tipo in STEPS_PADRAO_SOCIAL
    ]


class ConfigColetaSocial(BaseModel):
    termo_busca: str = ""
    redes: list[str] = Field(default_factory=lambda: list(REDES))
    quantidade: int = 40


class SocialRun(BaseModel):
    """Registro de uma execução — mesma ideia de `PipelineRun`, mas sem
    versionamento de pipeline/schedule (fora de escopo nesta rodada)."""
    id: str = Field(default_factory=lambda: novo_id("social_run"))
    origem: str = "playground"
    status: StatusRun = StatusRun.PENDENTE
    config: ConfigColetaSocial = Field(default_factory=ConfigColetaSocial)
    steps: list[StepResultSocial] = Field(default_factory=montar_steps_social)

    raw: dict[str, Any] = Field(default_factory=dict)
    refined: dict[str, Any] = Field(default_factory=dict)
    trust: dict[str, Any] = Field(default_factory=dict)

    usage: dict[str, Any] = Field(default_factory=dict)
    started_at: str = Field(default_factory=agora_iso)
    finished_at: str | None = None

    def step(self, tipo: TipoStepSocial) -> StepResultSocial | None:
        return next((s for s in self.steps if s.type == tipo), None)
