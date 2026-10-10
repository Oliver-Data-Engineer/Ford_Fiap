"""
test_specs_api.py — verifica a categorização das especificações extraídas
(src/utils/normalizador.py) e a gravação best-effort no warehouse do
Scraper Mode (src/api/specs_api.py::_salvar_no_warehouse), sem tocar rede,
scrapers reais nem BigQuery real: injeta um warehouse falso no lugar de
`scraper_warehouse` e um settings falso no lugar de `settings`, mesmo
espírito de tests/test_scraper_warehouse.py.
"""
import asyncio
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api import specs_api
from src.api.specs_api import (
    ExecutarRequest,
    _buscar_historico_bigquery,
    _fontes_saudaveis,
    _salvar_no_warehouse,
    _verificar_saude_fonte,
)
from src.utils.normalizador import categorizar_atributo, normalizar

falhas = []


def checar(condicao, descricao):
    print(("  OK   " if condicao else "  FALHA") + f"  {descricao}")
    if not condicao:
        falhas.append(descricao)


class WarehouseFalso:
    def __init__(self):
        self.chamadas: list[tuple[str, list[dict]]] = []

    def upsert(self, nome, linhas):
        self.chamadas.append((nome, linhas))


class WarehouseQueFalha:
    def upsert(self, nome, linhas):
        raise RuntimeError("BigQuery indisponível (simulado)")


class SettingsFalso:
    def __init__(self, configurado: bool):
        self.bigquery_configurado = configurado


