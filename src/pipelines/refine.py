"""
refine.py
Camada REFINED — tratamento determinístico dos dados RAW.

Regra 6 da spec: nada aqui interpreta o conteúdo.  São só normalização,
limpeza, deduplicação e padronização de campos.  Rodar duas vezes sobre o
mesmo RAW produz exatamente o mesmo REFINED.

Herda de `limpar_texto_para_llm()` do notebook e generaliza o tratamento.
"""
from __future__ import annotations

import hashlib
import html
import logging
import re
import unicodedata
from typing import Any

logger = logging.getLogger(__name__)

# ── Padrões de limpeza ───────────────────────────────────────────────────────

_URL = re.compile(r"https?://\S+|www\.\S+")
_TAG_HTML = re.compile(r"<[^>]+>")
_ESPACOS = re.compile(r"\s+")
_PONTUACAO_REPETIDA = re.compile(r"([.!?,])\1+")
_CONTROLE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# ISO-8601 de duração devolvido por contentDetails.duration (ex.: PT12M34S)
_DURACAO_ISO = re.compile(
    r"^P(?:(?P<dias>\d+)D)?T?(?:(?P<horas>\d+)H)?(?:(?P<min>\d+)M)?(?:(?P<seg>\d+)S)?$"
)


# ─────────────────────────────────────────────────────────────────────────────
# Normalização de texto
# ─────────────────────────────────────────────────────────────────────────────

def limpar_texto(texto: Any) -> str:
    """
    Normaliza texto para consumo por LLM sem alterar seu sentido.

    Ordem importa: entidades HTML são desescapadas antes de remover tags, para
    que `&lt;b&gt;` não sobreviva como texto visível.
    """
    if not isinstance(texto, str) or not texto.strip():
        return ""

    limpo = unicodedata.normalize("NFC", texto)
    limpo = html.unescape(limpo)
    limpo = _TAG_HTML.sub(" ", limpo)
    limpo = _URL.sub("", limpo)
    limpo = _CONTROLE.sub(" ", limpo)
    limpo = _ESPACOS.sub(" ", limpo)
    limpo = _PONTUACAO_REPETIDA.sub(r"\1", limpo)
    # Aspas irregulares quebram o JSON que o modelo devolve
    limpo = limpo.replace('"', "'").replace("`", "'")

    return limpo.strip()


def chave_dedup(texto: str) -> str:
    """
    Chave de deduplicação: minúsculas, sem acento e sem pontuação.
    Dois comentários que só diferem em caixa/acento contam como um.
    """
    base = unicodedata.normalize("NFKD", texto.lower())
    base = "".join(c for c in base if not unicodedata.combining(c))
    base = re.sub(r"[^a-z0-9 ]", "", base)
    return _ESPACOS.sub(" ", base).strip()


def _hash_curto(texto: str) -> str:
    return hashlib.sha1(texto.encode("utf-8")).hexdigest()[:12]


# ─────────────────────────────────────────────────────────────────────────────
# Normalização de campos estruturados
# ─────────────────────────────────────────────────────────────────────────────

def duracao_para_segundos(iso: Any) -> int | None:
    """PT1H2M3S → 3723.  Devolve None quando o formato não é reconhecido."""
    if not isinstance(iso, str):
        return None

    achado = _DURACAO_ISO.match(iso.strip())
    if not achado:
        return None

    partes = {k: int(v or 0) for k, v in achado.groupdict().items()}
    return (
        partes["dias"] * 86400
        + partes["horas"] * 3600
        + partes["min"] * 60
        + partes["seg"]
    )


def para_int(valor: Any) -> int | None:
    """
    A YouTube API devolve contadores como string ('12345').
    Campos ocultos pelo criador (likes/comentários) vêm ausentes → None.
    """
    if valor is None or valor == "":
        return None
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# REFINED por entidade
# ─────────────────────────────────────────────────────────────────────────────

