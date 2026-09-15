"""
store.py
Persistência dos sites cadastrados e execuções do Scraper Mode — mesmo
padrão de `src/govmode/store.py` (JSON em `data/`, escrita atômica), em
arquivos próprios (`scraper_sites.json`, `scraper_runs.json`) e com um lock
independente.

Reaproveita os helpers de I/O de baixo nível de `src.pipelines.store`
(`_carregar`/`_salvar`) por serem genéricos.
"""
from __future__ import annotations

import logging
import threading

from src.pipelines.models import StatusPipeline, agora_iso
from src.pipelines.store import _carregar, _salvar
from src.scraper.models import EtapaScraper, MapaSeletor, SiteScraper, SiteScraperEntrada, ScraperRun

logger = logging.getLogger(__name__)

_LOCK = threading.RLock()

ARQUIVO_SITES = "scraper_sites.json"
ARQUIVO_RUNS = "scraper_runs.json"

MAX_RUNS_HISTORICO = 200


# ─────────────────────────────────────────────────────────────────────────────
# Sites
# ─────────────────────────────────────────────────────────────────────────────

def listar_sites() -> list[SiteScraper]:
    with _LOCK:
        sites = _carregar(ARQUIVO_SITES, SiteScraper)
    sites.sort(key=lambda s: s.updated_at, reverse=True)
    return sites


def obter_site(site_id: str) -> SiteScraper | None:
    return next((s for s in listar_sites() if s.id == site_id), None)


def criar_site(entrada: SiteScraperEntrada) -> SiteScraper:
    site = SiteScraper(**entrada.model_dump())
    # Seeda uma cadeia de UMA etapa por objetivo, a partir da URL de amostra
    # simples do cadastro — mantém o fluxo "um site, uma página" de sempre
    # funcionando sem exigir o CRUD de etapas pra esse caso comum. Sites com
    # navegação de várias páginas adicionam mais etapas depois, via
    # `POST /objetivos/{objetivo}/etapas`.
    for objetivo in site.objetivos:
        url = site.amostra_urls.get(objetivo.value, "")
        site.seletores[objetivo.value] = MapaSeletor(
            objetivo=objetivo, etapas=[EtapaScraper(ordem=0, url_amostra=url)],
        )
    site.versions = [site.snapshot()]

    with _LOCK:
        sites = _carregar(ARQUIVO_SITES, SiteScraper)
        sites.append(site)
        _salvar(ARQUIVO_SITES, sites)

    logger.info("[scraper] site criado: %s (%s)", site.name, site.id)
    return site


def atualizar_site(site_id: str, entrada: SiteScraperEntrada) -> SiteScraper | None:
    """Mudança em `url_base`/`objetivos`/`amostra_urls`/`schedule` gera
    versão nova — mesmo critério de `govmode.store.atualizar_pipeline`.
    Editar `url_base`/`objetivos`/`amostra_urls` também invalida os
    seletores já descobertos/testados (a página-alvo mudou; a config antiga
    não é mais confiável) — o usuário precisa mapear/testar de novo antes de
    poder registrar outra vez.
    """
    with _LOCK:
        sites = _carregar(ARQUIVO_SITES, SiteScraper)
        indice = next((i for i, s in enumerate(sites) if s.id == site_id), None)
        if indice is None:
            return None

        atual = sites[indice]

        mudou_alvo = (
            atual.url_base != entrada.url_base
            or atual.objetivos != entrada.objetivos
            or atual.amostra_urls != entrada.amostra_urls
        )
        mudou_config = mudou_alvo or atual.schedule != entrada.schedule or atual.llm != entrada.llm

        if mudou_config:
            if not any(v.version == atual.version for v in atual.versions):
                atual.versions.append(atual.snapshot())
            atual.version += 1
            atual.url_base = entrada.url_base
            atual.objetivos = entrada.objetivos
            atual.amostra_urls = entrada.amostra_urls
            atual.llm = entrada.llm
            atual.schedule = entrada.schedule

            if mudou_alvo:
                # Reseeda uma etapa por objetivo a partir da nova URL de
                # amostra (mesmo critério de `criar_site`) — sem isso, o
                # objetivo ficaria sem etapa nenhuma até o usuário passar
                # pelo CRUD de etapas à mão.
                atual.seletores = {
                    objetivo.value: MapaSeletor(
                        objetivo=objetivo,
                        etapas=[EtapaScraper(ordem=0, url_amostra=atual.amostra_urls.get(objetivo.value, ""))],
                    )
                    for objetivo in atual.objetivos
                }
                atual.registrado = False
                atual.status = StatusPipeline.PAUSADA

            atual.versions.append(atual.snapshot())

        atual.name = entrada.name
        atual.description = entrada.description
        if not mudou_alvo:
            atual.status = entrada.status
        atual.updated_at = agora_iso()

        sites[indice] = atual
        _salvar(ARQUIVO_SITES, sites)

    return atual


def salvar_site(site: SiteScraper) -> SiteScraper:
    """Persiste um objeto `SiteScraper` já montado (discovery/test/register
    mexem direto no objeto e chamam isto pra gravar)."""
    with _LOCK:
        sites = _carregar(ARQUIVO_SITES, SiteScraper)
        indice = next((i for i, s in enumerate(sites) if s.id == site.id), None)
        site.updated_at = agora_iso()

        if indice is None:
            sites.append(site)
        else:
            sites[indice] = site

        _salvar(ARQUIVO_SITES, sites)

    return site


def alterar_status_site(site_id: str, status: StatusPipeline) -> SiteScraper | None:
    site = obter_site(site_id)
    if site is None:
        return None
    site.status = status
    return salvar_site(site)


def remover_site(site_id: str) -> bool:
    with _LOCK:
        sites = _carregar(ARQUIVO_SITES, SiteScraper)
        restantes = [s for s in sites if s.id != site_id]
        if len(restantes) == len(sites):
            return False
        _salvar(ARQUIVO_SITES, restantes)
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Execuções
# ─────────────────────────────────────────────────────────────────────────────

def salvar_run(run: ScraperRun) -> ScraperRun:
    with _LOCK:
        runs = _carregar(ARQUIVO_RUNS, ScraperRun)
        indice = next((i for i, r in enumerate(runs) if r.id == run.id), None)

        if indice is None:
            runs.append(run)
        else:
            runs[indice] = run

        runs.sort(key=lambda r: r.started_at, reverse=True)
        _salvar(ARQUIVO_RUNS, runs[:MAX_RUNS_HISTORICO])

    return run


def listar_runs(site_id: str | None = None, limite: int = 50) -> list[ScraperRun]:
    with _LOCK:
        runs = _carregar(ARQUIVO_RUNS, ScraperRun)

    if site_id:
        runs = [r for r in runs if r.site_id == site_id]

    runs.sort(key=lambda r: r.started_at, reverse=True)
    return runs[:limite]


def obter_run(run_id: str) -> ScraperRun | None:
    with _LOCK:
        runs = _carregar(ARQUIVO_RUNS, ScraperRun)
    return next((r for r in runs if r.id == run_id), None)


def registrar_execucao_no_site(site_id: str, status: str, quando: str) -> None:
    site = obter_site(site_id)
    if site is None:
        return
    site.last_run_at = quando
    site.last_run_status = status
    salvar_site(site)
