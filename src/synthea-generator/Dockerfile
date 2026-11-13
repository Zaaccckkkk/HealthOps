FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# Synthea is a Java program (Java 11+); the distribution default headless JRE is enough.
RUN apt-get update \
 && apt-get install -y --no-install-recommends default-jre-headless curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*
# Pinned Synthea release, verified against its SHA-256 so the image is reproducible.
ARG SYNTHEA_VERSION=v3.3.0
ARG SYNTHEA_SHA256=8ba04f7d73abadd5a377e41edf24c5c83935a1cb07c6d982cd5db731ef1cf445
RUN mkdir -p /opt/synthea \
 && curl -fsSL -o /opt/synthea/synthea-with-dependencies.jar \
    "https://github.com/synthetichealth/synthea/releases/download/${SYNTHEA_VERSION}/synthea-with-dependencies.jar" \
 && echo "${SYNTHEA_SHA256}  /opt/synthea/synthea-with-dependencies.jar" | sha256sum -c -
ENV SYNTHEA_VERSION=${SYNTHEA_VERSION} SYNTHEA_JAR=/opt/synthea/synthea-with-dependencies.jar
RUN pip install --no-cache-dir uv==0.8.17
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"
ENV DATA_ROOT=/data CONFIG_DIR=/configs
CMD ["synthea-generator"]
