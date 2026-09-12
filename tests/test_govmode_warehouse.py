"""
test_govmode_warehouse.py — verifica o warehouse do catálogo FIPE
(src/govmode/) sem tocar rede nem credencial real: injeta um cliente falso
no lugar do `bigquery.Client()`, mesmo espírito de
tests/test_analytics_warehouse.py.

Cobre especificamente o que é novo/diferente do warehouse do YouTube:
  1. `BigQueryWarehouse` (src/bigquery_warehouse.py) é genérica — dataset
     separado, sem nada hardcoded do YouTube.
  2. `write_disposition` por tabela (TRUNCATE para catálogos completos,
     APPEND para o que cresce incrementalmente) é respeitado.
  3. A normalização de TIMESTAMP (bug real corrigido no warehouse do
     YouTube) é reaproveitada aqui também, não duplicada/perdida.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.config as config

TEMP = Path(tempfile.mkdtemp(prefix="fordfiap_govmode_"))
config.DATA_DIR = TEMP / "data"

import pandas as pd
from google.api_core.exceptions import NotFound
from google.cloud.bigquery import _pandas_helpers

from src.bigquery_warehouse import BigQueryWarehouse
from src.govmode import schemas

falhas = []


def checar(condicao, descricao):
    print(("  OK   " if condicao else "  FALHA") + f"  {descricao}")
    if not condicao:
        falhas.append(descricao)


class ClienteFalso:
    project = "projeto-teste"

    def __init__(self):
        self.datasets_criados = []
        self.tabelas_criadas = []
        self.datasets_existentes = set()
        self.tabelas_existentes = set()
        self.cargas = []  # (table_id, dataframe, job_config)

    def get_dataset(self, dataset_id):
        if dataset_id not in self.datasets_existentes:
            raise NotFound(f"dataset {dataset_id} não existe")
        return dataset_id

    def create_dataset(self, dataset):
        self.datasets_criados.append(str(dataset.reference))
        self.datasets_existentes.add(str(dataset.reference))

    def get_table(self, table_id):
        if table_id not in self.tabelas_existentes:
            raise NotFound(f"tabela {table_id} não existe")
        return table_id

    def create_table(self, table):
        self.tabelas_criadas.append(str(table.reference))
        self.tabelas_existentes.add(str(table.reference))

    def load_table_from_dataframe(self, df, table_id, job_config=None):
        self.cargas.append((table_id, df, job_config))

        class JobFalso:
            def result(self_inner):
                return None

        return JobFalso()


def principal() -> int:
    print("=== 1. BigQueryWarehouse: genérica, dataset separado do YouTube ===")

    cliente = ClienteFalso()
    wh = BigQueryWarehouse(dataset="analytics_ford")
    wh._client = cliente

    checar(wh.dataset == "analytics_ford", "dataset é o do catálogo FIPE, não youtube_analytics")
    checar(wh.dataset_id() == "projeto-teste.analytics_ford", "dataset_id monta certo com o project do client falso")

    table_id = wh.preparar_tabela(schemas.MARCAS_VEICULOS, schemas.SCHEMA_MARCAS_VEICULOS)
    checar(len(cliente.datasets_criados) == 1, "dataset criado na primeira chamada")
    checar(len(cliente.tabelas_criadas) == 1, "tabela criada na primeira chamada")

    wh.preparar_tabela(schemas.MARCAS_VEICULOS, schemas.SCHEMA_MARCAS_VEICULOS)
    checar(len(cliente.tabelas_criadas) == 1, "segunda chamada não recria a tabela (idempotente)")

    print("\n=== 2. write_disposition por tabela: TRUNCATE vs APPEND ===")

    linha_marca = {
        "tipo_veiculo": "carros", "marca_nome": "Ford", "marca_codigo": "22",
        "atualizado_em": "2026-01-01T00:00:00+00:00",
    }
    wh.upsert(
        schemas.MARCAS_VEICULOS, [linha_marca],
        schema=schemas.SCHEMA_MARCAS_VEICULOS,
        write_disposition=schemas.WRITE_DISPOSITION[schemas.MARCAS_VEICULOS],
    )
    _, _, job_config_marcas = cliente.cargas[-1]
    checar(job_config_marcas.write_disposition == "WRITE_TRUNCATE",
           "marcas_veiculos grava com WRITE_TRUNCATE (catálogo completo recarregado)")

    linha_modelo = {
        "tipo_veiculo": "carros", "marca_codigo": "22", "modelo_codigo": "9163",
        "modelo_nome": "TERRITORY", "atualizado_em": "2026-01-01T00:00:00+00:00",
    }
    wh.upsert(
        schemas.MODELOS_VEICULOS, [linha_modelo],
        schema=schemas.SCHEMA_MODELOS_VEICULOS,
        write_disposition=schemas.WRITE_DISPOSITION[schemas.MODELOS_VEICULOS],
    )
    _, _, job_config_modelos = cliente.cargas[-1]
    checar(job_config_modelos.write_disposition == "WRITE_APPEND",
           "modelos_veiculos grava com WRITE_APPEND (cresce incrementalmente)")

    linha_fipe = {
        "tipo_veiculo": "carros", "marca_codigo": "22", "modelo_codigo": "9163",
        "modelo_nome": "TERRITORY", "ano_codigo": "2026-1", "ano_modelo": "2026 Gasolina",
        "codigo_fipe": "003095-3", "atualizado_em": "2026-01-01T00:00:00+00:00",
    }
    wh.upsert(
        schemas.MODELOS_COM_FIPE, [linha_fipe],
        schema=schemas.SCHEMA_MODELOS_COM_FIPE,
        write_disposition=schemas.WRITE_DISPOSITION[schemas.MODELOS_COM_FIPE],
    )
    _, df_fipe, job_config_fipe = cliente.cargas[-1]
    checar(job_config_fipe.write_disposition == "WRITE_APPEND",
           "modelos_com_fipe grava com WRITE_APPEND (um registro por modelo+ano, cresce)")
    checar(str(df_fipe["atualizado_em"].dtype).startswith("datetime64"),
           "atualizado_em (TIMESTAMP) foi normalizado de string pra datetime real — mesma correção do YouTube reaproveitada")

    print("\n=== 3. Conversão pandas→arrow funciona de verdade pras 4 tabelas do catálogo ===")

    for nome, linha in (
        (schemas.MARCAS_VEICULOS, linha_marca),
        (schemas.MODELOS_VEICULOS, linha_modelo),
        (schemas.MODELOS_COM_FIPE, linha_fipe),
        (schemas.REFERENCIA_MESES_FIPE, {
            "codigo": 332, "descricao_original": "abril/2026", "ano": 2026,
            "mes": "04", "mes_abrev": "abr",
        }),
    ):
        from src.bigquery_warehouse import normalizar_tipos_timestamp
        df = normalizar_tipos_timestamp(pd.DataFrame([linha]), schemas.TABELAS[nome])
        try:
            _pandas_helpers.dataframe_to_arrow(df, schemas.TABELAS[nome])
            checar(True, f"{nome}: schema converte pra arrow sem erro")
        except Exception as exc:
            checar(False, f"{nome}: FALHOU na conversão arrow — {exc}")

    print("\n" + "=" * 62)
    if falhas:
        print(f"{len(falhas)} VERIFICAÇÃO(ÕES) FALHARAM:")
        for f in falhas:
            print(f"  - {f}")
    else:
        print("Todas as verificações do warehouse do Gov Mode passaram.")
    print("=" * 62)
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(principal())
