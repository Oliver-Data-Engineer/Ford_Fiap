"""
src.youtube — Camada API do YouTube.

Responsabilidade única: comunicação com serviços externos.
Nenhuma regra de negócio, nenhum tratamento de dado, nenhuma dependência da UI.

    from src.youtube import YouTubeAPI, TranscriptAPI, extrair_video_id
"""
from src.youtube.client import YouTubeAPI, YouTubeAPIError, extrair_video_id
from src.youtube.transcript import TranscriptAPI, TranscriptError

__all__ = [
    "YouTubeAPI",
    "YouTubeAPIError",
    "extrair_video_id",
    "TranscriptAPI",
    "TranscriptError",
]
