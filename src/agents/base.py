"""
base.py
Contratos da camada de agentes.

Define o que todo provider de LLM precisa oferecer (`LLMProvider`) e o
formato único de resposta que o restante da aplicação consome
(`AgentResult`).  Nada aqui sabe qual provider está em uso.
"""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# Resultado padronizado (Regra 15 da spec)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AgentError:
    type: str
    message: str
    retryable: bool = False


@dataclass
class AgentResult:
    """
    Contrato de saída de qualquer execução de agente — sucesso ou falha.

    O consumidor nunca precisa de try/except: checa `success` e segue.
    """
    success: bool
    provider: str
    model: str
    output: Any = None
    raw_response: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    error: AgentError | None = None

    def to_dict(self) -> dict:
        dados = asdict(self)
        if self.error is None:
            dados["error"] = None
        return dados

    @classmethod
    def falha(
        cls,
        provider: str,
        model: str,
        tipo: str,
        mensagem: str,
        *,
        retryable: bool = False,
        raw_response: str | None = None,
    ) -> AgentResult:
        return cls(
            success=False,
            provider=provider,
            model=model,
            output=None,
            raw_response=raw_response,
            usage={},
            error=AgentError(type=tipo, message=mensagem, retryable=retryable),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Resposta crua de um provider — o que o adapter devolve ao Agente
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ProviderResponse:
    text: str
    usage: dict[str, Any] = field(default_factory=dict)


class ProviderError(RuntimeError):
    """Falha na comunicação com o provider de LLM."""

    def __init__(self, mensagem: str, tipo: str = "ProviderError", retryable: bool = False):
        super().__init__(mensagem)
        self.tipo = tipo
        self.retryable = retryable


# ─────────────────────────────────────────────────────────────────────────────
# Contrato de provider
# ─────────────────────────────────────────────────────────────────────────────

class LLMProvider(ABC):
    """
    Adapter de um fornecedor de LLM.

    Implementar um provider novo é implementar `complete()` — e nada mais
    do projeto precisa mudar (Regra 9: provider é substituível).
    """

    #: identificador usado em `Agente(provider="...")`
    nome: str = "abstract"

    #: modelo assumido quando o chamador não informa um
    modelo_padrao: str = ""

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 120.0,
        **kwargs: Any,
    ):
        self.model = model or self.modelo_padrao
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.opcoes = kwargs

    @abstractmethod
    async def complete(
        self,
        *,
        system_prompt: str | None,
        user_prompt: str,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> ProviderResponse:
        """Envia o prompt ao modelo e devolve o texto gerado + uso de tokens."""

    def __repr__(self) -> str:  # pragma: no cover - conveniência de debug
        return f"<{type(self).__name__} model={self.model!r}>"


# ─────────────────────────────────────────────────────────────────────────────
# Parsing de saída estruturada
# ─────────────────────────────────────────────────────────────────────────────

# LLMs frequentemente embrulham o JSON em ```json … ```
_CERCA_MARKDOWN = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def extrair_json(texto: str) -> Any:
    """
    Tenta interpretar a resposta do modelo como JSON.

    Estratégia em três tentativas, da mais estrita à mais tolerante:
      1. parse direto;
      2. conteúdo de um bloco cercado por ``` … ```;
      3. maior trecho entre o primeiro '{' ou '[' e seu fechamento.

    Levanta ValueError se nada for interpretável.
    """
    bruto = (texto or "").strip()
    if not bruto:
        raise ValueError("Resposta vazia do modelo.")

    try:
        return json.loads(bruto)
    except json.JSONDecodeError:
        pass

    cercado = _CERCA_MARKDOWN.search(bruto)
    if cercado:
        try:
            return json.loads(cercado.group(1))
        except json.JSONDecodeError:
            pass

    for abre, fecha in (("{", "}"), ("[", "]")):
        inicio = bruto.find(abre)
        fim = bruto.rfind(fecha)
        if inicio != -1 and fim > inicio:
            try:
                return json.loads(bruto[inicio:fim + 1])
            except json.JSONDecodeError:
                continue

    raise ValueError("A resposta do modelo não contém JSON interpretável.")
