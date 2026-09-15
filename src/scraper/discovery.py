"""
discovery.py
Motor de descoberta — a ÚNICA camada do Scraper Mode que chama o LLM
(Regra 7 do SPEC generalizada: um só lugar chama o modelo). Nunca é
reaproveitado pelo motor de extração (`engine.py`/`extractor.py`), que é
puramente determinístico.

Fluxo por ETAPA (uma etapa de navegação, ou a última — a de dados de
verdade — de um objetivo): busca HTML (Playwright) → simplifica → `Agente`
(com `ConfigLLM` do próprio site, caindo pro padrão global se não
informado) → valida a saída contra o Pydantic certo (`Seletor*` flat na
última etapa, `SeletorEtapaIntermediaria` nas anteriores) → `EtapaScraper`
preenchida ou `ErroStep` isolado.

Cada etapa (e cada objetivo) tem seu próprio try/except: uma falha nunca
contamina as outras (mesma isolação de falha do `GovEngine.executar`, um
nível mais fundo agora que um objetivo pode ter várias etapas).

Este módulo também gera:
  - o resumo de confirmação em linguagem natural da tela de teste
    (`gerar_resumo_confirmacao`) — roda sobre dados JÁ EXTRAÍDOS
    (determinístico), nunca sobre HTML;
  - a classificação de parâmetros de URL como filtros dinâmicos
    (`classificar_filtros`/`detectar_filtros`) — a parte determinística
    (parsing da query string) vive em `url_utils.py`, sem LLM nenhum;
    só a classificação semântica de cada parâmetro passa pelo `Agente`.
"""
from __future__ import annotations

import logging

from pydantic import ValidationError

from src.agents import Agente
from src.config import settings
from src.pipelines.models import ConfigLLM, ErroStep, agora_iso
from src.scraper import browser, url_utils
from src.scraper.html_simplifier import simplificar
from src.scraper.models import (
    PAPEIS_FILTRO,
    SELETOR_POR_OBJETIVO,
    EtapaScraper,
    FiltroParam,
    SeletorEtapaIntermediaria,
    SiteScraper,
    TipoObjetivoScraper,
)
from src.scraper.prompts import (
    PROMPT_CONFIRMACAO,
    PROMPT_ETAPA_NAVEGACAO,
    PROMPT_FILTROS,
    PROMPT_POR_OBJETIVO,
    SCHEMA_ETAPA_NAVEGACAO,
    SCHEMA_FILTROS,
    SCHEMA_POR_OBJETIVO,
)

logger = logging.getLogger(__name__)


async def descobrir_etapa(
    site: SiteScraper, objetivo: TipoObjetivoScraper, etapa: EtapaScraper, *, ultima: bool,
) -> EtapaScraper | ErroStep:
    """Descobre o mapa de seletores de UMA etapa. Nunca levanta — devolve
    `ErroStep` em qualquer falha (fetch, LLM, parse do JSON, validação).
    `ultima=True` usa o `Seletor*` flat do objetivo (dados de verdade);
    `False` usa `SeletorEtapaIntermediaria` (etapa de navegação)."""
    url = etapa.url_amostra
    if not url:
        return ErroStep(
            error_type="MissingSampleUrl",
            message=f"Etapa {etapa.ordem} do objetivo '{objetivo.value}' não tem URL de amostra.",
        )

    html = await browser.buscar_html(url)
    if html is None:
        return ErroStep(
            error_type="FetchError",
            message=f"Não foi possível carregar '{url}' (todas as tentativas falharam).",
            retryable=True,
        )

    html_simplificado = simplificar(html, settings.scraper_discovery_max_html_chars)
    if not html_simplificado.strip():
        return ErroStep(error_type="EmptyPage", message=f"'{url}' devolveu uma página vazia.")

    agente = Agente(
        provider=site.llm.provider,
        model=site.llm.model,
        temperature=site.llm.temperature,
        max_tokens=site.llm.max_tokens,
    )

    if ultima:
        prompt = PROMPT_POR_OBJETIVO[objetivo]
        schema = SCHEMA_POR_OBJETIVO[objetivo]
        modelo_seletor = SELETOR_POR_OBJETIVO[objetivo]
    else:
        prompt = PROMPT_ETAPA_NAVEGACAO
        schema = SCHEMA_ETAPA_NAVEGACAO
        modelo_seletor = SeletorEtapaIntermediaria

    resultado = await agente.run(
        html_simplificado, prompt=prompt, output_schema=schema, output_format="json",
    )

    if not resultado.success:
        erro = resultado.error
        return ErroStep(
            error_type=erro.type if erro else "AgentError",
            message=erro.message if erro else "Falha desconhecida do agente.",
            retryable=erro.retryable if erro else False,
        )

    try:
        selecionado = modelo_seletor(**(resultado.output or {}))
    except (ValidationError, TypeError) as exc:
        return ErroStep(
            error_type="OutputParseError",
            message=f"Seletores devolvidos pelo LLM não batem com o formato esperado: {exc}",
            retryable=True,
        )

    return etapa.model_copy(update={
        "selectors": selecionado.model_dump(),
        "llm": agente.descrever(),
        "descoberto_em": agora_iso(),
    })


