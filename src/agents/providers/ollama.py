"""
ollama.py
Adapter do Ollama (modelos locais).

O Ollama serve uma API OpenAI-compatível em /v1 e não exige credencial.
A base URL pode ser sobrescrita por OLLAMA_BASE_URL.
"""
from src.agents.providers.openai_compat import OpenAICompatProvider


class OllamaProvider(OpenAICompatProvider):
    nome = "ollama"
    base_url_padrao = "http://localhost:11434/v1"
    modelo_padrao = "llama3.1"
    exige_api_key = False
