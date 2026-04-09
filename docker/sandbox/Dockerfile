FROM ubuntu:24.04

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip \
    git curl wget jq \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN useradd -m -s /bin/bash sandboxuser
USER sandboxuser

WORKDIR /workspace
