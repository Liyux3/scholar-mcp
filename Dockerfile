FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/scholar \
    SCIHUB_ENABLED=0

RUN groupadd --system scholar \
    && useradd --system --gid scholar --create-home scholar

WORKDIR /opt/scholar-mcp
COPY pyproject.toml README.md LICENSE ./
COPY scholar_mcp ./scholar_mcp
RUN pip install --no-cache-dir ".[compression,rerank]"

USER scholar
ENTRYPOINT ["scholar-mcp"]
