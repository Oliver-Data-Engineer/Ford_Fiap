"""
transcript.py
Cliente de transcrição de vídeos do YouTube.

Encapsula a biblioteca `youtube_transcript_api`, que não tem endpoint REST
público — é a única dependência externa síncrona da camada API, então as
chamadas são despachadas para uma thread para não travar o event loop.

A resposta devolvida preserva os segmentos originais (texto + start +
duration); juntar tudo num bloco de texto é trabalho da camada REFINED.
"""
import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Ordem de preferência de idioma ao escolher a transcrição disponível
IDIOMAS_PADRAO = ("pt", "pt-BR", "en")


class TranscriptError(RuntimeError):
    """Transcrição indisponível ou falha ao buscá-la."""

    def __init__(self, mensagem: str, motivo: str = "unavailable"):
        super().__init__(mensagem)
        self.motivo = motivo


class TranscriptAPI:
    """
    Busca transcrições do YouTube.

    A importação de `youtube_transcript_api` é adiada para o momento do uso:
    a aplicação inteira continua subindo mesmo sem o pacote instalado, e só
    a etapa de transcrição falha — com mensagem clara.
    """

    def __init__(self, idiomas: tuple[str, ...] = IDIOMAS_PADRAO):
        self._idiomas = idiomas

    @staticmethod
    def _importar():
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
        except ImportError as exc:
            raise TranscriptError(
                "Pacote 'youtube-transcript-api' não instalado. "
                "Rode: pip install youtube-transcript-api",
                motivo="missing_dependency",
            ) from exc
        return YouTubeTranscriptApi

    def _buscar_sync(self, video_id: str, idiomas: tuple[str, ...]) -> dict[str, Any]:
        """Execução bloqueante — sempre chamada dentro de um worker thread."""
        YouTubeTranscriptApi = self._importar()

        try:
            api = YouTubeTranscriptApi()
            disponiveis = api.list(video_id)
            transcricao = disponiveis.find_transcript(list(idiomas))
            segmentos_brutos = transcricao.fetch()
        except TranscriptError:
            raise
        except Exception as exc:
            raise TranscriptError(
                f"Transcrição indisponível para o vídeo {video_id}: {exc}",
                motivo="unavailable",
            ) from exc

        # A lib devolve dicts em versões antigas e objetos nas novas
        segmentos: list[dict] = []
        for item in segmentos_brutos:
            if isinstance(item, dict):
                segmentos.append({
                    "text": item.get("text", ""),
                    "start": item.get("start", 0.0),
                    "duration": item.get("duration", 0.0),
                })
            else:
                segmentos.append({
                    "text": getattr(item, "text", ""),
                    "start": getattr(item, "start", 0.0),
                    "duration": getattr(item, "duration", 0.0),
                })

        return {
            "video_id": video_id,
            "language": getattr(transcricao, "language_code", None),
            "is_generated": getattr(transcricao, "is_generated", None),
            "segments": segmentos,
        }

    async def get_transcript(
        self,
        video_id: str,
        idiomas: tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """
        Transcrição de um vídeo.

        Retorna:
            {"video_id", "language", "is_generated", "segments": [...]}

        Levanta TranscriptError quando o vídeo não tem legendas disponíveis.
        """
        return await asyncio.to_thread(
            self._buscar_sync, video_id, idiomas or self._idiomas
        )
