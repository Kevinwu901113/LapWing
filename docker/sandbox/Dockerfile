FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl wget jq \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    requests beautifulsoup4 httpx lxml \
    pandas numpy \
    pyyaml toml

RUN useradd -m -s /bin/bash sandboxuser
USER sandboxuser

WORKDIR /workspace
