"""
bigquery_warehouse.py
Classe genérica de acesso ao BigQuery — dataset/tabela idempotentes,
upsert (append ou truncate) e query parametrizada. Extraída da
implementação original do warehouse do YouTube (`src/analytics/
warehouse.py`) para ser reaproveitada por qualquer domínio de dado que
precise do mesmo padrão — hoje: `src/govmode/warehouse.py` (dataset
`analytics_ford`, o mesmo do notebook `tabela_marcas.ipynb`).

`src/analytics/warehouse.py` **não foi migrado por inteiro** pra usar esta
classe — é código já em produção com testes passando (docs/SPEC.md seções
12.7-12.8 documentam bugs reais já corrigidos ali); só a normalização de
tipos (`normalizar_tipos_timestamp`) foi extraída pra cá e é importada de
lá, pra não duplicar essa correção específica em dois lugares.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
from google.api_core.exceptions import NotFound
from google.cloud import bigquery

logger = logging.getLogger(__name__)


def agora_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _valor_serializavel(valor: Any) -> Any:
    """
    Campos REPEATED do BigQuery voltam de `to_dataframe()` como
    `numpy.ndarray`, e TIMESTAMP como `pandas.Timestamp`/`NaT` — nenhum dos
    dois `json.dumps`/`jsonable_encoder` (FastAPI) sabem serializar sozinho
    (achado real: quebrava com um erro obscuro toda vez que uma linha com
    REPEATED preenchido aparecia numa resposta de API). Normaliza pro
    equivalente nativo do Python.
    """
    if isinstance(valor, np.ndarray):
        return valor.tolist()
    if isinstance(valor, np.integer):
        return int(valor)
    if isinstance(valor, np.floating):
        return None if np.isnan(valor) else float(valor)
    if isinstance(valor, pd.Timestamp):
        return valor.isoformat()
    if valor is pd.NaT:
        return None
    return valor


def registros_serializaveis(df: pd.DataFrame) -> list[dict]:
    """Converte o resultado de `BigQueryWarehouse.query()` numa lista de
    dicts seguros pra devolver como resposta de API (ver `_valor_serializavel`)."""
    df = df.where(df.notnull(), None)
    registros = df.to_dict(orient="records")
    return [{chave: _valor_serializavel(valor) for chave, valor in registro.items()} for registro in registros]


def normalizar_tipos_timestamp(df: pd.DataFrame, schema: list[bigquery.SchemaField]) -> pd.DataFrame:
    """
    Converte colunas TIMESTAMP de string ISO 8601 para datetime real.

    Achado real (docs/SPEC.md seção 12.8): uma coluna `object` de strings
    não converte sozinha para TIMESTAMP na conversão pandas→arrow que
    `load_table_from_dataframe` faz por baixo — e o load inteiro falha com
    `ArrowTypeError`, **antes de qualquer chamada de rede**.
    """
    for campo in schema:
        if campo.field_type == "TIMESTAMP" and campo.name in df.columns:
            df[campo.name] = pd.to_datetime(df[campo.name], utc=True, errors="coerce")
    return df


class BigQueryWarehouse:
    """
    Um warehouse = um dataset. `project_id=None` deixa `bigquery.Client()`
    resolver o projeto pelas credenciais ativas (ADC) — mesmo comportamento
    do notebook `tabela_marcas.ipynb`.

    Cliente resolvido de forma lazy (só na primeira gravação/consulta real,
    nunca no `__init__`) — mesmo cuidado que `YouTubeAPI`/`TranscriptAPI` já
    têm com o proxy pool (docs/SPEC.md seção 8): resolver credencial pode
    falhar ou envolver rede, e isso nunca pode acontecer na hora de só
    instanciar o objeto.
    """

    def __init__(self, dataset: str, project_id: str | None = None):
        self.dataset = dataset
        self.project_id = project_id
        self._client: bigquery.Client | None = None
        self._tabelas_prontas: set[str] = set()
        self._ultimo_erro: dict[str, dict[str, str]] | None = None

    def status(self, configurado: bool) -> dict[str, Any]:
        """`ultimo_erro` é um mapa `{nome_da_tabela: {"quando", "mensagem"}}`
        — vazio/None quando todas as tabelas gravaram com sucesso na última
        tentativa de cada uma. Nunca inclui a exceção crua nem a credencial."""
        return {
            "configurado": configurado,
            "ultimo_erro": self._ultimo_erro or None,
        }

    def obter_client(self) -> bigquery.Client:
        if self._client is None:
            kwargs: dict[str, Any] = {}
            if self.project_id:
                kwargs["project"] = self.project_id
            self._client = bigquery.Client(**kwargs)
        return self._client

    def dataset_id(self) -> str:
        return f"{self.obter_client().project}.{self.dataset}"

    def table_id(self, nome: str) -> str:
        return f"{self.dataset_id()}.{nome}"

    def preparar_tabela(self, nome: str, schema: list[bigquery.SchemaField]) -> str:
        """Idempotente — cria dataset/tabela só na primeira vez que são usados."""
        client = self.obter_client()
        table_id = self.table_id(nome)
        if table_id in self._tabelas_prontas:
            return table_id

        dataset_id = self.dataset_id()
        try:
            client.get_dataset(dataset_id)
        except NotFound:
            client.create_dataset(bigquery.Dataset(dataset_id))
            logger.info("[bigquery_warehouse] dataset %s criado.", dataset_id)

        try:
            client.get_table(table_id)
        except NotFound:
            client.create_table(bigquery.Table(table_id, schema=schema))
            logger.info("[bigquery_warehouse] tabela %s criada.", table_id)

        self._tabelas_prontas.add(table_id)
        return table_id

    def upsert(
        self,
        nome: str,
        linhas: list[dict],
        schema: list[bigquery.SchemaField],
        write_disposition: str = "WRITE_APPEND",
    ) -> None:
        """
        Grava `linhas` na tabela `nome`. `write_disposition`:
        - `WRITE_APPEND` (padrão) — soma linhas, nunca trunca.
        - `WRITE_TRUNCATE` — substitui o conteúdo inteiro; usado por
          tabelas de catálogo que são sempre recarregadas por completo
          (ex.: lista de marcas de veículos).

        Quem chama deve isolar cada tabela em seu próprio try/except — uma
        falha aqui não deve impedir outras tabelas de serem gravadas (bug
        real já visto quando isso não acontecia — docs/SPEC.md seção 12.8).
        Esta função só registra o erro em `status()` antes de relançar.
        """
        if not linhas:
            return

        try:
            client = self.obter_client()
            table_id = self.preparar_tabela(nome, schema)

            df = normalizar_tipos_timestamp(pd.DataFrame(linhas), schema)
            job_config = bigquery.LoadJobConfig(schema=schema, write_disposition=write_disposition)
            job = client.load_table_from_dataframe(df, table_id, job_config=job_config)
            job.result()
        except Exception as exc:
            if self._ultimo_erro is None:
                self._ultimo_erro = {}
            self._ultimo_erro[nome] = {"quando": agora_iso(), "mensagem": f"{type(exc).__name__}: {exc}"}
            raise

        if self._ultimo_erro is not None:
            self._ultimo_erro.pop(nome, None)

    def query(self, sql: str, params: list[bigquery.ScalarQueryParameter] | None = None) -> pd.DataFrame:
        """
        `client.query(sql, job_config=...).to_dataframe()`. `params` vira
        `@nome` na query (query parametrizada) — nunca interpolar filtro
        vindo do usuário direto na string SQL (injeção); nomes de
        tabela/dataset são constantes internas, esses sim podem entrar por
        f-string.
        """
        job_config = bigquery.QueryJobConfig(query_parameters=params or [])
        return self.obter_client().query(sql, job_config=job_config).to_dataframe()
