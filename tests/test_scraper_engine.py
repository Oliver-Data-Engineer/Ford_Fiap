"""
test_scraper_engine.py — verifica ScraperEngine (src/scraper/engine.py) sem
tocar rede real: dublê de `browser.buscar_html` (HTML fixo por URL, sem
Playwright) + dublê de warehouse (sem BigQuery real). O extrator
(`src/scraper/extractor.py`) roda de verdade — é o próprio contrato
"determinístico, sem LLM" sendo exercitado.

Cobre especificamente o que é novo/arriscado nesta engine:
  1. Um objetivo falhando (fetch quebrado) não derruba os outros — a
     execução termina "partial", não "failed".
  2. Ficha técnica é append puro (histórico); anúncios/comentários
     deduplicam por anti-join — rodar duas vezes a mesma página não
     duplica anúncio nenhum, mas o histórico de ficha técnica cresce.
  3. Cadeia de 2 etapas (fan-out de verdade — a etapa 0 é uma listagem,
     cada linha carrega uma URL que a etapa 1 busca uma vez): a tabela
     final tem linhas de VÁRIAS páginas de detalhe, não só uma; uma URL
     quebrada no meio não derruba o objetivo (tolerância parcial).
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from src.pipelines.models import ConfigLLM, ConfigSchedule, StatusRun, StatusStep
from src.scraper import engine as scraper_engine
from src.scraper import schemas
from src.scraper.models import (
    EtapaScraper,
    MapaSeletor,
    ScraperRun,
    SiteScraperVersao,
    TipoObjetivoScraper,
    TipoStepScraper,
)

falhas = []


def checar(condicao, descricao):
    print(("  OK   " if condicao else "  FALHA") + f"  {descricao}")
    if not condicao:
        falhas.append(descricao)


# ─────────────────────────────────────────────────────────────────────────────
# Dublês
# ─────────────────────────────────────────────────────────────────────────────

class FakeWarehouse:
    """'Banco' em memória que entende as consultas de anti-join que
    `ScraperEngine._chaves_existentes` faz — filtra por `site_id` e devolve
    só as colunas pedidas, igual `tests/test_govmode_engine.py::FakeWarehouse`."""

    def __init__(self):
        self.tabelas = {nome: [] for nome in schemas.TABELAS}

    def table_id(self, nome):
        return f"projeto-teste.scraper_ford.{nome}"

    def upsert(self, nome, linhas):
        if not linhas:
            return
        self.tabelas[nome].extend(linhas)

    def query(self, sql, params=None):
        params = params or []
        valores = {p.name: p.value for p in params}
        site = valores.get("site")

        for tabela in (schemas.ANUNCIOS, schemas.COMENTARIOS):
            if tabela in sql:
                linhas = [l for l in self.tabelas[tabela] if l.get("site_id") == site]
                return pd.DataFrame(linhas) if linhas else pd.DataFrame()

        return pd.DataFrame()


HTML_FICHA_TECNICA = """
<table>
  <tr><th>Motor</th><td>2.0 Turbo</td></tr>
  <tr><th>Potência</th><td>170cv</td></tr>
