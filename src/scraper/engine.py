"""
engine.py
Motor de execução do Scraper Mode — mesmo padrão de dispatch de
`src/govmode/engine.py`, mas cada "passo" é um objetivo (ficha técnica /
anúncios / comentários) do site cadastrado, e cada objetivo agora é uma
CADEIA de etapas (`_executar_cadeia`) em vez de uma única página — fan-out
igual ao que `src/utils/CarrosWeb.py` já faz à mão pra um site só
(fabricante → modelo → versões → ficha).

Nunca chama o LLM (isso é só `discovery.py`) — usa o `MapaSeletor` já
salvo em `config.seletores` e `src/scraper/extractor.py` (determinístico).
Um objetivo falhando (fetch, seletor ruim, warehouse fora do ar) nunca
derruba os outros — mesma isolação por passo do `GovEngine.executar`. Uma
LINHA de uma etapa intermediária falhando (URL quebrada, 404, etc.) nunca
derruba o objetivo inteiro — só quando NENHUMA linha sobrevive numa etapa
é que o passo falha.
"""
from __future__ import annotations

import logging
from typing import Any

from google.cloud import bigquery

from src.pipelines.models import StatusRun, StatusStep, agora_iso
from src.scraper import browser, extractor, schemas, warehouse
from src.scraper.models import (
    ROTULOS_OBJETIVO,
    MapaSeletor,
    ScraperRun,
    SiteScraperVersao,
    StepResultScraper,
    TipoObjetivoScraper,
    TipoStepScraper,
)

logger = logging.getLogger(__name__)

# Ordem estável de execução — não estrutural (cada objetivo é independente),
# só pra régua de progresso ficar previsível.
ORDEM_OBJETIVOS: list[TipoObjetivoScraper] = [
    TipoObjetivoScraper.FICHA_TECNICA,
    TipoObjetivoScraper.ANUNCIOS,
    TipoObjetivoScraper.COMENTARIOS,
]

_EXTRATOR_FINAL = {
    TipoObjetivoScraper.FICHA_TECNICA: extractor.extrair_ficha_tecnica,
    TipoObjetivoScraper.ANUNCIOS: extractor.extrair_anuncios,
    TipoObjetivoScraper.COMENTARIOS: extractor.extrair_comentarios,
}


class _CadeiaFalhou(Exception):
    """Sinaliza falha TOTAL da cadeia de etapas de um objetivo (etapa 0
    não carregou, ou nenhuma linha sobreviveu numa etapa) — capturada por
    cada `_passo_*`, nunca escapa do motor."""

    def __init__(self, tipo: str, mensagem: str, retryable: bool = False):
        super().__init__(mensagem)
        self.tipo = tipo
        self.mensagem = mensagem
        self.retryable = retryable


def montar_steps(config: SiteScraperVersao) -> list[StepResultScraper]:
    """Régua de progresso com todas as etapas em `pending`."""
    objetivos = ScraperEngine.resolver_objetivos(config)
    return [
        StepResultScraper(
            type=TipoStepScraper(objetivo.value),
            name=ROTULOS_OBJETIVO.get(objetivo, objetivo.value),
        )
        for objetivo in objetivos
    ]


