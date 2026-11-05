# The engine's development image: Python 3.13 and uv.
#
# The repository is mounted at /workspace. The packages live in the /venv
# volume and the tool caches in the /cache volume, so neither is written into
# the repository folder.

FROM ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv

FROM python:3.13.18-slim-trixie@sha256:ae14d4ce8771606477a81446e7ef429edd6762305fc50c84507c1041fa8285d1

COPY --from=uv /uv /uvx /usr/local/bin/

ENV UV_PROJECT_ENVIRONMENT=/venv \
    UV_CACHE_DIR=/cache/uv \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONPYCACHEPREFIX=/cache/pycache \
    RUFF_CACHE_DIR=/cache/ruff \
    MYPY_CACHE_DIR=/cache/mypy \
    PYTEST_ADDOPTS="-o cache_dir=/cache/pytest"

WORKDIR /workspace
