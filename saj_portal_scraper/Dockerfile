ARG BUILD_FROM
FROM ${BUILD_FROM}

ENV LANG=C.UTF-8

ARG BUILD_ARCH
ARG GECKODRIVER_VERSION=v0.34.0

#ENV S6_BEHAVIOUR_IF_STAGE2_FAILS=2

RUN apk add --no-cache \
    firefox-esr \
    py3-pip \
    python3-dev \
    gcc \
    musl-dev \
    libffi-dev \
    openssl-dev \
    wget \
    ca-certificates \
    unzip \
    bash \
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
    ttf-freefont

# Instala o geckodriver manualmente pois não existe pacote oficial no Alpine 3.19
RUN set -ex; \
    case "${BUILD_ARCH}" in \
    amd64) ARCH="linux64" ;; \
    aarch64) ARCH="aarch64" ;; \
    armv7) ARCH="arm7hf" ;; \
    *) ARCH="linux64" ;; \
    esac; \
    wget -qO- "https://github.com/mozilla/geckodriver/releases/download/${GECKODRIVER_VERSION}/geckodriver-${GECKODRIVER_VERSION}-${ARCH}.tar.gz" | tar xz -C /usr/local/bin; \
    chmod +x /usr/local/bin/geckodriver

# Define o diretório de trabalho
WORKDIR /app

# Copia apenas o requirements.txt primeiro
COPY requirements.txt .

# Instala pacotes Python
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

# Copia todo o código da aplicação (incluindo run.sh)
COPY . .

#COPY etc /etc

RUN chmod a+x run.sh

#RUN chmod a+x /etc/services.d/saj/run

#RUN chmod +x run.sh && chmod +x /etc/services.d/saj/run

#RUN ls -l /etc/services.d/saj

#ENTRYPOINT [ "/init" ]

# Define run.sh como o comando de entrada
CMD [ "/app/run.sh" ]