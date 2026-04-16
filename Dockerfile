FROM python:3.12-slim

# System deps para Firefox headless
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        firefox-esr \
        wget \
        ca-certificates \
        libgtk-3-0 \
        libdbus-glib-1-2 \
        libxt6 \
    && rm -rf /var/lib/apt/lists/*

# Geckodriver (versão fixa para reproducibilidade)
ARG GECKODRIVER_VERSION=0.34.0
RUN wget -q -O /tmp/gecko.tar.gz \
        "https://github.com/mozilla/geckodriver/releases/download/v${GECKODRIVER_VERSION}/geckodriver-v${GECKODRIVER_VERSION}-linux64.tar.gz" && \
    tar -xzf /tmp/gecko.tar.gz -C /usr/local/bin/ && \
    rm /tmp/gecko.tar.gz && \
    chmod +x /usr/local/bin/geckodriver

WORKDIR /app

# Instala o InstaT
COPY pyproject.toml README.md ./
COPY instat ./instat
RUN pip install --no-cache-dir -e .

# Usuário não-root
RUN useradd --create-home --shell /bin/bash instat && \
    mkdir -p /app/output /app/.instat_checkpoints /app/.instat_sessions && \
    chown -R instat:instat /app

USER instat

ENTRYPOINT ["python", "-m", "instat"]
CMD ["--help"]
