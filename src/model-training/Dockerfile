FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# LightGBM needs libgomp; git lets the run record the commit when the repository is mounted.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 git \
 && rm -rf /var/lib/apt/lists/*
# Pin the installer; dependencies (CPU torch, LightGBM, W&B) are pinned by uv.lock.
RUN pip install --no-cache-dir uv==0.8.17
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
RUN uv sync --frozen --no-dev
# GPU image: swap the locked CPU torch for the CUDA wheel of the same version, named with its
# local version tag so uv does not treat the CPU build as already satisfying it (torch 2.15 ships
# +cu132; a CUDA 13 driver, 580 or newer, is required on the host, which Vertex AI g2 machines
# provide). The wheel bundles its CUDA runtime libraries.
ARG TORCH_VARIANT=cpu
RUN if [ "$TORCH_VARIANT" != "cpu" ]; then \
      TORCH_VERSION=$(.venv/bin/python -c "import torch; print(torch.__version__.split('+')[0])") \
      && uv pip install --python .venv/bin/python --no-cache-dir --reinstall-package torch \
           --index-url "https://download.pytorch.org/whl/${TORCH_VARIANT}" \
           "torch==${TORCH_VERSION}+${TORCH_VARIANT}" \
      && .venv/bin/python -c "import torch; assert torch.version.cuda, 'CUDA torch expected'; print(torch.__version__)"; \
    fi
ENV PATH="/app/.venv/bin:$PATH"
# Vertex AI mounts the host's NVIDIA driver into a GPU job's container under /usr/local/nvidia;
# without these paths CUDA torch cannot load libcuda and silently trains on the CPU (observed
# 2026-11-07: W&B recorded no GPU for jobs on g2-standard-4 + L4). Harmless on CPU machines.
ENV LD_LIBRARY_PATH="/usr/local/nvidia/lib64:/usr/local/nvidia/lib:${LD_LIBRARY_PATH:-}" \
    NVIDIA_VISIBLE_DEVICES=all NVIDIA_DRIVER_CAPABILITIES=compute,utility
# Data is mounted at runtime (Compose) or read from GCS (Vertex AI); nothing is baked in.
ENV DATA_ROOT=/data TRAIN_OUT=/app/.local/runs/latest WANDB_DIR=/app/.local/wandb
CMD ["healthops-train"]
