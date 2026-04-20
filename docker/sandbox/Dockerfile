FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl wget jq \
    && rm -rf /var/lib/apt/lists/* \
    && find / -perm /6000 -type f -exec chmod a-s {} + 2>/dev/null || true

RUN pip install --no-cache-dir \
    requests beautifulsoup4 httpx lxml \
    pandas numpy \
    pyyaml toml \
    matplotlib pillow scipy sympy

RUN useradd -m -s /bin/bash sandboxuser
USER sandboxuser

WORKDIR /workspace
