FROM python:3.12-slim

# uv provides fast, reproducible installs from uv.lock.
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Install runtime deps + the project (no dev group) from the lockfile. The package
# sources and README must exist when the project is installed, so copy those first;
# the rest of the project (migrations, scripts, alembic.ini) is copied afterward.
COPY pyproject.toml uv.lock README.md ./
COPY app ./app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

COPY . .

# Put the venv on PATH so `uvicorn`, `alembic`, and `python` resolve to it (the
# Compose service commands rely on this).
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# Default command runs the web app; the worker service overrides this.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
