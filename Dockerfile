FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install the package first (deps + app) for layer caching. setuptools needs the
# package sources and README at build time, so copy those before installing; the
# rest of the project (migrations, scripts, alembic.ini) is copied afterward.
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --upgrade pip && pip install .

COPY . .

EXPOSE 8000

# Default command runs the web app; the worker service overrides this.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
