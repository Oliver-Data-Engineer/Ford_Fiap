"""
groq.py
Adapter do Groq.

O Groq expõe a API OpenAI-compatível em https://api.groq.com/openai/v1 —
o mesmo endpoint que o snippet de referência da spec usa via SDK OpenAI:

    client = OpenAI(api_key=..., base_url="https://api.groq.com/openai/v1")

Toda a especificidade do Groq fica confinada a este arquivo.
"""
from src.agents.providers.openai_compat import OpenAICompatProvider


class GroqProvider(OpenAICompatProvider):
    nome = "groq"
    base_url_padrao = "https://api.groq.com/openai/v1"
    modelo_padrao = "openai/gpt-oss-20b"
