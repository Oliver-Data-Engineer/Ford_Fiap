"""
openai_compat.py
Adapter base para qualquer serviço que exponha a API /chat/completions
no formato OpenAI — que hoje inclui a própria OpenAI, Groq e Ollama.

A comunicação usa httpx diretamente (mesma dependência do resto do backend)
contra o endpoint REST OpenAI-compatível; o SDK `openai` faz exatamente a
mesma requisição.  Isso mantém o projeto sem dependência adicional e, mais
importante, mantém a chamada HTTP inteiramente dentro do adapter — nenhum
`OpenAI(...)` espalhado pelo projeto (Regra 17 da spec).
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from src.agents.base import LLMProvider, ProviderError, ProviderResponse

logger = logging.getLogger(__name__)


class OpenAICompatProvider(LLMProvider):
    """Base compartilhada — subclasses só definem nome, URL e modelo padrão."""

    nome = "openai-compat"
    base_url_padrao = "https://api.openai.com/v1"
    exige_api_key = True

    def _headers(self) -> dict[str, str]:
        cabecalhos = {"Content-Type": "application/json"}
        if self.api_key:
            cabecalhos["Authorization"] = f"Bearer {self.api_key}"
        return cabecalhos

    def _url(self) -> str:
        base = (self.base_url or self.base_url_padrao).rstrip("/")
        return f"{base}/chat/completions"

    def _payload(
        self,
        *,
        system_prompt: str | None,
        user_prompt: str,
        temperature: float,
        max_tokens: int | None,
        json_mode: bool,
    ) -> dict[str, Any]:
        mensagens: list[dict[str, str]] = []
        if system_prompt:
            mensagens.append({"role": "system", "content": system_prompt})
        mensagens.append({"role": "user", "content": user_prompt})

        corpo: dict[str, Any] = {
            "model": self.model,
            "messages": mensagens,
            "temperature": temperature,
        }
        if max_tokens:
            corpo["max_tokens"] = max_tokens
        if json_mode:
            corpo["response_format"] = {"type": "json_object"}

        # Parâmetros extras passados no construtor (top_p, seed, …)
        corpo.update(self.opcoes.get("extra_body", {}))
        return corpo

    async def complete(
        self,
        *,
        system_prompt: str | None,
        user_prompt: str,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> ProviderResponse:
        if self.exige_api_key and not self.api_key:
            raise ProviderError(
                f"Credencial ausente para o provider '{self.nome}'.",
                tipo="AuthenticationError",
            )

        corpo = self._payload(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
        )

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resposta = await client.post(self._url(), headers=self._headers(), json=corpo)
        except httpx.TimeoutException as exc:
            raise ProviderError(
                f"Tempo limite excedido ao chamar '{self.nome}'.",
                tipo="TimeoutError",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"Falha de rede ao chamar '{self.nome}': {exc}",
                tipo="NetworkError",
                retryable=True,
            ) from exc

        if resposta.status_code >= 400:
            raise self._traduzir_erro(resposta)

        return self._ler_resposta(resposta.json())

    # ── Tradução de erros / leitura ──────────────────────────────────────────

    def _traduzir_erro(self, resposta: httpx.Response) -> ProviderError:
        try:
            mensagem = resposta.json().get("error", {}).get("message", resposta.text[:300])
        except Exception:
            mensagem = resposta.text[:300]

        status = resposta.status_code
        if status in (401, 403):
            tipo, retry = "AuthenticationError", False
        elif status == 429:
            tipo, retry = "RateLimitError", True
        elif status >= 500:
            tipo, retry = "ServerError", True
        else:
            tipo, retry = "InvalidRequestError", False

        return ProviderError(f"{self.nome} respondeu {status}: {mensagem}", tipo=tipo, retryable=retry)

    def _ler_resposta(self, dados: dict) -> ProviderResponse:
        try:
            texto = dados["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                f"Resposta inesperada de '{self.nome}': {str(dados)[:300]}",
                tipo="MalformedResponseError",
            ) from exc

        uso = dados.get("usage") or {}
        return ProviderResponse(
            text=texto,
            usage={
                "prompt_tokens": uso.get("prompt_tokens"),
                "completion_tokens": uso.get("completion_tokens"),
                "total_tokens": uso.get("total_tokens"),
            },
        )
