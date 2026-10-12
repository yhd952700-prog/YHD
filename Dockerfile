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

# 仓库根级的共享错误类型模块。src/ai/providers.py 的 `generate_with_retry` 在
# 函数体内 `from error_types import classify_error, ProviderRateLimitError`，
# 而该导入是**顶层模块名**（不是 `src.error_types`），所以它只在 CWD/镜像根下
# 才能解析。缺这一行时，镜像里根本没有这个文件：
#   import src.ai.providers  成功（它在 src/ 下，随 COPY 进镜像），
#   get_provider()           成功（只构造对象，不发请求），
#   generate_with_retry(...) ModuleNotFoundError: No module named 'error_types'
# 也就是说 provider 抽象在镜像里「能构造、能出现在 /v1/dashboard 上，但任何一次
# 真实生成都 100% 崩」—— 正是本项目最危险的「看起来在工作、实际什么都没做」。
# 实测确认（2026-10-04）：用与 Dockerfile COPY 清单一致的目录复现，STEP3 即崩。
# 之所以不把 error_types.py 移进 src/：本仓库 .pth 把 src/ 同时暴露为顶层包，
# 移动会让 `import error_types` 与 `import src.error_types` 成为**两个不同的
# 模块对象**，打断 LiuHao-O/packages/common 的 identity 断言
# （tests/test_liuhao_packages_facade.py 断言 common.classify_error is
# error_types.classify_error）。保持单模块身份 + 随镜像发布，是风险最低的正解。
# 防回归：tests/test_container_packaging_guard.py 会静态扫描 src/ 的仓库根级
# 导入，并逐个核对本文件的 COPY 清单，漏掉即测试失败。
COPY --chown=app:app error_types.py ./error_types.py

# 权威能力注册表。src/ai/roster_payload.py 的 REGISTRY_PATH 是
# `<repo_root>/capability-registry.yaml`（由 __file__ 上溯三层得出），
# 所以它必须落在 /app/ 根上。缺这一行时文件在镜像里根本不存在，
# GET /v1/dashboard/roster 会 available=false 并把 kernels/layers 报成空数组
# —— 而那不是"没注册能力"，是"镜像没带文件"，两者在界面上长得一样。
# 实测确认（2026-10-04）：此前的 COPY 清单里没有它。
COPY --chown=app:app capability-registry.yaml ./capability-registry.yaml

# 驾驶舱构建产物：路径必须与 src/gateway/console_static.py 的
# DEFAULT_CONSOLE_DIST（<repo>/apps/console/console/dist）一致。
COPY --from=console --chown=app:app /console/dist/ ./apps/console/console/dist/

# 运行期状态（身份库、SQLite）落在这里；由 compose 挂卷持久化。
RUN mkdir -p /app/data && chown -R app:app /app/data

# 插件注册表默认写入 ./plugins（相对于 WORKDIR /app）。
# 容器以非 root 用户运行，/app 根目录不可写，必须预建并赋权。
RUN mkdir -p /app/plugins && chown -R app:app /app/plugins

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
