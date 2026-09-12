"""
test_analytics_api.py — verifica src/api/analytics_api.py sem rede real:
substitui `warehouse.query` por uma função que devolve um DataFrame já
pronto, no formato que o BigQuery realmente devolve (numpy.ndarray para
campos REPEATED, pandas.Timestamp/NaT para TIMESTAMP).

Cobre a regressão de um bug real: `/analytics/comentarios` devolvia 500
("Object of type ndarray is not JSON serializable" / erro obscuro do
jsonable_encoder) toda vez que uma linha trazia `adjetivos` preenchido —
o Dashboard "não carregava dado nenhum" por causa disso.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json

import numpy as np
import pandas as pd

from src.api import analytics_api
from src.analytics import warehouse

falhas = []


def checar(condicao, descricao):
    print(("  OK   " if condicao else "  FALHA") + f"  {descricao}")
    if not condicao:
        falhas.append(descricao)


def principal() -> int:
    print("=== 1. _valor_serializavel: tipos numpy/pandas viram nativos do Python ===")

    checar(analytics_api._valor_serializavel(np.array(["bom", "rapido"])) == ["bom", "rapido"],
           "numpy.ndarray de strings vira list")
    checar(analytics_api._valor_serializavel(np.array([])) == [],
           "numpy.ndarray vazio vira lista vazia, não erro")
    checar(analytics_api._valor_serializavel(np.int64(42)) == 42
           and isinstance(analytics_api._valor_serializavel(np.int64(42)), int),
           "numpy.int64 vira int nativo")
    checar(analytics_api._valor_serializavel(np.float64(3.5)) == 3.5,
           "numpy.float64 vira float nativo")
    checar(analytics_api._valor_serializavel(np.float64("nan")) is None,
           "numpy.float64 NaN vira None")
    checar(analytics_api._valor_serializavel(pd.NaT) is None,
           "pandas.NaT vira None")
    checar(analytics_api._valor_serializavel(pd.Timestamp("2026-01-01", tz="UTC")) == "2026-01-01T00:00:00+00:00",
           "pandas.Timestamp vira string ISO 8601")
    checar(analytics_api._valor_serializavel("positivo") == "positivo",
           "string comum passa direto, sem alteração")

    print("\n=== 2. _executar(): resultado do BigQuery real (com ndarray/Timestamp) serializa em JSON ===")

    df_fake = pd.DataFrame([
        {
            "comment_id": "c1", "sentimento": "positivo",
            "adjetivos": np.array(["bom", "rapido"]),
            "coletado_em": pd.Timestamp("2026-01-01", tz="UTC"),
        },
        {
            "comment_id": "c2", "sentimento": None,
            "adjetivos": np.array([]),
            "coletado_em": pd.NaT,
        },
    ])

    original_query = warehouse.query
    warehouse.query = lambda sql, params=None: df_fake
    try:
        registros = analytics_api._executar("SELECT 1")
    finally:
        warehouse.query = original_query

    checar(len(registros) == 2, "duas linhas devolvidas")
    checar(registros[0]["adjetivos"] == ["bom", "rapido"],
           "adjetivos (numpy.ndarray) virou lista python")
    checar(registros[1]["adjetivos"] == [], "ndarray vazio virou lista vazia")
    checar(registros[1]["sentimento"] is None, "None permanece None (não NaN)")
    checar(registros[1]["coletado_em"] is None, "NaT virou None")

    try:
        texto = json.dumps(registros)
        checar(True, "json.dumps(registros) não levanta TypeError (bug original corrigido)")
    except TypeError as exc:
        checar(False, f"json.dumps ainda falha: {exc}")

    from fastapi.encoders import jsonable_encoder
    try:
        jsonable_encoder(registros)
        checar(True, "jsonable_encoder (o que o FastAPI usa de verdade) também aceita o resultado")
    except Exception as exc:
        checar(False, f"jsonable_encoder ainda falha: {exc}")

    print("\n" + "=" * 62)
    if falhas:
        print(f"{len(falhas)} VERIFICAÇÃO(ÕES) FALHARAM:")
        for f in falhas:
            print(f"  - {f}")
    else:
        print("Todas as verificações de analytics_api passaram.")
    print("=" * 62)
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(principal())
