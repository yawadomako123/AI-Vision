# Vision worker. Build context: repository root.
#   docker compose --profile full build vision
# The ONNX model is mounted from ./models at runtime (not baked in).
# Intel iGPU on a Linux host: pass /dev/dri (see compose.yaml) and set INFERENCE_DEVICE=GPU;
# the image includes Intel's OpenCL runtime for that. Otherwise it runs on CPU.

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
COPY --from=ghcr.io/astral-sh/uv:0.10.7 /uv /usr/local/bin/uv

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 ocl-icd-libopencl1 \
    && (apt-get install -y --no-install-recommends intel-opencl-icd || true) \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/services/vision
COPY services/vision/pyproject.toml services/vision/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY services/vision/app ./app

ENV PATH="/app/services/vision/.venv/bin:$PATH" \
    MODEL_PATH=/models/rfdetr-nano.onnx \
    EVENT_STORAGE_PATH=/data/events \
    RULES_PATH=/data/rules.json \
    OPENVINO_CACHE_DIR=/data/ov_cache \
    INFERENCE_DEVICE=CPU
RUN useradd --uid 10001 --create-home app && mkdir -p /data/events && chown -R app /data
USER app
CMD ["python", "-m", "app.main"]
