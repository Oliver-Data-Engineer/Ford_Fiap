"""
enrichment.py (Redes Sociais)
Classificação de sentimento dos posts sintéticos — mesmo padrão de
`src/analytics/enrichment.py` (prompt fixo, versionado, não seletável como
"modo"): struct único `{sentimento, motivo, adjetivos, objeto}`, validado
contra `AnaliseSocialStruct` (diferente de uma etapa `agent` comum, cujo
`output_schema` é só texto sugestivo, nunca verificado).

Reaproveita `Agente.run()` (`src/agents/agent.py`) — é aqui que a IA de
verdade entra: os posts são sintéticos, mas esta classificação é uma chamada
real ao LLM configurado no `.env` (Regra 7 do SPEC: só o engine chama o LLM).
"""
from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from src.agents import Agente
from src.pipelines.models import Prompt, PromptEntrada, TipoEntrada
from src.pipelines.store import criar_prompt, listar_prompts
from src.social.models import AnaliseSocialStruct

logger = logging.getLogger(__name__)

NOME_PROMPT_SOCIAL = "Análise de posts em redes sociais (fixo)"

TEXTO_PROMPT_SOCIAL = (
    "Você recebe uma lista de posts de redes sociais (Reddit, X "
    "ou Instagram) sobre veículos. Para CADA post da lista, na mesma ordem "
    "em que foram recebidos, extraia:\n\n"
    "- sentimento: \"positivo\", \"negativo\", \"neutro\" ou \"misto\".\n"
    "- motivo: uma frase curta explicando o porquê desse sentimento.\n"
    "- adjetivos: lista de adjetivos usados ou implícitos no post "
    "(em português, minúsculas).\n"
    "- objeto: sobre o que o post fala (ex.: \"motor\", \"design\", "
    "\"preço\", \"consumo\", \"revenda\", \"acabamento\").\n\n"
    "Responda em JSON no formato:\n"
    "{\"posts\": [{\"indice\": 0, \"sentimento\": \"...\", "
    "\"motivo\": \"...\", \"adjetivos\": [\"...\"], \"objeto\": \"...\"}, ...]}\n\n"
    "O campo \"indice\" é a posição (começando em 0) do post na lista "
    "recebida — é assim que a resposta é associada ao post original. "
    "Devolva exatamente um item por post recebido, na mesma ordem."
)

SCHEMA_PROMPT_SOCIAL: dict[str, Any] = {
    "posts": [
        {
            "indice": "number — posição do post na lista recebida, começando em 0",
            "sentimento": "string — positivo | negativo | neutro | misto",
            "motivo": "string curta",
            "adjetivos": ["string"],
            "objeto": "string — do que o post fala",
        }
    ]
}


def garantir_prompt() -> Prompt:
    """Localiza (ou cria, na primeira vez) o prompt fixo de análise de posts
    — mesmo esquema de `src.analytics.enrichment.garantir_prompt`, versionado
    como qualquer outro `Prompt` e visível no mesmo CRUD/UI de prompts."""
    existente = next(
        (p for p in listar_prompts() if p.name == NOME_PROMPT_SOCIAL), None
    )
    if existente:
        return existente

    logger.info("[social] criando prompt fixo de análise de posts em redes sociais.")
    return criar_prompt(PromptEntrada(
        name=NOME_PROMPT_SOCIAL,
        description=(
            "Prompt fixo do sistema — roda automaticamente sobre todo post "
            "coletado pela aba Redes Sociais. O texto pode ser "
            "ajustado; o formato de saída (schema) não deve mudar."
        ),
        prompt=TEXTO_PROMPT_SOCIAL,
        input_type=TipoEntrada.DATASET,
        output_format="json",
        output_schema=SCHEMA_PROMPT_SOCIAL,
    ))


async def enriquecer_posts(
    agente: Agente,
    posts: list[dict],
    *,
    prompt: Prompt | None = None,
) -> list[dict]:
    """
    Roda o prompt fixo sobre `posts` (uma chamada por lote) e devolve um item
    por post de entrada: `{post_id, texto, success, struct, error, usage,
    prompt_id, prompt_version}`.
    """
    if not posts:
        return []

    prompt = prompt or garantir_prompt()
    versao = prompt.na_versao(None)

    entrada = {
        "posts": [
            {"indice": i, "texto": p.get("texto", "")} for i, p in enumerate(posts)
        ]
    }

    saida = await agente.run(
        entrada,
        prompt=versao.prompt,
        output_schema=versao.output_schema,
        output_format=versao.output_format,
    )

    if not saida.success:
        erro = saida.error.__dict__ if saida.error else {"message": "erro desconhecido"}
        return [
            {
                "post_id": p.get("post_id"),
                "texto": p.get("texto"),
                "success": False,
                "struct": None,
                "error": erro,
                "usage": saida.usage,
                "prompt_id": prompt.id,
                "prompt_version": versao.version,
            }
            for p in posts
        ]

    por_indice: dict[int, dict] = {}
    for item in (saida.output or {}).get("posts") or []:
        try:
            por_indice[int(item.get("indice"))] = item
        except (TypeError, ValueError):
            continue

    resultados: list[dict] = []
    for i, p in enumerate(posts):
        bruto = por_indice.get(i)
        struct: dict | None = None
        erro: dict | None = None

        if bruto is None:
            erro = {"type": "MissingOutput", "message": f"LLM não devolveu item para o índice {i}."}
        else:
            try:
                struct = AnaliseSocialStruct(
                    sentimento=bruto.get("sentimento"),
                    motivo=bruto.get("motivo"),
                    adjetivos=bruto.get("adjetivos"),
                    objeto=bruto.get("objeto"),
                ).model_dump()
            except ValidationError as exc:
                erro = {"type": "OutputParseError", "message": str(exc)}

        resultados.append({
            "post_id": p.get("post_id"),
            "texto": p.get("texto"),
            "success": struct is not None,
            "struct": struct,
            "error": erro,
            "usage": saida.usage,
            "prompt_id": prompt.id,
            "prompt_version": versao.version,
        })

    return resultados
