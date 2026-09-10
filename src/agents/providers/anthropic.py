"""
anthropic.py
Adapter da Anthropic (Claude).

A Messages API não é OpenAI-compatível, então este adapter traduz o contrato
interno para o formato próprio da Anthropic — e só ele conhece esse formato.

Diferenças relevantes tratadas aqui:
  • `system` é campo de topo, não uma mensagem com role "system";
  • `max_tokens` é obrigatório;
  • `temperature` foi removido nos modelos atuais (Opus 5, Sonnet 5, família 4.6+)
    e devolve HTTP 400 se enviado — por isso nunca é repassado;
  • não existe "JSON mode": a instrução de formato vai no próprio prompt;
  • a resposta é uma lista de blocos de conteúdo, e `stop_reason` pode ser
    "refusal" (recusa por política, com HTTP 200).
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from src.agents.base import LLMProvider, ProviderError, ProviderResponse

logger = logging.getLogger(__name__)

VERSAO_API = "2023-06-01"
MAX_TOKENS_PADRAO = 8192


class AnthropicProvider(LLMProvider):
    nome = "anthropic"
    base_url_padrao = "https://api.anthropic.com/v1"
    modelo_padrao = "claude-opus-5"

    def _url(self) -> str:
        base = (self.base_url or self.base_url_padrao).rstrip("/")
        return f"{base}/messages"

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
                "Credencial ausente para o provider 'anthropic'.",
                tipo="AuthenticationError",
            )

        sistema = system_prompt or ""
        if json_mode:
            # Sem JSON mode nativo — a exigência de formato entra na instrução.
            instrucao = "Responda exclusivamente com JSON válido, sem texto fora do JSON."
            sistema = f"{sistema}\n\n{instrucao}".strip()

        corpo: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens or MAX_TOKENS_PADRAO,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        if sistema:
            corpo["system"] = sistema
        # `temperature` é deliberadamente omitido — ver docstring do módulo.
        corpo.update(self.opcoes.get("extra_body", {}))

        cabecalhos = {
            "x-api-key": self.api_key,
            "anthropic-version": VERSAO_API,
            "content-type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resposta = await client.post(self._url(), headers=cabecalhos, json=corpo)
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "Tempo limite excedido ao chamar 'anthropic'.",
                tipo="TimeoutError",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"Falha de rede ao chamar 'anthropic': {exc}",
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

        return ProviderError(f"anthropic respondeu {status}: {mensagem}", tipo=tipo, retryable=retry)

    def _ler_resposta(self, dados: dict) -> ProviderResponse:
        # Recusa por política chega com HTTP 200 — precisa virar erro explícito
        if dados.get("stop_reason") == "refusal":
            detalhes = dados.get("stop_details") or {}
            raise ProviderError(
                "O modelo recusou a solicitação"
                + (f" (categoria: {detalhes.get('category')})" if detalhes.get("category") else "")
                + ".",
                tipo="RefusalError",
            )

        blocos = dados.get("content") or []
        texto = "".join(
            bloco.get("text", "")
            for bloco in blocos
            if isinstance(bloco, dict) and bloco.get("type") == "text"
        )

        if not texto:
            raise ProviderError(
                f"Resposta sem conteúdo de texto: {str(dados)[:300]}",
                tipo="MalformedResponseError",
            )

        uso = dados.get("usage") or {}
        entrada = uso.get("input_tokens")
        saida = uso.get("output_tokens")
        return ProviderResponse(
            text=texto,
            usage={
                "prompt_tokens": entrada,
                "completion_tokens": saida,
                "total_tokens": (entrada or 0) + (saida or 0) if entrada is not None else None,
            },
        )
