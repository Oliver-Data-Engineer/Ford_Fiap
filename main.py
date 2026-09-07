from fastapi import FastAPI, HTTPException
import httpx

# Importe a classe que criamos. Ajuste o caminho conforme o local do seu arquivo principal
from src.utils.CarrosWeb import CarrosWeb

app = FastAPI(title="API Carros na Web")

# Instancia a classe utilitária
carros_web = CarrosWeb()

@app.get("/api/fabricantes")
async def listar_fabricantes():
    try:
        # Abrindo a sessão HTTP compartilhada com os cabeçalhos anti-bloqueio
        async with httpx.AsyncClient(timeout=15.0, headers=carros_web.headers, follow_redirects=True) as client:
            
            # TRUQUE: Visita a página inicial silenciosamente para pegar o cookie (ASPSESSIONID)
            await client.get("https://www.carrosnaweb.com.br/")
            
            # Chama a função da classe externa repassando a sessão 'client'
            fabricantes = await carros_web.obter_fabricantes(client)
        
        return {
            "status": "sucesso",
            "total_fabricantes": len(fabricantes),
            "dados": fabricantes
        }
        
    except ConnectionError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro interno do servidor: {str(e)}")


@app.get("/api/modelos")
async def listar_modelos(fabricante: str):
    try:
        # Sessão HTTP protegida
        async with httpx.AsyncClient(timeout=15.0, headers=carros_web.headers, follow_redirects=True) as client:
            
            # Validação de sessão (Cookie)
            await client.get("https://www.carrosnaweb.com.br/")
            
            # Chama a função repassando a sessão e o fabricante
            modelos = await carros_web.obter_modelos(client, fabricante)
        
        return {
            "status": "sucesso",
            "total_modelos": len(modelos),
            "dados": modelos
        }
        
    except ConnectionError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro interno do servidor: {str(e)}")


@app.get("/api/versoes")
async def listar_versoes(fabricante: str, modelo: str):
    url_alvo = f"https://www.carrosnaweb.com.br/catalogo.asp?fabricante={fabricante}&varnome={modelo}"
    dados_compilados = []
    
    try:
        # Sessão HTTP protegida para todo o fluxo de paginação e extração
        async with httpx.AsyncClient(timeout=15.0, headers=carros_web.headers, follow_redirects=True) as client:
            
            # Validação de sessão (Cookie)
            await client.get("https://www.carrosnaweb.com.br/")
            
            # 1. Obtém todas as URLs de paginação usando a sessão autenticada
            urls_pagina = await carros_web.obter_todas_urls_paginacao(client, url_alvo)
            
            # 2. Varre cada página encontrada
            for url in urls_pagina:
                dados_da_pagina = await carros_web.extrair_versoes(client, url) 
                
                # 3. 'extend' junta os itens de 'dados_da_pagina' na lista principal
                dados_compilados.extend(dados_da_pagina)
        
        # 4. Retorna os dados completos
        return {
            "status": "sucesso",
            "total": len(dados_compilados),
            "paginas": len(urls_pagina),
            "dados": dados_compilados
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro interno do servidor: {str(e)}")