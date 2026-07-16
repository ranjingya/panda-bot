FROM python:3.14-slim

ENV TZ=Asia/Shanghai

RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY docs/README.md ./docs/README.md

# 先安装运行依赖，充分利用 Docker 缓存
RUN uv sync --frozen --no-dev --no-install-project -i https://mirrors.aliyun.com/pypi/simple/

COPY src ./src
COPY config ./config

# 安装当前项目
RUN uv sync --frozen --no-dev -i https://mirrors.aliyun.com/pypi/simple/

CMD ["./.venv/bin/panda-bot"]
