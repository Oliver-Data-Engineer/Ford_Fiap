"""
exemplos.py
Sites de exemplo do Scraper Mode — cadastros fixos, com o mapeamento completo
já validado, servindo de modelo do fluxo pra quem for cadastrar um site novo:

  - CarroNaWeb: fluxo de 2 etapas (navegação — catálogo por fabricante — até a
    página de ficha técnica de uma versão, usando `campo_herdado`).
  - iCarros: fluxo de etapa única (página de ficha técnica direta, sem
    navegação prévia — o próprio iCarros não usa paginação tradicional, ver
    `src/utils/iCarros.py`).

`data/scraper_sites.json` está no `.gitignore` (dado local, por ambiente), então
`garantir_sites_exemplo()` recria qualquer um dos dois que estiver faltando toda
vez que a API sobe — idempotente por nome, nunca duplica quem já existe.
"""
from __future__ import annotations

import logging

from src.pipelines.models import ConfigLLM, StatusPipeline, agora_iso
from src.scraper.models import EtapaScraper, MapaSeletor, SiteScraper, TipoObjetivoScraper
from src.scraper.store import listar_sites, salvar_site

logger = logging.getLogger(__name__)

# Nome, no cadastro, que identifica cada exemplo — usado só pra checar se já
# existe algum site equivalente antes de recriar (case-insensitive, por
# substring, pra cobrir variações como "CarrosWeb"/"CarroNaWeb"/"carrosnaweb").
_NOMES_EXEMPLO = {
    "carronaweb": ("carronaweb", "carrosweb", "carrosnaweb"),
    "icarros": ("icarros",),
}


def _existe_exemplo(sites: list[SiteScraper], chaves: tuple[str, ...]) -> bool:
    for site in sites:
        nome = site.name.strip().lower()
        if any(chave in nome for chave in chaves):
            return True
    return False


def _exemplo_carronaweb() -> SiteScraper:
    site = SiteScraper(
        name="CarroNaWeb",
        description=(
            "Exemplo fixo — mapeamento completo (fluxo de 2 etapas: catálogo "
            "por fabricante → ficha técnica), modelo de referência do fluxo."
        ),
        url_base="https://www.carrosnaweb.com.br",
        objetivos=[TipoObjetivoScraper.FICHA_TECNICA],
        amostra_urls={
            "ficha_tecnica": "https://www.carrosnaweb.com.br/catalogo.asp?fabricante=Ford",
        },
        llm=ConfigLLM(provider="groq"),
        status=StatusPipeline.ATIVA,
        registrado=True,
    )

    agora = agora_iso()
    site.seletores = {
        "ficha_tecnica": MapaSeletor(
            objetivo=TipoObjetivoScraper.FICHA_TECNICA,
            etapas=[
                EtapaScraper(
                    ordem=0,
                    url_amostra="https://www.carrosnaweb.com.br/catalogo.asp?fabricante=Ford",
                    selectors={
                        "linha": "td[align=left]",
                        "campos": {"url_ficha": "a[href*=fichadetalhe]"},
                    },
                    campo_herdado="url_ficha",
                    validado_em=agora,
                ),
                EtapaScraper(
                    ordem=1,
                    url_amostra="https://www.carrosnaweb.com.br/fichadetalhe.asp?codigo=48111",
                    selectors={
                        "linha_atributo": "table tr",
                        "nome_atributo": "td:nth-child(1)",
                        "valor_atributo": "td:nth-child(2)",
                    },
                    validado_em=agora,
                ),
            ],
        ),
    }
    site.versions = [site.snapshot()]
    return site


def _exemplo_icarros() -> SiteScraper:
    """Seletores adaptados da estrutura moderna do iCarros descrita em
    `src/utils/iCarros.py` (`_extrair_ficha_tecnica`/`_extrair_valor`):
    `div.sheet-theme-container` > `div.technical-sheet-item`/
    `.brakes-suspension-item` (linha repetida) > `span[...bold]` (nome) +
    `span[...regular]` (valor). Mapeamento best-effort, como qualquer seletor
    de site externo — pode precisar reajuste se o HTML mudar; o objetivo aqui
    é servir de modelo do fluxo de etapa única, não garantir scraping perpétuo.
    """
    site = SiteScraper(
        name="iCarros",
        description=(
            "Exemplo fixo — mapeamento completo (fluxo de etapa única: "
            "página de ficha técnica direta, sem navegação prévia), modelo "
            "de referência do fluxo."
        ),
        url_base="https://www.icarros.com.br",
        objetivos=[TipoObjetivoScraper.FICHA_TECNICA],
        amostra_urls={
            "ficha_tecnica": (
                "https://www.icarros.com.br/catalogo-zero-km/marcas/ford/ranger/"
                "3-2-cd-limited-4x4-aut-4p/2024/ficha-tecnica"
            ),
        },
        llm=ConfigLLM(provider="groq"),
        status=StatusPipeline.ATIVA,
        registrado=True,
    )

    agora = agora_iso()
    site.seletores = {
        "ficha_tecnica": MapaSeletor(
            objetivo=TipoObjetivoScraper.FICHA_TECNICA,
            etapas=[
                EtapaScraper(
                    ordem=0,
                    url_amostra=site.amostra_urls["ficha_tecnica"],
                    selectors={
                        "linha_atributo": ".technical-sheet-item, .brakes-suspension-item",
                        "nome_atributo": "span[class*='bold']",
                        "valor_atributo": "span[class*='regular']",
                    },
                    validado_em=agora,
                ),
            ],
        ),
    }
    site.versions = [site.snapshot()]
    return site


def garantir_sites_exemplo() -> None:
    """Chamado no startup da API — garante que os dois exemplos fixos
    existam no cadastro, recriando só o que estiver faltando (idempotente por
    nome, nunca duplica)."""
    sites = listar_sites()

    if not _existe_exemplo(sites, _NOMES_EXEMPLO["carronaweb"]):
        salvar_site(_exemplo_carronaweb())
        logger.info("[scraper] site de exemplo recriado: CarroNaWeb")

    if not _existe_exemplo(sites, _NOMES_EXEMPLO["icarros"]):
        salvar_site(_exemplo_icarros())
        logger.info("[scraper] site de exemplo recriado: iCarros")
