"""
client.py
Cliente da YouTube Data API v3.

Fala HTTP direto com googleapis.com via httpx (async) em vez de usar o
googleapiclient — a API é REST pura e o restante do backend é assíncrono.

Superfície pública:
    YouTubeAPI
     ├── search_videos()     busca vídeos por query + filtros
     ├── get_videos()        metadados/estatísticas de vários vídeos em lote
     ├── get_video()         metadados de um único vídeo
     ├── get_channels()      estatísticas de vários canais em lote
     └── get_comments()      comentários paginados de um vídeo

Toda resposta devolvida é o JSON **original** da API — a camada RAW depende
disso (Regra 5: RAW é imutável e preserva o máximo da resposta original).
"""
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://www.googleapis.com/youtube/v3"

# Limites da própria API do YouTube
MAX_IDS_POR_LOTE = 50        # videos.list / channels.list aceitam até 50 ids
MAX_RESULTS_POR_PAGINA = 50  # search.list / commentThreads.list

TIMEOUT_PADRAO = 20.0


class YouTubeAPIError(RuntimeError):
    """Falha ao consultar a YouTube Data API."""

    def __init__(self, mensagem: str, status: int | None = None, motivo: str | None = None):
        super().__init__(mensagem)
        self.status = status
        self.motivo = motivo


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de módulo
# ─────────────────────────────────────────────────────────────────────────────

# Formatos aceitos: watch?v=, youtu.be/, /embed/, /shorts/, /live/ ou o id puro
_PADROES_VIDEO_ID = (
    re.compile(r"(?:youtube\.com|youtube-nocookie\.com)/watch\?(?:.*&)?v=([A-Za-z0-9_-]{11})"),
    re.compile(r"youtu\.be/([A-Za-z0-9_-]{11})"),
    re.compile(r"/(?:embed|shorts|live|v)/([A-Za-z0-9_-]{11})"),
)
_ID_PURO = re.compile(r"^[A-Za-z0-9_-]{11}$")


def extrair_video_id(entrada: str) -> str | None:
    """
    Extrai o video_id de uma URL do YouTube — ou devolve a própria string
    quando ela já é um id de 11 caracteres.

    >>> extrair_video_id("https://youtube.com/watch?v=BmYsInLDmrI")
    'BmYsInLDmrI'
    """
    if not entrada:
        return None

    texto = entrada.strip()

    if _ID_PURO.match(texto):
        return texto

    for padrao in _PADROES_VIDEO_ID:
        achado = padrao.search(texto)
        if achado:
            return achado.group(1)

    return None


