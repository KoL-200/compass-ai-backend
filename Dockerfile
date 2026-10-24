# syntax=docker/dockerfile:1

# ---- build stage: install dependencies into a virtualenv ----
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /build
COPY requirements.txt requirements-dev.txt ./
ARG INSTALL_DEV=false
RUN pip install -r requirements.txt \
    && if [ "$INSTALL_DEV" = "true" ]; then pip install -r requirements-dev.txt; fi

# ---- runtime stage: only what is needed to run, as a non-root user ----
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/opt/venv/bin:$PATH"
RUN useradd --create-home --uid 10001 appuser
WORKDIR /code
COPY --from=builder /opt/venv /opt/venv
COPY --chown=appuser:appuser . .
RUN sed -i 's/\r$//' scripts/start.sh
USER appuser
EXPOSE 8000
CMD ["sh", "scripts/start.sh"]