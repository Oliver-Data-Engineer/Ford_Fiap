"""
enrichment.py
Enriquecimento automático de comentários — struct único e fixo
`{sentimento, motivo, adjetivos, objeto}`, sem "modos" selecionáveis: a
pipeline que executa isso é automática (roda junto da extração de vídeo/
comentários já existente no Playground), o usuário só controla o tamanho da
amostra no teste manual (ver `src/api/youtube_api.py`).

Reaproveita `Agente.run()` (`src/agents/agent.py`) — a mesma infraestrutura
de chamada à LLM usada pelos passos 'agent' normais da pipeline (Regra 7 do
SPEC: só o engine chama o LLM). A diferença é que aqui a saída É validada
contra `AnaliseComentarioStruct` — no resto do app, `output_schema` é só
texto sugestivo para o prompt, nunca verificado.
"""
from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from src.agents import Agente
from src.analytics.models import AnaliseComentarioStruct
from src.pipelines.models import Prompt, PromptEntrada, TipoEntrada
from src.pipelines.store import criar_prompt, listar_prompts

logger = logging.getLogger(__name__)

NOME_PROMPT_ENRIQUECIMENTO = "Enriquecimento de comentários (fixo)"

TEXTO_PROMPT_ENRIQUECIMENTO = (
    "Você recebe uma lista de comentários de vídeos do YouTube sobre um "
    "veículo. Para CADA comentário da lista, na mesma ordem em que foram "
    "recebidos, extraia:\n\n"
    "- sentimento: \"positivo\", \"negativo\", \"neutro\" ou \"misto\".\n"
    "- motivo: uma frase curta explicando o porquê desse sentimento.\n"
    "- adjetivos: lista de adjetivos usados ou implícitos no comentário "
    "(em português, minúsculas).\n"
    "- objeto: sobre o que o comentário fala (ex.: \"motor\", \"design\", "
    "\"preço\", \"consumo\", \"atendimento\", \"acabamento\").\n\n"
    "Responda em JSON no formato:\n"
    "{\"comentarios\": [{\"indice\": 0, \"sentimento\": \"...\", "
    "\"motivo\": \"...\", \"adjetivos\": [\"...\"], \"objeto\": \"...\"}, ...]}\n\n"
    "O campo \"indice\" é a posição (começando em 0) do comentário na lista "
    "recebida — é assim que a resposta é associada ao comentário original. "
    "Devolva exatamente um item por comentário recebido, na mesma ordem."
)

SCHEMA_PROMPT_ENRIQUECIMENTO: dict[str, Any] = {
    "comentarios": [
        {
            "indice": "number — posição do comentário na lista recebida, começando em 0",
            "sentimento": "string — positivo | negativo | neutro | misto",
            "motivo": "string curta",
            "adjetivos": ["string"],
            "objeto": "string — do que o comentário fala",
        }
    ]
}


def garantir_prompt() -> Prompt:
    """
    Localiza (ou cria, na primeira vez) o único prompt fixo de
    enriquecimento. Não é seletável pelo usuário como um "modo", mas fica
    versionado como qualquer outro `Prompt` (Regra 8 do SPEC — prompt é
    configuração, não código) e reaproveita o mesmo CRUD/UI de prompts.
    """
    existente = next(
        (p for p in listar_prompts() if p.name == NOME_PROMPT_ENRIQUECIMENTO), None
    )
    if existente:
        return existente

    logger.info("[analytics] criando prompt fixo de enriquecimento de comentários.")
    return criar_prompt(PromptEntrada(
        name=NOME_PROMPT_ENRIQUECIMENTO,
        description=(
            "Prompt fixo do sistema — roda automaticamente para todo "
            "comentário coletado. O texto pode ser ajustado; o formato de "
            "saída (schema) não deve mudar."
        ),
        prompt=TEXTO_PROMPT_ENRIQUECIMENTO,
        input_type=TipoEntrada.COMMENTS,
        output_format="json",
        output_schema=SCHEMA_PROMPT_ENRIQUECIMENTO,
    ))


async def enriquecer_comentarios(
    agente: Agente,
    comentarios: list[dict],
    *,
    prompt: Prompt | None = None,
) -> list[dict]:
    """
    Roda o prompt fixo sobre `comentarios` (uma chamada por lote) e devolve
    um item por comentário de entrada:
    `{comment_id, refined_record_id, texto, success, struct, error, usage,
    prompt_id, prompt_version}`.

    `comentarios` — lista de dicts com pelo menos `texto`; `comment_id`/
    `refined_record_id` são opcionais (o endpoint de teste por amostra roda
    sobre comentários que ainda não passaram por nenhuma persistência
    formal — usa só o índice para correlacionar a resposta da LLM).
    """
    if not comentarios:
        return []

    prompt = prompt or garantir_prompt()
    versao = prompt.na_versao(None)

    entrada = {
        "comentarios": [
            {"indice": i, "texto": c.get("texto", "")} for i, c in enumerate(comentarios)
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
                "comment_id": c.get("comment_id"),
                "refined_record_id": c.get("refined_record_id"),
                "texto": c.get("texto"),
                "success": False,
                "struct": None,
                "error": erro,
                "usage": saida.usage,
                "prompt_id": prompt.id,
                "prompt_version": versao.version,
            }
            for c in comentarios
        ]

    por_indice: dict[int, dict] = {}
    for item in (saida.output or {}).get("comentarios") or []:
        try:
            por_indice[int(item.get("indice"))] = item
        except (TypeError, ValueError):
            continue

    resultados: list[dict] = []
    for i, c in enumerate(comentarios):
        bruto = por_indice.get(i)
        struct: dict | None = None
        erro: dict | None = None

        if bruto is None:
            erro = {"type": "MissingOutput", "message": f"LLM não devolveu item para o índice {i}."}
        else:
            try:
                struct = AnaliseComentarioStruct(
                    sentimento=bruto.get("sentimento"),
                    motivo=bruto.get("motivo"),
                    adjetivos=bruto.get("adjetivos"),
                    objeto=bruto.get("objeto"),
                ).model_dump()
            except ValidationError as exc:
                erro = {"type": "OutputParseError", "message": str(exc)}

        resultados.append({
            "comment_id": c.get("comment_id"),
            "refined_record_id": c.get("refined_record_id"),
            "texto": c.get("texto"),
            "success": struct is not None,
            "struct": struct,
            "error": erro,
            "usage": saida.usage,
            "prompt_id": prompt.id,
            "prompt_version": versao.version,
        })

    return resultados