def periodo_rfc3339(meses_atras: int) -> tuple[str, str]:
    """
    Janela temporal no formato RFC 3339 exigido pelo YouTube (sufixo 'Z' = UTC).
    Herdado de `definir_periodo_busca()` do notebook.
    """
    agora = datetime.now(timezone.utc)
    inicio = agora - timedelta(days=meses_atras * 30)
    return (
        inicio.strftime("%Y-%m-%dT%H:%M:%SZ"),
        agora.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def _lotes(itens: list[str], tamanho: int):
    """Fatia uma lista em blocos de no máximo `tamanho` elementos."""
    for i in range(0, len(itens), tamanho):
        yield itens[i:i + tamanho]


# ─────────────────────────────────────────────────────────────────────────────
# Cliente
# ─────────────────────────────────────────────────────────────────────────────

class YouTubeAPI:
    """
    Cliente da YouTube Data API v3.

    A chave vem de `settings.youtube_api_key` por padrão; o parâmetro
    `api_key` existe só para testes e para execuções que precisem de uma
    chave distinta (nunca vem do frontend).

    Sempre chamada direto, sem proxy: a API oficial (autenticada por chave)
    não tem o problema de bloqueio por IP que a coleta de transcrição tem
    (essa é anônima, baseada em scraping) — rotear por proxy aqui só
    adicionaria latência sem resolver nada. Proxy fica restrito a
    `TranscriptAPI`, e mesmo lá só entra em jogo se o YouTube bloquear.
    """

    def __init__(self, api_key: str | None = None, timeout: float = TIMEOUT_PADRAO):
        self._api_key = api_key or settings.youtube_api_key
        self._timeout = timeout

    # ── Infra ────────────────────────────────────────────────────────────────

    @property
    def configurado(self) -> bool:
        return bool(self._api_key)

    async def _get(self, recurso: str, params: dict[str, Any]) -> dict:
        """Executa GET /{recurso} e devolve o JSON original da API."""
        if not self._api_key:
            raise YouTubeAPIError(
                "YOUTUBE_API_KEY não configurada no ambiente.",
                motivo="missing_api_key",
            )

        # Remove parâmetros vazios — a API rejeita valores None
        limpos = {k: v for k, v in params.items() if v not in (None, "", [])}
        limpos["key"] = self._api_key

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resposta = await client.get(f"{BASE_URL}/{recurso}", params=limpos)
        except httpx.TimeoutException as exc:
            raise YouTubeAPIError(
                f"Tempo limite excedido ao consultar '{recurso}'.",
                motivo="timeout",
            ) from exc
        except httpx.HTTPError as exc:
            raise YouTubeAPIError(
                f"Falha de rede ao consultar '{recurso}': {exc}",
                motivo="network",
            ) from exc

        if resposta.status_code >= 400:
            detalhe = self._extrair_erro(resposta)
            # A mensagem nunca inclui a URL, que carrega a chave na query string
            raise YouTubeAPIError(
                f"YouTube API respondeu {resposta.status_code} em '{recurso}': {detalhe['mensagem']}",
                status=resposta.status_code,
                motivo=detalhe["motivo"],
            )

        return resposta.json()

    @staticmethod
    def _extrair_erro(resposta: httpx.Response) -> dict[str, str]:
        """Traduz o corpo de erro da API do Google para mensagem + motivo."""
        try:
            corpo = resposta.json().get("error", {})
            erros = corpo.get("errors") or [{}]
            return {
                "mensagem": corpo.get("message", resposta.text[:200]),
                "motivo": erros[0].get("reason", "unknown"),
            }
        except Exception:
            return {"mensagem": resposta.text[:200], "motivo": "unknown"}

    # ── search.list ──────────────────────────────────────────────────────────

    async def search_videos(
        self,
        query: str,
        *,
        max_results: int = 15,
        order: str = "relevance",
        published_after: str | None = None,
        published_before: str | None = None,
        relevance_language: str | None = "pt",
        region_code: str | None = None,
        channel_id: str | None = None,
        video_duration: str | None = None,   # any | short | medium | long
        page_token: str | None = None,
    ) -> dict:
        """
        Busca vídeos. Devolve o JSON original de `search.list`.

        `max_results` acima de 50 é resolvido com paginação automática: os
        `items` de todas as páginas são concatenados no mesmo envelope.
        """
        restante = max(1, max_results)
        itens: list[dict] = []
        envelope: dict = {}
        token = page_token

        while restante > 0:
            pagina = await self._get("search", {
                "q": query,
                "part": "snippet",
                "type": "video",
                "maxResults": min(MAX_RESULTS_POR_PAGINA, restante),
                "order": order,
                "publishedAfter": published_after,
                "publishedBefore": published_before,
                "relevanceLanguage": relevance_language,
                "regionCode": region_code,
                "channelId": channel_id,
                "videoDuration": video_duration,
                "pageToken": token,
            })

            envelope = pagina
            recebidos = pagina.get("items", [])
            itens.extend(recebidos)
            restante -= len(recebidos)

            token = pagina.get("nextPageToken")
            if not token or not recebidos:
                break

        envelope["items"] = itens[:max_results]
        return envelope

    # ── videos.list ──────────────────────────────────────────────────────────

    async def get_videos(
        self,
        video_ids: list[str],
        *,
        part: str = "snippet,statistics,contentDetails",
    ) -> list[dict]:
        """
        Metadados de vários vídeos, em lotes de 50 (1 quota unit por lote em
        vez de 1 por vídeo — é o "batching" do notebook, generalizado).
        """
        if not video_ids:
            return []

        coletados: list[dict] = []
        for lote in _lotes(video_ids, MAX_IDS_POR_LOTE):
            pagina = await self._get("videos", {"part": part, "id": ",".join(lote)})
            coletados.extend(pagina.get("items", []))

        return coletados

    async def get_video(self, video_id: str, *, part: str = "snippet,statistics,contentDetails") -> dict | None:
        """Metadados de um único vídeo, ou None se o id não existir."""
        itens = await self.get_videos([video_id], part=part)
        return itens[0] if itens else None

    # ── channels.list ────────────────────────────────────────────────────────

    async def get_channels(self, channel_ids: list[str], *, part: str = "snippet,statistics") -> list[dict]:
        """Metadados/estatísticas de vários canais, em lotes de 50."""
        if not channel_ids:
            return []

        unicos = list(dict.fromkeys(channel_ids))  # dedup preservando ordem
        coletados: list[dict] = []
        for lote in _lotes(unicos, MAX_IDS_POR_LOTE):
            pagina = await self._get("channels", {"part": part, "id": ",".join(lote)})
            coletados.extend(pagina.get("items", []))

        return coletados

    # ── commentThreads.list ──────────────────────────────────────────────────

    async def get_comments(
        self,
        video_id: str,
        *,
        max_results: int = 100,
        order: str = "relevance",
    ) -> list[dict]:
        """
        Comentários de topo de um vídeo, paginando até `max_results`.
        Devolve os `items` originais de `commentThreads.list`.

        Vídeos com comentários desabilitados devolvem [] em vez de estourar —
        é uma condição normal, não uma falha da coleta.
        """
        coletados: list[dict] = []
        token: str | None = None

        while len(coletados) < max_results:
            try:
                pagina = await self._get("commentThreads", {
                    "part": "snippet,replies",
                    "videoId": video_id,
                    "maxResults": min(MAX_RESULTS_POR_PAGINA, max_results - len(coletados)),
                    "textFormat": "plainText",
                    "order": order,
                    "pageToken": token,
                })
            except YouTubeAPIError as exc:
                if exc.motivo in ("commentsDisabled", "videoNotFound"):
                    logger.info(
                        "[youtube] comentários indisponíveis para %s (%s)", video_id, exc.motivo
                    )
                    return coletados
                raise

            coletados.extend(pagina.get("items", []))

            token = pagina.get("nextPageToken")
            if not token:
                break

        return coletados[:max_results]
