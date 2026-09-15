"""
test_scraper_warehouse.py — verifica o warehouse do Scraper Mode
(src/scraper/) sem tocar rede nem credencial real: injeta um cliente falso
no lugar do `bigquery.Client()`, mesmo espírito de
tests/test_govmode_warehouse.py.

Cobre:
  1. `BigQueryWarehouse` genérica, dataset separado do Gov Mode/YouTube.
  2. As 3 tabelas gravam com WRITE_APPEND (são logs históricos, nunca
     recarregadas por inteiro).
  3. Conversão pandas→arrow funciona de verdade pras 3 tabelas (schema
     TIMESTAMP normalizado corretamente, mesma correção reaproveitada).
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.config as config

TEMP = Path(tempfile.mkdtemp(prefix="fordfiap_scraper_warehouse_"))
config.DATA_DIR = TEMP / "data"

import pandas as pd
from google.api_core.exceptions import NotFound
from google.cloud.bigquery import _pandas_helpers

from src.bigquery_warehouse import BigQueryWarehouse
from src.scraper import schemas

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
        self.cargas = []

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
    print("=== 1. BigQueryWarehouse: genérica, dataset separado do Gov Mode/YouTube ===")

    cliente = ClienteFalso()
    wh = BigQueryWarehouse(dataset="scraper_ford")
    wh._client = cliente

    checar(wh.dataset == "scraper_ford", "dataset é o do Scraper Mode")
    checar(wh.dataset_id() == "projeto-teste.scraper_ford", "dataset_id monta certo com o project do client falso")

    table_id = wh.preparar_tabela(schemas.FICHA_TECNICA, schemas.SCHEMA_FICHA_TECNICA)
    checar(len(cliente.datasets_criados) == 1, "dataset criado na primeira chamada")
    checar(len(cliente.tabelas_criadas) == 1, "tabela criada na primeira chamada")

    wh.preparar_tabela(schemas.FICHA_TECNICA, schemas.SCHEMA_FICHA_TECNICA)
    checar(len(cliente.tabelas_criadas) == 1, "segunda chamada não recria a tabela (idempotente)")

    print("\n=== 2. As 3 tabelas gravam com WRITE_APPEND (logs históricos) ===")

    for nome in schemas.TABELAS:
        checar(schemas.WRITE_DISPOSITION[nome] == "WRITE_APPEND", f"{nome} grava com WRITE_APPEND")

    linha_ficha = {
        "site_id": "site_1", "site_nome": "Teste", "marca": "Ford", "modelo": "Ka",
        "versao": None, "ano": None, "atributo": "Motor", "valor": "1.5",
        "url_origem": "https://x/ficha", "coletado_em": "2026-01-01T00:00:00+00:00",
    }
    wh.upsert(schemas.FICHA_TECNICA, [linha_ficha], schema=schemas.SCHEMA_FICHA_TECNICA,
              write_disposition=schemas.WRITE_DISPOSITION[schemas.FICHA_TECNICA])
    _, df_ficha, job_config_ficha = cliente.cargas[-1]
    checar(job_config_ficha.write_disposition == "WRITE_APPEND", "ficha_tecnica grava com WRITE_APPEND")
    checar(str(df_ficha["coletado_em"].dtype).startswith("datetime64"),
           "coletado_em (TIMESTAMP) normalizado de string pra datetime real")

    print("\n=== 3. Conversão pandas→arrow funciona de verdade pras 3 tabelas ===")

    linha_anuncio = {
        "site_id": "site_1", "site_nome": "Teste", "marca": "Ford", "modelo": "Ka",
        "versao": None, "ano": "2024", "preco_texto": "R$ 50.000", "preco_numerico": 50000.0,
        "url_anuncio": "https://x/anuncio/1", "localizacao": None, "vendedor": None,
        "publicado_em": None, "coletado_em": "2026-01-01T00:00:00+00:00",
    }
    linha_comentario = {
        "site_id": "site_1", "site_nome": "Teste", "marca": "Ford", "modelo": "Ka",
        "versao": None, "ano": None, "autor": "Fulano", "texto": "Ótimo carro", "nota": 5.0,
        "publicado_em": None, "url_origem": "https://x/anuncio/1", "coletado_em": "2026-01-01T00:00:00+00:00",
    }

    from src.bigquery_warehouse import normalizar_tipos_timestamp

    for nome, linha in (
        (schemas.FICHA_TECNICA, linha_ficha),
        (schemas.ANUNCIOS, linha_anuncio),
        (schemas.COMENTARIOS, linha_comentario),
    ):
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
        print("Todas as verificações do warehouse do Scraper Mode passaram.")
    print("=" * 62)
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(principal())
