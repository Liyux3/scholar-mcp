FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/scholar \
    SCIHUB_ENABLED=0 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=5

RUN groupadd --system scholar \
    && useradd --system --gid scholar --create-home scholar

WORKDIR /opt/scholar-mcp
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY scholar_mcp ./scholar_mcp
ARG SCHOLAR_EXTRAS=rerank
RUN pip install --no-cache-dir ".[${SCHOLAR_EXTRAS}]" \
    && python -I -c "import importlib.util; from pathlib import Path; import scholar_mcp.model_assets as assets; assets.prepare_cpu_model(Path(assets.__file__).with_name('_models')) if importlib.util.find_spec('flashrank') else None"

USER scholar
ENTRYPOINT ["scholar-mcp"]
