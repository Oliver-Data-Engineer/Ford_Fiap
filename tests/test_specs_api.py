"""
test_specs_api.py — verifica a categorização das especificações extraídas
(src/utils/normalizador.py) e a gravação best-effort no warehouse do
Scraper Mode (src/api/specs_api.py::_salvar_no_warehouse), sem tocar rede,
scrapers reais nem BigQuery real: injeta um warehouse falso no lugar de
`scraper_warehouse` e um settings falso no lugar de `settings`, mesmo
espírito de tests/test_scraper_warehouse.py.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api import specs_api
from src.api.specs_api import ExecutarRequest, _salvar_no_warehouse
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
