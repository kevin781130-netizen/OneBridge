FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --system onebridge \
    && useradd --system --gid onebridge --create-home onebridge

COPY pyproject.toml README.md LICENSE THIRD_PARTY_NOTICES.md ./
COPY src ./src

RUN python -m pip install --upgrade pip \
    && python -m pip install '.[postgres]'

RUN mkdir -p /var/lib/onebridge \
    && chown -R onebridge:onebridge /var/lib/onebridge /app

USER onebridge

EXPOSE 8000

CMD ["onebridge", "serve", "--host", "0.0.0.0", "--port", "8000"]
