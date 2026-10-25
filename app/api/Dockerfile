# syntax=docker/dockerfile:1
# Build with the repository root as the context, so the workspace lockfile is available:
#   docker build -f app/api/Dockerfile -t flock-api .

FROM python:3.14-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# Dependencies first, so source changes don't invalidate this layer: the locked versions (with
# hashes) of everything this project depends on.
WORKDIR /workspace
COPY pyproject.toml uv.lock ./
COPY app/api/pyproject.toml app/api/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv export --frozen --package flock-app-api --no-dev --no-default-groups \
        --no-emit-workspace -o /tmp/requirements.txt \
    && uv venv /opt/venv \
    && uv pip install --python /opt/venv/bin/python --require-hashes -r /tmp/requirements.txt

COPY app/api/app.py app/api/__init__.py /app/app/api/
COPY app/api/src /app/app/api/src
COPY app/api/scripts /app/app/api/scripts
# flock.hub, which the API runs as `python -m flock.hub.importer` for Hugging Face mirrors. Its
# third-party dependencies came with the export above; `--no-emit-workspace` leaves out its code.
COPY flock/hub/*.py /app/flock/hub/
RUN /opt/venv/bin/python -m compileall -q /app


FROM python:3.14-slim
RUN useradd --system --uid 10001 --home-dir /data flock \
    && mkdir -p /data/files \
    && chown -R flock /data
COPY --from=build /opt/venv /opt/venv
COPY --from=build /app /app
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    FLOCK_FILES_DIR=/data/files
USER flock
WORKDIR /data
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=60s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"]
CMD ["uvicorn", "app.api.app:app", "--host", "0.0.0.0", "--port", "8080"]
