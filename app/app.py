from flask import Flask, render_template, request

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Veículo selecionado — vem por query string (?marca=...&modelo=...&versao=...)
# em todas as páginas do dashboard. Esse helper evita repetir as três linhas
# de request.args.get em cada rota abaixo.
# ---------------------------------------------------------------------------

def veiculo_selecionado():
    return {
        "marca": request.args.get("marca", ""),
        "modelo": request.args.get("modelo", ""),
        "versao": request.args.get("versao", ""),
    }


@app.route("/")
def busca():
    return render_template("busca/index.html")


@app.route("/dashboard")
def dashboard():
    return render_template("dashboard/index.html", **veiculo_selecionado())


# ---------------------------------------------------------------------------
# Web Scraping Mode
# ---------------------------------------------------------------------------

@app.route("/scraping")
def scraping():
    return render_template("scraping/index.html", **veiculo_selecionado())


@app.route("/scraping/executar", methods=["POST"])
def scraping_executar():
    # TODO: disparar a varredura de fato e devolver as peças encontradas.
    return {"pecas": []}


# ---------------------------------------------------------------------------
# YouTube Search Mode
# ---------------------------------------------------------------------------

@app.route("/youtube")
def youtube():
    return render_template("youtube/index.html", **veiculo_selecionado())


@app.route("/youtube/executar", methods=["POST"])
def youtube_executar():
    # TODO: buscar vídeos, transcrever e classificar o sentimento.
    return {"videos": []}


# ---------------------------------------------------------------------------
# X Search Mode
# (endpoint chamado "x_search" porque "x" sozinho colide com o módulo x do
#  Python em alguns editores/linters — mas a URL continua sendo /x)
# ---------------------------------------------------------------------------

@app.route("/x")
def x_search():
    return render_template("x/index.html", **veiculo_selecionado())


@app.route("/x/executar", methods=["POST"])
def x_executar():
    # TODO: coletar menções na plataforma X e classificar o sentimento.
    return {"mencoes": []}


# ---------------------------------------------------------------------------
# Gov Mode
# ---------------------------------------------------------------------------

@app.route("/gov")
def gov():
    return render_template("gov/index.html", **veiculo_selecionado())


@app.route("/gov/consultar", methods=["POST"])
def gov_consultar():
    # TODO: consultar a base oficial do governo pela placa/Renavam.
    return {"dados": None}


# ---------------------------------------------------------------------------
# Pipeline Mode
# ---------------------------------------------------------------------------

@app.route("/pipeline")
def pipeline():
    return render_template("pipeline/index.html", **veiculo_selecionado())


@app.route("/pipeline/criar", methods=["POST"])
def pipeline_criar():
    # TODO: gravar o agendamento (modo + frequência) e devolver a lista atualizada.
    return {"pipelines": []}


# ---------------------------------------------------------------------------
# Compare Mode
# ---------------------------------------------------------------------------

@app.route("/compare")
def compare():
    return render_template("compare/index.html", **veiculo_selecionado())


@app.route("/compare/rodar", methods=["POST"])
def compare_rodar():
    # TODO: reunir os dados já coletados de cada carro informado e montar as linhas do comparativo.
    return {"carros": [], "linhas": []}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
