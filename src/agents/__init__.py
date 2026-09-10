"""
src.agents — Camada de inteligência (LLM), desacoplada do fornecedor.

    from src.agents import Agente

    agente = Agente(provider="groq", model="openai/gpt-oss-20b")
    resultado = await agente.run(dados, prompt=prompt, output_schema=schema)

`resultado` é sempre um AgentResult — sucesso ou falha, mesmo contrato.
"""
from src.agents.agent import Agente
from src.agents.base import (
    AgentError,
    AgentResult,
    LLMProvider,
    ProviderError,
    ProviderResponse,
)
from src.agents.providers import listar_providers, obter_provider

__all__ = [
    "Agente",
    "AgentResult",
    "AgentError",
    "LLMProvider",
    "ProviderError",
    "ProviderResponse",
    "obter_provider",
    "listar_providers",
]
