# 鎏灏（LIUHAO X）生产镜像 —— 单端口：一个进程同时服务 API 与驾驶舱。
#
# 三个阶段：
#   1. console   用 Node 构建驾驶舱前端（产物拷进运行镜像，容器里不需要 Node）
#   2. builder   装 Python 依赖与包本身
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
# src/brand.ts 通过 '../../../../src/brand.json' 引用品牌条款文件（在仓库根 src/ 下，
# 已随仓库入库）；镜像内工作目录被展平为 /console，该相对引用解析到 /src/brand.json。
# 不补这一行，npm run build 就会 TS2307：Cannot find module '../../../../src/brand.json'。
COPY src/brand.json /src/brand.json
RUN npm run build


# ---------- 阶段 2：Python 依赖 ----------
FROM python:3.12-slim AS builder

WORKDIR /app

# libpq-dev 只有构建某些 wheel 时才需要；psycopg 并未被实际使用，但保留
# 构建依赖可以避免源码包编译失败（见 docker-compose.prod.yml 的说明）。
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# 安装包本身（此前只装了 requirements.txt 的第三方依赖，liuhao-ai-os
# 这个发行版从未被安装：容器里没有 dist-info，`pip show liuhao-ai-os`
# 查无此包）。
# --no-deps：依赖已由上一行按 requirements.txt 的锁定版本装好，这里只
# 注册发行版本身，不再重新解析整条依赖树。
# 需要 pyproject.toml + README.md（pyproject 声明 readme="README.md"，
# 缺了它构建直接失败）以及源码目录都在构建上下文里；.dockerignore 已
# 为 README.md 开了例外（`*.md` 会把它拦掉）。
# 注意：setuptools 把本仓库识别为 src 布局，可编辑安装只暴露 src/ 下
# 的顶层包与 dist-info，`import src` 本身仍靠运行镜像的 WORKDIR /
# PYTHONPATH（见阶段 3 的 ENV PYTHONPATH），不是靠这次安装。
COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY plugins/ ./plugins/
COPY main.py error_types.py ./
RUN pip install --no-cache-dir --user --no-deps -e .


# ---------- 阶段 3：运行镜像 ----------
FROM python:3.12-slim

WORKDIR /app

RUN useradd --create-home --shell /bin/bash app

COPY --from=builder /root/.local /home/app/.local

COPY --chown=app:app src/ ./src/
COPY --chown=app:app config/ ./config/
COPY --chown=app:app scripts/ ./scripts/

# 仓库根入口与源码目录（此前缺失，容器里只有空目录/根本没有）：
# - main.py：度量运行时 CLI（`python main.py health|ready|startup|record`），
#   仓库根入口点；不进镜像它在容器里就不存在。
# - plugins/：旧版工具插件源码（anagram/barcode/…）与插件注册表
#   （plugins/registry.py）。src/ 里的新插件面（src/plugins、
#   src/kernels/plugin）不 import 它，但内核插件注册表的回退状态目录
#   就是 <repo>/plugins（src/kernels/plugin/__init__.py 的
#   _default_registry_dir）；此前镜像里只有下面那个 mkdir 建的空目录。
# - core/、libs/：core/ai_employee 目前只有一个测试文件、libs/liuhao-core
#   是独立小库，二者今天都不被网关 import；随镜像发布是为了容器内的
#   源码与仓库一致，而不是因为运行时需要它们 —— 别把"在镜像里"
#   误读成"被加载了"。
# - pyproject.toml：已安装发行版的元数据来源（版本、依赖声明），留在
#   镜像里供 `pip show liuhao-ai-os` / 排障时核对。
COPY --chown=app:app main.py ./main.py
COPY --chown=app:app plugins/ ./plugins/
COPY --chown=app:app core/ ./core/
COPY --chown=app:app libs/ ./libs/
COPY --chown=app:app pyproject.toml ./pyproject.toml

