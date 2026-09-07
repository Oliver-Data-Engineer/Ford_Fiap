from typing import Optional
import asyncio
from bs4 import BeautifulSoup
from curl_cffi import requests  # Usando a API direta que funcionou no teste simples

class CarrosWeb:
    def __init__(self):
        self.base_url = "https://www.carrosnaweb.com.br"

    async def _get(self, url: str, retries: int = 3) -> Optional[BeautifulSoup]:
        for attempt in range(1, retries + 1):
            try:
                # Executa a requisição de forma isolada, igual ao teste simples
                resp = requests.get(url, impersonate="chrome", timeout=15.0)

                if resp.status_code == 404:
                    print(f"[!] Página não encontrada (404): {url}")
                    return None

                if resp.status_code != 200 or "erro.asp" in str(resp.url):
                    print(f"[!] Erro {resp.status_code} ou bloqueio. Tentativa {attempt}/{retries}. Aguardando...")
                    await asyncio.sleep(2 ** attempt)
                    continue

                return BeautifulSoup(resp.text, "html.parser")

            except Exception as e:
                print(f"[!] Exceção na requisição: {e}")
                if attempt == retries:
                    return None
                await asyncio.sleep(2 ** attempt)

        return None
        
    async def obter_fabricantes(self) -> list:
            # Usa o _get() para fazer a requisição já blindada contra bloqueios
            url = f"{self.base_url}/catalogo.asp"
            soup = await self._get(url)
            
            if not soup:
                raise ConnectionError("Falha ou bloqueio ao acessar o diretório de fabricantes")
                
            select_fabricante = soup.find("select", id="fabricante")
            
            if not select_fabricante:
                raise ValueError("Elemento <select id='fabricante'> não encontrado no HTML")
                
            fabricantes = []
            for option in select_fabricante.find_all("option"):
                nome = option.text.strip()
                valor = option.get("value", "").strip()
                
                if valor:
                    fabricantes.append({
                        "id_busca": valor,
                        "nome_exibicao": nome
                    })
                    
            return fabricantes

    async def obter_modelos(self, fabricante: str) -> list:
            fabricante_formatado = fabricante.title()
            url = f"{self.base_url}/catalogo.asp?fabricante={fabricante_formatado}"
            
            soup = await self._get(url)
                
            if not soup:
                raise ConnectionError(f"Falha ao acessar os modelos da fabricante {fabricante}")
                
            select_modelos = soup.find("select", id="varnome")
            
            if not select_modelos:
                raise ValueError("Elemento <select id='varnome'> não encontrado no HTML")
                
            modelos = []
            for option in select_modelos.find_all("option"):
                nome = option.text.strip()
                valor = option.get("value", "").strip()
                
                if valor:
                    modelos.append({
                        "fabricante": fabricante_formatado,
                        "id_busca": valor,
                        "nome_exibicao": nome
                    })
                    
            return modelos
        
    async def extrair_versoes(self, url: str) -> list[dict]:
            # O método _get já cuida de verificar o erro.asp internamente!
            soup = await self._get(url)
                
            if not soup:
                return []
            
            versoes_encontradas = []
            celulas_info = soup.find_all("td", align="left")

            for td in celulas_info:
                link_tag = td.find("a", href=True)
                if link_tag and "fichadetalhe.asp" in link_tag["href"]:
                    href = link_tag["href"]
                    codigo = href.split("codigo=")[-1]
                    url_completa = f"{self.base_url}/{href}"
                    
                    textos = list(link_tag.stripped_strings)
                    if len(textos) >= 3:
                        versao_limpa = textos[1].replace('\xa0', ' ')
                        versoes_encontradas.append({
                            "id_veiculo": codigo,
                            "fabricante": textos[0],
                            "versao": versao_limpa,
                            "ano": textos[2],
                            "url_ficha": url_completa
                        })
                        
            return versoes_encontradas
        
    async def obter_todas_urls_paginacao(self, url_base: str) -> list[str]:
            urls_para_raspar = [url_base]
            
            # Visita a home primeiro para pegar cookies novos antes de varrer
            await self._get(self.base_url)
            
            soup = await self._get(url_base)
            
            if not soup:
                return urls_para_raspar
                
            fonts = soup.find_all('font')
            total_paginas = 1 
            
            for font in fonts:
                texto = font.get_text().strip()
                match = re.search(r'P.*?gina\s+\d+\s+de\s+(\d+)', texto, re.IGNORECASE)
                if match:
                    total_paginas = int(match.group(1))
                    break

            if total_paginas > 1:
                for i in range(2, total_paginas + 1):
                    nova_url = f"{url_base}&curpage={i}"
                    
                    # Testamos rapidamente se a página 2, 3, etc. existe
                    # retries=1 faz o processo ser mais ágil
                    soup_teste = await self._get(nova_url, retries=1)
                    
                    if soup_teste:
                        urls_para_raspar.append(nova_url)
                        
            return urls_para_raspar