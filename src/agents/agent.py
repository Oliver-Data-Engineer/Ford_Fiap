"""
agent.py
A classe `Agente` — contrato genérico para LLMs.

Quem usa o Agente não sabe (e não deve saber) qual provider está por trás:

    agente = Agente(provider="groq", model="openai/gpt-oss-20b")
    resultado = agente.run(input_data, prompt=prompt, output_schema=schema)

Trocar `provider="groq"` por `provider="anthropic"` é a única mudança
necessária para trocar de fornecedor (Regra 9 da spec).
"""
from __future__ import annotations

import json
import logging
from typing import Any

from src.agents.base import (
    AgentResult,
    LLMProvider,
    ProviderError,
    extrair_json,
)
from src.agents.providers import obter_provider
from src.config import settings

logger = logging.getLogger(__name__)

# Instrução de sistema usada quando o chamador não fornece uma.
SYSTEM_PROMPT_PADRAO = (
    "Você é um analista de conteúdo. Analise os dados fornecidos e responda "
    "exatamente o que for pedido, sem inventar informação que não esteja no texto."
)

# Acrescentada ao system prompt quando output_format == "json".
INSTRUCAO_JSON = (
    "Responda EXCLUSIVAMENTE com um documento JSON válido. "
    "Não escreva nenhum texto antes ou depois do JSON e não use blocos de código."
)


class Agente:
    """
    Agente de LLM desacoplado do fornecedor.

    Parâmetros:
        provider       nome registrado do adapter ("groq", "openai", …)
        model          id do modelo; None usa o padrão do provider
        api_key        credencial; None lê da variável de ambiente do provider
        base_url       sobrescreve o endpoint (útil para gateways/proxies)
        temperature    determinismo da geração
        system_prompt  instrução de sistema padrão das execuções
        output_schema  schema JSON esperado na saída (descritivo, vai no prompt)
        max_tokens     teto de tokens de saída
    """

    def __init__(
        self,
        provider: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        temperature: float = 0.0,
        system_prompt: str | None = None,
        output_schema: dict | None = None,
        max_tokens: int | None = None,
        timeout: float = 120.0,
        **kwargs: Any,
    ):
        self.provider_nome = (provider or settings.llm_provider_padrao).strip().lower()

        classe = obter_provider(self.provider_nome)

        # A credencial nunca vem do frontend: se não for injetada explicitamente
        # (testes, execuções especiais), sai do ambiente.
        chave = api_key or settings.llm_api_key(self.provider_nome)
        url_base = base_url or settings.llm_base_url(self.provider_nome)

        self._provider: LLMProvider = classe(
            model=model,
            api_key=chave,
            base_url=url_base,
            timeout=timeout,
            **kwargs,
        )

        self.temperature = temperature
        self.system_prompt = system_prompt
        self.output_schema = output_schema
        self.max_tokens = max_tokens

    # ── Identificação ────────────────────────────────────────────────────────

    @property
    def model(self) -> str:
        return self._provider.model

    @property
    def provider(self) -> str:
        return self.provider_nome

    def descrever(self) -> dict[str, str]:
        """Identificação do agente — vai no bloco de rastreabilidade do TRUST."""
        return {"provider": self.provider_nome, "model": self.model}

    # ── Execução ─────────────────────────────────────────────────────────────

    async def run(
        self,
        input_data: Any,
        *,
        prompt: str | None = None,
        output_schema: dict | None = None,
        output_format: str = "json",
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AgentResult:
        """
        Executa o agente sobre `input_data`.

        `prompt`, `output_schema` e `input_data` são componentes independentes:
        o mesmo Agente serve a prompts, entradas e schemas diferentes
        (Regra 18 da spec).

        Nunca levanta exceção — falhas voltam como `AgentResult(success=False)`
        com `error.type` e `error.retryable` preenchidos.
        """
        formato = (output_format or "json").lower()
        schema = output_schema if output_schema is not None else self.output_schema

        sistema = self._montar_system_prompt(
            system_prompt or self.system_prompt, formato, schema
        )
        usuario = self._montar_user_prompt(prompt, input_data)

        try:
            resposta = await self._provider.complete(
                system_prompt=sistema,
                user_prompt=usuario,
                temperature=self.temperature if temperature is None else temperature,
                max_tokens=max_tokens or self.max_tokens,
                json_mode=(formato == "json"),
            )
        except ProviderError as exc:
            logger.error(
                "[agente] %s/%s falhou: %s", self.provider_nome, self.model, exc
            )
            return AgentResult.falha(
                self.provider_nome, self.model, exc.tipo, str(exc), retryable=exc.retryable
            )
        except Exception as exc:  # rede/lib inesperada — nunca deve escapar
            logger.exception("[agente] erro inesperado em %s", self.provider_nome)
            return AgentResult.falha(
                self.provider_nome, self.model, "UnexpectedError", str(exc)
            )

        # ── Interpretação da saída ───────────────────────────────────────────
        if formato == "json":
            try:
                saida = extrair_json(resposta.text)
            except ValueError as exc:
                return AgentResult.falha(
                    self.provider_nome,
                    self.model,
                    "OutputParseError",
                    str(exc),
                    retryable=True,          # reexecutar costuma resolver
                    raw_response=resposta.text,
                )
        else:
            saida = resposta.text

        return AgentResult(
            success=True,
            provider=self.provider_nome,
            model=self.model,
            output=saida,
            raw_response=resposta.text,
            usage=resposta.usage,
            error=None,
        )

    # ── Montagem dos prompts ─────────────────────────────────────────────────

    def _montar_system_prompt(
        self, base: str | None, formato: str, schema: dict | None
    ) -> str:
        partes = [base or SYSTEM_PROMPT_PADRAO]

        if formato == "json":
            partes.append(INSTRUCAO_JSON)

        if schema:
            partes.append(
                "A resposta deve seguir este schema JSON:\n"
                + json.dumps(schema, ensure_ascii=False, indent=2)
            )

        return "\n\n".join(p for p in partes if p)

    @staticmethod
    def _montar_user_prompt(prompt: str | None, input_data: Any) -> str:
        """
        Junta a instrução do usuário com os dados de entrada.

        `input_data` string entra como texto puro; dict/list é serializado em
        JSON para que o modelo enxergue a estrutura dos campos.
        """
        if isinstance(input_data, str):
            dados = input_data
        else:
            dados = json.dumps(input_data, ensure_ascii=False, indent=2, default=str)

        if not prompt:
            return dados

        return f"{prompt}\n\n--- DADOS DE ENTRADA ---\n{dados}"
