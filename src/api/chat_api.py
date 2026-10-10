"""
chat_api.py
Chatbot simples — responde perguntas com base SOMENTE nos dados que a
página atual já carregou/filtrou (nunca conhecimento geral). Reaproveita
a mesma camada de LLM dos pipelines (`src/agents/agent.py::Agente`) e o
mesmo par global `LLM_PROVIDER`/`LLM_MODEL` do `.env` usado por eles —
não introduz nenhuma configuração nova.

Endpoint:
    POST /chat/perguntar

Stateless: nenhuma conversa é persistida no servidor — o histórico vem do
próprio frontend a cada chamada (ver `app/static/shared/chat_widget.js`).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.agents.agent import Agente
from src.config import settings

router = APIRouter(prefix="/chat", tags=["chat"])

# Limite de trocas de histórico enviadas ao modelo — evita que uma conversa
# longa faça o prompt crescer sem fim (mesmo espírito de MAX_ATRIBUTO/
# MAX_VALOR em src/utils/normalizador.py: truncar em vez de deixar ilimitado).
MAX_MENSAGENS_HISTORICO = 6

SYSTEM_PROMPT_CHAT = (
    "Você é o assistente do Painel do Veículo. Responda sempre em português, "
    "de forma curta e direta. Baseie a resposta EXCLUSIVAMENTE nos DADOS DE "
    "ENTRADA fornecidos (o que está carregado/filtrado nesta página agora) — "
    "nunca em conhecimento geral, mesmo que soubesse a resposta de outra "
    "forma. Se a resposta não puder ser encontrada nos dados fornecidos, "
    "diga claramente que essa informação não está disponível nesta página; "
    "nunca invente um valor. Se os dados de entrada vierem vazios ou nulos, "
    "diga que ainda não há nada carregado nesta página pra responder."
)


class MensagemHistorico(BaseModel):
    papel: str  # "usuario" | "assistente"
    texto: str


class ChatRequest(BaseModel):
    pergunta: str
    pagina: str | None = None
    contexto: object = None  # snapshot livre — cada página decide o formato
    historico: list[MensagemHistorico] | None = None


@router.post("/perguntar")
async def perguntar(body: ChatRequest):
    """
    Resposta 200: {"resposta": str}
    Resposta 400: {"erro": "..."}   — pergunta vazia
    Resposta 503: {"erro": "..."}   — nenhum provider de LLM configurado no .env
    Resposta 502: {"erro": "...", "retryable": bool} — falha ao consultar o LLM
    """
    if not body.pergunta.strip():
        raise HTTPException(status_code=400, detail={"erro": "Pergunta vazia."})

    provider = settings.llm_provider_padrao
    if not settings.provider_configurado(provider):
        raise HTTPException(
            status_code=503,
            detail={
                "erro": f"Chat não configurado — nenhuma credencial disponível "
                        f"para o provider '{provider}' (LLM_PROVIDER no .env)."
            },
        )

    linhas_prompt = [
        f"{msg.papel}: {msg.texto}"
        for msg in (body.historico or [])[-MAX_MENSAGENS_HISTORICO:]
    ]
    linhas_prompt.append(f"Pergunta atual do usuário: {body.pergunta.strip()}")

    agente = Agente(temperature=0.2, max_tokens=500)  # provider/model = default do .env
    resultado = await agente.run(
        {"pagina": body.pagina, "dados": body.contexto},
        prompt="\n".join(linhas_prompt),
        system_prompt=SYSTEM_PROMPT_CHAT,
        output_format="text",
    )

    if not resultado.success:
        raise HTTPException(
            status_code=502,
            detail={
                "erro": f"Falha ao consultar o assistente: {resultado.error.message}",
                "retryable": resultado.error.retryable,
            },
        )

    return {"resposta": resultado.output}
