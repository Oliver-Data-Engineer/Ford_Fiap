"""
app.py
Servidor Flask — renderiza os templates do dashboard e atua como proxy
reverso para a API FastAPI (porta 8000), evitando problemas de CORS no browser.

Inicia com:
    flask --app app/app.py run --port 5000 --debug
  ou:
    python app/app.py
"""
import os

import httpx
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

# URL base da API FastAPI — 127.0.0.1 quando os dois processos rodam na
# mesma máquina (dev local); no docker-compose, FASTAPI_BASE_URL aponta
# para o serviço "fastapi" (containers em hosts de rede separados).
FASTAPI_BASE = os.getenv("FASTAPI_BASE_URL", "http://127.0.0.1:8000")


# ---------------------------------------------------------------------------
# Helper: veículo selecionado via query string
# ---------------------------------------------------------------------------

def veiculo_selecionado() -> dict:
    """Lê marca/modelo/versao da query string — presente em todas as páginas."""
    return {
        "marca":  request.args.get("marca",  ""),
        "modelo": request.args.get("modelo", ""),
        "versao": request.args.get("versao", ""),
    }


# ---------------------------------------------------------------------------
# Páginas do dashboard
# ---------------------------------------------------------------------------

@app.route("/")
def busca():
    return render_template("busca/index.html")


@app.route("/dashboard")
def dashboard():
    return render_template("dashboard/index.html", **veiculo_selecionado())


@app.route("/scraping")
def scraping():
    return render_template("scraping/index.html", **veiculo_selecionado())


@app.route("/youtube")
def youtube():
    return render_template("youtube/index.html", **veiculo_selecionado())


@app.route("/social")
def social():
    return render_template("social/index.html", **veiculo_selecionado())


@app.route("/gov")
def gov():
    return render_template("gov/index.html", **veiculo_selecionado())


@app.route("/scraper")
def scraper():
    return render_template("scraper/index.html", **veiculo_selecionado())


@app.route("/pipeline")
def pipeline():
    return render_template("pipeline/index.html", **veiculo_selecionado())


@app.route("/analytics")
def analytics():
    return render_template("analytics/index.html", **veiculo_selecionado())


@app.route("/compare")
def compare():
    return render_template("compare/index.html", **veiculo_selecionado())


@app.route("/conhecimento")
def conhecimento():
    """Base de Conhecimento — visualização do banco vetorial de grafos
    (transcrições, PDFs, comentários); todo o dado é gerado no próprio
    JavaScript da página."""
    return render_template("conhecimento/index.html", **veiculo_selecionado())


# ---------------------------------------------------------------------------
# Proxy GET — repassa listagens para a FastAPI (/specs/*)
#
# O browser chama Flask (mesma origem, sem CORS) e Flask encaminha para
# FastAPI internamente.  Qualquer rota /specs/<caminho> é suportada.
# ---------------------------------------------------------------------------

@app.route("/specs/<path:caminho>")
def specs_proxy_get(caminho: str):
    """
    Proxy transparente para GET /specs/*.
    Repassa a query string original e devolve a resposta da FastAPI.
    """
    url = f"{FASTAPI_BASE}/specs/{caminho}"
    params = request.args.to_dict(flat=False)  # preserva multi-valor

    try:
        resp = httpx.get(url, params=params, timeout=12.0)
        return jsonify(resp.json()), resp.status_code
    except httpx.TimeoutException:
        return jsonify({"erro": "Tempo limite excedido ao consultar a API."}), 504
    except Exception as exc:
        return jsonify({"erro": f"Erro interno: {exc}"}), 500


# ---------------------------------------------------------------------------
# Proxy POST — executa busca de especificações técnicas
# ---------------------------------------------------------------------------

@app.route("/scraping/executar", methods=["POST"])
def scraping_executar():
    """
    Proxy para POST /specs/executar.

    Recebe o JSON do browser, encaminha para a FastAPI e devolve
    {"especificacoes": [...]} ou {"erro": "..."}.
    """
    payload = request.get_json(force=True, silent=True) or {}

    try:
        resp = httpx.post(
            f"{FASTAPI_BASE}/specs/executar",
            json=payload,
            timeout=35.0,  # margem acima dos 30 s do AbortController no browser
        )
        return jsonify(resp.json()), resp.status_code

    except httpx.TimeoutException:
        return jsonify({"erro": "Tempo limite excedido ao consultar as fontes."}), 504
    except Exception as exc:
        return jsonify({"erro": f"Erro interno: {exc}"}), 500


# ---------------------------------------------------------------------------
# Proxy /api/* — encaminha para a FastAPI
#
# O browser fala apenas com o Flask (mesma origem, sem CORS) e o Flask
# repassa para a FastAPI.  É por aqui que passam /youtube, /prompts e
# /pipelines — o JavaScript nunca conhece a porta 8000 nem credencial alguma.
# ---------------------------------------------------------------------------

# Operações que envolvem rede externa (busca no YouTube, chamada ao LLM)
# levam bem mais tempo que uma listagem.
TIMEOUT_LEITURA = 20.0
TIMEOUT_ESCRITA = 180.0


@app.route("/api/<path:caminho>", methods=["GET", "POST", "PUT", "DELETE"])
def api_proxy(caminho: str):
    """
    Proxy transparente para a FastAPI.

    Preserva método, query string e corpo JSON, e devolve a resposta original
    (status incluído) para que o frontend trate os erros da API.
    """
    url = f"{FASTAPI_BASE}/{caminho}"
    metodo = request.method
    timeout = TIMEOUT_LEITURA if metodo == "GET" else TIMEOUT_ESCRITA

    try:
        resp = httpx.request(
            metodo,
            url,
            params=request.args.to_dict(flat=False),
            json=(request.get_json(silent=True) if metodo in ("POST", "PUT") else None),
            timeout=timeout,
        )
    except httpx.TimeoutException:
        return jsonify({"erro": "Tempo limite excedido ao consultar a API."}), 504
    except httpx.ConnectError:
        return jsonify({
            "erro": "API indisponível — inicie a FastAPI com "
                    "'uvicorn main:app --port 8000'."
        }), 503
    except Exception as exc:
        return jsonify({"erro": f"Erro interno: {exc}"}), 500

    try:
        return jsonify(resp.json()), resp.status_code
    except ValueError:
        return jsonify({"erro": "Resposta inválida da API."}), 502


# ---------------------------------------------------------------------------
# Proxies POST restantes (stubs existentes — mantidos para compatibilidade)
# ---------------------------------------------------------------------------

@app.route("/compare/rodar", methods=["POST"])
def compare_rodar():
    return jsonify({"carros": [], "linhas": []})


# ---------------------------------------------------------------------------
# Ponto de entrada direto
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
