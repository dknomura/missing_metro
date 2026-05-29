FROM python:3.13-slim

RUN apt-get update && apt-get install -y \
    libgdal-dev \
    libgeos-dev \
    libproj-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -Lsf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

WORKDIR /app
COPY . .

RUN uv sync --frozen && uv cache prune --ci

EXPOSE 7860

CMD ["sh", "-c", "uv run marimo run notebooks/ --host 0.0.0.0 --port ${PORT:-7860}"]
