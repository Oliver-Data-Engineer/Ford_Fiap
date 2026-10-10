"""
gemini.py
Adapter do Google Gemini (Generative Language API).

Formato próprio, traduzido inteiramente aqui:
  • a chave vai na query string (`?key=`), não em header;
  • o prompt de sistema é `systemInstruction`;
  • parâmetros de geração ficam sob `generationConfig`;
  • JSON estruturado é pedido via `responseMimeType: application/json`.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from src.agents.base import LLMProvider, ProviderError, ProviderResponse

logger = logging.getLogger(__name__)


class GeminiProvider(LLMProvider):
    nome = "gemini"
    base_url_padrao = "https://generativelanguage.googleapis.com/v1beta"
    modelo_padrao = "gemini-3.5-flash-lite"

    def _url(self) -> str:
        base = (self.base_url or self.base_url_padrao).rstrip("/")
        return f"{base}/models/{self.model}:generateContent"

    async def complete(
        self,
        *,
        system_prompt: str | None,
        user_prompt: str,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> ProviderResponse:
        if not self.api_key:
            raise ProviderError(
                "Credencial ausente para o provider 'gemini'.",
                tipo="AuthenticationError",
            )

        geracao: dict[str, Any] = {"temperature": temperature}
        if max_tokens:
            geracao["maxOutputTokens"] = max_tokens
        if json_mode:
            geracao["responseMimeType"] = "application/json"

        corpo: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": geracao,
        }
        if system_prompt:
            corpo["systemInstruction"] = {"parts": [{"text": system_prompt}]}
        corpo.update(self.opcoes.get("extra_body", {}))

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resposta = await client.post(
                    self._url(),
                    params={"key": self.api_key},
                    headers={"Content-Type": "application/json"},
                    json=corpo,
                )
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "Tempo limite excedido ao chamar 'gemini'.",
                tipo="TimeoutError",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"Falha de rede ao chamar 'gemini': {exc}",
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

        # A mensagem de erro do Google não repete a chave — mas a URL sim,
        # por isso ela nunca entra no texto do erro.
        return ProviderError(f"gemini respondeu {status}: {mensagem}", tipo=tipo, retryable=retry)

    def _ler_resposta(self, dados: dict) -> ProviderResponse:
        candidatos = dados.get("candidates") or []
        if not candidatos:
            motivo = (dados.get("promptFeedback") or {}).get("blockReason")
            raise ProviderError(
                f"Gemini não retornou candidatos"
                + (f" (bloqueio: {motivo})" if motivo else "")
                + ".",
                tipo="RefusalError" if motivo else "MalformedResponseError",
            )

        partes = (candidatos[0].get("content") or {}).get("parts") or []
        texto = "".join(p.get("text", "") for p in partes if isinstance(p, dict))

        if not texto:
            raise ProviderError(
                f"Resposta sem conteúdo de texto: {str(dados)[:300]}",
                tipo="MalformedResponseError",
            )

        uso = dados.get("usageMetadata") or {}
        return ProviderResponse(
            text=texto,
            usage={
                "prompt_tokens": uso.get("promptTokenCount"),
                "completion_tokens": uso.get("candidatesTokenCount"),
                "total_tokens": uso.get("totalTokenCount"),
            },
        )
