FROM python:3.11-slim-bookworm AS builder

ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_DEFAULT_TIMEOUT=300 \
    PIP_RETRIES=10 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN sed -i \
    -e 's|http://deb.debian.org/debian|https://mirrors.tuna.tsinghua.edu.cn/debian|g' \
    -e 's|http://deb.debian.org/debian-security|https://mirrors.tuna.tsinghua.edu.cn/debian-security|g' \
    /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

RUN python -m venv /opt/venv

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN --mount=type=cache,target=/root/.cache/pip \
    /opt/venv/bin/python -m pip install ".[integrations]" \
    && /opt/venv/bin/python -m pip check


FROM python:3.11-slim-bookworm AS runtime

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    APP_CONFIG_FILE=/app/configs/app.yaml \
    PARLANT_HOME=/data/parlant

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates \
       libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 adapter \
    && useradd \
       --uid 10001 \
       --gid adapter \
       --create-home \
       --shell /usr/sbin/nologin \
       adapter \
    && mkdir -p /data/parlant \
    && chown -R adapter:adapter /data

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv

COPY configs/ ./configs/
COPY scripts/ ./scripts/

USER adapter:adapter

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "agent_adapter_service.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