</table>
"""

HTML_ANUNCIOS = """
<div class="anuncio"><span class="preco">R$ 50.000</span><a class="link" href="/anuncio/1">ver</a></div>
<div class="anuncio"><span class="preco">R$ 60.000</span><a class="link" href="/anuncio/2">ver</a></div>
"""

URL_FICHA = "https://x/ficha"
URL_ANUNCIOS = "https://x/anuncios"
URL_COMENTARIOS = "https://x/comentarios"  # propositalmente sem HTML — simula falha de fetch

HTML_POR_URL = {
    URL_FICHA: HTML_FICHA_TECNICA,
    URL_ANUNCIOS: HTML_ANUNCIOS,
}


async def fake_buscar_html(url, **kwargs):
    return HTML_POR_URL.get(url)  # None para URL_COMENTARIOS — mesmo contrato de falha do browser.py real


def config_padrao() -> SiteScraperVersao:
    return SiteScraperVersao(
        version=1,
        url_base="https://x",
        objetivos=[TipoObjetivoScraper.FICHA_TECNICA, TipoObjetivoScraper.ANUNCIOS, TipoObjetivoScraper.COMENTARIOS],
        amostra_urls={
            "ficha_tecnica": URL_FICHA, "anuncios": URL_ANUNCIOS, "comentarios": URL_COMENTARIOS,
        },
        seletores={
            "ficha_tecnica": MapaSeletor(
                objetivo=TipoObjetivoScraper.FICHA_TECNICA,
                selectors={"linha_atributo": "tr", "nome_atributo": "th", "valor_atributo": "td"},
                url_amostra=URL_FICHA,
            ),
            "anuncios": MapaSeletor(
                objetivo=TipoObjetivoScraper.ANUNCIOS,
                selectors={"item_listagem": ".anuncio", "preco": ".preco", "url_anuncio": ".link"},
                url_amostra=URL_ANUNCIOS,
            ),
            "comentarios": MapaSeletor(
                objetivo=TipoObjetivoScraper.COMENTARIOS,
                selectors={"item_comentario": ".c", "texto": ".t"},
                url_amostra=URL_COMENTARIOS,
            ),
        },
        llm=ConfigLLM(),
        schedule=ConfigSchedule(),
    )


def principal() -> int:
    print("=== 1. Execução com um objetivo falhando: os outros dois concluem, status='partial' ===")

    fake_wh = FakeWarehouse()
    scraper_engine.warehouse.query = fake_wh.query
    scraper_engine.warehouse.upsert = fake_wh.upsert
    scraper_engine.warehouse.table_id = fake_wh.table_id
    scraper_engine.warehouse.garantir_tabelas = lambda: None
    scraper_engine.browser.buscar_html = fake_buscar_html

    motor = scraper_engine.ScraperEngine()
    config = config_padrao()
    run = ScraperRun(site_id="site_1", site_name="Site Teste")

    run = asyncio.run(motor.executar(config, run))

    for s in run.steps:
        print(f"  [{s.status.value:8}] {s.type.value:15} {s.resumo}")

    checar(run.status == StatusRun.PARCIAL, f"execução geral 'partial' (obtido {run.status.value})")

    checar(run.step(TipoStepScraper.FICHA_TECNICA).status == StatusStep.OK, "passo ficha técnica OK")
    checar(len(fake_wh.tabelas[schemas.FICHA_TECNICA]) == 2, "2 atributos gravados (Motor + Potência)")

    checar(run.step(TipoStepScraper.ANUNCIOS).status == StatusStep.OK, "passo anúncios OK")
    checar(len(fake_wh.tabelas[schemas.ANUNCIOS]) == 2, "2 anúncios gravados")

    checar(run.step(TipoStepScraper.COMENTARIOS).status == StatusStep.FALHOU, "passo comentários falha (fetch quebrado)")
    checar(run.step(TipoStepScraper.COMENTARIOS).error.error_type == "FetchError",
           "erro tipado como FetchError, não uma exceção genérica")
    checar(run.step(TipoStepScraper.COMENTARIOS).error.retryable is True, "FetchError marcado como retryable")

    print("\n=== 2. Rodar de novo: ficha técnica cresce (histórico); anúncios não duplicam (anti-join) ===")

    run2 = ScraperRun(site_id="site_1", site_name="Site Teste")
    run2 = asyncio.run(motor.executar(config, run2))

    checar(len(fake_wh.tabelas[schemas.FICHA_TECNICA]) == 4,
           f"ficha técnica é append puro — 4 linhas após 2 execuções (obtido {len(fake_wh.tabelas[schemas.FICHA_TECNICA])})")
    checar(len(fake_wh.tabelas[schemas.ANUNCIOS]) == 2,
           f"anúncios não duplicaram na segunda execução (anti-join) — ainda 2 (obtido {len(fake_wh.tabelas[schemas.ANUNCIOS])})")
    checar("0 anúncio(s) novo(s)" in run2.step(TipoStepScraper.ANUNCIOS).resumo,
           f"resumo do passo reflete zero novidades (obtido: {run2.step(TipoStepScraper.ANUNCIOS).resumo!r})")

    print("\n=== 3. Objetivo sem seletor mapeado é pulado, não falha ===")

    config_incompleto = config_padrao()
    config_incompleto.seletores.pop("comentarios")
    run3 = ScraperRun(site_id="site_1", site_name="Site Teste")
    run3 = asyncio.run(motor.executar(config_incompleto, run3))

    checar(run3.step(TipoStepScraper.COMENTARIOS).status == StatusStep.PULADO,
           "sem MapaSeletor, o passo é pulado (não conta como falha)")
    checar(run3.status == StatusRun.OK, f"execução com passo pulado (não falho) conclui 'success' (obtido {run3.status.value})")

    print("\n=== 4. Cadeia de 2 etapas: fan-out real, tolerância parcial numa linha quebrada ===")

    html_listagem = (
        '<div class="item"><a class="link" href="https://x/detalhe/1">ver</a></div>'
        '<div class="item"><a class="link" href="https://x/detalhe/2">ver</a></div>'
        '<div class="item"><a class="link" href="https://x/detalhe/quebrado">ver</a></div>'
    )
    url_listagem = "https://x/listagem"
    html_por_url_cadeia = {
        url_listagem: html_listagem,
        "https://x/detalhe/1": "<table><tr><th>Motor</th><td>1.0</td></tr></table>",
        "https://x/detalhe/2": "<table><tr><th>Motor</th><td>2.0</td></tr></table>",
        # "https://x/detalhe/quebrado" propositalmente ausente — simula 404/fetch quebrado
    }

    async def fake_buscar_html_cadeia(url, **kwargs):
        return html_por_url_cadeia.get(url)

    scraper_engine.browser.buscar_html = fake_buscar_html_cadeia

    fake_wh2 = FakeWarehouse()
    scraper_engine.warehouse.query = fake_wh2.query
    scraper_engine.warehouse.upsert = fake_wh2.upsert
    scraper_engine.warehouse.table_id = fake_wh2.table_id
    scraper_engine.warehouse.garantir_tabelas = lambda: None

    config_cadeia = SiteScraperVersao(
        version=1, url_base="https://x", objetivos=[TipoObjetivoScraper.FICHA_TECNICA],
        amostra_urls={"ficha_tecnica": url_listagem},
        seletores={
            "ficha_tecnica": MapaSeletor(objetivo=TipoObjetivoScraper.FICHA_TECNICA, etapas=[
                EtapaScraper(
                    ordem=0, url_amostra=url_listagem,
                    selectors={"linha": ".item", "campos": {"url_ficha": ".link"}},
                    campo_herdado="url_ficha",
                ),
                EtapaScraper(
                    ordem=1, url_amostra="",
                    selectors={"linha_atributo": "tr", "nome_atributo": "th", "valor_atributo": "td"},
                ),
            ]),
        },
        llm=ConfigLLM(), schedule=ConfigSchedule(),
    )

    motor_cadeia = scraper_engine.ScraperEngine()
    run4 = ScraperRun(site_id="site_cadeia", site_name="Site Cadeia")
    run4 = asyncio.run(motor_cadeia.executar(config_cadeia, run4))

    passo4 = run4.step(TipoStepScraper.FICHA_TECNICA)
    print(f"  [{passo4.status.value:8}] {passo4.resumo}")

    checar(passo4.status == StatusStep.OK, f"passo OK mesmo com 1 URL quebrada de 3 (obtido {passo4.status.value})")
    checar(len(fake_wh2.tabelas[schemas.FICHA_TECNICA]) == 2,
           f"2 atributos gravados — só os 2 detalhes que carregaram (obtido {len(fake_wh2.tabelas[schemas.FICHA_TECNICA])})")
    checar("2/3" in passo4.resumo, f"resumo reflete 2/3 sobreviventes (obtido: {passo4.resumo!r})")

    valores = {l["valor"] for l in fake_wh2.tabelas[schemas.FICHA_TECNICA]}
    checar(valores == {"1.0", "2.0"}, f"linhas vêm dos DOIS detalhes diferentes — fan-out de verdade (obtido {valores})")

    print("\n" + "=" * 62)
    if falhas:
        print(f"{len(falhas)} VERIFICAÇÃO(ÕES) FALHARAM:")
        for f in falhas:
            print(f"  - {f}")
    else:
        print("Todas as verificações da engine do Scraper Mode passaram.")
    print("=" * 62)
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(principal())
