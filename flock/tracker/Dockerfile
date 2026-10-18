# syntax=docker/dockerfile:1
# Build from this directory. Dependencies are this pyproject.toml resolved through the
# workspace's uv.lock, which lives at the repository root; pass it as the "workspace" build
# context (compose.yaml does this):
#   docker build --build-context workspace=../.. -t flock-tracker .

FROM python:3.14-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# Dependencies first, so source changes don't invalidate this layer: the locked versions (with
# hashes) of everything this project depends on.
WORKDIR /workspace
COPY --from=workspace pyproject.toml uv.lock ./
COPY pyproject.toml flock/tracker/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv export --frozen --package flock-tracker --no-dev --no-default-groups \
        --no-emit-workspace -o /tmp/requirements.txt \
    && uv venv /opt/venv \
    && uv pip install --python /opt/venv/bin/python --require-hashes -r /tmp/requirements.txt

COPY app.py /app/
COPY src /app/src
COPY scripts /app/scripts
RUN /opt/venv/bin/python -m compileall -q /app


FROM python:3.14-slim
RUN useradd --system --uid 10001 --home-dir /data tracker \
    && mkdir -p /data/files \
    && chown -R tracker /data
COPY --from=build /opt/venv /opt/venv
COPY --from=build /app /app
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    TRACKER_FILES_DIR=/data/files
USER tracker
WORKDIR /data
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=60s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"]
CMD ["fastapi", "run", "/app/app.py", "--host", "0.0.0.0", "--port", "8080"]