def principal() -> int:
    print("=== 1. categorizar_atributo classifica atributos conhecidos ===")
    checar(categorizar_atributo("Potência") == "Motor & Transmissão", "Potência -> Motor & Transmissão")
    checar(categorizar_atributo("Airbag (cada)") == "Segurança", "Airbag -> Segurança")
    checar(
        categorizar_atributo("Farol Full LED") == "Iluminação",
        "Farol Full LED -> Iluminação (não confunde com 'aro' dentro de 'Farol')",
    )
    checar(
        categorizar_atributo("Teto Solar Elétrico") == "Teto Solar",
        "Teto Solar Elétrico -> Teto Solar (não confunde 'elétrico' com motor elétrico)",
    )
    checar(categorizar_atributo("Zzz atributo desconhecido") == "Outros", "atributo sem match -> Outros")

    print("\n=== 2. normalizar() inclui o campo 'categoria' em cada spec ===")
    specs = normalizar({"icarros": [{"atributo": "Potência", "valor": "272 cv"}]})
    checar(len(specs) == 1 and specs[0]["categoria"] == "Motor & Transmissão", "normalizar() atribui categoria")

    print("\n=== 3. _salvar_no_warehouse grava linhas no formato esperado (best-effort) ===")

    especificacoes = [
        {"atributo": "Potência", "valor": "272 cv", "fonte": "icarros", "categoria": "Motor & Transmissão"},
        {"atributo": "Airbag", "valor": "7", "fonte": "icarros", "categoria": "Segurança"},
    ]
    url_ficha_por_fonte = {"icarros": "https://www.icarros.com.br/x/ficha-tecnica"}
    body = ExecutarRequest(fabricante="Ford", modelo="Ka", versao="1.0")

    warehouse_falso = WarehouseFalso()
    specs_api.scraper_warehouse = warehouse_falso
    specs_api.settings = SettingsFalso(configurado=True)

    _salvar_no_warehouse(body, especificacoes, url_ficha_por_fonte)

    checar(len(warehouse_falso.chamadas) == 1, "upsert foi chamado uma vez")
    if warehouse_falso.chamadas:
        nome, linhas = warehouse_falso.chamadas[0]
        checar(nome == specs_api.scraper_schemas.FICHA_TECNICA, "grava na tabela scraper_ficha_tecnica")
        checar(len(linhas) == 2, "uma linha por especificação")
        linha = linhas[0]
        checar(linha["site_id"] == "icarros", "site_id vem da fonte")
        checar(linha["marca"] == "Ford" and linha["modelo"] == "Ka" and linha["versao"] == "1.0", "dados do veículo presentes")
        checar(linha["atributo"] == "Potência" and linha["valor"] == "272 cv", "atributo/valor vêm da spec")
        checar(linha["url_origem"] == url_ficha_por_fonte["icarros"], "url_origem vem da url_ficha resolvida")
        checar(bool(linha["coletado_em"]), "coletado_em preenchido")

    print("\n=== 4. Casos que NÃO devem gravar (best-effort, nunca derruba a busca) ===")

    warehouse_falso_vazio = WarehouseFalso()
    specs_api.scraper_warehouse = warehouse_falso_vazio
    specs_api.settings = SettingsFalso(configurado=True)
    _salvar_no_warehouse(body, [], {})
    checar(len(warehouse_falso_vazio.chamadas) == 0, "lista de especificações vazia -> não chama upsert")

    warehouse_falso_desativado = WarehouseFalso()
    specs_api.scraper_warehouse = warehouse_falso_desativado
    specs_api.settings = SettingsFalso(configurado=False)
    _salvar_no_warehouse(body, especificacoes, url_ficha_por_fonte)
    checar(len(warehouse_falso_desativado.chamadas) == 0, "BigQuery não configurado -> não chama upsert")

    specs_api.scraper_warehouse = WarehouseQueFalha()
    specs_api.settings = SettingsFalso(configurado=True)
    try:
        _salvar_no_warehouse(body, especificacoes, url_ficha_por_fonte)
        checar(True, "upsert falhando não propaga exceção (best-effort)")
    except Exception as exc:
        checar(False, f"upsert falhando NÃO deveria propagar exceção, mas propagou: {exc}")

    print("\n=== 5. _verificar_saude_fonte trata exceção e catálogo pequeno como não-saudável ===")

    class FonteFalsa:
        def __init__(self, resultado=None, excecao=None):
            self._resultado = resultado
            self._excecao = excecao

        async def obter_fabricantes(self):
            if self._excecao:
                raise self._excecao
            return self._resultado

    saudavel = asyncio.run(_verificar_saude_fonte(
        "fake", FonteFalsa(resultado=[{"nome_exibicao": f"Marca {i}"} for i in range(20)])
    ))
    checar(saudavel is True, "catálogo com dezenas de fabricantes -> saudável")

    nao_saudavel_pequeno = asyncio.run(_verificar_saude_fonte(
        "fake", FonteFalsa(resultado=[{"nome_exibicao": "Só Uma"}])
    ))
    checar(nao_saudavel_pequeno is False, "catálogo com poucos fabricantes (< limiar) -> não-saudável")

    nao_saudavel_excecao = asyncio.run(_verificar_saude_fonte(
        "fake", FonteFalsa(excecao=ConnectionError("fora do ar"))
    ))
    checar(nao_saudavel_excecao is False, "exceção na fonte -> não-saudável")

    print("\n=== 6. _fontes_saudaveis() filtra fonte não-saudável, mas nunca esvazia por completo ===")

    saude_original = dict(specs_api._SAUDE_FONTES)
    try:
        nomes = list(specs_api.FONTES.keys())
        checar(len(nomes) >= 3, "FONTES tem as 3 fontes esperadas (carrosnaweb, icarros, fichacompleta)")

        specs_api._SAUDE_FONTES.clear()
        specs_api._SAUDE_FONTES.update({nomes[0]: False, nomes[1]: True, nomes[2]: True})
        saudaveis = _fontes_saudaveis()
        checar(nomes[0] not in saudaveis, "fonte marcada como não-saudável é excluída")
        checar(nomes[1] in saudaveis and nomes[2] in saudaveis, "fontes saudáveis continuam presentes")

        specs_api._SAUDE_FONTES.clear()
        specs_api._SAUDE_FONTES.update({nome: False for nome in nomes})
        saudaveis_todas_ruins = _fontes_saudaveis()
        checar(
            set(saudaveis_todas_ruins.keys()) == set(nomes),
            "todas marcadas como não-saudáveis -> degrada pra usar todas mesmo assim",
        )
    finally:
        specs_api._SAUDE_FONTES.clear()
        specs_api._SAUDE_FONTES.update(saude_original)

    print("\n=== 7. _buscar_historico_bigquery — histórico como último recurso ===")

    class WarehouseComHistorico:
        def __init__(self, df):
            self._df = df

        def table_id(self, nome):
            return f"projeto.dataset.{nome}"

        def query(self, sql, params=None):
            return self._df

    df_historico = pd.DataFrame([
        {"atributo": "Potência", "valor": "272 cv", "fonte": "icarros"},
        {"atributo": "Torque", "valor": "42 kgfm", "fonte": "icarros"},
    ])
    specs_api.scraper_warehouse = WarehouseComHistorico(df_historico)
    specs_api.settings = SettingsFalso(configurado=True)

    historico = _buscar_historico_bigquery("Ford", "Ranger", "XLT 3.2")
    checar(historico is not None and len(historico) == 2, "devolve as linhas do histórico")
    if historico:
        checar(
            all("categoria" in linha for linha in historico),
            "cada linha ganha 'categoria' via categorizar_atributo",
        )

    print("\n=== 8. _buscar_historico_bigquery — casos que devolvem None sem propagar ===")

    specs_api.settings = SettingsFalso(configurado=False)
    checar(_buscar_historico_bigquery("Ford", "Ranger", "XLT 3.2") is None, "BigQuery não configurado -> None")

    class WarehouseVazio:
        def table_id(self, nome):
            return f"projeto.dataset.{nome}"

        def query(self, sql, params=None):
            return pd.DataFrame([])

    specs_api.scraper_warehouse = WarehouseVazio()
    specs_api.settings = SettingsFalso(configurado=True)
    checar(_buscar_historico_bigquery("Ford", "Ranger", "XLT 3.2") is None, "resultado vazio -> None")

    class WarehouseQueFalhaConsulta:
        def table_id(self, nome):
            return f"projeto.dataset.{nome}"

        def query(self, sql, params=None):
            raise RuntimeError("BigQuery indisponível (simulado)")

    specs_api.scraper_warehouse = WarehouseQueFalhaConsulta()
    try:
        resultado = _buscar_historico_bigquery("Ford", "Ranger", "XLT 3.2")
        checar(resultado is None, "consulta falhando -> None, sem propagar exceção")
    except Exception as exc:
        checar(False, f"consulta falhando NÃO deveria propagar exceção, mas propagou: {exc}")

    print("\n" + "=" * 62)
    if falhas:
        print(f"{len(falhas)} VERIFICAÇÃO(ÕES) FALHARAM:")
        for f in falhas:
            print(f"  - {f}")
    else:
        print("Todas as verificações de specs_api/normalizador passaram.")
    print("=" * 62)
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(principal())