class ScraperEngine:
    @staticmethod
    def resolver_objetivos(config: SiteScraperVersao) -> list[TipoObjetivoScraper]:
        escolhidos = set(config.objetivos)
        return [o for o in ORDEM_OBJETIVOS if o in escolhidos]

    async def executar(
        self, config: SiteScraperVersao, run: ScraperRun, *, filtros: dict[str, str] | None = None,
    ) -> ScraperRun:
        """Nunca levanta exceção: falhas ficam registradas na etapa
        correspondente (mesmo contrato de `GovEngine.executar`). `filtros`
        (opcional) substitui os marcadores do `url_template` da primeira
        etapa de cada objetivo (ex.: `{"marca": "Fiat"}`) — permite
        consultar o site cadastrado por uma marca/modelo/ano específico em
        vez de sempre reusar a URL de amostra fixa."""
        objetivos = self.resolver_objetivos(config)
        if not run.steps:
            run.steps = montar_steps(config)
        run.status = StatusRun.EXECUTANDO

        try:
            warehouse.garantir_tabelas()
        except Exception as exc:
            logger.warning("[scraper] não foi possível garantir as tabelas antes da execução: %s", exc)

        contexto: dict[str, Any] = {
            "config": config, "site_id": run.site_id, "site_nome": run.site_name,
            "filtros": filtros, "progresso": {},
        }

        despacho = {
            TipoObjetivoScraper.FICHA_TECNICA: self._passo_ficha_tecnica,
            TipoObjetivoScraper.ANUNCIOS: self._passo_anuncios,
            TipoObjetivoScraper.COMENTARIOS: self._passo_comentarios,
        }

        houve_falha = False
        for objetivo, resultado in zip(objetivos, run.steps):
            resultado.iniciar()
            handler = despacho.get(objetivo)
            try:
                if handler is None:
                    resultado.pular(f"objetivo não suportado: {objetivo.value}")
                else:
                    await handler(contexto, resultado)
            except Exception as exc:
                logger.exception("[scraper] objetivo '%s' falhou", objetivo.value)
                resultado.falhar(type(exc).__name__, str(exc))

            if resultado.status == StatusStep.FALHOU:
                houve_falha = True

        run.progresso = contexto.get("progresso", {})
        run.finished_at = agora_iso()

        concluidas = [s for s in run.steps if s.status == StatusStep.OK]
        if houve_falha:
            run.status = StatusRun.PARCIAL if concluidas else StatusRun.FALHOU
        else:
            run.status = StatusRun.OK

        return run

    # ─────────────────────────────────────────────────────────────────────
    # Cadeia de etapas — comum aos 3 objetivos (fan-out genérico)
    # ─────────────────────────────────────────────────────────────────────

    async def _executar_cadeia(
        self, objetivo: TipoObjetivoScraper, mapa: MapaSeletor, contexto: dict,
    ) -> tuple[list[dict], str]:
        """Busca a etapa 0, e pra cada etapa seguinte busca UMA VEZ POR
        LINHA que a etapa anterior produziu (usando `campo_herdado` como
        URL) — o mesmo fan-out que `CarrosWeb.py` faz à mão. Devolve as
        linhas da ÚLTIMA etapa (as de verdade, no formato do objetivo) +
        um resumo textual por etapa. Levanta `_CadeiaFalhou` só quando a
        etapa 0 não carrega ou uma etapa inteira fica sem sobreviventes —
        uma linha isolada falhando é só descontada da contagem."""
        etapas = sorted(mapa.etapas, key=lambda e: e.ordem)
        if not etapas:
            raise _CadeiaFalhou("EmptyData", "Nenhuma etapa configurada.")

        extrator_final = _EXTRATOR_FINAL[objetivo]
        partes_resumo: list[str] = []

        url0 = etapas[0].url_amostra
        filtros = contexto.get("filtros")
        if filtros and etapas[0].url_template:
            try:
                url0 = etapas[0].url_template.format(**filtros)
            except (KeyError, IndexError):
                logger.warning(
                    "[scraper] filtros %s incompletos pro template '%s' — usando a URL de amostra.",
                    filtros, etapas[0].url_template,
                )

        html = await browser.buscar_html(url0)
        if html is None:
            raise _CadeiaFalhou("FetchError", f"Não foi possível carregar '{url0}'.", retryable=True)

        if len(etapas) == 1:
            linhas = extrator_final(html, etapas[0].selectors, url0)
            partes_resumo.append(f"{len(linhas)} linha(s) encontrada(s)")
            return linhas, "; ".join(partes_resumo)

        linhas_atual = extractor.extrair_linhas_genericas(html, etapas[0].selectors, url0)
        partes_resumo.append(f"etapa 1: {len(linhas_atual)} linha(s) encontrada(s)")

        for indice in range(1, len(etapas)):
            etapa = etapas[indice]
            campo_herdado = etapas[indice - 1].campo_herdado
            eh_ultima = indice == len(etapas) - 1

            linhas_seguintes: list[dict] = []
            ok, falhou = 0, 0

            for linha in linhas_atual:
                url_proxima = linha.get(campo_herdado) if campo_herdado else None
                if not url_proxima:
                    falhou += 1
                    continue

                html_proxima = await browser.buscar_html(url_proxima)
                if html_proxima is None:
                    falhou += 1
                    continue

                try:
                    if eh_ultima:
                        extraidas = extrator_final(html_proxima, etapa.selectors, url_proxima)
                    else:
                        extraidas = extractor.extrair_linhas_genericas(html_proxima, etapa.selectors, url_proxima)
                except Exception as exc:
                    logger.debug("[scraper] linha da etapa %d ignorada (%s): %s", indice + 1, url_proxima, exc)
                    falhou += 1
                    continue

                linhas_seguintes.extend(extraidas)
                ok += 1

            resumo_etapa = f"etapa {indice + 1}: {ok}/{ok + falhou} concluída(s)"
            if falhou:
                resumo_etapa += f", {falhou} falharam"
            partes_resumo.append(resumo_etapa)

            if not linhas_seguintes:
                raise _CadeiaFalhou("EmptyData", f"etapa {indice + 1}: nenhuma linha sobreviveu.")

            linhas_atual = linhas_seguintes

        return linhas_atual, "; ".join(partes_resumo)

    def _chaves_existentes(self, tabela: str, campos: list[str], site_id: str) -> set[tuple]:
        colunas = ", ".join(campos)
        sql = f"SELECT {colunas} FROM `{warehouse.table_id(tabela)}` WHERE site_id = @site"
        params = [bigquery.ScalarQueryParameter("site", "STRING", site_id)]
        try:
            df = warehouse.query(sql, params)
        except Exception:
            return set()  # tabela pode não existir ainda na primeira execução
        if df.empty:
            return set()
        df = df.where(df.notnull(), None)
        return {tuple(registro[c] for c in campos) for registro in df.to_dict(orient="records")}

    # ─────────────────────────────────────────────────────────────────────
    # Passo — ficha técnica (append puro, sem dedupe)
    # ─────────────────────────────────────────────────────────────────────

    async def _passo_ficha_tecnica(self, contexto: dict, resultado: StepResultScraper) -> None:
        config: SiteScraperVersao = contexto["config"]
        mapa = config.seletores.get(TipoObjetivoScraper.FICHA_TECNICA.value)
        if mapa is None or not mapa.etapas:
            resultado.pular("nenhuma etapa mapeada para ficha técnica")
            return

        try:
            linhas, resumo_cadeia = await self._executar_cadeia(TipoObjetivoScraper.FICHA_TECNICA, mapa, contexto)
        except _CadeiaFalhou as exc:
            resultado.falhar(exc.tipo, exc.mensagem, retryable=exc.retryable)
            return

        if not linhas:
            resultado.falhar("EmptyData", "Nenhum atributo foi extraído da página.")
            return

        agora = agora_iso()
        for linha in linhas:
            linha["site_id"] = contexto["site_id"]
            linha["site_nome"] = contexto["site_nome"]
            linha["coletado_em"] = agora

        try:
            warehouse.upsert(schemas.FICHA_TECNICA, linhas)
        except Exception as exc:
            resultado.falhar("WarehouseError", str(exc))
            return

        contexto["progresso"]["ficha_tecnica"] = {"linhas_gravadas": len(linhas)}
        resultado.concluir(f"{resumo_cadeia}; {len(linhas)} atributo(s) gravado(s)")

    # ─────────────────────────────────────────────────────────────────────
    # Passo — anúncios (dedupe por anti-join)
    # ─────────────────────────────────────────────────────────────────────

    async def _passo_anuncios(self, contexto: dict, resultado: StepResultScraper) -> None:
        config: SiteScraperVersao = contexto["config"]
        mapa = config.seletores.get(TipoObjetivoScraper.ANUNCIOS.value)
        if mapa is None or not mapa.etapas:
            resultado.pular("nenhuma etapa mapeada para anúncios")
            return

        try:
            linhas, resumo_cadeia = await self._executar_cadeia(TipoObjetivoScraper.ANUNCIOS, mapa, contexto)
        except _CadeiaFalhou as exc:
            resultado.falhar(exc.tipo, exc.mensagem, retryable=exc.retryable)
            return

        if not linhas:
            resultado.falhar("EmptyData", "Nenhum anúncio foi extraído da página.")
            return

        campos_chave = ["marca", "modelo", "versao", "ano", "url_anuncio"]
        existentes = self._chaves_existentes(schemas.ANUNCIOS, campos_chave, contexto["site_id"])

        agora = agora_iso()
        novas = []
        for linha in linhas:
            chave = tuple(linha.get(c) for c in campos_chave)
            if chave in existentes:
                continue
            linha["site_id"] = contexto["site_id"]
            linha["site_nome"] = contexto["site_nome"]
            linha["coletado_em"] = agora
            novas.append(linha)
            existentes.add(chave)

        contexto["progresso"]["anuncios"] = {"encontrados": len(linhas), "novos": len(novas)}

        if not novas:
            resultado.concluir(f"{resumo_cadeia}; 0 anúncio(s) novo(s) (todos já coletados antes)")
            return

        try:
            warehouse.upsert(schemas.ANUNCIOS, novas)
        except Exception as exc:
            resultado.falhar("WarehouseError", str(exc))
            return

        resultado.concluir(f"{resumo_cadeia}; {len(novas)} anúncio(s) novo(s) de {len(linhas)} encontrado(s)")

    # ─────────────────────────────────────────────────────────────────────
    # Passo — comentários (dedupe por anti-join)
    # ─────────────────────────────────────────────────────────────────────

    async def _passo_comentarios(self, contexto: dict, resultado: StepResultScraper) -> None:
        config: SiteScraperVersao = contexto["config"]
        mapa = config.seletores.get(TipoObjetivoScraper.COMENTARIOS.value)
        if mapa is None or not mapa.etapas:
            resultado.pular("nenhuma etapa mapeada para comentários")
            return

        try:
            linhas, resumo_cadeia = await self._executar_cadeia(TipoObjetivoScraper.COMENTARIOS, mapa, contexto)
        except _CadeiaFalhou as exc:
            resultado.falhar(exc.tipo, exc.mensagem, retryable=exc.retryable)
            return

        if not linhas:
            resultado.falhar("EmptyData", "Nenhum comentário foi extraído da página.")
            return

        campos_chave = ["marca", "modelo", "versao", "ano", "url_origem", "texto"]
        existentes = self._chaves_existentes(schemas.COMENTARIOS, campos_chave, contexto["site_id"])

        agora = agora_iso()
        novas = []
        for linha in linhas:
            chave = tuple(linha.get(c) for c in campos_chave)
            if chave in existentes:
                continue
            linha["site_id"] = contexto["site_id"]
            linha["site_nome"] = contexto["site_nome"]
            linha["coletado_em"] = agora
            novas.append(linha)
            existentes.add(chave)

        contexto["progresso"]["comentarios"] = {"encontrados": len(linhas), "novos": len(novas)}

        if not novas:
            resultado.concluir(f"{resumo_cadeia}; 0 comentário(s) novo(s) (todos já coletados antes)")
            return

        try:
            warehouse.upsert(schemas.COMENTARIOS, novas)
        except Exception as exc:
            resultado.falhar("WarehouseError", str(exc))
            return

        resultado.concluir(f"{resumo_cadeia}; {len(novas)} comentário(s) novo(s) de {len(linhas)} encontrado(s)")
