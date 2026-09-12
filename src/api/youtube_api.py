"""
youtube_api.py
Router FastAPI da aba YouTube (Playground).

Endpoints:
    GET  /youtube/config              o que está configurado no backend
    POST /youtube/search              busca vídeos (Modo A)
    POST /youtube/video               resolve uma URL/ID de vídeo (Modo B)
    POST /youtube/transcript          transcrição de um vídeo
    POST /youtube/comments            comentários de um vídeo
    POST /youtube/playground/run      executa a pipeline ad-hoc do Playground
    POST /youtube/playground/preview  monta a configuração sem executar

Nenhuma resposta daqui expõe credencial (Regra 33): `/config` devolve apenas
flags booleanas dizendo o que está disponível.
"""
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.agents import listar_providers
from src.analytics import warehouse
from src.config import ENV_VAR_POR_PROVIDER, settings
from src.pipelines import refine
from src.pipelines.executor import iniciar_em_background
from src.pipelines.models import (
    ConfigLLM,
    FonteYouTube,
    PassoPipeline,
    PipelineVersao,
    TipoStep,
)
from src.youtube import (
    TranscriptAPI,
    TranscriptError,
    YouTubeAPI,
    YouTubeAPIError,
    extrair_video_id,
)
from src.youtube.client import periodo_rfc3339

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/youtube", tags=["youtube"])

_youtube = YouTubeAPI()
_transcript = TranscriptAPI()


# ─────────────────────────────────────────────────────────────────────────────
# Modelos de request
# ─────────────────────────────────────────────────────────────────────────────

class BuscaRequest(BaseModel):
    query: str
    max_results: int = Field(default=10, ge=1, le=50)
    order: str = "relevance"
    meses_atras: int | None = 6
    published_after: str | None = None
    published_before: str | None = None
    channel_id: str | None = None
    relevance_language: str | None = "pt"
    region_code: str | None = None
    video_duration: str | None = None

    # filtros aplicados após a coleta das estatísticas
    min_views: int | None = None
    min_likes: int | None = None
    min_comments: int | None = None
    duracao_min_segundos: int | None = None
    duracao_max_segundos: int | None = None


class VideoRequest(BaseModel):
    """Aceita URL completa (qualquer formato do YouTube) ou o id de 11 chars."""
    url: str


class TranscriptRequest(BaseModel):
    video_id: str
    idiomas: list[str] = Field(default_factory=lambda: ["pt", "pt-BR", "en"])


class ComentariosRequest(BaseModel):
    video_id: str
    max_results: int = Field(default=100, ge=1, le=500)
    order: str = "relevance"


class PlaygroundRequest(BaseModel):
    """
    Configuração ad-hoc executada pelo Playground.

    É a mesma estrutura que uma pipeline salva usa — por isso "Gerar Pipeline"
    é só persistir o que já está aqui.
    """
    source: FonteYouTube = Field(default_factory=FonteYouTube)
    steps: list[PassoPipeline] = Field(default_factory=list)
    llm: ConfigLLM = Field(default_factory=ConfigLLM)


# ─────────────────────────────────────────────────────────────────────────────
# GET /youtube/config
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/config")
async def obter_config():
    """
    O que o backend tem configurado.

    Devolve apenas presença/ausência de credencial e a variável de ambiente
    esperada — nunca o valor da chave.
    """
    providers = []
    for item in listar_providers():
        nome = item["provider"]
        providers.append({
            **item,
            "configurado": settings.provider_configurado(nome),
            "env_var": ENV_VAR_POR_PROVIDER.get(nome, ""),
        })

    return {
        "youtube": {
            "configurado": _youtube.configurado,
            "env_var": "YOUTUBE_API_KEY",
        },
        "transcript": {
            "disponivel": _transcript_disponivel(),
            "pacote": "youtube-transcript-api",
        },
        "llm": {
            "provider_padrao": settings.llm_provider_padrao,
            "model_padrao": settings.llm_model_padrao,
            "providers": providers,
        },
        "warehouse": warehouse.status(),
    }


def _transcript_disponivel() -> bool:
    try:
        TranscriptAPI._importar()
        return True
    except TranscriptError:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# POST /youtube/search
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/search")
async def buscar_videos(body: BuscaRequest):
    """
    Busca vídeos e devolve os cards já normalizados (camada REFINED da busca),
    junto do envelope RAW original para inspeção.

    Vídeos reprovados pelos filtros numéricos voltam em `descartados`, com o
    motivo — o usuário vê por que o conjunto encolheu.
    """
    if not body.query.strip():
        raise HTTPException(400, detail={"erro": "O campo 'query' é obrigatório."})

    if not _youtube.configurado:
        raise HTTPException(
            503,
            detail={"erro": "YOUTUBE_API_KEY não configurada no servidor."},
        )

    if body.published_after or body.published_before:
        inicio, fim = body.published_after, body.published_before
    elif body.meses_atras:
        inicio, fim = periodo_rfc3339(body.meses_atras)
    else:
        inicio, fim = None, None

    try:
        busca = await _youtube.search_videos(
            body.query,
            max_results=body.max_results,
            order=body.order,
            published_after=inicio,
            published_before=fim,
            relevance_language=body.relevance_language,
            region_code=body.region_code,
            channel_id=body.channel_id,
            video_duration=body.video_duration,
        )

        ids = [
            item["id"]["videoId"]
            for item in busca.get("items", [])
            if (item.get("id") or {}).get("videoId")
        ]
        if not ids:
            return {"videos": [], "descartados": [], "total": 0, "raw": {"search": busca}}

        videos_brutos = await _youtube.get_videos(ids)
        canais_brutos = await _youtube.get_channels(
            [(v.get("snippet") or {}).get("channelId") for v in videos_brutos]
        )
    except YouTubeAPIError as exc:
        raise HTTPException(
            502,
            detail={"erro": str(exc), "motivo": exc.motivo},
        ) from exc

    refinado = refine.refinar({
        "videos": videos_brutos,
        "channels": canais_brutos,
        "transcripts": {},
        "comments": {},
    })

    aprovados, descartados = refine.aplicar_filtros(refinado["videos"], body)

    return {
        "videos": aprovados,
        "descartados": descartados,
        "total": len(aprovados),
        "periodo": {"de": inicio, "ate": fim},
        "raw": {"search": busca, "videos": videos_brutos, "channels": canais_brutos},
    }


