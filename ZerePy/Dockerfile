FROM python:3.11-slim

# poetry.lock 由 Poetry 2.0.1 生成，保持版本一致
ENV POETRY_VERSION=2.0.1 \
    POETRY_VIRTUALENVS_CREATE=false \
    PIP_NO_CACHE_DIR=1

RUN pip install --no-cache-dir "poetry==${POETRY_VERSION}"

WORKDIR /app

# 先拷依赖清单，利用构建缓存（server extra 提供 FastAPI 服务器模式）
COPY pyproject.toml poetry.lock ./
RUN poetry install --no-root --no-interaction --no-ansi --extras server

# 再拷源码
COPY . .

CMD ["python", "main.py"]
