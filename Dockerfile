# Imagem única, compartilhada pelos dois serviços (fastapi e flask) do
# docker-compose.yml — o comando executado é que muda por serviço.
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Dependências Python + navegador do Playwright (Scraper Mode).
# --with-deps resolve as libs de sistema do Chromium (libnss3, libatk etc.)
# via apt — exige a imagem base ser Debian/Ubuntu, como esta.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium

COPY . .

EXPOSE 5000 8000

# Sobrescrito por `command:` no docker-compose.yml para cada serviço —
# este é só o fallback caso o container rode isolado.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
