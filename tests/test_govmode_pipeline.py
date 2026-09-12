"""
test_govmode_pipeline.py — verifica src/govmode/store.py e
src/govmode/executor.py (persistência de pipelines/execuções + orquestração
em background), sem tocar BigQuery real: usa o mesmo dublê de warehouse/API
de test_govmode_engine.py.

Roda inteiramente contra um diretório de dados temporário (mesmo padrão de
tests/test_pipeline.py) — nunca toca `data/gov_pipelines.json`/
`data/gov_runs.json` reais.
"""
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.config as config

TEMP = Path(tempfile.mkdtemp(prefix="fordfiap_govmode_pipeline_"))
config.DATA_DIR = TEMP / "data"

import pandas as pd

from src.govmode import engine as govmode_engine
from src.govmode import executor, schemas, store
from src.govmode.models import (
    FonteGovFipe,
    GovPipelineEntrada,
    PassoGovPipeline,
    StatusPipeline,
    TipoStepGov,
)
from src.pipelines.models import ConfigSchedule, StatusRun

falhas = []


def checar(condicao, descricao):
    print(("  OK   " if condicao else "  FALHA") + f"  {descricao}")
    if not condicao:
        falhas.append(descricao)


class FakeWarehouse:
    def __init__(self):
        self.tabelas = {nome: [] for nome in schemas.TABELAS}

    def table_id(self, nome):
        return f"projeto-teste.analytics_ford.{nome}"

    def upsert(self, nome, linhas):
        if not linhas:
            return
        if schemas.WRITE_DISPOSITION.get(nome) == "WRITE_TRUNCATE":
            self.tabelas[nome] = list(linhas)
        else:
            self.tabelas[nome].extend(linhas)

    def query(self, sql, params=None):
        return pd.DataFrame()  # runs deste teste não dependem de dedup fino


class FakeFipeAPI:
    token_configurado = True

    async def buscar_marcas(self, tipo_veiculo):
        return [{"nome": "Ford", "codigo": "22"}]

    async def buscar_modelos(self, tipo_veiculo, marca_codigo):
        return [{"nome": "KA", "codigo": "1"}]

    async def buscar_anos(self, tipo_veiculo, marca_codigo, modelo_codigo):
        return [{"nome": "2026 Gasolina", "codigo": "2026-1"}]

    async def buscar_codigo_fipe(self, tipo_veiculo, marca_codigo, modelo_codigo, ano_codigo):
        return {"CodigoFipe": "001234-5"}

    async def buscar_meses_referencia(self):
        return [{"codigo": 332, "mes": "abril/2026"}]


def principal() -> int:
    govmode_engine.PAUSA_ENTRE_CHAMADAS = 0.001

    print("=== 1. store: CRUD de pipelines + versionamento ===")

    entrada = GovPipelineEntrada(
        name="Extração FIPE Ford",
        description="marcas/modelos/fipe da Ford",
        source=FonteGovFipe(tipo_veiculo="carros", marcas_alvo=["Ford"]),
        steps=[
            PassoGovPipeline(type=TipoStepGov.MARCAS),
            PassoGovPipeline(type=TipoStepGov.MODELOS),
            PassoGovPipeline(type=TipoStepGov.FIPE),
            PassoGovPipeline(type=TipoStepGov.REFERENCIA_MESES),
        ],
        schedule=ConfigSchedule(mode="manual"),
    )
    pipeline = store.criar_pipeline(entrada)
    checar(pipeline.version == 1, "pipeline criada na versão 1")
    checar(len(store.listar_pipelines()) == 1, "pipeline aparece na listagem")

    # só nome/descrição — não consome versão
    entrada_renome = entrada.model_copy(update={"name": "Extração FIPE Ford (renomeada)"})
    atualizada = store.atualizar_pipeline(pipeline.id, entrada_renome)
    checar(atualizada.version == 1, "renomear não cria versão nova")

    # mudar a fonte — consome versão
    entrada_nova_fonte = entrada.model_copy(
        update={"source": FonteGovFipe(tipo_veiculo="carros", marcas_alvo=["Ford", "Fiat"])}
    )
    atualizada2 = store.atualizar_pipeline(pipeline.id, entrada_nova_fonte)
    checar(atualizada2.version == 2, "mudar marcas_alvo cria versão nova")
    checar(len(atualizada2.versions) >= 1, "versão anterior arquivada")

    checar(store.remover_pipeline("id-que-nao-existe") is False, "remover id inexistente devolve False")
    checar(store.remover_pipeline(pipeline.id) is True, "remover pipeline existente devolve True")
    checar(store.obter_pipeline(pipeline.id) is None, "pipeline removida não aparece mais")

    print("\n=== 2. executor.executar_agora: roda de ponta a ponta e persiste o run ===")

    fake_wh = FakeWarehouse()
    govmode_engine.warehouse.query = fake_wh.query
    govmode_engine.warehouse.upsert = fake_wh.upsert
    govmode_engine.warehouse.table_id = fake_wh.table_id
    govmode_engine.warehouse.garantir_tabelas = lambda: None
    # executor.py sempre instancia GovEngine() sem argumentos — troca o
    # cliente FIPE padrão pelo dublê pra não bater na API real.
    govmode_engine.FipeAPI = FakeFipeAPI

    pipeline2 = store.criar_pipeline(entrada)
    config_v = pipeline2.na_versao(None)

    run = asyncio.run(executor.executar_agora(
        config_v, pipeline_id=pipeline2.id, pipeline_name=pipeline2.name, pipeline_version=config_v.version,
    ))

    checar(run.status == StatusRun.OK, f"execução concluiu com sucesso (status={run.status.value})")
    checar(store.obter_run(run.id) is not None, "run persistido no histórico (data/gov_runs.json)")

    pipeline2_atualizada = store.obter_pipeline(pipeline2.id)
    checar(pipeline2_atualizada.last_run_status == "success", "last_run_status da pipeline atualizado")
    checar(pipeline2_atualizada.last_run_at is not None, "last_run_at da pipeline preenchido")

    print("\n=== 3. executor.iniciar_em_background + polling por run_id ===")

    # iniciar_em_background usa asyncio.create_task — precisa de um loop já
    # rodando, igual acontece de verdade (chamado de dentro de um handler
    # async do FastAPI). Por isso início + polling ficam na mesma corrotina.
    async def rodar_em_background_e_esperar():
        run_bg = executor.iniciar_em_background(
            config_v, pipeline_id=pipeline2.id, pipeline_name=pipeline2.name, pipeline_version=config_v.version,
        )
        checar(run_bg.status.value == "pending", "run em background começa 'pending' e devolve na hora")
        checar(len(run_bg.steps) == 4, "régua de progresso já vem com as 4 etapas, mesmo antes de rodar")

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
        print("Todas as verificações de pipeline/store/executor do Gov Mode passaram.")
    print("=" * 62)
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(principal())
