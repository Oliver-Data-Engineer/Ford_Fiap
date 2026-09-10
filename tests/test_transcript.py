"""
test_transcript.py — verifica que src/youtube/transcript.py distingue
"YouTube bloqueou a requisição" (RequestBlocked/IpBlocked — nada a ver com o
vídeo, comum em IP de datacenter) de "vídeo genuinamente sem legenda"
(TranscriptsDisabled/NoTranscriptFound) — motivo real de um erro que
usuários já bateram em produção (a mensagem genérica "sem legenda" mandava
procurar o problema no vídeo errado).

Roda contra as classes de exceção REAIS de youtube_transcript_api — não um
dublê — porque o que está em jogo é justamente se `isinstance()` bate certo
com o que a biblioteca de fato levanta.
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.youtube.transcript import TranscriptAPI, TranscriptError
from youtube_transcript_api import (
    IpBlocked,
    NoTranscriptFound,
    RequestBlocked,
    TranscriptsDisabled,
)

falhas = []


def checar(condicao, descricao):
    print(("  OK   " if condicao else "  FALHA") + f"  {descricao}")
    if not condicao:
        falhas.append(descricao)


def motivo_para(excecao_da_lib) -> str:
    """Roda _buscar_sync com um cliente falso que levanta a exceção dada."""
    class ClienteFalso:
        def __init__(self, proxy_config=None):
            pass

        def list(self, video_id):
            raise excecao_da_lib

    api = TranscriptAPI()
    with patch.object(TranscriptAPI, "_importar", staticmethod(lambda: ClienteFalso)):
        try:
            api._buscar_sync("vid_teste", ("pt",))
        except TranscriptError as exc:
            return exc.motivo
    return None  # não deveria chegar aqui — a exceção sempre deve propagar


def principal() -> int:
    print("\n=== Classificação de erros de transcrição ===")

    checar(motivo_para(RequestBlocked("vid_teste")) == "blocked",
           "RequestBlocked vira motivo='blocked'")
    checar(motivo_para(IpBlocked("vid_teste")) == "blocked",
           "IpBlocked (subclasse de RequestBlocked) também vira 'blocked'")
    checar(motivo_para(TranscriptsDisabled("vid_teste")) == "unavailable",
           "TranscriptsDisabled continua 'unavailable' (não é bloqueio)")
    checar(motivo_para(NoTranscriptFound("vid_teste", ["pt"], None)) == "unavailable",
           "NoTranscriptFound continua 'unavailable' (não é bloqueio)")
    checar(motivo_para(RuntimeError("erro genérico qualquer")) == "unavailable",
           "erro não reconhecido pela lib cai em 'unavailable', não quebra")

    print("\n" + "=" * 62)
    if falhas:
        print(f"{len(falhas)} VERIFICAÇÃO(ÕES) FALHARAM:")
        for f in falhas:
            print(f"  - {f}")
    else:
        print("Todas as verificações de transcrição passaram.")
    print("=" * 62)
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(principal())
