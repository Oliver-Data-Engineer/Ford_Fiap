"""
prompts_api.py
Router FastAPI de prompts.

Um prompt é configuração reutilizável e versionada (Regras 8, 12 e 13 da spec):
nenhuma regra de análise fica embutida no código da aplicação.

Endpoints:
    GET    /prompts
    POST   /prompts
    GET    /prompts/{id}
    PUT    /prompts/{id}
    DELETE /prompts/{id}
    GET    /prompts/{id}/versions
    POST   /prompts/{id}/test
"""
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.agents import Agente
from src.pipelines.models import ConfigLLM, PromptEntrada
from src.pipelines.store import (
    atualizar_prompt,
    criar_prompt,
    listar_prompts,
    obter_prompt,
    remover_prompt,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/prompts", tags=["prompts"])


class TestePromptRequest(BaseModel):
    """Execução avulsa de um prompt sobre um texto qualquer, sem pipeline."""
    texto: str
    llm: ConfigLLM = Field(default_factory=ConfigLLM)
    version: int | None = None


@router.get("")
async def listar():
    """Todos os prompts salvos, do mais recente para o mais antigo."""
    return {"prompts": [p.model_dump(mode="json") for p in listar_prompts()]}


@router.post("", status_code=201)
async def criar(body: PromptEntrada):
    if not body.name.strip():
        raise HTTPException(400, detail={"erro": "O campo 'name' é obrigatório."})
    if not body.prompt.strip():
        raise HTTPException(400, detail={"erro": "O campo 'prompt' é obrigatório."})

    prompt = criar_prompt(body)
    return prompt.model_dump(mode="json")


@router.get("/{prompt_id}")
async def obter(prompt_id: str):
    prompt = obter_prompt(prompt_id)
    if prompt is None:
        raise HTTPException(404, detail={"erro": f"Prompt '{prompt_id}' não encontrado."})
    return prompt.model_dump(mode="json")


@router.put("/{prompt_id}")
async def atualizar(prompt_id: str, body: PromptEntrada):
    """
    Atualiza o prompt.

    Mudar o texto/schema cria uma versão nova; mudar só nome ou descrição não.
    Execuções antigas continuam apontando para a versão que de fato rodou.
    """
    if not body.prompt.strip():
        raise HTTPException(400, detail={"erro": "O campo 'prompt' é obrigatório."})

    prompt = atualizar_prompt(prompt_id, body)
    if prompt is None:
        raise HTTPException(404, detail={"erro": f"Prompt '{prompt_id}' não encontrado."})
    return prompt.model_dump(mode="json")


@router.delete("/{prompt_id}")
async def remover(prompt_id: str):
    if not remover_prompt(prompt_id):
        raise HTTPException(404, detail={"erro": f"Prompt '{prompt_id}' não encontrado."})
    return {"removido": prompt_id}


@router.get("/{prompt_id}/versions")
async def listar_versoes(prompt_id: str):
    prompt = obter_prompt(prompt_id)
    if prompt is None:
        raise HTTPException(404, detail={"erro": f"Prompt '{prompt_id}' não encontrado."})

    return {
        "prompt_id": prompt.id,
        "version": prompt.version,
        "versions": [v.model_dump(mode="json") for v in prompt.versions],
    }


@router.post("/{prompt_id}/test")
async def testar(prompt_id: str, body: TestePromptRequest):
    """
    Roda o prompt sobre um texto avulso — o ciclo curto de ajuste do
    Playground ("ajustar prompt → executar de novo") sem recoletar dados.
    """
    prompt = obter_prompt(prompt_id)
    if prompt is None:
        raise HTTPException(404, detail={"erro": f"Prompt '{prompt_id}' não encontrado."})

    try:
        versao = prompt.na_versao(body.version)
    except ValueError as exc:
        raise HTTPException(404, detail={"erro": str(exc)}) from exc

    agente = Agente(
        provider=body.llm.provider,
        model=body.llm.model,
        temperature=body.llm.temperature,
        max_tokens=body.llm.max_tokens,
    )

    resultado = await agente.run(
        body.texto,
        prompt=versao.prompt,
        output_schema=versao.output_schema,
        output_format=versao.output_format,
    )

    return {
        "prompt_id": prompt.id,
        "prompt_version": versao.version,
        **resultado.to_dict(),
    }
