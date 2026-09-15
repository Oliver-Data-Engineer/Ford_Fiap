"""
test_scraper_discovery.py — verifica o motor de descoberta
(src/scraper/discovery.py) sem tocar rede/LLM real: dublê de
`browser.buscar_html` (HTML fixo por URL) + dublê de `Agente` (decide a
resposta olhando um marcador no HTML recebido, ou o formato do `entrada`
pros dois casos que não recebem HTML — resumo de confirmação e
classificação de filtros).

Cobre especificamente o que é novo/arriscado neste módulo:
  1. Descoberta bem sucedida (última etapa) valida a saída do LLM contra o
     Pydantic `Seletor*` certo e devolve uma `EtapaScraper` rastreável
     (llm usado).
  2. Falha do provider (rede/limite/etc) numa etapa não vaza pras outras —
     cada etapa (e cada objetivo) tem seu próprio try/except isolado.
  3. Saída do LLM que não bate com o schema esperado vira `OutputParseError`
     isolado (Pydantic `ValidationError` nunca escapa pro chamador).
  4. Etapa de NAVEGAÇÃO (`ultima=False`) valida contra
     `SeletorEtapaIntermediaria` (linha + campos livres), não o `Seletor*`
     flat do objetivo.
  5. Sem URL de amostra configurada, sem etapa nenhuma, ou fetch falhando
     (página fora do ar), o objetivo/etapa falha com um erro tipado, sem
     exceção.
  6. `gerar_resumo_confirmacao` — o resumo em linguagem natural da tela de
     teste: roda sobre a amostra REAL já extraída (não sobre HTML), nunca
     levanta (falha do provider vira frase de aviso), e não chama a IA à
     toa quando não há nenhum dado extraído.
  7. `classificar_filtros`/`detectar_filtros` — classifica parâmetros de
     query string contra o vocabulário fixo (marca/modelo/.../outro) e
     monta o template; sem query string, nem chama a IA.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agents.base import AgentResult
from src.pipelines.models import ConfigLLM, ErroStep
from src.scraper import discovery
from src.scraper.models import EtapaScraper, MapaSeletor, SiteScraper, TipoObjetivoScraper

falhas = []


def checar(condicao, descricao):
    print(("  OK   " if condicao else "  FALHA") + f"  {descricao}")
    if not condicao:
        falhas.append(descricao)


# ─────────────────────────────────────────────────────────────────────────────
# Dublês
# ─────────────────────────────────────────────────────────────────────────────

URL_FICHA = "https://x/ficha"
URL_ANUNCIOS = "https://x/anuncios"        # simula falha do provider de LLM
URL_COMENTARIOS = "https://x/comentarios"  # simula saída inválida do LLM
URL_SEM_HTML = "https://x/fora-do-ar"      # simula falha de fetch (página indisponível)
URL_ETAPA_NAV = "https://x/nav"            # etapa de navegação (ultima=False)

HTML_POR_URL = {
    URL_FICHA: "<html>MARCADOR_FICHA<body><table></table></body></html>",
    URL_ANUNCIOS: "<html>MARCADOR_FALHA_PROVIDER<body></body></html>",
    URL_COMENTARIOS: "<html>MARCADOR_SAIDA_INVALIDA<body></body></html>",
    URL_ETAPA_NAV: "<html>MARCADOR_ETAPA_NAV<body></body></html>",
}

# Vocabulário esperado pro dublê de classificar_filtros — nomes de parâmetro
# → papel; qualquer nome fora daqui cai em "outro" (mesmo default do prompt real).
PAPEL_ESPERADO = {"fabricante": "marca", "varnome": "modelo"}


async def fake_buscar_html(url, **kwargs):
    return HTML_POR_URL.get(url)  # None para URL_SEM_HTML — mesmo contrato de falha do browser.py real


class FakeAgente:
    """Decide a resposta a partir de um marcador embutido no HTML
    simplificado recebido (descoberta) — ou do formato do `entrada` quando
    não há HTML nenhum (resumo de confirmação, classificação de filtros).
    Não faz nenhuma inferência de verdade, só exercita os desfechos
    possíveis de `Agente.run()`."""

    def __init__(self, provider=None, model=None, temperature=0.0, max_tokens=None):
        self.provider_nome = provider or "groq"
        self._model = model or "modelo-padrao"

    def descrever(self):
        return {"provider": self.provider_nome, "model": self._model}

    async def run(self, entrada, prompt=None, output_schema=None, output_format="json"):
        if isinstance(entrada, dict) and "parametros" in entrada:
            saida = {
                "parametros": [
                    {"nome_param": p["nome_param"], "papel": PAPEL_ESPERADO.get(p["nome_param"], "outro")}
                    for p in entrada["parametros"]
                ],
            }
            return AgentResult(success=True, provider=self.provider_nome, model=self._model, output=saida)

        if isinstance(entrada, dict):
            # gerar_resumo_confirmacao manda {"objetivo","amostra","total_encontrado"}
            total = entrada.get("total_encontrado", 0)
            return AgentResult(success=True, provider=self.provider_nome, model=self._model,
                                output=f"Encontrados {total} registro(s) na amostra do objetivo '{entrada.get('objetivo')}'.")

        if "MARCADOR_FALHA_PROVIDER" in entrada:
            return AgentResult.falha(self.provider_nome, self._model, "ProviderError", "falha simulada do provider")

        if "MARCADOR_SAIDA_INVALIDA" in entrada:
            # sucesso do provider, mas o JSON não bate com o Seletor* esperado
            # (falta linha_atributo/nome_atributo/valor_atributo obrigatórios)
            return AgentResult(success=True, provider=self.provider_nome, model=self._model,
                                output={"algum_campo_errado": "x"})

        if "MARCADOR_FICHA" in entrada:
            return AgentResult(success=True, provider=self.provider_nome, model=self._model, output={
                "marca": None, "modelo": None, "versao": None, "ano": None,
                "linha_atributo": "tr", "nome_atributo": "th", "valor_atributo": "td",
            })

        if "MARCADOR_ETAPA_NAV" in entrada:
            return AgentResult(success=True, provider=self.provider_nome, model=self._model, output={
                "linha": ".item", "campos": {"url_ficha": ".link"},
            })

        return AgentResult.falha(self.provider_nome, self._model, "UnexpectedError", "marcador desconhecido")


class FakeAgenteSempreFalha:
    """Só pra testar que `gerar_resumo_confirmacao` nunca levanta mesmo
    quando o provider falha de verdade — vira frase de aviso, não exceção."""

    def __init__(self, provider=None, model=None, temperature=0.0, max_tokens=None):
        self.provider_nome = provider or "groq"
        self._model = model or "modelo-padrao"

    async def run(self, entrada, prompt=None, output_schema=None, output_format="json"):
        return AgentResult.falha(self.provider_nome, self._model, "ProviderError", "falha simulada do provider")


def _mapa_uma_etapa(objetivo: TipoObjetivoScraper, url: str) -> MapaSeletor:
    return MapaSeletor(objetivo=objetivo, etapas=[EtapaScraper(ordem=0, url_amostra=url)])


def site_padrao() -> SiteScraper:
    return SiteScraper(
        name="Site Teste",
        url_base="https://x",
        objetivos=[TipoObjetivoScraper.FICHA_TECNICA, TipoObjetivoScraper.ANUNCIOS, TipoObjetivoScraper.COMENTARIOS],
        amostra_urls={"ficha_tecnica": URL_FICHA, "anuncios": URL_ANUNCIOS, "comentarios": URL_COMENTARIOS},
        seletores={
            "ficha_tecnica": _mapa_uma_etapa(TipoObjetivoScraper.FICHA_TECNICA, URL_FICHA),
            "anuncios": _mapa_uma_etapa(TipoObjetivoScraper.ANUNCIOS, URL_ANUNCIOS),
            "comentarios": _mapa_uma_etapa(TipoObjetivoScraper.COMENTARIOS, URL_COMENTARIOS),
        },
        llm=ConfigLLM(),
    )


def principal() -> int:
    discovery.browser.buscar_html = fake_buscar_html
    discovery.Agente = FakeAgente

    print("=== 1. descobrir_site: 3 objetivos, 3 desfechos diferentes, isolados entre si ===")

    resultados = asyncio.run(discovery.descobrir_site(site_padrao()))

    ficha = resultados[TipoObjetivoScraper.FICHA_TECNICA][0]
    checar(isinstance(ficha, EtapaScraper), "ficha técnica: descoberta bem sucedida (EtapaScraper)")
    if isinstance(ficha, EtapaScraper):
        checar(ficha.selectors.get("linha_atributo") == "tr", "seletores devolvidos batem com a saída do LLM")
        checar(ficha.llm == {"provider": "groq", "model": "modelo-padrao"}, "EtapaScraper rastreia qual provider/model gerou")
        checar(ficha.descoberto_em is not None, "descoberto_em preenchido")
        checar(ficha.validado_em is None, "validado_em SÓ é preenchido depois de um /test — discovery não valida sozinho")

    anuncios = resultados[TipoObjetivoScraper.ANUNCIOS][0]
    checar(isinstance(anuncios, ErroStep), "anúncios: falha do provider isolada (ErroStep), não derruba a descoberta inteira")
    if isinstance(anuncios, ErroStep):
        checar(anuncios.error_type == "ProviderError", "erro do provider repassado com o tipo correto")

    comentarios = resultados[TipoObjetivoScraper.COMENTARIOS][0]
    checar(isinstance(comentarios, ErroStep), "comentários: saída fora do schema também isolada (ErroStep)")
    if isinstance(comentarios, ErroStep):
        checar(comentarios.error_type == "OutputParseError",
               "ValidationError do Pydantic nunca escapa — vira OutputParseError tipado")
        checar(comentarios.retryable is True, "OutputParseError marcado como retryable (reexecutar costuma resolver)")

    # ficha técnica não é afetada pelos dois objetivos que falharam ao lado
    checar(isinstance(ficha, EtapaScraper), "falha em anúncios/comentários não retroage sobre o resultado de ficha técnica")

    print("\n=== 2. Etapa de NAVEGAÇÃO (ultima=False): valida contra SeletorEtapaIntermediaria ===")

    etapa_nav = EtapaScraper(ordem=0, url_amostra=URL_ETAPA_NAV)
    resultado_nav = asyncio.run(discovery.descobrir_etapa(
        site_padrao(), TipoObjetivoScraper.FICHA_TECNICA, etapa_nav, ultima=False,
    ))
    checar(isinstance(resultado_nav, EtapaScraper), "etapa de navegação descoberta com sucesso")
    if isinstance(resultado_nav, EtapaScraper):
        checar(resultado_nav.selectors.get("linha") == ".item", "seletor 'linha' bate com SeletorEtapaIntermediaria")
        checar(resultado_nav.selectors.get("campos", {}).get("url_ficha") == ".link",
               "campo livre 'url_ficha' capturado em 'campos'")

    print("\n=== 3. Página fora do ar (fetch falha): erro tipado, sem exceção ===")

    etapa_sem_pagina = EtapaScraper(ordem=0, url_amostra=URL_SEM_HTML)
    resultado_sem_pagina = asyncio.run(discovery.descobrir_etapa(
        site_padrao(), TipoObjetivoScraper.FICHA_TECNICA, etapa_sem_pagina, ultima=True,
    ))
    checar(isinstance(resultado_sem_pagina, ErroStep), "fetch falhando devolve ErroStep, não levanta exceção")
    if isinstance(resultado_sem_pagina, ErroStep):
        checar(resultado_sem_pagina.error_type == "FetchError", "erro tipado como FetchError")
        checar(resultado_sem_pagina.retryable is True, "FetchError marcado como retryable")

    print("\n=== 4. Sem URL de amostra / sem etapa nenhuma: erro tipado, sem exceção ===")

    site_sem_url = site_padrao()
    site_sem_url.seletores["ficha_tecnica"] = MapaSeletor(
        objetivo=TipoObjetivoScraper.FICHA_TECNICA, etapas=[EtapaScraper(ordem=0, url_amostra="")],
    )
    resultados_sem_url = asyncio.run(discovery.descobrir_site(site_sem_url, [TipoObjetivoScraper.FICHA_TECNICA]))
    resultado = resultados_sem_url[TipoObjetivoScraper.FICHA_TECNICA][0]
    checar(isinstance(resultado, ErroStep) and resultado.error_type == "MissingSampleUrl",
           "etapa sem url_amostra falha com MissingSampleUrl, não uma exceção genérica")

    site_sem_etapas = site_padrao()
    site_sem_etapas.seletores["ficha_tecnica"] = MapaSeletor(objetivo=TipoObjetivoScraper.FICHA_TECNICA, etapas=[])
    resultados_sem_etapas = asyncio.run(discovery.descobrir_site(site_sem_etapas, [TipoObjetivoScraper.FICHA_TECNICA]))
    resultado2 = resultados_sem_etapas[TipoObjetivoScraper.FICHA_TECNICA][0]
    checar(isinstance(resultado2, ErroStep) and resultado2.error_type == "MissingEtapas",
           "objetivo sem etapa nenhuma falha com MissingEtapas, não uma exceção genérica")

    print("\n=== 5. gerar_resumo_confirmacao: resumo em linguagem natural pra tela de teste ===")

    linhas_amostra = [{"atributo": "Motor", "valor": "2.0 Turbo"}, {"atributo": "Potência", "valor": "170cv"}]

    resumo_vazio = asyncio.run(discovery.gerar_resumo_confirmacao(
        TipoObjetivoScraper.FICHA_TECNICA, [], ConfigLLM(),
    ))
    checar("Nenhum dado" in resumo_vazio, "sem linhas extraídas, nem chama a IA — devolve aviso direto")

    resumo_sucesso = asyncio.run(discovery.gerar_resumo_confirmacao(
        TipoObjetivoScraper.FICHA_TECNICA, linhas_amostra, ConfigLLM(),
    ))
    checar("2 registro" in resumo_sucesso, f"resumo da IA reflete a amostra recebida (obtido: {resumo_sucesso!r})")

    discovery.Agente = FakeAgenteSempreFalha
    resumo_falha = asyncio.run(discovery.gerar_resumo_confirmacao(
        TipoObjetivoScraper.FICHA_TECNICA, linhas_amostra, ConfigLLM(),
    ))
    checar("Não foi possível gerar" in resumo_falha,
           f"falha do provider vira frase de aviso, nunca uma exceção (obtido: {resumo_falha!r})")
    discovery.Agente = FakeAgente  # restaura pro resto do módulo

    print("\n=== 6. classificar_filtros/detectar_filtros: parâmetros de URL viram filtros ===")

    url_com_filtros = "https://www.carrosnaweb.com.br/catalogo.asp?fabricante=Ford&varnome=Ranger&ordem=1"
    resultado_filtros = asyncio.run(discovery.detectar_filtros(url_com_filtros, ConfigLLM()))
    checar(not isinstance(resultado_filtros, ErroStep), "detectar_filtros roda sem erro")
    if not isinstance(resultado_filtros, ErroStep):
        filtros, template = resultado_filtros
        papeis = {f.nome_param: f.papel for f in filtros}
        checar(papeis.get("fabricante") == "marca", "parâmetro 'fabricante' classificado como 'marca'")
        checar(papeis.get("varnome") == "modelo", "parâmetro 'varnome' classificado como 'modelo'")
        checar(papeis.get("ordem") == "outro", "parâmetro fora do vocabulário cai em 'outro'")
        checar("{marca}" in template and "{modelo}" in template,
               f"template usa os marcadores de papel certos (obtido: {template!r})")

    sem_query, template_sem_query = asyncio.run(discovery.detectar_filtros("https://x/sem-query", ConfigLLM()))
    checar(sem_query == [] and template_sem_query == "https://x/sem-query",
           "sem query string, não chama a IA — devolve vazio/URL original direto")

    print("\n" + "=" * 62)
    if falhas:
        print(f"{len(falhas)} VERIFICAÇÃO(ÕES) FALHARAM:")
        for f in falhas:
            print(f"  - {f}")
    else:
        print("Todas as verificações da descoberta do Scraper Mode passaram.")
    print("=" * 62)
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(principal())
