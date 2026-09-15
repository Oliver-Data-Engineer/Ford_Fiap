"""
test_scraper_pipeline.py — verifica src/scraper/store.py e
src/scraper/executor.py (persistência de sites/execuções + orquestração em
background), sem tocar rede/BigQuery real. Mesmo espírito de
tests/test_govmode_pipeline.py.

Roda inteiramente contra um diretório de dados temporário — nunca toca
`data/scraper_sites.json`/`data/scraper_runs.json` reais.
"""
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.config as config

TEMP = Path(tempfile.mkdtemp(prefix="fordfiap_scraper_pipeline_"))
config.DATA_DIR = TEMP / "data"

import pandas as pd

from src.pipelines.models import ConfigSchedule, StatusRun
from src.scraper import engine as scraper_engine
from src.scraper import executor, schemas, store
from src.scraper.models import MapaSeletor, SiteScraperEntrada, TipoObjetivoScraper

falhas = []


def checar(condicao, descricao):
    print(("  OK   " if condicao else "  FALHA") + f"  {descricao}")
    if not condicao:
        falhas.append(descricao)


class FakeWarehouse:
    def __init__(self):
        self.tabelas = {nome: [] for nome in schemas.TABELAS}

    def table_id(self, nome):
        return f"projeto-teste.scraper_ford.{nome}"

    def upsert(self, nome, linhas):
        if not linhas:
            return
        self.tabelas[nome].extend(linhas)

    def query(self, sql, params=None):
        return pd.DataFrame()  # runs deste teste não dependem de dedup fino


HTML_FICHA = "<table><tr><th>Motor</th><td>2.0</td></tr></table>"


async def fake_buscar_html(url, **kwargs):
    return HTML_FICHA


def entrada_padrao() -> SiteScraperEntrada:
    return SiteScraperEntrada(
        name="Site Teste",
        description="ficha técnica de exemplo",
        url_base="https://x",
        objetivos=[TipoObjetivoScraper.FICHA_TECNICA],
        amostra_urls={"ficha_tecnica": "https://x/ficha"},
        schedule=ConfigSchedule(mode="manual"),
    )


def principal() -> int:
    print("=== 1. store: CRUD de sites + versionamento ===")

    entrada = entrada_padrao()
    site = store.criar_site(entrada)
    checar(site.version == 1, "site criado na versão 1")
    checar(len(store.listar_sites()) == 1, "site aparece na listagem")
    checar(site.registrado is False, "site criado como rascunho (registrado=False)")

    # só nome/descrição — não consome versão
    entrada_renome = entrada.model_copy(update={"name": "Site Teste (renomeado)"})
    atualizado = store.atualizar_site(site.id, entrada_renome)
    checar(atualizado.version == 1, "renomear não cria versão nova")

    # simula um site já registrado com seletor validado, pra testar que
    # mudar a URL-alvo invalida o que já foi mapeado/testado
    site.seletores["ficha_tecnica"] = MapaSeletor(
        objetivo=TipoObjetivoScraper.FICHA_TECNICA,
        selectors={"linha_atributo": "tr", "nome_atributo": "th", "valor_atributo": "td"},
        url_amostra="https://x/ficha", validado_em="2026-01-01T00:00:00+00:00",
    )
    site.registrado = True
    store.salvar_site(site)

    entrada_nova_url = entrada.model_copy(update={"url_base": "https://outro-site.com"})
    atualizado2 = store.atualizar_site(site.id, entrada_nova_url)
    checar(atualizado2.version == 2, "mudar url_base cria versão nova")
    checar(len(atualizado2.versions) >= 1, "versão anterior arquivada")
    checar(atualizado2.registrado is False,
           "mudar a URL-alvo invalida o registro — seletores antigos não são confiáveis pra outro site")
    mapa_ficha = atualizado2.seletores.get("ficha_tecnica")
    checar(
        mapa_ficha is not None and len(mapa_ficha.etapas) == 1 and not mapa_ficha.etapas[0].validado_em,
        "seletores antigos descartados — reseeda uma etapa nova (não validada) pra URL atual",
    )

    checar(store.remover_site("id-que-nao-existe") is False, "remover id inexistente devolve False")
    checar(store.remover_site(site.id) is True, "remover site existente devolve True")
    checar(store.obter_site(site.id) is None, "site removido não aparece mais")

    print("\n=== 2. executor.disparar_site recusa site não registrado ===")

    site2 = store.criar_site(entrada_padrao())
    erro_capturado = None
    try:
        executor.disparar_site(site2.id)
    except ValueError as exc:
        erro_capturado = exc
    checar(erro_capturado is not None, "disparar_site levanta ValueError pra site ainda não registrado")

    print("\n=== 3. executor.executar_agora: roda de ponta a ponta e persiste o run ===")

    fake_wh = FakeWarehouse()
    scraper_engine.warehouse.query = fake_wh.query
    scraper_engine.warehouse.upsert = fake_wh.upsert
    scraper_engine.warehouse.table_id = fake_wh.table_id
    scraper_engine.warehouse.garantir_tabelas = lambda: None
    scraper_engine.browser.buscar_html = fake_buscar_html

    site2.seletores["ficha_tecnica"] = MapaSeletor(
        objetivo=TipoObjetivoScraper.FICHA_TECNICA,
        selectors={"linha_atributo": "tr", "nome_atributo": "th", "valor_atributo": "td"},
        url_amostra="https://x/ficha", validado_em="2026-01-01T00:00:00+00:00",
    )
    site2.registrado = True
    site2 = store.salvar_site(site2)
    config_v = site2.na_versao(None)

    run = asyncio.run(executor.executar_agora(
        config_v, site_id=site2.id, site_name=site2.name, site_version=config_v.version,
    ))

    checar(run.status == StatusRun.OK, f"execução concluiu com sucesso (status={run.status.value})")
    checar(store.obter_run(run.id) is not None, "run persistido no histórico (data/scraper_runs.json)")

    site2_atualizado = store.obter_site(site2.id)
    checar(site2_atualizado.last_run_status == "success", "last_run_status do site atualizado")
    checar(site2_atualizado.last_run_at is not None, "last_run_at do site preenchido")

    print("\n=== 4. executor.iniciar_em_background + polling por run_id ===")

    async def rodar_em_background_e_esperar():
        run_bg = executor.iniciar_em_background(
            config_v, site_id=site2.id, site_name=site2.name, site_version=config_v.version,
        )
        checar(run_bg.status.value == "pending", "run em background começa 'pending' e devolve na hora")
        checar(len(run_bg.steps) == 1, "régua de progresso já vem com a etapa configurada, mesmo antes de rodar")

        for _ in range(200):
            status = executor.obter_status(run_bg.id)
            if status and status.status.value in ("success", "partial", "failed"):
                return status
            await asyncio.sleep(0.02)
        return executor.obter_status(run_bg.id)

    status_final = asyncio.run(rodar_em_background_e_esperar())
    checar(status_final is not None and status_final.status == StatusRun.OK,
           f"execução em background termina OK (status={status_final.status.value if status_final else None})")

    print("\n" + "=" * 62)
    if falhas:
        print(f"{len(falhas)} VERIFICAÇÃO(ÕES) FALHARAM:")
        for f in falhas:
            print(f"  - {f}")
    else:
        print("Todas as verificações de pipeline/store/executor do Scraper Mode passaram.")
    print("=" * 62)
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(principal())