# ─────────────────────────────────────────────────────────────────────────────
# POST /youtube/video
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/video")
async def obter_video(body: VideoRequest):
    """Resolve uma URL ou id em um card de vídeo normalizado (Modo B)."""
    video_id = extrair_video_id(body.url)
    if not video_id:
        raise HTTPException(
            400,
            detail={"erro": "Não foi possível extrair o ID do vídeo a partir do valor informado."},
        )

    if not _youtube.configurado:
        raise HTTPException(503, detail={"erro": "YOUTUBE_API_KEY não configurada no servidor."})

    try:
        bruto = await _youtube.get_video(video_id)
        if bruto is None:
            raise HTTPException(404, detail={"erro": f"Vídeo '{video_id}' não encontrado."})

        canais = await _youtube.get_channels(
            [(bruto.get("snippet") or {}).get("channelId")]
        )
    except YouTubeAPIError as exc:
        raise HTTPException(502, detail={"erro": str(exc), "motivo": exc.motivo}) from exc

    refinado = refine.refinar({
        "videos": [bruto], "channels": canais, "transcripts": {}, "comments": {},
    })

    return {"video": refinado["videos"][0], "raw": {"video": bruto, "channels": canais}}


# ─────────────────────────────────────────────────────────────────────────────
# POST /youtube/transcript
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/transcript")
async def obter_transcricao(body: TranscriptRequest):
    """Transcrição de um vídeo, nas camadas RAW e REFINED."""
    video_id = extrair_video_id(body.video_id) or body.video_id

    try:
        bruto = await _transcript.get_transcript(video_id, tuple(body.idiomas))
    except TranscriptError as exc:
        status = 503 if exc.motivo == "missing_dependency" else 404
        raise HTTPException(status, detail={"erro": str(exc), "motivo": exc.motivo}) from exc

    return {"raw": bruto, "refined": refine.refinar_transcricao(bruto)}


# ─────────────────────────────────────────────────────────────────────────────
# POST /youtube/comments
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/comments")
async def obter_comentarios(body: ComentariosRequest):
    """Comentários de um vídeo, nas camadas RAW e REFINED (deduplicados)."""
    video_id = extrair_video_id(body.video_id) or body.video_id

    if not _youtube.configurado:
        raise HTTPException(503, detail={"erro": "YOUTUBE_API_KEY não configurada no servidor."})

    try:
        brutos = await _youtube.get_comments(
            video_id, max_results=body.max_results, order=body.order
        )
    except YouTubeAPIError as exc:
        raise HTTPException(502, detail={"erro": str(exc), "motivo": exc.motivo}) from exc

    refinados = refine.refinar_comentarios(brutos, video_id)

    return {
        "raw": brutos,
        "refined": refinados,
        "total_bruto": len(brutos),
        "total_refinado": len(refinados),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Playground
# ─────────────────────────────────────────────────────────────────────────────

def _montar_config(body: PlaygroundRequest) -> PipelineVersao:
    """
    Converte o corpo do Playground na mesma `PipelineVersao` que uma pipeline
    salva produz — garantindo que Playground e Airflow executem a mesma coisa.
    """
    from src.pipelines.models import ConfigSchedule

    steps = list(body.steps)
    if not steps:
        steps = [
            PassoPipeline(type=TipoStep.YOUTUBE_SEARCH),
            PassoPipeline(type=TipoStep.TRANSCRIPT),
            PassoPipeline(type=TipoStep.COMMENTS),
            PassoPipeline(type=TipoStep.RAW),
            PassoPipeline(type=TipoStep.REFINED),
        ]

    return PipelineVersao(
        version=0,                       # 0 = configuração não persistida
        source=body.source,
        steps=steps,
        llm=body.llm,
        schedule=ConfigSchedule(),
    )


@router.post("/playground/preview")
async def preview_pipeline(body: PlaygroundRequest):
    """
    Devolve a configuração normalizada e a lista de etapas que rodariam,
    sem executar nada. Usado para desenhar a régua do Playground.
    """
    config = _montar_config(body)

    return {
        "source": config.source.model_dump(mode="json"),
        "llm": config.llm.model_dump(mode="json"),
        "steps": [
            {"type": p.type.value, "prompt_id": p.prompt_id,
             "prompt_version": p.prompt_version, "enabled": p.enabled}
            for p in config.steps
        ],
    }


@router.post("/playground/run")
async def executar_playground(body: PlaygroundRequest):
    """
    Executa a pipeline do Playground em background.

    Devolve `run_id` na hora; o frontend acompanha por
    `GET /pipelines/runs/{run_id}` (Regra 35: nada bloqueia a interface).
    """
    if not body.source.query.strip() and not body.source.video_ids:
        raise HTTPException(
            400,
            detail={"erro": "Informe uma query de busca ou selecione ao menos um vídeo."},
        )

    config = _montar_config(body)
    run = iniciar_em_background(config, pipeline_name="Playground", origem="playground")

    return {
        "run_id": run.id,
        "status": run.status.value,
        "steps": [{"type": s.type.value, "name": s.name, "status": s.status.value}
                  for s in run.steps],
    }
