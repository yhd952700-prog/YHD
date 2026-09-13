# 鎏灏（LIUHAO X）生产镜像 —— 单端口：一个进程同时服务 API 与驾驶舱。
#
# 三个阶段：
#   1. console   用 Node 构建驾驶舱前端（产物拷进运行镜像，容器里不需要 Node）
#   2. builder   装 Python 依赖
#   3. runtime   运行镜像，只带运行需要的东西，非 root 用户
#
# 为什么前端要进镜像：原先容器只跑 API，驾驶舱得另起一个 Vite 进程。任何
# 「只给你一个端口」的环境（容器编排、反向代理、发布沙箱）都到不了第二个
# 端口。src/gateway/console_static.py 让网关直接服务构建产物，一个进程就是
# 完整产品，因此这里必须把 dist 一起打进去。

# ---------- 阶段 1：构建驾驶舱 ----------
FROM node:22-slim AS console

WORKDIR /console

# 先只拷清单，让依赖层能被缓存：改业务代码不会触发重新装依赖。
COPY apps/console/console/package.json apps/console/console/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY apps/console/console/ ./
RUN npm run build


# ---------- 阶段 2：Python 依赖 ----------
FROM python:3.11-slim AS builder

WORKDIR /app

# libpq-dev 只有构建某些 wheel 时才需要；psycopg 并未被实际使用，但保留
# 构建依赖可以避免源码包编译失败（见 docker-compose.prod.yml 的说明）。
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt


# ---------- 阶段 3：运行镜像 ----------
FROM python:3.11-slim

WORKDIR /app

RUN useradd --create-home --shell /bin/bash app

COPY --from=builder /root/.local /home/app/.local

COPY --chown=app:app src/ ./src/
COPY --chown=app:app config/ ./config/
COPY --chown=app:app scripts/ ./scripts/

# 驾驶舱构建产物：路径必须与 src/gateway/console_static.py 的
# DEFAULT_CONSOLE_DIST（<repo>/apps/console/console/dist）一致。
COPY --from=console --chown=app:app /console/dist/ ./apps/console/console/dist/

# 运行期状态（身份库、SQLite）落在这里；由 compose 挂卷持久化。
RUN mkdir -p /app/data && chown -R app:app /app/data

USER app

ENV PATH=/home/app/.local/bin:$PATH
ENV PYTHONUNBUFFERED=1

# 平台注入端口；本文件不再把 8080 写死在启动命令里（见 src/gateway/__main__.py）。
ENV PORT=8080
EXPOSE 8080

# python:3.11-slim 没有 curl，用标准库自检。端点 /v1/health 的 /v1 前缀来自
# src/gateway/main.py 的 health_router(prefix="/v1")。
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c 'import os,urllib.request; urllib.request.urlopen("http://127.0.0.1:"+os.environ.get("PORT","8080")+"/v1/health", timeout=5)' || exit 1

CMD ["python", "-m", "src.gateway"]
