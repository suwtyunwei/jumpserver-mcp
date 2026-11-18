FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 复制依赖声明与源码后统一安装（setuptools 需要源码目录存在）
COPY pyproject.toml ./
COPY jumpserver_mcp_server/ ./jumpserver_mcp_server/
COPY main.py ./
RUN pip install --no-cache-dir .

EXPOSE 8099

CMD ["python", "main.py"]
