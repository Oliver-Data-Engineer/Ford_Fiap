"""
deteccao_bloqueio.py
Heurística para detectar páginas de bloqueio/captcha/erro genérico
disfarçadas de resposta HTTP 200 — usada pelos 3 scrapers de
especificações técnicas (CarrosWeb, iCarros, FichaCompleta), e por
qualquer scraper futuro que precise da mesma checagem. Extraída pra cá
em vez de duplicada por scraper porque é a MESMA lista de marcadores nos
três casos (não é lógica específica de estrutura de página, ao contrário
do parsing de HTML de cada fonte) — melhorar essa lista uma vez melhora
os três scrapers de uma vez.
"""
from __future__ import annotations

MARCADORES_BLOQUEIO: tuple[str, ...] = (
    "cf-browser-verification", "just a moment",
    "enable javascript and cookies", "captcha",
    "access denied", "bot detection", "verifying you are human",
)


def parece_bloqueado_ou_erro(html: str, limite_tamanho: int = 20_000) -> bool:
    """
    True quando o HTML parece uma página de bloqueio/captcha/erro genérico
    em vez do conteúdo esperado.

    Só considera "suspeito" um HTML curto (páginas de erro genéricas
    raramente têm mais que `limite_tamanho` caracteres) — evita falso
    positivo numa página de conteúdo real que só cite a palavra
    'captcha' de passagem em algum texto.
    """
    if not html or len(html) > limite_tamanho:
        return False
    alvo = html.lower()
    return any(marcador in alvo for marcador in MARCADORES_BLOQUEIO)
