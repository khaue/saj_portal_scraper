ARG BUILD_FROM=ghcr.io/home-assistant/base:3.19
FROM ${BUILD_FROM}

ENV LANG=C.UTF-8

ARG BUILD_ARCH
ARG GECKODRIVER_VERSION=v0.36.0

# Dependências principais
RUN apk add --no-cache \
    firefox-esr \
    python3 \
    py3-pip \
    bash \
    wget \
    ca-certificates \
    unzip \
    jq \
    tzdata \
    dbus \
    dbus-libs \
    nss \
    gtk+3.0 \
    alsa-lib \
    libxcomposite \
    libxrandr \
    libxdamage \
    libxfixes \
    libc6-compat \
    libxext \
    libx11 \
    libxcb \
    libxtst \
    libxrender \
    libsm \
    libice \
    mesa-gl \
    xvfb \
    libxshmfence \
    libdrm \
    ttf-freefont \
    fontconfig \
    freetype \
    harfbuzz \
    && apk add --no-cache --virtual .build-deps \
    gcc \
    musl-dev \
    python3-dev \
    libffi-dev \
    openssl-dev

# Instala geckodriver compatível com arquitetura
RUN set -ex; \
    case "${BUILD_ARCH}" in \
        amd64) ARCH="linux64" ;; \
        aarch64) ARCH="linux-aarch64" ;; \
        armv7) ARCH="linux-arm7hf" ;; \
        *) ARCH="linux64" ;; \
    esac; \
    URL="https://github.com/mozilla/geckodriver/releases/download/${GECKODRIVER_VERSION}/geckodriver-${GECKODRIVER_VERSION}-${ARCH}.tar.gz"; \
    echo "Downloading: $URL"; \
    wget -O /tmp/geckodriver.tar.gz "$URL"; \
    tar xzf /tmp/geckodriver.tar.gz -C /usr/local/bin; \
    chmod +x /usr/local/bin/geckodriver; \
    rm -f /tmp/geckodriver.tar.gz

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
