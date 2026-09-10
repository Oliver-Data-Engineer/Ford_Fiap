"""
src.agents.providers — Registro de adapters de LLM.

Adicionar um provider novo:
    1. criar o módulo com uma subclasse de LLMProvider;
    2. registrá-lo em REGISTRO abaixo;
    3. mapear a variável de ambiente da chave em src/config.py.

Nenhum outro ponto do projeto muda (Regra 9: provider é substituível).
"""
from src.agents.base import LLMProvider
from src.agents.providers.anthropic import AnthropicProvider
from src.agents.providers.gemini import GeminiProvider
from src.agents.providers.groq import GroqProvider
from src.agents.providers.ollama import OllamaProvider
from src.agents.providers.openai import OpenAIProvider

REGISTRO: dict[str, type[LLMProvider]] = {
    GroqProvider.nome:      GroqProvider,
    OpenAIProvider.nome:    OpenAIProvider,
    AnthropicProvider.nome: AnthropicProvider,
    GeminiProvider.nome:    GeminiProvider,
    OllamaProvider.nome:    OllamaProvider,
}


def obter_provider(nome: str) -> type[LLMProvider]:
    """Classe do adapter registrado sob `nome`."""
    classe = REGISTRO.get((nome or "").strip().lower())
    if classe is None:
        disponiveis = ", ".join(sorted(REGISTRO))
        raise ValueError(
            f"Provider '{nome}' desconhecido. Disponíveis: {disponiveis}."
        )
    return classe


def listar_providers() -> list[dict[str, str]]:
    """Providers registrados e seus modelos padrão — usado pela UI."""
    return [
        {"provider": nome, "modelo_padrao": classe.modelo_padrao}
        for nome, classe in sorted(REGISTRO.items())
    ]


__all__ = ["REGISTRO", "obter_provider", "listar_providers"]
