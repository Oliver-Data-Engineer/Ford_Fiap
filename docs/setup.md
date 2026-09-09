# Guia de Instalação e Execução Local

Este guia cobre todos os passos para rodar o projeto do zero: ambiente virtual, instalação de dependências, configuração e inicialização dos dois servidores (FastAPI e Flask).

---

## Pré-requisitos

| Requisito | Versão mínima | Verificar |
|---|---|---|
| Python | 3.11 | `python --version` |
| pip | qualquer | `pip --version` |
| Git | qualquer | `git --version` |

> **Windows:** use sempre o **PowerShell** ou **Prompt de Comando** — não o Git Bash para passos de `venv` e `pip`.

---

## 1. Clonar o repositório

```bash
git clone <url-do-repositorio>
cd Ford_Fiap
```

---

## 2. Criar e ativar o ambiente virtual

O projeto usa **um único `venv`** na raiz para os dois servidores.

**Criar o venv:**
```bash
python -m venv venv
```

**Ativar — Windows (PowerShell):**
```powershell
.\venv\Scripts\Activate.ps1
```

**Ativar — Windows (CMD):**
```cmd
venv\Scripts\activate.bat
```

**Ativar — macOS / Linux:**
```bash
source venv/bin/activate
```

Após ativar, o prompt exibirá `(venv)` no início.

---

## 3. Instalar as dependências

O projeto tem **dois arquivos de requisitos**:

| Arquivo | Para quê |
|---|---|
| `requirements.txt` | FastAPI, scrapers, curl_cffi, uvicorn |
| `app/requirements.txt` | Flask, httpx |

**Instalar tudo de uma vez (recomendado):**
```bash
pip install -r requirements.txt
pip install -r app/requirements.txt
```

**Dependências principais instaladas:**

| Pacote | Versão | Função |
|---|---|---|
| `fastapi` | 0.141.1 | Framework da API |
| `uvicorn` | 0.52.4 | Servidor ASGI para a FastAPI |
| `flask` | 3.1.3 | Servidor web do frontend |
| `httpx` | 0.28.1 | HTTP client (proxy Flask → FastAPI) |
| `curl_cffi` | 0.16.3 | HTTP com TLS impersonation (scrapers) |
| `beautifulsoup4` | 4.15.0 | Parse de HTML |
| `pydantic` | 2.13.5 | Validação de dados da API |
| `requests` | 2.34.2 | HTTP client de fallback |

> **Nota sobre `curl_cffi`:** este pacote instala binários pré-compilados. Se a instalação falhar, verifique se o pip está atualizado: `pip install --upgrade pip` e tente novamente.

---

## 4. Verificar a instalação

```bash
python -c "import fastapi, flask, curl_cffi, bs4, httpx; print('OK — todas as dependências disponíveis')"
```

Saída esperada:
```
OK — todas as dependências disponíveis
```

---

## 5. Iniciar a FastAPI (porta 8000)

A FastAPI é o motor de dados — deve ser iniciada **antes** do Flask.

**Terminal 1:**
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Saída esperada:
```
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     Started reloader process
INFO:     Started server process
INFO:     Waiting for application startup.
INFO:     Application startup complete.
```

**Verificar que está rodando:**
```bash
curl http://localhost:8000/docs
# ou abra http://localhost:8000/docs no browser
```

A flag `--reload` reinicia automaticamente a API quando você salva um arquivo Python. Remova em produção.

---

## 6. Iniciar o Flask (porta 5000)

O Flask serve o frontend e atua como proxy para a FastAPI.

**Terminal 2** (com o venv ativado):
```bash
flask --app app/app.py run --port 5000 --debug
```

**Alternativa (equivalente):**
```bash
python app/app.py
```

Saída esperada:
```
 * Serving Flask app 'app/app.py'
 * Debug mode: on
 * Running on http://0.0.0.0:5000
 * Restarting with stat
 * Debugger is active!
```

**Verificar que está rodando:**

Abra `http://localhost:5000` no browser — você deve ver a tela de seleção de veículo.

---

## 7. Fluxo de uso após inicialização

```
1. Abra http://localhost:5000
2. Selecione a Marca (ex: Ford)
3. Selecione o Modelo (ex: Ranger)
4. Selecione a Versão (ex: Ranger Raptor 3.0 V6 2026)
5. Clique em "Buscar" → redireciona para /scraping
6. Opcionalmente, preencha os atributos desejados
7. Clique em "Buscar especificações"
8. Aguarde o resultado (até 30 s)
9. Exporte em CSV se necessário
```

---

## 8. Testar a API diretamente

Com a FastAPI rodando, você pode testar os endpoints via Swagger UI ou `curl`:

**Listar fabricantes:**
```bash
curl http://localhost:8000/specs/fabricantes
```

**Listar modelos da Ford:**
```bash
curl "http://localhost:8000/specs/modelos?fabricante=Ford"
```

**Listar versões da Ranger:**
```bash
curl "http://localhost:8000/specs/versoes?fabricante=Ford&modelo=Ranger"
```

**Buscar specs da Ranger Raptor:**
```bash
curl -X POST http://localhost:8000/specs/executar \
  -H "Content-Type: application/json" \
  -d '{"fabricante":"Ford","modelo":"Ranger","versao":"Raptor"}'
```

**Via Swagger UI:** acesse `http://localhost:8000/docs` e use a interface interativa.

---

## 9. Testar o scraper standalone (CLI)

O `scraper.py` pode ser usado independentemente dos servidores:

```bash
# Busca básica
python scraper.py --marca Ford --modelo "Ranger Raptor" --versao "3.0 V6"

# Com exportação
python scraper.py --marca Ford --modelo "Ranger Raptor" --json saida.json

# Com debug detalhado
python scraper.py --marca Ford --modelo "Ranger Raptor" --debug
```

---

## Resumo rápido

```bash
# 1. Clonar e entrar na pasta
git clone <url> && cd Ford_Fiap

# 2. Ambiente virtual
python -m venv venv
.\venv\Scripts\Activate.ps1   # Windows PowerShell

# 3. Dependências
pip install -r requirements.txt
pip install -r app/requirements.txt

# 4. Terminal 1 — FastAPI
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# 5. Terminal 2 — Flask
flask --app app/app.py run --port 5000 --debug

# 6. Abrir no browser
# http://localhost:5000
```

---

## Solução de problemas comuns

**`ModuleNotFoundError: No module named 'curl_cffi'`**
```bash
pip install --upgrade pip
pip install curl_cffi
```

**`Address already in use` na porta 8000 ou 5000**

Windows PowerShell:
```powershell
# Verificar o que está usando a porta
netstat -ano | findstr :8000

# Matar o processo pelo PID encontrado
taskkill /PID <PID> /F
```

**`ImportError` no `src/utils/`**

Certifique-se de executar os comandos a partir da **raiz do projeto** (`Ford_Fiap/`), não de dentro de `src/` ou `app/`.

**Scrapers retornam lista vazia**

Os sites externos (carrosnaweb, icarros) podem estar temporariamente indisponíveis ou aplicando bloqueios. Tente novamente após alguns minutos. Use `--debug` no `scraper.py` para ver os detalhes da requisição.