# 迁移图（alembic.ini + alembic/versions/*）。缺了它，镜像只能靠运行时的
# ``CREATE TABLE IF NOT EXISTS`` 自建库表：那意味着上线前无法预建、无法
# 审计真实 schema、也无法回滚（``alembic upgrade/downgrade`` 在容器里根本跑
# 不起来）。revision 006 是运行时状态表的基线，001-005 是 ORM 表。
COPY --chown=app:app alembic.ini ./alembic.ini
COPY --chown=app:app alembic/ ./alembic/

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
# /app/logs：config/logging.yaml 的 FileHandler 指向相对路径 logs/liuhao.log，
# 运行时 CWD 是 /app，即 /app/logs。该目录此前从未创建，且 /app 由 root 拥有、
# 进程以 USER app 运行 ⇒ 容器启动即 PermissionError: '/app/logs' 并退出
# （Build Verification 实测 state=exited, health=unhealthy）。日志是运行必需品，
# 目录必须在镜像里就存在且属主正确；不进卷（日志随容器生命周期，避免卷里堆积）。
RUN mkdir -p /app/data /app/logs && chown -R app:app /app/data /app/logs

# 插件注册表状态（registry_index.json）默认写入 ./plugins（相对于 WORKDIR /app）。
# 上面已把 plugins/ 源码随镜像发布；这里只保证该目录存在且属主正确，
# 容器以非 root 用户运行时注册表才能写入状态文件。
RUN mkdir -p /app/plugins && chown -R app:app /app/plugins

# 单实例锁默认落在 <instance_root>/run/instance.lock（src/common/instance_lock.py），
# 而网关的 instance_root 在容器里解析成 CWD（/app）。/app 由 root 拥有、进程以
# USER app 运行 ⇒ 启动即 "无法创建单实例锁文件 /app/run/instance.lock
# ([Errno 13] Permission denied: '/app/run')" 并 fail-closed 退出
# （Build Verification 实测 state=exited, health=unhealthy）。锁是运行必需品，
# 目录必须在镜像里就存在且属主正确。
RUN mkdir -p /app/run && chown -R app:app /app/run

USER app

ENV PATH=/home/app/.local/bin:$PATH
ENV PYTHONUNBUFFERED=1

# 仓库根进 sys.path：`import src.*`、`import main`、`import error_types`
# 之前只靠"启动时 CWD 恰好是 /app"才能解析，换个工作目录进容器
# （docker exec / 排障）就 ModuleNotFoundError。显式写死，不依赖 CWD。
ENV PYTHONPATH=/app

# 平台注入端口；本文件不再把 8080 写死在启动命令里（见 src/gateway/__main__.py）。
ENV PORT=8080
# 网关默认只绑 127.0.0.1（桌面产品：只本机可达）。容器必须能从外部访问，
# 所以这里**显式**选择对外绑定 —— 这是唯一能让镜像可用的方式，而不是默认。
ENV HOST=0.0.0.0
EXPOSE 8080

# python:3.12-slim 没有 curl，用标准库自检。端点用 /v1/ready 而不是 /v1/health：
# /v1/health 只表示"进程还在回 HTTP"，依赖（审计链、身份库、模型配置）全坏时
# 它依然 200 —— 容器会被编排系统当成健康，把"假健康"传播到负载均衡后面。
# /v1/ready 在依赖不健康时如实返回 503，HEALTHCHECK 才会真的失败并重启/摘流量。
# /v1 前缀来自 src/gateway/main.py 的 health_router(prefix="/v1")。
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c 'import os,urllib.request; urllib.request.urlopen("http://127.0.0.1:"+os.environ.get("PORT","8080")+"/v1/ready", timeout=5)' || exit 1

# 启动前先应用迁移（DATABASE_URL 由 compose 指向 /app/data 的卷，可写）。
# ``&&`` 保证迁移失败时容器直接退出，而不是带着未迁移的 schema 提供服务。
# 回滚路径保留在镜像里：``docker run ... python -m alembic downgrade <rev>``。
CMD ["sh", "-c", "python -m alembic upgrade head && exec python -m src.gateway"]
