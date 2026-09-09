"""
AutoSpec — Web Scraper de Ficha Técnica de Carros
==================================================
Dependências obrigatórias:
    pip install curl_cffi beautifulsoup4

Dependências opcionais (melhora cobertura e exportação):
    pip install cloudscraper pandas openpyxl   # cloudscraper=fallback CF, pandas=Excel
    pip install playwright && playwright install chromium  # sites JS pesado

Uso:
    python car_scraper.py --marca Ford    --modelo "Ranger Raptor" --versao "3.0 V6 2026"
    python car_scraper.py --marca Toyota  --modelo Corolla         --json saida.json
    python car_scraper.py --marca Honda   --modelo Civic           --xlsx ficha.xlsx --debug
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field
from typing import Optional
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

# ── curl_cffi (substitui requests — bypassa TLS fingerprinting) ──────────────
try:
    from curl_cffi import requests as cffi_requests
    _CFFI_OK = True
except ImportError:
    import requests as cffi_requests          # fallback padrão
    _CFFI_OK = False

import requests                               # mantido para compatibilidade

# ── dependências opcionais ───────────────────────────────────────────────────
try:
    import cloudscraper
    _CLOUD_OK = True
except ImportError:
    _CLOUD_OK = False

try:
    import pandas as pd
    _PANDAS_OK = True
except ImportError:
    _PANDAS_OK = False

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    _PW_OK = True
except ImportError:
    _PW_OK = False


# ═══════════════════════════════════════════════════════════════════════════════
# MODELO DE DADOS
# ═══════════════════════════════════════════════════════════════════════════════
@dataclass
class FichaTecnica:
    marca:   str = ""
    modelo:  str = ""
    versao:  str = ""
    ano:     str = ""
    fonte:   str = ""
    url:     str = ""

    # Motor
    motor:       str = ""
    potencia:    str = ""
    torque:      str = ""
    combustivel: str = ""
    cilindrada:  str = ""
    cilindros:   str = ""
    valvulas:    str = ""
    alimentacao: str = ""
    compressao:  str = ""

    # Transmissão
    transmissao: str = ""
    tracao:      str = ""
    marchas:     str = ""

    # Dimensões
    comprimento:             str = ""
    largura:                 str = ""
    altura:                  str = ""
    entre_eixos:             str = ""
    peso:                    str = ""
    capacidade_porta_malas:  str = ""
    tanque:                  str = ""

    # Desempenho
    velocidade_maxima: str = ""
    aceleracao_0_100:  str = ""
    consumo_urbano:    str = ""
    consumo_estrada:   str = ""

    # Suspensão / Freios / Pneus
    suspensao_dianteira: str = ""
    suspensao_traseira:  str = ""
    freios_dianteiros:   str = ""
    freios_traseiros:    str = ""
    pneus: str = ""
    rodas: str = ""

    extras: dict = field(default_factory=dict)

    def campos_preenchidos(self) -> int:
        ignore = {"extras", "marca", "modelo", "versao", "ano", "fonte", "url"}
        return sum(
            1 for k, v in asdict(self).items()
            if k not in ignore and v and isinstance(v, str)
        )


# ═══════════════════════════════════════════════════════════════════════════════
# MAPEAMENTO DE CAMPOS
# ═══════════════════════════════════════════════════════════════════════════════
FIELD_MAP: dict[str, list[str]] = {
    "motor":       ["motor", "engine", "tipo de motor", "tipo motor"],
    "potencia":    ["potência", "potencia", "cavalos", "cv ", " cv", "hp"],
    "torque":      ["torque"],
    "combustivel": ["combustível", "combustivel", "tipo de combustível", "fuel"],
    "cilindrada":  ["cilindrada", "displacement", "volume", "cc"],
    "cilindros":   ["cilindros", "cylinders", "nº de cilindros"],
    "valvulas":    ["válvulas", "valvulas", "valves"],
    "alimentacao": ["alimentação", "alimentacao", "injeção", "injecao",
                    "injection", "sistema de alimentação"],
    "compressao":  ["compressão", "compressao", "taxa de compressão", "compression"],
    "transmissao": ["transmissão", "transmissao", "câmbio", "cambio",
                    "gearbox", "caixa de câmbio"],
    "tracao":      ["tração", "tracao", "traction", "tipo de tração"],
    "marchas":     ["marchas", "velocidades", "speeds", "nº de marchas"],
    "comprimento": ["comprimento", "length", "comp."],
    "largura":     ["largura", "width", "larg."],
    "altura":      ["altura", "height", "alt."],
    "entre_eixos": ["entre-eixos", "entre eixos", "wheelbase",
                    "distância entre eixos"],
    "peso":        ["peso", "weight", "massa", "peso bruto", "peso em ordem"],
    "capacidade_porta_malas": ["porta-malas", "porta malas", "bagageiro",
                               "trunk", "capacidade do porta"],
    "tanque":      ["tanque", "capacidade do tanque", "tank",
                    "combustível (litros)"],
    "velocidade_maxima": ["velocidade máxima", "velocidade maxima",
                          "top speed", "vel. máx", "veloc. max"],
    "aceleracao_0_100":  ["0 a 100", "0-100", "aceleração de 0", "acceleration"],
    "consumo_urbano":    ["consumo urbano", "city", "cidade (km/l)", "urbano"],
    "consumo_estrada":   ["consumo estrada", "highway", "estrada (km/l)",
                          "rodoviário", "rodovia"],
    "suspensao_dianteira": ["suspensão dianteira", "dianteira",
                            "front suspension"],
    "suspensao_traseira":  ["suspensão traseira", "traseira", "rear suspension"],
    "freios_dianteiros":   ["freios dianteiros", "freio dianteiro",
                            "front brake"],
    "freios_traseiros":    ["freios traseiros", "freio traseiro", "rear brake"],
    "pneus": ["pneus", "pneu", "tires", "pneumáticos", "medida do pneu"],
    "rodas": ["rodas", "wheels", "aros", "aro"],
}


def _apply_pair(key: str, value: str, ficha: FichaTecnica):
    if not key or not value or len(key) > 80 or len(value) > 300:
        return
    key_low = key.lower().strip()
    for attr, keywords in FIELD_MAP.items():
        if any(kw in key_low for kw in keywords):
            if not getattr(ficha, attr):
                setattr(ficha, attr, value.strip())
            return
    clean_key = key.strip()
    if clean_key and clean_key not in ficha.extras:
        ficha.extras[clean_key] = value.strip()


def _parse_element(el, ficha: FichaTecnica):
    """Extrai pares chave/valor de table ou dl."""
    if not el:
        return
    if el.name == "dl":
        for dt, dd in zip(el.find_all("dt"), el.find_all("dd")):
            _apply_pair(dt.get_text(strip=True), dd.get_text(strip=True), ficha)
        return
    for row in el.find_all("tr"):
        cols = row.find_all(["td", "th"])
        if len(cols) >= 2:
            _apply_pair(
                cols[0].get_text(strip=True),
                cols[1].get_text(strip=True),
                ficha,
            )


# ═══════════════════════════════════════════════════════════════════════════════
# CAMADA DE HTTP — retries + cloudscraper + Playwright
# ═══════════════════════════════════════════════════════════════════════════════

USER_AGENTS = [
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
     "AppleWebKit/537.36 (KHTML, like Gecko) "
     "Chrome/124.0.0.0 Safari/537.36"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) "
     "AppleWebKit/605.1.15 (KHTML, like Gecko) "
     "Version/17.4 Safari/605.1.15"),
    ("Mozilla/5.0 (X11; Linux x86_64) "
     "AppleWebKit/537.36 (KHTML, like Gecko) "
     "Chrome/123.0.0.0 Safari/537.36"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
     "Gecko/20100101 Firefox/125.0"),
]

DEBUG = False


def _log(msg: str):
    if DEBUG:
        print(f"  [DBG] {msg}")


def _build_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": ("text/html,application/xhtml+xml,application/xml;"
                   "q=0.9,image/avif,image/webp,*/*;q=0.8"),
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    })
    return s


def _is_blocked(html: str) -> bool:
    markers = [
        "cf-browser-verification", "Just a moment",
        "Enable JavaScript and cookies", "captcha",
        "Access Denied", "bot detection", "verifying you are human",
    ]
    lower = html.lower()
    return any(m.lower() in lower for m in markers) and len(html) < 20_000


def get_soup(
    url: str,
    retries: int = 3,
    backoff: float = 2.0,
    use_cloud: bool = True,
    referer: str = "",
) -> Optional[BeautifulSoup]:
    """
    Recupera HTML de uma URL com:
    1. requests com retries + backoff exponencial + rotação de UA
    2. cloudscraper (bypass Cloudflare leve)        — se instalado
    3. Playwright  (renderização JS headless)        — se instalado
    """
    last_err: Optional[Exception] = None

    # ── Tentativas com requests ──────────────────────────────────────────────
    for attempt in range(1, retries + 1):
        session = _build_session()
        if referer:
            session.headers["Referer"] = referer
        try:
            _log(f"GET {url}  (tentativa {attempt}/{retries})")
            resp = session.get(url, timeout=20, allow_redirects=True)
            resp.raise_for_status()
            if _is_blocked(resp.text):
                raise ValueError("Bloqueio/captcha detectado")
            return BeautifulSoup(resp.text, "html.parser")
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else 0
            _log(f"HTTP {status}: {e}")
            # 404 → não adianta tentar de novo
            if status == 404:
                _log("404 — URL não existe, abortando tentativas")
                break
            last_err = e
        except requests.exceptions.SSLError as e:
            _log(f"SSL: {e} — tentando sem verificação")
            try:
                resp = session.get(url, timeout=20, verify=False)
                resp.raise_for_status()
                if not _is_blocked(resp.text):
                    return BeautifulSoup(resp.text, "html.parser")
            except Exception as e2:
                last_err = e2
        except requests.exceptions.ConnectionError as e:
            _log(f"Conexão: {e}")
            last_err = e
        except requests.exceptions.Timeout:
            _log("Timeout")
            last_err = TimeoutError(f"Timeout em {url}")
        except Exception as e:
            _log(f"Erro: {e}")
            last_err = e

        if attempt < retries:
            wait = backoff * (2 ** (attempt - 1)) + random.uniform(0, 1)
            _log(f"Aguardando {wait:.1f}s…")
            time.sleep(wait)

    # ── Fallback 1: cloudscraper ─────────────────────────────────────────────
    if use_cloud and _CLOUD_OK:
        _log("Fallback → cloudscraper")
        try:
            scraper = cloudscraper.create_scraper(
                browser={"browser": "chrome", "platform": "windows", "mobile": False}
            )
            resp = scraper.get(url, timeout=25)
            resp.raise_for_status()
            if not _is_blocked(resp.text):
                return BeautifulSoup(resp.text, "html.parser")
            _log("cloudscraper: ainda bloqueado")
        except Exception as e:
            _log(f"cloudscraper: {e}")

    # ── Fallback 2: Playwright ───────────────────────────────────────────────
    if _PW_OK:
        _log("Fallback → Playwright")
        soup = _get_with_playwright(url)
        if soup:
            return soup

    if last_err:
        _log(f"Último erro registrado: {last_err}")
    return None


def _get_with_playwright(
    url: str,
    wait_for: str = "networkidle",
    timeout: int = 25_000,
) -> Optional[BeautifulSoup]:
    """Renderiza a página com Chromium headless via Playwright."""
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            ctx = browser.new_context(
                user_agent=random.choice(USER_AGENTS),
                locale="pt-BR",
                viewport={"width": 1366, "height": 768},
                java_script_enabled=True,
            )
            # Remove flag webdriver para evitar detecção
            ctx.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
            )
            page = ctx.new_page()
            try:
                page.goto(url, wait_until=wait_for, timeout=timeout)
            except PWTimeout:
                # Timeout no wait_until mas a página pode estar parcialmente carregada
                _log("Playwright: timeout no wait_until, usando HTML parcial")
            time.sleep(random.uniform(1.5, 2.5))
            html = page.content()
            browser.close()

            if _is_blocked(html):
                _log("Playwright: página ainda bloqueada")
                return None
            return BeautifulSoup(html, "html.parser")

    except PWTimeout:
        _log(f"Playwright: timeout ao navegar para {url}")
        return None
    except Exception as e:
        _log(f"Playwright: {e}")
        if DEBUG:
            traceback.print_exc()
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# UTILITÁRIOS
# ═══════════════════════════════════════════════════════════════════════════════
def _limpar(texto: str) -> str:
    """Remove espaços/tabs/quebras extras e retorna 'N/D' se vazio."""
    if not texto:
        return "N/D"
    cleaned = " ".join(texto.split())
    return cleaned if cleaned else "N/D"


def slugify(text: str) -> str:
    text = text.lower()
    for src, dst in [("àáâãä","a"),("èéêë","e"),("ìíîï","i"),
                     ("òóôõö","o"),("ùúûü","u"),("ç","c")]:
        for c in src:
            text = text.replace(c, dst)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


# ═══════════════════════════════════════════════════════════════════════════════
# SCRAPERS ESPECÍFICOS
# ═══════════════════════════════════════════════════════════════════════════════

class ICarrosScraper:
    """
    Scraper principal do iCarros — usa curl_cffi para bypass de TLS fingerprinting.
    """

    NAME = "iCarros"
    BASE = "https://www.icarros.com.br"

    # Mapeamento categoria iCarros → campos de FichaTecnica
    _CAT_CAMPO: dict[str, dict[str, str]] = {
        "MOTORIZACAO": {
            "motor":         "motor",
            "câmbio":        "transmissao",
            "combustível":   "combustivel",
            "direção":       "extras",
            "tração":        "tracao",
            "cilindrada":    "cilindrada",
            "cilindros":     "cilindros",
            "válvulas":      "valvulas",
            "alimentação":   "alimentacao",
            "compressão":    "compressao",
        },
        "PERFORMANCE": {
            "potência":              "potencia",
            "torque":                "torque",
            "tempo 0-100":           "aceleracao_0_100",
            "velocidade max":        "velocidade_maxima",
            "consumo cidade":        "consumo_urbano",
            "consumo estrada":       "consumo_estrada",
        },
        "DIMENSOES": {
            "comprimento":   "comprimento",
            "largura":       "largura",
            "altura":        "altura",
            "entre-eixos":   "entre_eixos",
            "peso":          "peso",
            "porta-malas":   "capacidade_porta_malas",
            "tanque":        "tanque",
        },
        "FREIOSESUSPENSAO": {
            "suspensão dianteira": "suspensao_dianteira",
            "suspensao dianteira": "suspensao_dianteira",
            "suspensão traseira":  "suspensao_traseira",
            "suspensao traseira":  "suspensao_traseira",
            "freios":              "freios_dianteiros",
            "freio dianteiro":     "freios_dianteiros",
            "freio traseiro":      "freios_traseiros",
            "pneus":               "pneus",
            "rodas":               "rodas",
        },
    }

    # ── MÉTODOS DE NAVEGAÇÃO PARA O APP STREAMLIT ─────────────────────────────
    
    def capturar_todas_marcas(self) -> list:
        url_marcas = f"{self.BASE}/catalogo-zero-km/marcas/"
        soup = self._get(url_marcas)
        if not soup: return []

        marcas_data = []
        for card in soup.find_all('div', class_='card-base'):
            nome_tag = card.find('p')
            if not nome_tag: continue
            nome_marca = nome_tag.get_text(strip=True)
            img_tag = card.find('img')
            logo_url = (img_tag.get('data-src') or img_tag.get('src')) if img_tag else "Sem logo"
            
            parent_a = card.find_parent('a')
            if parent_a and parent_a.get('href'):
                link_marca = f"{self.BASE}{parent_a.get('href')}"
            else:
                link_marca = f"{self.BASE}/catalogo-zero-km/marcas/{slugify(nome_marca)}"

            marcas_data.append({"Marca": nome_marca, "Logo_URL": logo_url, "Link_Catalogo": link_marca})
        return marcas_data

    def capturar_modelos_marca(self, marca_slug: str) -> list:
        url = f"{self.BASE}/catalogo-zero-km/marcas/{marca_slug}"
        soup = self._get(url)
        if not soup: return []

        car_data = []
        for card in soup.find_all('a', class_='complete-ad-card'):
            title_tag = card.find('h2')
            if not title_tag: continue
            href = card.get('href')
            car_data.append({
                "Modelo": title_tag.get_text(strip=True),
                "Link_Catalogo": f"{self.BASE}{href}" if href else "Sem link"
            })
        return car_data

    def capturar_anos_versoes_modelo(self, marca_slug: str, modelo_slug: str) -> dict:
        url = f"{self.BASE}/catalogo-zero-km/marcas/{marca_slug}/{modelo_slug}/"
        
        def extrair(html_soup):
            if not html_soup: return {"anos": [], "versoes": []}
            
            anos = []
            # Tenta encontrar o select de anos pela classe
            select_anos = html_soup.find("select", class_=re.compile(r'years-select', re.I))
            
            # Se falhar, procura por qualquer select que contenha um ano (ex: "2024") nas opções
            if not select_anos:
                for sel in html_soup.find_all("select"):
                    opts = sel.find_all("option")
                    if opts and re.search(r'20\d{2}', opts[0].get("value", "")):
                        select_anos = sel
                        break
                        
            if select_anos:
                for opt in select_anos.find_all("option"):
                    val = opt.get("value", "").strip()
                    txt = opt.get_text(strip=True)
                    if val and txt: anos.append({"valor": val, "texto": txt})

            versoes = []
            # Tenta encontrar o select de versões pela classe
            select_versoes = html_soup.find("select", class_=re.compile(r'versions-select', re.I))
            
            # Se falhar, procura por selects cujas opções contenham o padrão de rota do iCarros (ex: marca/modelo/versao)
            if not select_versoes:
                for sel in html_soup.find_all("select"):
                    opts = sel.find_all("option")
                    if opts and "/" in opts[0].get("value", ""):
                        select_versoes = sel
                        break
                        
            if select_versoes:
                for opt in select_versoes.find_all("option"):
                    val = opt.get("value", "").strip()
                    txt = opt.get_text(strip=True)
                    if val and txt: 
                        slug_versao = val.strip("/").split("/")[-1]
                        versoes.append({"slug": slug_versao, "texto": txt})
                        
            return {"anos": anos, "versoes": versoes}

        soup = self._get(url)
        dados = extrair(soup)
        
        # Aciona Playwright se as caixas de seleção vierem vazias (carregamento dinâmico)
        if (not dados["anos"] or not dados["versoes"]) and globals().get('_PW_OK'):
            print(f"[*] Fallback ativado: Renderizando JS para anos/versões do modelo {modelo_slug}")
            soup_pw = globals().get('_get_with_playwright')(url)
            dados = extrair(soup_pw)
            
        return dados

    def buscar(self, marca: str, modelo: str, versao: str) -> Optional[FichaTecnica]:
        urls = self._montar_urls(marca, modelo, versao)
        for url in urls:
            print(f"  → {self.NAME}: {url}")
            soup = self._get(url)
            if not soup:
                _log("Sem resposta, tentando próxima URL…")
                continue

            # Tenta extração moderna (estrutura sheet-theme-container)
            rows = self._extrair_moderno(soup)
            if not rows:
                _log("Estrutura moderna não encontrada, tentando legada…")
                rows = self._extrair_legado(soup)

            if not rows:
                _log("Nenhum dado extraído desta URL")
                continue

            ficha = self._rows_para_ficha(rows, marca, modelo, versao, url)
            n = ficha.campos_preenchidos()
            _log(f"Campos preenchidos: {n}")
            if n >= 3:
                # Guarda todas as rows brutas para exportação DataFrame
                ficha._raw_rows = rows          # type: ignore[attr-defined]
                return ficha

        return None

    # ── construção de URLs ────────────────────────────────────────────────────
    def _montar_urls(self, marca: str, modelo: str, versao: str) -> list[str]:
        """
        Tenta montar a URL no formato real do iCarros:
          /catalogo-zero-km/marcas/{marca}/{modelo}/{versao-slug}/ficha-tecnica
        e também fallbacks legados.
        """
        m  = slugify(marca)
        mo = slugify(modelo)
        v  = slugify(versao) if versao else ""

        urls: list[str] = []

        # Formato novo (zero km)
        if v:
            urls.append(
                f"{self.BASE}/catalogo-zero-km/marcas/{m}/{mo}/{v}/ficha-tecnica"
            )
        urls.append(
            f"{self.BASE}/catalogo-zero-km/marcas/{m}/{mo}/ficha-tecnica"
        )
        # Formato legado
        urls.append(f"{self.BASE}/catalogo/{mo}/{m}/ficha-tecnica/")
        urls.append(
            f"{self.BASE}/catalogo/ficha_tecnica.asp?"
            f"mca={quote_plus(marca)}&mod={quote_plus(modelo)}"
        )
        # Busca textual como último recurso
        q = quote_plus(f"{marca} {modelo} {versao}")
        urls.append(f"{self.BASE}/busca?q={q}&tp=c")

        return urls

    # ── HTTP com curl_cffi ────────────────────────────────────────────────────
    def _get(self, url: str) -> Optional[BeautifulSoup]:
        """
        Usa curl_cffi (impersonation Chrome120) como método primário.
        Cai para get_soup() genérico como fallback.
        """
        headers = {
            "accept": ("text/html,application/xhtml+xml,application/xml;"
                       "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"),
            "accept-language": "pt-BR,pt;q=0.9,en-US;q=0.8",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        }

        if _CFFI_OK:
            for attempt in range(1, 4):
                try:
                    _log(f"curl_cffi GET {url} (tentativa {attempt})")
                    session = cffi_requests.Session()
                    resp = session.get(
                        url, headers=headers,
                        impersonate="chrome120", timeout=30,
                    )
                    if resp.status_code == 404:
                        _log("404 — URL inexistente")
                        return None
                    if resp.status_code != 200:
                        _log(f"HTTP {resp.status_code}")
                        time.sleep(2 ** attempt)
                        continue
                    if _is_blocked(resp.text):
                        _log("Bloqueio detectado")
                        break
                    return BeautifulSoup(resp.text, "html.parser")
                except Exception as e:
                    _log(f"curl_cffi erro: {e}")
                    time.sleep(2 ** attempt)

        # Fallback para a camada genérica (cloudscraper / Playwright)
        return get_soup(url, retries=2, referer=self.BASE)

    # ── extração moderna (iCarros 2024+) ─────────────────────────────────────
    def _extrair_moderno(self, soup: BeautifulSoup) -> list[dict]:
        """
        Percorre TODOS os div.sheet-theme-container[id] e extrai
        categoria, atributo e valor.

        Problemas reais observados no HTML do iCarros:
          - IDs mistos: minúsculos (motorizacao, performance, dimensoes,
            freiosesuspensao) E maiúsculos (SEGURANCA, CONFORTO, BANCOS,
            JANELAS, SOM, OUTROS)  → solução: .upper() em todos
          - Items em dois tipos de div:
              technical-sheet-item  → motorização / performance / dimensões
              brakes-suspension-item → freios e suspensão
          - Valores "N/D" explícitos no HTML devem ser preservados como
            string vazia para não poluir o DataFrame
        """
        rows: list[dict] = []

        # Pega TODOS os containers com id, sem filtrar por nome
        containers = soup.find_all("div", class_="sheet-theme-container", id=True)

        if not containers:
            # HTML levemente diferente: tenta sem exigir a classe exata
            containers = soup.find_all("div", id=True)
            containers = [
                c for c in containers
                if "sheet-theme-container" in " ".join(c.get("class", []))
            ]

        _log(f"Containers encontrados: {[c.get('id') for c in containers]}")

        for container in containers:
            # Normaliza sempre para MAIÚSCULO independente do HTML
            categoria = container.get("id", "OUTROS").upper()

            # Captura AMBOS os tipos de item dentro do container
            items = container.find_all(
                "div",
                class_=re.compile(r"technical-sheet-item|brakes-suspension-item"),
            )

            _log(f"  [{categoria}] {len(items)} itens")

            for item in items:
                # ── Nome do campo ────────────────────────────────────────────
                label_tag = item.find(
                    "span", class_=re.compile(r"ids_textStyle_label_medium_bold")
                )
                if not label_tag:
                    continue

                atributo = _limpar(label_tag.get_text())
                if not atributo or atributo == "N/D":
                    continue

                # ── Valor ────────────────────────────────────────────────────
                valor = self._extrair_valor(item)

                rows.append({
                    "Categoria": categoria,
                    "Atributo":  atributo,
                    "Valor":     valor,   # mantém "N/D" — filtragem fica no DataFrame
                })

        return rows

    def _extrair_valor(self, item) -> str:
        """
        Hierarquia de extração baseada no HTML real do iCarros:

          A) span.ids_textStyle_label_medium_regular
             → valores de texto: Motor, Câmbio, Combustível, Potência, etc.
             → trata <br> convertendo para " | " antes de limpar
             → ignora se o texto resultante for apenas "N/D"

          B) p.ids_textStyle_label_small_regular
             → valores de parágrafo: Suspensão dianteira/traseira, Freios

          C) img[src*=check_circle]
             → equipamento presente (Segurança, Conforto, Bancos, Som, etc.)
             → retorna "Sim"

          D) fallback: qualquer span que não seja o label bold

        Em todos os casos, "N/D" é retornado literalmente para permitir
        que o caller decida filtrar ou não.
        """
        # A — span de valor principal (motorização, performance, dimensões)
        val_span = item.find(
            "span", class_=re.compile(r"ids_textStyle_label_medium_regular")
        )
        if val_span:
            # Trata <br> como separador antes de extrair texto
            for br in val_span.find_all("br"):
                br.replace_with(" | ")
            raw = _limpar(val_span.get_text())
            # Remove separadores soltos no início/fim
            raw = raw.strip(" |").strip()
            if raw:
                return raw

        # B — parágrafo (freios/suspensão) — class exata conforme HTML real
        val_p = item.find(
            "p", class_=re.compile(r"ids_textStyle_label_small_regular")
        )
        if val_p:
            raw = _limpar(val_p.get_text())
            if raw:
                return raw

        # C — ícone check_circle (todos os equipamentos booleanos)
        #     O src pode ser relativo (/comum/ids/...) ou absoluto
        val_img = item.find(
            "img",
            src=re.compile(r"check_circle", re.I),
        )
        if val_img:
            return "Sim"

        # D — fallback conservador: primeiro span que não seja o label
        label_text = ""
        label_tag = item.find("span", class_=re.compile(r"bold"))
        if label_tag:
            label_text = label_tag.get_text(strip=True)

        for sp in item.find_all("span"):
            txt = _limpar(sp.get_text())
            if txt and txt != label_text and len(txt) > 0:
                cls = " ".join(sp.get("class", []))
                if "bold" not in cls.lower():
                    return txt

        return "N/D"

    # ── exportação DataFrame (usada pelo orquestrador) ─────────────────────
    @staticmethod
    def rows_para_dataframe(rows: list[dict], incluir_nd: bool = False) -> "pd.DataFrame":
        """
        Converte a lista de rows brutas para DataFrame pandas.

        Args:
            rows:       lista de dicts com Categoria/Atributo/Valor
            incluir_nd: se False (padrão), remove linhas onde Valor == "N/D"
        """
        if not _PANDAS_OK:
            raise ImportError("pandas não instalado — pip install pandas openpyxl")

        df = pd.DataFrame(rows, columns=["Categoria", "Atributo", "Valor"])

        # Remove duplicatas preservando a primeira ocorrência
        df = df.drop_duplicates(subset=["Categoria", "Atributo"], keep="first")

        if not incluir_nd:
            df = df[df["Valor"] != "N/D"]

        # Ordena por categoria conforme ordem natural do iCarros
        ordem = [
            "MOTORIZACAO", "PERFORMANCE", "DIMENSOES", "FREIOSESUSPENSAO",
            "SEGURANCA", "CONFORTO", "JANELAS", "BANCOS", "SOM", "OUTROS",
        ]
        df = df.reset_index(drop=True)
        df["_ordem"] = df["Categoria"].apply(
            lambda c: ordem.index(c) if c in ordem else len(ordem)
        )
        df = (
            df.sort_values(["_ordem"])
              .drop(columns=["_ordem"])
              .reset_index(drop=True)
        )

        return df

    # ── extração legada (tabelas HTML) ────────────────────────────────────────
    def _extrair_legado(self, soup: BeautifulSoup) -> list[dict]:
        rows: list[dict] = []
        for sel in [".ficha-tecnica table", "table.fichaTable",
                    "table.tabelaFicha", "table"]:
            for table in soup.select(sel):
                for row in table.find_all("tr"):
                    cols = row.find_all(["td", "th"])
                    if len(cols) >= 2:
                        rows.append({
                            "Categoria": "GERAL",
                            "Atributo":  _limpar(cols[0].get_text()),
                            "Valor":     _limpar(cols[1].get_text()),
                        })
        for dl in soup.find_all("dl"):
            for dt, dd in zip(dl.find_all("dt"), dl.find_all("dd")):
                rows.append({
                    "Categoria": "GERAL",
                    "Atributo":  _limpar(dt.get_text()),
                    "Valor":     _limpar(dd.get_text()),
                })
        seen: set[tuple] = set()
        dedup: list[dict] = []
        for r in rows:
            key = (r["Categoria"], r["Atributo"])
            if key not in seen:
                seen.add(key)
                dedup.append(r)
        return dedup

    # ── converte rows brutas → FichaTecnica ───────────────────────────────────
    def _rows_para_ficha(
        self,
        rows: list[dict],
        marca: str, modelo: str, versao: str, url: str,
    ) -> FichaTecnica:
        ficha = FichaTecnica(
            marca=marca, modelo=modelo, versao=versao,
            fonte=self.NAME, url=url,
        )

        for row in rows:
            cat   = row["Categoria"].upper()
            attr  = row["Atributo"].lower().strip()
            valor = row["Valor"]

            # Campos com N/D vão para extras com flag explícito, não para campos principais
            if not valor or valor == "N/D":
                continue

            # Mapeamento direto por categoria
            mapped = False
            campo_map = self._CAT_CAMPO.get(cat, {})
            for kw, campo in campo_map.items():
                if kw in attr:
                    if campo == "extras":
                        ficha.extras[row["Atributo"]] = valor
                    elif not getattr(ficha, campo, ""):
                        setattr(ficha, campo, valor)
                    mapped = True
                    break

            # Fallback: mapeamento global por keywords
            if not mapped:
                _apply_pair(row["Atributo"], valor, ficha)

        # Extrai ano da URL se não mapeado
        if not ficha.ano:
            m = re.search(r"\b(20\d{2})\b", url)
            if m:
                ficha.ano = m.group(1)

        return ficha


class CarrosDotComScraper:
    NAME = "carros.com.br"
    BASE = "https://www.carros.com.br"

    def buscar(self, marca: str, modelo: str, versao: str) -> Optional[FichaTecnica]:
        for url in self._candidatos(marca, modelo):
            print(f"  → {self.NAME}: {url}")
            soup = get_soup(url, retries=3, referer=self.BASE)
            if not soup:
                continue
            ficha = FichaTecnica(marca=marca, modelo=modelo, versao=versao,
                                 fonte=self.NAME, url=url)
            self._extrair(soup, ficha)
            if ficha.campos_preenchidos() >= 3:
                return ficha
        return None

    def _candidatos(self, marca: str, modelo: str) -> list[str]:
        m, mo = slugify(marca), slugify(modelo)
        return [
            f"{self.BASE}/{m}/{mo}/ficha-tecnica/",
            f"{self.BASE}/{m}/{mo}/",
        ]

    def _extrair(self, soup: BeautifulSoup, ficha: FichaTecnica):
        for sel in [".ficha-tecnica", ".spec-table", ".especificacoes",
                    "table", "dl"]:
            for el in soup.select(sel):
                _parse_element(el, ficha)
        for li in soup.select("li"):
            spans = li.find_all("span")
            if len(spans) >= 2:
                _apply_pair(spans[0].get_text(strip=True),
                            spans[1].get_text(strip=True), ficha)
                
class CarScraper:
    def __init__(self):
        self.scrapers = [
            ICarrosScraper(),
            CarrosDotComScraper()
        ]

    def buscar(self, marca: str, modelo: str, versao: str = "") -> Optional[FichaTecnica]:
        print(f"\n🔍  {marca} {modelo} {versao or '(sem versão)'}")
        print("─" * 55)

        melhor: Optional[FichaTecnica] = None

        for scraper in self.scrapers:
            print(f"\n▶ [{scraper.NAME}]")
            try:
                ficha = scraper.buscar(marca, modelo, versao)
            except KeyboardInterrupt:
                raise
            except Exception:
                print("  ❌ Erro inesperado")
                if DEBUG:
                    traceback.print_exc()
                continue

            if not ficha:
                print("  ⚠️  Sem resultado")
                continue

            n = ficha.campos_preenchidos()
            print(f"  ✅ {n} campo(s) preenchido(s)")

            if melhor is None or n > melhor.campos_preenchidos():
                melhor = ficha

            if n >= 8:          # suficientemente completo → para
                break

        if melhor and melhor.campos_preenchidos() >= 2:
            return melhor

        print("\n❌  Nenhum scraper retornou dados suficientes.")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# EXIBIÇÃO
# ═══════════════════════════════════════════════════════════════════════════════
GRUPOS_EXIB = [
    ("🔧 MOTOR",
     ["motor","potencia","torque","combustivel","cilindrada",
      "cilindros","valvulas","alimentacao","compressao"]),
    ("⚙️  TRANSMISSÃO",
     ["transmissao","tracao","marchas"]),
    ("📐 DIMENSÕES",
     ["comprimento","largura","altura","entre_eixos",
      "peso","capacidade_porta_malas","tanque"]),
    ("🏎️  DESEMPENHO",
     ["velocidade_maxima","aceleracao_0_100","consumo_urbano","consumo_estrada"]),
    ("🔩 SUSPENSÃO / FREIOS",
     ["suspensao_dianteira","suspensao_traseira",
      "freios_dianteiros","freios_traseiros","pneus","rodas"]),
]

LABELS = {
    "motor":"Motor",           "potencia":"Potência (cv)",
    "torque":"Torque (Nm)",    "combustivel":"Combustível",
    "cilindrada":"Cilindrada", "cilindros":"Cilindros",
    "valvulas":"Válvulas",     "alimentacao":"Alimentação",
    "compressao":"Compressão", "transmissao":"Transmissão",
    "tracao":"Tração",         "marchas":"Marchas",
    "comprimento":"Comprimento (mm)", "largura":"Largura (mm)",
    "altura":"Altura (mm)",    "entre_eixos":"Entre-eixos (mm)",
    "peso":"Peso (kg)",        "capacidade_porta_malas":"Porta-malas (L)",
    "tanque":"Tanque (L)",     "velocidade_maxima":"Vel. Máxima",
    "aceleracao_0_100":"0-100 km/h", "consumo_urbano":"Consumo urbano",
    "consumo_estrada":"Consumo estrada",
    "suspensao_dianteira":"Susp. dianteira",
    "suspensao_traseira":"Susp. traseira",
    "freios_dianteiros":"Freios diant.",
    "freios_traseiros":"Freios tras.",
    "pneus":"Pneus", "rodas":"Rodas",
}


def exibir(ficha: FichaTecnica):
    sep = "═" * 58
    print(f"\n{sep}")
    print(f"  🚗  {ficha.marca.upper()} {ficha.modelo.upper()}")
    if ficha.versao:
        print(f"      Versão : {ficha.versao}")
    if ficha.ano:
        print(f"      Ano    : {ficha.ano}")
    print(f"      Fonte  : {ficha.fonte}")
    print(sep)

    for titulo, campos in GRUPOS_EXIB:
        dados = [(LABELS.get(c, c), getattr(ficha, c))
                 for c in campos if getattr(ficha, c, "")]
        if not dados:
            continue
        print(f"\n  {titulo}")
        print("  " + "─" * 54)
        for k, v in dados:
            print(f"  {k:<26} {v}")

    if ficha.extras:
        print(f"\n  📋 OUTROS")
        print("  " + "─" * 54)
        for k, v in list(ficha.extras.items())[:20]:
            print(f"  {k:<26} {v}")

    print(f"\n  🔗 {ficha.url}")
    print(sep)
    print(f"  Total de campos preenchidos: {ficha.campos_preenchidos()}")


def salvar_xlsx(ficha: FichaTecnica, caminho: str):
    """Exporta a ficha para Excel usando as rows brutas do iCarros (se disponível)."""
    if not _PANDAS_OK:
        print("⚠️  pandas não instalado — use: pip install pandas openpyxl")
        return

    raw_rows = getattr(ficha, "_raw_rows", None)

    if raw_rows:
        df = ICarrosScraper.rows_para_dataframe(raw_rows)
    else:
        # Monta DataFrame a partir dos campos do dataclass
        ignore = {"extras", "fonte", "url", "_raw_rows"}
        records = []
        d = asdict(ficha)
        for k, v in d.items():
            if k in ignore:
                continue
            if k == "extras":
                continue
            if v:
                records.append({"Categoria": "FICHA", "Atributo": k, "Valor": v})
        for k, v in ficha.extras.items():
            records.append({"Categoria": "EXTRAS", "Atributo": k, "Valor": v})
        df = pd.DataFrame(records)

    df.to_excel(caminho, index=False)
    print(f"\n📊 Excel salvo em: {caminho}  ({len(df)} linhas)")


def salvar_json(ficha: FichaTecnica, caminho: str):
    """Exporta a ficha para JSON."""
    with open(caminho, "w", encoding="utf-8") as f:
        d = asdict(ficha)
        d.pop("_raw_rows", None)          # não serializa cache interno
        json.dump(d, f, ensure_ascii=False, indent=2)
    print(f"\n💾 JSON salvo em: {caminho}")


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════
def main():
    global DEBUG

    parser = argparse.ArgumentParser(
        description="AutoSpec — Scraper de Ficha Técnica de Carros",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemplos:
  python car_scraper.py --marca Ford     --modelo "Ranger Raptor" --versao "3.0-gtdi-cd-raptor-auto-4wd-2026-2026"
  python car_scraper.py --marca Toyota   --modelo Corolla         --json corolla.json
  python car_scraper.py --marca Honda    --modelo Civic           --xlsx civic.xlsx --debug
  python car_scraper.py --marca Chevrolet --modelo Onix           --json onix.json --xlsx onix.xlsx
        """,
    )
    parser.add_argument("--marca",  required=True, help="Ex: Ford")
    parser.add_argument("--modelo", required=True, help="Ex: 'Ranger Raptor'")
    parser.add_argument("--versao", default="",    help="Ex: '3.0 V6 2026'")
    parser.add_argument("--json",   default="",    help="Salvar resultado em JSON")
    parser.add_argument("--xlsx",   default="",    help="Salvar resultado em Excel (.xlsx)")
    parser.add_argument("--debug",  action="store_true",
                        help="Exibe logs detalhados de cada requisição")
    args = parser.parse_args()
    DEBUG = args.debug

    print("\n" + "═"*58)
    print("  AutoSpec — Ficha Técnica de Carros")
    print("═"*58)
    print(f"  curl_cffi    : {'✅ disponível' if _CFFI_OK    else '❌  pip install curl_cffi'}")
    print(f"  Playwright   : {'✅ disponível' if _PW_OK    else '❌  pip install playwright && playwright install chromium'}")
    print(f"  Cloudscraper : {'✅ disponível' if _CLOUD_OK else '❌  pip install cloudscraper'}")
    print(f"  Pandas/Excel : {'✅ disponível' if _PANDAS_OK else '❌  pip install pandas openpyxl'}")

    scraper = CarScraper()
    ficha = scraper.buscar(args.marca, args.modelo, args.versao)

    if ficha:
        exibir(ficha)
        if args.json:
            salvar_json(ficha, args.json)
        if args.xlsx:
            salvar_xlsx(ficha, args.xlsx)
    else:
        print("\n❌ Não foi possível obter a ficha técnica.")
        print("\n  Dicas para resolver:")
        print("  1. Verifique a grafia: --marca Toyota --modelo Corolla")
        print("  2. pip install cloudscraper")
        print("  3. pip install playwright && playwright install chromium")
        print("  4. Use --debug para ver detalhes dos erros")
        sys.exit(1)


if __name__ == "__main__":
    main()