def refinar_video(bruto: dict) -> dict:
    """
    Normaliza um item de `videos.list` (com o snippet da busca como fallback).

    Padroniza nomes de campos, converte contadores para inteiro e resolve a
    duração ISO-8601 para segundos.
    """
    snippet = bruto.get("snippet") or {}
    stats = bruto.get("statistics") or {}
    detalhes = bruto.get("contentDetails") or {}
    thumbs = snippet.get("thumbnails") or {}

    thumb = (
        (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {})
        .get("url")
    )

    video_id = bruto.get("id") or bruto.get("video_id") or ""
    duracao_iso = detalhes.get("duration")

    return {
        "video_id": video_id,
        "titulo": limpar_texto(snippet.get("title")),
        "descricao": limpar_texto(snippet.get("description")),
        "canal_id": snippet.get("channelId"),
        "canal_nome": limpar_texto(snippet.get("channelTitle")),
        "publicado_em": snippet.get("publishedAt"),
        "idioma": snippet.get("defaultAudioLanguage") or snippet.get("defaultLanguage"),
        "tags": snippet.get("tags") or [],
        "thumbnail": thumb,
        "url": f"https://www.youtube.com/watch?v={video_id}" if video_id else None,
        "duracao_iso": duracao_iso,
        "duracao_segundos": duracao_para_segundos(duracao_iso),
        "views": para_int(stats.get("viewCount")),
        "likes": para_int(stats.get("likeCount")),
        "comentarios": para_int(stats.get("commentCount")),
    }


def refinar_canal(bruto: dict) -> dict:
    """Normaliza um item de `channels.list`."""
    snippet = bruto.get("snippet") or {}
    stats = bruto.get("statistics") or {}

    return {
        "canal_id": bruto.get("id"),
        "nome": limpar_texto(snippet.get("title")),
        "criado_em": snippet.get("publishedAt"),
        "inscritos": para_int(stats.get("subscriberCount")),
        "views_totais": para_int(stats.get("viewCount")),
        "videos_totais": para_int(stats.get("videoCount")),
    }


def refinar_transcricao(bruto: dict) -> dict:
    """
    Junta os segmentos num texto contínuo, preservando os marcos de tempo.

    `clean_text` é o que vai ao LLM; `segmentos` fica para rastreabilidade
    (permite apontar em que minuto do vídeo uma frase aparece).
    """
    segmentos_brutos = bruto.get("segments") or []

    segmentos: list[dict] = []
    pedacos: list[str] = []
    for seg in segmentos_brutos:
        texto = limpar_texto(seg.get("text"))
        if not texto:
            continue
        segmentos.append({
            "texto": texto,
            "inicio_segundos": round(float(seg.get("start") or 0), 2),
        })
        pedacos.append(texto)

    texto_completo = " ".join(pedacos)

    return {
        "video_id": bruto.get("video_id"),
        "language": bruto.get("language"),
        "gerada_automaticamente": bruto.get("is_generated"),
        "clean_text": texto_completo,
        "caracteres": len(texto_completo),
        "palavras": len(texto_completo.split()) if texto_completo else 0,
        "segmentos": segmentos,
    }


def refinar_comentarios(brutos: list[dict], video_id: str) -> list[dict]:
    """
    Normaliza e deduplica os comentários de um vídeo.

    Comentários vazios após a limpeza são descartados (não têm valor para o
    LLM e só custariam tokens).  A ordem original é preservada.
    """
    refinados: list[dict] = []
    vistos: set[str] = set()

    for item in brutos:
        topo = ((item.get("snippet") or {}).get("topLevelComment") or {})
        snippet = topo.get("snippet") or {}

        texto = limpar_texto(snippet.get("textDisplay") or snippet.get("textOriginal"))
        if not texto:
            continue

        chave = chave_dedup(texto)
        if not chave or chave in vistos:
            continue
        vistos.add(chave)

        refinados.append({
            "comment_id": topo.get("id") or item.get("id"),
            "video_id": video_id,
            "texto": texto,
            "autor": limpar_texto(snippet.get("authorDisplayName")),
            "likes": para_int(snippet.get("likeCount")) or 0,
            "respostas": para_int((item.get("snippet") or {}).get("totalReplyCount")) or 0,
            "publicado_em": snippet.get("publishedAt"),
            "refined_record_id": f"cmt_{_hash_curto(f'{video_id}:{chave}')}",
        })

    return refinados


