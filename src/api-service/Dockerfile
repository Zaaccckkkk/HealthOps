FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# Pin the installer; application and test dependencies are pinned by uv.lock.
RUN pip install --no-cache-dir uv==0.8.17
COPY pyproject.toml uv.lock ./
ARG INSTALL_SYNTHETIC=false
RUN if [ "$INSTALL_SYNTHETIC" = true ]; then uv sync --frozen --extra synthetic --no-install-project; else uv sync --frozen --no-install-project; fi
COPY . .
RUN if [ "$INSTALL_SYNTHETIC" = true ]; then uv sync --frozen --extra synthetic; else uv sync --frozen; fi \
    && useradd --uid 10001 --create-home appuser
USER appuser
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "healthops.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
