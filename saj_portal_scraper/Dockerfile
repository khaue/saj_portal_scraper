ARG BUILD_FROM
FROM $BUILD_FROM

ENV LANG C.UTF-8


# Instala pacotes base
RUN apk add --no-cache \
    firefox-esr \
    geckodriver \
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
    tzdata



# Define o diretório de trabalho
WORKDIR /app

# Copia apenas o requirements.txt primeiro
COPY requirements.txt .

# Instala pacotes Python
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

# Copia todo o código da aplicação (incluindo run.sh)
COPY . .

# Garante que run.sh seja executável
RUN chmod a+x run.sh

# Define run.sh como o comando de entrada
CMD [ "/app/run.sh" ]

# Remove as linhas antigas:
# ENV PYTHONPATH /app  # Geralmente não necessário com run.sh neste cenário
# CMD [ "python3", "/app/run.py" ]
