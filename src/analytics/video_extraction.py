"""
video_extraction.py
Extração automática de vídeos — struct único e fixo `{resumo, topicos,
modelos_mencionados, pontos_positivos, pontos_negativos}`, sem "modos"
selecionáveis: roda sozinha junto da pipeline (mesma ideia de
`src/analytics/enrichment.py`, mas uma chamada de LLM por vídeo — sobre o
título + transcrição — em vez de por lote de comentários).

Reaproveita `Agente.run()` (`src/agents/agent.py`) — a mesma infraestrutura
de chamada à LLM usada pelos passos 'agent' normais da pipeline (Regra 7 do
SPEC: só o engine chama o LLM). A saída É validada contra
`ExtracaoVideoStruct` — no resto do app, `output_schema` é só texto
sugestivo para o prompt, nunca verificado.
"""
from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from src.agents import Agente
from src.analytics.models import ExtracaoVideoStruct
from src.pipelines.models import Prompt, PromptEntrada, TipoEntrada
from src.pipelines.store import criar_prompt, listar_prompts

logger = logging.getLogger(__name__)

NOME_PROMPT_EXTRACAO_VIDEO = "Extração de vídeos (fixo)"

TEXTO_PROMPT_EXTRACAO_VIDEO = (
    "Você recebe o título e a transcrição de um vídeo do YouTube sobre um "
    "veículo. Extraia:\n\n"
    "- resumo: um resumo objetivo do vídeo, em até 3 frases.\n"
    "- topicos: lista dos principais assuntos abordados (em português, "
    "minúsculas; ex.: \"motor\", \"consumo\", \"design\", \"preço\").\n"
    "- modelos_mencionados: lista dos modelos/versões de veículo citados no "
    "vídeo, exatamente como aparecem na fala (ex.: \"Ford Territory\", "
    "\"Ranger XLT\").\n"
    "- pontos_positivos: lista curta de elogios ou pontos favoráveis "
    "mencionados sobre o(s) veículo(s).\n"
    "- pontos_negativos: lista curta de críticas ou pontos desfavoráveis "
    "mencionados sobre o(s) veículo(s).\n\n"
    "Se a transcrição não permitir preencher uma lista, devolva-a vazia — "
    "nunca invente um item que não esteja no texto.\n\n"
    "Responda em JSON no formato:\n"
    "{\"resumo\": \"...\", \"topicos\": [\"...\"], "
    "\"modelos_mencionados\": [\"...\"], \"pontos_positivos\": [\"...\"], "
    "\"pontos_negativos\": [\"...\"]}"
)

SCHEMA_PROMPT_EXTRACAO_VIDEO: dict[str, Any] = {
    "resumo": "string — resumo objetivo do vídeo, até 3 frases",
    "topicos": ["string — principal assunto abordado"],
    "modelos_mencionados": ["string — modelo/versão de veículo citado"],
    "pontos_positivos": ["string — elogio/ponto favorável mencionado"],
    "pontos_negativos": ["string — crítica/ponto desfavorável mencionado"],
}


def garantir_prompt() -> Prompt:
    """
    Localiza (ou cria, na primeira vez) o único prompt fixo de extração de
    vídeos. Não é seletável pelo usuário como um "modo", mas fica
    versionado como qualquer outro `Prompt` (Regra 8 do SPEC — prompt é
    configuração, não código) e reaproveita o mesmo CRUD/UI de prompts.
    """
    existente = next(
        (p for p in listar_prompts() if p.name == NOME_PROMPT_EXTRACAO_VIDEO), None
    )
    if existente:
        return existente

    logger.info("[analytics] criando prompt fixo de extração de vídeos.")
    return criar_prompt(PromptEntrada(
        name=NOME_PROMPT_EXTRACAO_VIDEO,
        description=(
            "Prompt fixo do sistema — roda automaticamente para todo vídeo "
            "com transcrição coletada. O texto pode ser ajustado; o formato "
            "de saída (schema) não deve mudar."
        ),
        prompt=TEXTO_PROMPT_EXTRACAO_VIDEO,
        input_type=TipoEntrada.TRANSCRIPT,
        output_format="json",
        output_schema=SCHEMA_PROMPT_EXTRACAO_VIDEO,
    ))


async def extrair_video(
    agente: Agente,
    video: dict,
    transcricao_texto: str,
    *,
    prompt: Prompt | None = None,
) -> dict:
    """
    Roda o prompt fixo sobre um vídeo (uma chamada por vídeo, sobre a
    transcrição já recortada pelo chamador) e devolve:
    `{video_id, titulo, success, struct, error, usage, prompt_id, prompt_version}`.
    """
    prompt = prompt or garantir_prompt()
    versao = prompt.na_versao(None)

    entrada = {"titulo": video.get("titulo", ""), "transcricao": transcricao_texto}

    saida = await agente.run(
        entrada,
        prompt=versao.prompt,
        output_schema=versao.output_schema,
        output_format=versao.output_format,
    )

    base = {
        "video_id": video.get("video_id"),
        "titulo": video.get("titulo"),
        "usage": saida.usage,
        "prompt_id": prompt.id,
        "prompt_version": versao.version,
    }

    if not saida.success:
        erro = saida.error.__dict__ if saida.error else {"message": "erro desconhecido"}
        return {**base, "success": False, "struct": None, "error": erro}

    try:
        struct = ExtracaoVideoStruct(
            resumo=(saida.output or {}).get("resumo"),
            topicos=(saida.output or {}).get("topicos"),
            modelos_mencionados=(saida.output or {}).get("modelos_mencionados"),
            pontos_positivos=(saida.output or {}).get("pontos_positivos"),
            pontos_negativos=(saida.output or {}).get("pontos_negativos"),
        ).model_dump()
        return {**base, "success": True, "struct": struct, "error": None}
    except ValidationError as exc:
        return {**base, "success": False, "struct": None,
                "error": {"type": "OutputParseError", "message": str(exc)}}
