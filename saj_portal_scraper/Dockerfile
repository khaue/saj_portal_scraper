#ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:3.19
#FROM ${BUILD_FROM}
FROM ghcr.io/home-assistant/base:latest


ENV LANG=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apk add --no-cache \
    python3 \
    py3-pip \
    bash \
    ca-certificates \
    jq \
    tzdata \
    && apk add --no-cache --virtual .build-deps \
    gcc \
    musl-dev \
    python3-dev

# Diretório da aplicação
WORKDIR /app

# Copia requirements primeiro para aproveitar cache
COPY requirements.txt .

# Instala dependências Python
RUN python3 -m venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --no-cache-dir -r requirements.txt \
    && apk del .build-deps


# Copia restante da aplicação
COPY . .

# Instala script principal
COPY run.sh /run.sh
RUN chmod a+x /run.sh

# Comando inicial
CMD [ "/run.sh" ]