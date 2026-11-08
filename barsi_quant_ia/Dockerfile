FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    BARSI_ENV=production \
    BARSI_SQLITE_DB_PATH=/app/memory/barsi_memory.db \
    BARSI_CACHE_DIR=/app/cache

WORKDIR /app

# Dependências mínimas de sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copia metadados e código fonte
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Instala dependências do projeto
RUN pip install --upgrade pip && pip install -e .

# Cria diretórios persistentes
RUN mkdir -p /app/memory /app/cache /app/extratos

# Usuário não-root para segurança
RUN useradd -m -u 1000 barsi && chown -R barsi:barsi /app
USER barsi

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["python", "-m", "barsi", "api", "--host", "0.0.0.0", "--port", "8000"]
