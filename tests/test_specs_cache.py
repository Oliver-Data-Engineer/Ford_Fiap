"""
test_specs_cache.py — verifica src/utils/specs_cache.py (cache local em
disco do catálogo fabricante/modelo/versão do módulo specs).

Roda inteiramente contra um diretório de dados temporário — nunca toca
`data/specs_catalogo_cache.json` real.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.config as config

TEMP = Path(tempfile.mkdtemp(prefix="fordfiap_specs_cache_"))
config.DATA_DIR = TEMP / "data"

from src.utils import specs_cache

falhas = []


def checar(condicao, descricao):
    print(("  OK   " if condicao else "  FALHA") + f"  {descricao}")
    if not condicao:
        falhas.append(descricao)


def principal() -> int:
    print("=== 1. Fabricantes — vazio até a primeira gravação ===")

    checar(specs_cache.obter_fabricantes() is None, "sem cache ainda, devolve None")

    fabricantes = [{"id_busca": "ford", "nome_exibicao": "Ford"}]
    specs_cache.salvar_fabricantes(fabricantes)
    checar(specs_cache.obter_fabricantes() == fabricantes, "grava e lê de volta")

    specs_cache.salvar_fabricantes([])
    checar(specs_cache.obter_fabricantes() == fabricantes,
           "lista vazia nunca sobrescreve um cache válido (nunca 'trava' um resultado vazio)")

    print("\n=== 2. Modelos — por fabricante, case-insensitive ===")

    checar(specs_cache.obter_modelos("Ford") is None, "sem cache ainda pra Ford")

    modelos_ford = [{"id_busca": "ka", "nome_exibicao": "Ka"}]
    specs_cache.salvar_modelos("Ford", modelos_ford)
    checar(specs_cache.obter_modelos("Ford") == modelos_ford, "grava e lê de volta (mesma caixa)")
    checar(specs_cache.obter_modelos("  ford  ") == modelos_ford,
           "chave normalizada (espaços/caixa não importam)")
    checar(specs_cache.obter_modelos("Fiat") is None, "fabricante diferente não compartilha cache")

    print("\n=== 3. Versões — por (fabricante, modelo) ===")

    checar(specs_cache.obter_versoes("Ford", "Ka") is None, "sem cache ainda")

    versoes_ka = [{"versao": "Ka SE Plus 1.0", "ano": "2021"}]
    specs_cache.salvar_versoes("Ford", "Ka", versoes_ka)
    checar(specs_cache.obter_versoes("Ford", "Ka") == versoes_ka, "grava e lê de volta")
    checar(specs_cache.obter_versoes("Ford", "Territory") is None,
           "modelo diferente do mesmo fabricante não compartilha cache")

    print("\n=== 4. Fichas técnicas — por (fabricante, modelo, versão) ===")

    checar(specs_cache.obter_ficha("Ford", "Ka", "1.0 SE") is None, "sem cache ainda")

    ficha_ka = [{"atributo": "Potência", "valor": "84 cv", "fonte": "icarros", "categoria": "Motor & Transmissão"}]
    specs_cache.salvar_ficha("Ford", "Ka", "1.0 SE", ficha_ka)
    checar(specs_cache.obter_ficha("Ford", "Ka", "1.0 SE") == ficha_ka, "grava e lê de volta")
    checar(specs_cache.obter_ficha("  ford ", "ka", " 1.0 se ") == ficha_ka,
           "chave normalizada (espaços/caixa não importam)")
    checar(specs_cache.obter_ficha("Ford", "Ka", "1.5 SEL") is None,
           "versão diferente do mesmo fabricante/modelo não compartilha cache")
    specs_cache.salvar_ficha("Ford", "Ka", "1.0 SE", [])
    checar(specs_cache.obter_ficha("Ford", "Ka", "1.0 SE") == ficha_ka,
           "lista vazia nunca sobrescreve um cache válido")

    print("\n=== 5. Persistência sobrevive a uma nova leitura do arquivo ===")

    # `_ler`/`_escrever` releem o arquivo do zero a cada chamada (sem estado
    # em memória) — confirma que o que foi gravado está mesmo em disco, não
    # só numa variável do processo.
    caminho = specs_cache._caminho()
    checar(caminho.is_file(), "arquivo de cache foi criado em disco")

    import json
    bruto = json.loads(caminho.read_text(encoding="utf-8"))
    checar(bruto["fabricantes"] == fabricantes, "conteúdo em disco bate com o gravado (fabricantes)")
    checar(bruto["modelos"].get("ford") == modelos_ford, "conteúdo em disco bate com o gravado (modelos)")
    checar(bruto["versoes"].get("ford::ka") == versoes_ka, "conteúdo em disco bate com o gravado (versões)")
    checar(bruto["fichas"].get("ford::ka::1.0 se") == ficha_ka, "conteúdo em disco bate com o gravado (fichas)")

    print("\n=== 6. Arquivo ilegível é tratado como cache vazio, não derruba a leitura ===")

    caminho.write_text("{ isso não é json válido", encoding="utf-8")
    checar(specs_cache.obter_fabricantes() is None, "JSON corrompido -> None, sem levantar exceção")

    print("\n" + "=" * 62)
    if falhas:
        print(f"{len(falhas)} VERIFICAÇÃO(ÕES) FALHARAM:")
        for f in falhas:
            print(f"  - {f}")
    else:
        print("Todas as verificações de specs_cache passaram.")
    print("=" * 62)
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(principal())
