"""
client.py
Cliente HTTP das APIs usadas pelo catálogo FIPE — as mesmas fontes do
notebook `tabela_marcas.ipynb`:
    parallelum.com.br   marcas, modelos, anos, código FIPE (com token)
    brasilapi.com.br    meses de referência (sem token)

Fala HTTP direto via httpx (async), mesmo padrão de `src/youtube/client.py`.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

BASE_PARALLELUM = "https://parallelum.com.br/fipe/api/v1"
BASE_BRASILAPI = "https://brasilapi.com.br/api/fipe"

TIMEOUT_PADRAO = 20.0


class FipeAPIError(RuntimeError):
    """Falha ao consultar a API da FIPE (parallelum) ou a BrasilAPI."""

    def __init__(self, mensagem: str, status: int | None = None, motivo: str | None = None):
        super().__init__(mensagem)
        self.status = status
        self.motivo = motivo


class FipeAPI:
    """
    Cliente das APIs do catálogo FIPE.

    Marcas/modelos/anos não exigem credencial. Só a resolução do código
    FIPE de um `(modelo, ano)` exige `FIPE_API_TOKEN`
    (header `X-Subscription-Token`, igual o notebook já faz) — sem ele,
    essa chamada específica falha com `motivo="missing_token"`; as demais
    (marcas, modelos, anos, meses de referência) funcionam normalmente.
    """

    def __init__(self, token: str | None = None, timeout: float = TIMEOUT_PADRAO):
        self._token = token or settings.fipe_api_token
        self._timeout = timeout

    @property
    def token_configurado(self) -> bool:
        return bool(self._token)

    async def _get(self, url: str, *, com_token: bool = False) -> Any:
        headers = {"User-Agent": "Mozilla/5.0"}
        if com_token:
            if not self._token:
                raise FipeAPIError(
                    "FIPE_API_TOKEN não configurado no ambiente.",
                    motivo="missing_token",
                )
            headers["X-Subscription-Token"] = self._token

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resposta = await client.get(url, headers=headers)
        except httpx.TimeoutException as exc:
            raise FipeAPIError(f"Tempo limite excedido ao consultar '{url}'.", motivo="timeout") from exc
        except httpx.HTTPError as exc:
            raise FipeAPIError(f"Falha de rede ao consultar '{url}': {exc}", motivo="network") from exc

        if resposta.status_code >= 400:
            raise FipeAPIError(
                f"API respondeu {resposta.status_code} em '{url}': {resposta.text[:200]}",
                status=resposta.status_code,
                motivo="http_error",
            )

        return resposta.json()

    # ── parallelum.com.br ────────────────────────────────────────────────────

    async def buscar_marcas(self, tipo_veiculo: str) -> list[dict]:
        """`[{"nome": "Ford", "codigo": "22"}, ...]`"""
        return await self._get(f"{BASE_PARALLELUM}/{tipo_veiculo}/marcas")

    async def buscar_modelos(self, tipo_veiculo: str, marca_codigo: str) -> list[dict]:
        """Devolve só `modelos` do envelope `{"modelos": [...], "anos": [...]}`."""
        envelope = await self._get(f"{BASE_PARALLELUM}/{tipo_veiculo}/marcas/{marca_codigo}/modelos")
        return envelope.get("modelos", []) if isinstance(envelope, dict) else []

    async def buscar_anos(self, tipo_veiculo: str, marca_codigo: str, modelo_codigo: str) -> list[dict]:
        """`[{"nome": "2026 Gasolina", "codigo": "2026-1"}, ...]`"""
        return await self._get(
            f"{BASE_PARALLELUM}/{tipo_veiculo}/marcas/{marca_codigo}/modelos/{modelo_codigo}/anos"
        )

    async def buscar_codigo_fipe(
        self, tipo_veiculo: str, marca_codigo: str, modelo_codigo: str, ano_codigo: str
    ) -> dict:
        """
        Envelope completo (`Valor`, `Marca`, `Modelo`, `CodigoFipe`, ...) —
        só `CodigoFipe` é gravado hoje (`src/govmode/engine.py`), mas o
        resto vem disponível se precisar no futuro (ex.: preço).
        """
        return await self._get(
            f"{BASE_PARALLELUM}/{tipo_veiculo}/marcas/{marca_codigo}/modelos/{modelo_codigo}/anos/{ano_codigo}",
            com_token=True,
        )

    # ── brasilapi.com.br ─────────────────────────────────────────────────────

    async def buscar_meses_referencia(self) -> list[dict]:
        """`[{"codigo": 332, "mes": "abril/2026"}, ...]` — sem token."""
        return await self._get(f"{BASE_BRASILAPI}/tabelas/v1")
