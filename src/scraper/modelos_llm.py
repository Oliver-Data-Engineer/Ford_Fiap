"""
modelos_llm.py
Lista curada, mantida à mão, de modelos conhecidos por provider — só pra
alimentar o dropdown de "modelo" no cadastro de site (`GET /scraper/config`).

Fica de propósito FORA de `src/agents/providers/` — aquele módulo existe
pra ter "um modelo padrão por provider, sem catálogo" (Regra 9 do SPEC:
provider é substituível); generalizar ali acoplaria um domínio (Scraper) no
genérico à toa.

Esta lista VAI ficar desatualizada conforme novos modelos saem — por isso
o dropdown sempre tem uma opção "Custom/outro..." que revela um campo de
texto livre (`app/static/scraper/script.js`), pra nunca travar o usuário
num modelo que não esteja aqui.
"""
from __future__ import annotations

MODELOS_CURADOS: dict[str, list[str]] = {
    "groq": [
        "openai/gpt-oss-20b",
        "openai/gpt-oss-120b",
        "llama-3.3-70b-versatile",
        "moonshotai/kimi-k2-instruct",
    ],
    "openai": [
        "gpt-4o-mini",
        "gpt-4o",
        "gpt-5-mini",
    ],
    "anthropic": [
        "claude-sonnet-5",
        "claude-opus-5",
        "claude-haiku-4-5-20251001",
    ],
    "gemini": [
        "gemini-3.5-flash-lite",
        "gemini-2.5-flash",
        "gemini-2.5-pro",
    ],
    "ollama": [],  # local — sem catálogo fixo, só texto livre (modelo já instalado do usuário)
}
