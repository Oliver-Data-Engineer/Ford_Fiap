"""
test_scraper_url_utils.py — verifica src/scraper/url_utils.py: parsing de
query string e montagem de template. 100% determinístico (sem LLM/rede) —
mesmo espírito de tests/test_proxy_pool.py, funções puras testadas direto.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.scraper.models import FiltroParam
from src.scraper.url_utils import extrair_query_params, montar_template

falhas = []


def checar(condicao, descricao):
    print(("  OK   " if condicao else "  FALHA") + f"  {descricao}")
    if not condicao:
        falhas.append(descricao)


def principal() -> int:
    print("=== 1. extrair_query_params ===")

    multiplos = extrair_query_params("https://x.com/catalogo?fabricante=Ford&varnome=Ranger&ordem=1")
    checar(multiplos == [
        {"nome_param": "fabricante", "valor_amostra": "Ford"},
        {"nome_param": "varnome", "valor_amostra": "Ranger"},
        {"nome_param": "ordem", "valor_amostra": "1"},
    ], f"múltiplos parâmetros, na ordem da URL (obtido: {multiplos!r})")

    checar(extrair_query_params("https://x.com/sem-query") == [], "URL sem query string devolve lista vazia")
    checar(extrair_query_params("https://x.com/vazia?") == [], "'?' sem nenhum parâmetro devolve lista vazia")

    duplicado = extrair_query_params("https://x.com/busca?tag=novo&tag=usado")
    checar(duplicado == [
        {"nome_param": "tag", "valor_amostra": "novo"}, {"nome_param": "tag", "valor_amostra": "usado"},
    ], f"chave duplicada preserva as duas ocorrências, na ordem (obtido: {duplicado!r})")

    print("\n=== 2. montar_template ===")

    url = "https://x.com/catalogo?fabricante=Ford&varnome=Ranger&ordem=1"
    filtros = [
        FiltroParam(nome_param="fabricante", papel="marca", valor_amostra="Ford"),
        FiltroParam(nome_param="varnome", papel="modelo", valor_amostra="Ranger"),
    ]
    template = montar_template(url, filtros)
    checar(template == "https://x.com/catalogo?fabricante={marca}&varnome={modelo}&ordem=1",
           f"parâmetros classificados viram {{papel}}; não classificados mantêm o valor original (obtido: {template!r})")
    checar(template.format(marca="Fiat", modelo="Uno") == "https://x.com/catalogo?fabricante=Fiat&varnome=Uno&ordem=1",
           "template gerado é um .format() válido de verdade")

    filtro_outro = [FiltroParam(nome_param="sessao", papel="outro", valor_amostra="abc123")]
    template_outro = montar_template("https://x.com/busca?sessao=abc123", filtro_outro)
    checar(template_outro == "https://x.com/busca?sessao={sessao}",
           f"papel 'outro' usa o próprio nome do parâmetro como marcador, não colide entre si (obtido: {template_outro!r})")

    template_sem_filtro = montar_template(url, [])
    checar(template_sem_filtro == url, "sem filtros classificados, o template é idêntico à URL original")

    print("\n" + "=" * 62)
    if falhas:
        print(f"{len(falhas)} VERIFICAÇÃO(ÕES) FALHARAM:")
        for f in falhas:
            print(f"  - {f}")
    else:
        print("Todas as verificações de url_utils do Scraper Mode passaram.")
    print("=" * 62)
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(principal())