async def descobrir_site(
    site: SiteScraper, objetivos: list[TipoObjetivoScraper] | None = None,
) -> dict[TipoObjetivoScraper, dict[int, EtapaScraper | ErroStep]]:
    """Roda `descobrir_etapa` pra cada etapa de cada objetivo pedido
    (default: todos os `site.objetivos`) — isolado por etapa E por
    objetivo, uma falha não afeta as demais."""
    alvo = objetivos if objetivos is not None else site.objetivos
    resultados: dict[TipoObjetivoScraper, dict[int, EtapaScraper | ErroStep]] = {}

    for objetivo in alvo:
        mapa = site.seletores.get(objetivo.value)
        if not mapa or not mapa.etapas:
            resultados[objetivo] = {
                0: ErroStep(
                    error_type="MissingEtapas",
                    message=f"Nenhuma etapa configurada para o objetivo '{objetivo.value}'.",
                ),
            }
            continue

        etapas_ordenadas = sorted(mapa.etapas, key=lambda e: e.ordem)
        por_etapa: dict[int, EtapaScraper | ErroStep] = {}
        for indice, etapa in enumerate(etapas_ordenadas):
            ultima = indice == len(etapas_ordenadas) - 1
            try:
                por_etapa[etapa.ordem] = await descobrir_etapa(site, objetivo, etapa, ultima=ultima)
            except Exception as exc:
                logger.exception(
                    "[scraper] descoberta da etapa %s do objetivo '%s' falhou inesperadamente",
                    etapa.ordem, objetivo.value,
                )
                por_etapa[etapa.ordem] = ErroStep(error_type=type(exc).__name__, message=str(exc))
        resultados[objetivo] = por_etapa

    return resultados


async def gerar_resumo_confirmacao(
    objetivo: TipoObjetivoScraper, linhas: list[dict], llm: ConfigLLM,
) -> str:
    """
    Resumo em linguagem natural, gerado pela IA, sobre uma amostra de dados
    já extraída (determinística — sem LLM nenhum em `extractor.py`) — é
    isto que a tela de teste mostra ao lado da tabela, pro usuário confirmar
    de leitura rápida se "é exatamente aquilo mesmo" antes de registrar o
    site. Nunca levanta exceção: uma falha aqui vira uma frase de aviso,
    nunca derruba o `/test` (a tabela de dados continua sendo a fonte de
    verdade, isto é só um apoio de leitura).
    """
    if not linhas:
        return "Nenhum dado foi extraído com esses seletores — confira a URL e os seletores abaixo."

    agente = Agente(
        provider=llm.provider, model=llm.model, temperature=llm.temperature, max_tokens=llm.max_tokens,
    )

    resultado = await agente.run(
        {"objetivo": objetivo.value, "amostra": linhas[:10], "total_encontrado": len(linhas)},
        prompt=PROMPT_CONFIRMACAO.format(objetivo=objetivo.value),
        output_format="text",
    )

    if not resultado.success:
        return "Não foi possível gerar o resumo automático — revise a tabela de dados abaixo manualmente."

    return str(resultado.output or "").strip() or "O modelo não devolveu um resumo — revise a tabela de dados abaixo."


async def classificar_filtros(
    params: list[dict[str, str]], llm: ConfigLLM,
) -> list[FiltroParam] | ErroStep:
    """Classifica cada parâmetro de query string (`{nome_param,
    valor_amostra}`) contra o vocabulário fixo de papéis
    (`marca|modelo|versao|ano|id|outro`). Nunca levanta — falha do
    provider vira `ErroStep`; um papel fora do vocabulário devolvido pelo
    LLM cai em "outro" (nunca quebra por causa de uma alucinação de rótulo)."""
    if not params:
        return []

    agente = Agente(
        provider=llm.provider, model=llm.model, temperature=llm.temperature, max_tokens=llm.max_tokens,
    )

    resultado = await agente.run(
        {"parametros": params}, prompt=PROMPT_FILTROS, output_schema=SCHEMA_FILTROS, output_format="json",
    )

    if not resultado.success:
        erro = resultado.error
        return ErroStep(
            error_type=erro.type if erro else "AgentError",
            message=erro.message if erro else "Falha desconhecida do agente.",
            retryable=erro.retryable if erro else False,
        )

    papel_por_nome = {
        item.get("nome_param"): item.get("papel")
        for item in (resultado.output or {}).get("parametros") or []
        if isinstance(item, dict)
    }

    filtros: list[FiltroParam] = []
    for p in params:
        papel = papel_por_nome.get(p["nome_param"]) or "outro"
        if papel not in PAPEIS_FILTRO:
            papel = "outro"
        filtros.append(FiltroParam(nome_param=p["nome_param"], papel=papel, valor_amostra=p["valor_amostra"]))
    return filtros


async def detectar_filtros(url: str, llm: ConfigLLM) -> tuple[list[FiltroParam], str] | ErroStep:
    """Orquestra a detecção completa: extrai os parâmetros da query string
    (determinístico) → classifica cada um (LLM) → monta o template.
    Sem query string, devolve `([], url)` sem chamar o LLM à toa."""
    params = url_utils.extrair_query_params(url)
    if not params:
        return [], url

    filtros = await classificar_filtros(params, llm)
    if isinstance(filtros, ErroStep):
        return filtros

    template = url_utils.montar_template(url, filtros)
    return filtros, template
