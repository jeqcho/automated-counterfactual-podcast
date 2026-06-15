# Cloudflare Container image for the counterfactual-podcast FastAPI server.
# TTS runs on Google Cloud (cloud API) — NO local Kokoro model is baked in, so
# data/ is deliberately not copied. Keep the image lean.
FROM python:3.12-slim

# ffmpeg is a system prereq for pydub (MP3 concat from Google TTS chunks).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# uv: fast, lockfile-faithful installs. Copy the static binary from the official image.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# uv settings: install into the system env (no extra venv layer), copy (don't symlink)
# from the build cache, and don't try to install the project as editable before src/ exists.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/usr/local

# 1) Dependency layer — cached unless the lockfile/manifest changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --extra google --no-dev --frozen --no-install-project

# 2) App source + ONLY the scoped profile doc (the rest of private/ is secret/local).
COPY src/ ./src/
COPY private/jay-profile-for-article-classification.scoped.md ./private/jay-profile-for-article-classification.scoped.md

# 3) Install the project itself against the now-present source.
RUN uv sync --extra google --no-dev --frozen

# Default cloud config (overridable via wrangler vars/secrets):
# PYTHONUNBUFFERED so stdout/stderr flush immediately to Cloudflare's container logs
# (no block-buffering) — pairs with the /logs ring-buffer endpoint for run visibility.
ENV TTS_ENGINE=google \
    PYTHONUNBUFFERED=1 \
    CF_BUILD_MARKER=logs-2

EXPOSE 8080

CMD ["uvicorn", "counterfactual_podcast.server:app", "--host", "0.0.0.0", "--port", "8080"]
