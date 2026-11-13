FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# Pin the installer; dependencies are pinned by uv.lock.
RUN pip install --no-cache-dir uv==0.8.17
# Dependencies first so code edits do not invalidate this layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"
# Data and configs are mounted at runtime (see docker-compose.yml); nothing is baked in.
ENV DATA_ROOT=/data CONFIG_DIR=/configs
CMD ["data-synthesizer"]
