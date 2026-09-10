"""
openai.py
Adapter da OpenAI — o formato de referência de `openai_compat`.
"""
from src.agents.providers.openai_compat import OpenAICompatProvider


class OpenAIProvider(OpenAICompatProvider):
    nome = "openai"
    base_url_padrao = "https://api.openai.com/v1"
    modelo_padrao = "gpt-4o-mini"
