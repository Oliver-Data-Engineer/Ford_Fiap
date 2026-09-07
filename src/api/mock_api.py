from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx


app = FastAPI(title="API Mock Carros")

# Libera o front-end (rodando em outra porta, ex: Flask em :5000) a consumir a API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


# Dados mockados: modelos por marca
MODELOS_POR_MARCA = {
    "Ford": ["Ranger", "Ka", "Fiesta", "EcoSport"],
    "BMW": ["Serie 3", "X1", "X5", "320i"],
    "Ferrari": ["488", "F8 Tributo", "Portofino", "Roma"],
}

# Dados mockados: versões por marca + modelo
VERSOES_POR_MARCA_MODELO = {
    ("Ford", "Ranger"): ["XL 2.2 4x2", "XLS 3.2 4x4", "Limited 3.2 4x4"],
    ("Ford", "Ka"): ["SE 1.0", "SE Plus 1.5", "FreeStyle 1.5"],
    ("Ford", "Fiesta"): ["SE 1.5", "Titanium 1.6"],
    ("Ford", "EcoSport"): ["Freestyle 1.5", "Titanium 2.0"],

    ("BMW", "Serie 3"): ["320i Sport", "330i M Sport", "320d"],
    ("BMW", "X1"): ["sDrive20i", "xDrive25i"],
    ("BMW", "X5"): ["xDrive30d", "xDrive50e"],
    ("BMW", "320i"): ["GP Sport", "Active Flex"],

    ("Ferrari", "488"): ["GTB", "Spider", "Pista"],
    ("Ferrari", "F8 Tributo"): ["Coupe", "Spider"],
    ("Ferrari", "Portofino"): ["Base", "M"],
    ("Ferrari", "Roma"): ["Base", "Spider"],
}


@app.get("/api/marcas")
async def get_marcas():
    marcas = [
        'Ford',
        'BMW',
        'Ferrari']

    return {
        'data': marcas
    }


@app.get("/api/modelos")
async def get_modelos(marca: str):
    modelos = MODELOS_POR_MARCA.get(marca.title())

    if modelos is None:
        raise HTTPException(status_code=404, detail=f"Marca '{marca}' não encontrada")

    return {
        'data': modelos
    }


@app.get("/api/versoes")
async def get_versoes(marca: str, modelo: str):
    marca_formatada = marca.title()
    modelo_formatado = modelo.title()

    versoes = VERSOES_POR_MARCA_MODELO.get((marca_formatada, modelo_formatado))

    if versoes is None:
        raise HTTPException(
            status_code=404,
            detail=f"Nenhuma versão encontrada para marca '{marca}' e modelo '{modelo}'"
        )

    return {
        'data': versoes
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)