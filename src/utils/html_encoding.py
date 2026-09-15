"""
html_encoding.py
Decodifica bytes de resposta HTTP de sites de terceiros (CarrosWeb, iCarros).

Tenta UTF-8 primeiro (rígido); se a página não for UTF-8 — comum em sites
legados em .asp/.php antigos como carrosnaweb.com.br —, cai para
Windows-1252 (superset de Latin-1, cobre a esmagadora maioria dos casos de
acentuação em português usados nesses sites).

Importante: nunca usar `errors="replace"` direto num decode UTF-8 forçado —
um byte de acentuação Latin-1 (ex.: "í" = 0xED) não é um byte de início
válido em UTF-8, então "replace" o descarta como "�" de forma irreversível
(o dado é perdido ali, não dá pra "consertar" depois no texto já decodificado
— esse era o bug: atributos como "Altura mínima do solo" chegavam truncados
como "Altura m�nima do solo").
"""
from __future__ import annotations


def decodificar_html_bytes(conteudo: bytes) -> str:
    try:
        return conteudo.decode("utf-8")
    except UnicodeDecodeError:
        return conteudo.decode("cp1252", errors="replace")