# ─────────────────────────────────────────────────────────────────────────────
# REFINED do conjunto
# ─────────────────────────────────────────────────────────────────────────────

def refinar(raw: dict) -> dict:
    """
    Transforma o pacote RAW inteiro em REFINED.

    RAW esperado:
        {"videos": [...], "channels": [...],
         "transcripts": {vid: {...}}, "comments": {vid: [...]},
         "search": {...}}

    REFINED produzido:
        {"videos": [...], "channels": {...},
         "transcripts": {vid: {...}}, "comments": {vid: [...]},
         "estatisticas": {...}}

    O RAW recebido nunca é modificado (Regra 5).
    """
    videos = [refinar_video(v) for v in raw.get("videos") or []]

    canais = {}
    for bruto in raw.get("channels") or []:
        canal = refinar_canal(bruto)
        if canal["canal_id"]:
            canais[canal["canal_id"]] = canal

    # Anexa dados do canal a cada vídeo — evita join na UI e no prompt
    for video in videos:
        canal = canais.get(video.get("canal_id"))
        if canal:
            video["canal_inscritos"] = canal["inscritos"]
            video["canal_views_totais"] = canal["views_totais"]

    transcricoes = {
        vid: refinar_transcricao(dados)
        for vid, dados in (raw.get("transcripts") or {}).items()
    }

    comentarios = {
        vid: refinar_comentarios(lista, vid)
        for vid, lista in (raw.get("comments") or {}).items()
    }

    total_comentarios = sum(len(c) for c in comentarios.values())
    total_brutos = sum(len(c) for c in (raw.get("comments") or {}).values())

    return {
        "videos": videos,
        "channels": canais,
        "transcripts": transcricoes,
        "comments": comentarios,
        "estatisticas": {
            "total_videos": len(videos),
            "total_transcricoes": len(transcricoes),
            "total_comentarios": total_comentarios,
            "comentarios_descartados": max(0, total_brutos - total_comentarios),
            "total_palavras_transcricao": sum(
                t.get("palavras", 0) for t in transcricoes.values()
            ),
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Filtros pós-coleta (determinísticos, parte do REFINED)
# ─────────────────────────────────────────────────────────────────────────────

def aplicar_filtros(videos: list[dict], fonte: Any) -> tuple[list[dict], list[dict]]:
    """
    Separa os vídeos refinados entre aprovados e descartados pelos filtros
    numéricos da fonte (views/likes/comentários/duração mínimos e máximos).

    Devolve (aprovados, descartados) — o Playground mostra os dois, para que o
    usuário entenda por que um vídeo saiu do conjunto.
    """
    aprovados: list[dict] = []
    descartados: list[dict] = []

    regras = (
        ("views", getattr(fonte, "min_views", None), "min"),
        ("likes", getattr(fonte, "min_likes", None), "min"),
        ("comentarios", getattr(fonte, "min_comments", None), "min"),
        ("duracao_segundos", getattr(fonte, "duracao_min_segundos", None), "min"),
        ("duracao_segundos", getattr(fonte, "duracao_max_segundos", None), "max"),
    )

    for video in videos:
        motivo = None
        for campo, limite, tipo in regras:
            if limite is None:
                continue
            valor = video.get(campo)
            if valor is None:
                continue  # métrica oculta pelo criador não reprova o vídeo
            if tipo == "min" and valor < limite:
                motivo = f"{campo} ({valor}) abaixo do mínimo ({limite})"
                break
            if tipo == "max" and valor > limite:
                motivo = f"{campo} ({valor}) acima do máximo ({limite})"
                break

        if motivo:
            descartados.append({**video, "motivo_descarte": motivo})
        else:
            aprovados.append(video)

    return aprovados, descartados
