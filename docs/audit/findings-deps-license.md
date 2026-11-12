# 依赖与许可域深度体检（只读）

- 目标仓库：`d:\LiuHao-AI-OS`
- 体检时间：2026-11-08（Asia/Shanghai）
- 代码基线：`HEAD = 2a4a681f`（工作树含其他并行任务的未提交修改，见“边界与前提”）
- 环境：CPython 3.12.14（`.venv`，uv 管理的 cpython-3.12）/ 全局 CPython 3.13.15 / Node v26.7.0 / npm 11.19.0
- 体检范围：`requirements.txt`、`pyproject.toml`（根 + `libs/liuhao-core`）、`uv.lock`、`apps/console/console`、`frontend`、`sdk/javascript/liuhao-sdk`、`eap/01-browser-click-through` 的 `package.json` / lockfile、`packaging/**`、`oss-registry.yaml`、`dist/payload/app` 实际随包内容。

## 边界与前提（重要）

1. 本次为**只读**体检：未修改任何代码、依赖声明、lockfile；未 `git add`、未 commit；`packaging/**` 仅读取。
2. 审计时工作树里 `packaging/stage_payload.py`、`packaging/third_party_notices.py`、`pyproject.toml`、`apps/console/console/package-lock.json` 等已被其他并行任务修改（未提交）。本报告结论基于**当时工作树快照**，不是某个干净 commit。
3. 为跑漏洞扫描，在仓库外（`%TEMP%`）建了隔离 venv 安装 `pip-audit`（未污染仓库 `.venv`，未在仓库内 `pip install`）。npm 审计全部基于现有 `package-lock.json` / `node_modules`，`npm ci` 仅 `--dry-run`。
4. `.venv` 是**开发环境**（271 个分发），`dist/payload/app/runtime` 是**实际随包环境**（93 个分发）；两者结论分开陈述，不混用。
5. `dist/payload/app` 的 runtime 文件时间戳为 **2026-08-30**，而 `THIRD-PARTY-NOTICES.txt` 为 2026-11-07：随包内容明显早于当前锁文件，本文把它作为“当前分发事实”，但不把它当作“当前源码可构建结果”。

---

## 0. 结论速览

| 维度 | 结论 |
|---|---|
| 直接依赖规模 | Python：`pyproject` 48 个运行期直接依赖（+8 组 extras 共 13 条）；Node：console 3 生产 / 11 开发，frontend 2 / 8，SDK 1 / 9，EAP 0 / 1 |
| 解析规模 | `requirements.txt` 210 个精确 pin；`uv.lock` 223 条（221 个唯一名，numpy/scipy 各有 2 个版本）；`.venv` 271 个分发；随包 93 个分发 |
| 锁与随包一致性 | **不一致**：210 个 pin 中 122 个未随包；随包 93 个中 61 个不在锁里；7 个同名版本漂移（含 protobuf 5.29.6 vs 锁 6.33.6） |
| 许可 | 随包全是宽松许可（MIT/Apache/BSD/PSF/ZPL/MPL），**无 GPL/AGPL/SSPL**；Elastic-2.0（arize-phoenix）与 LGPL-3.0/LGPL v3 仅在开发环境，未随包 |
| 许可门禁 | `third_party_notices --check` 对当前 payload **通过**；但 `oss-registry.yaml` 只登记 38 个，**68 个随包组件未登记**，校验脚本只遍历登记册 → 门禁存在系统性盲区 |
| 漏洞 | 随包：1 条（opentelemetry-api 1.44.0，CVE-2026-114603，当前代码路径不可触发）；锁定集（3.12 实测）：7 条 / 3 包（opentelemetry-api、pydantic-ai-slim、strawberry-graphql，后两者不随包）；开发环境：19 条原始记录（去重 13 条，含 pip 6 条）；Node：console 0、EAP 0、frontend 3（全为构建期）、SDK 15（全为开发依赖，生产 0） |
| 停止维护 | `ldap3 2.9.1`（2021-07，仅开发环境）、`google-generativeai 0.8.6`（2025-12 最后发布、已弃用但仍随包且无业务引用）、numpy/scipy 的 1.26/1.13 pin（约 2 年） |
| 体积 | 随包 site-packages 合计 90.8 MB；Top10 合计 59.34 MB（65.4%）；其中 `google-ai-generativelanguage` 7.01 MB 属无用死重 |
| 可复现性 | npm：3 个 lockfile `npm ci --dry-run` 全部通过；Python：`requirements.txt` 在 CPython 3.12 可解析（numpy/scipy 有 cp312 轮子），在 **CPython 3.13 上因 numpy 1.26.4/scipy 1.13.1 无 cp313 轮子而失败**；`requires-python = ">=3.11"` 未设上限，无 `.python-version` |

**总判定：NOT READY（依赖域）** — 无 P0，但有 3 条 P1（随包与锁漂移、`pywebview` 默认桌面后端未随包、18 个运行期依赖未随包），另有 7 条 P2（含 1 条许可已书面接受）。

---

## 1. 依赖清单现状

### 1.1 Python

| 清单 | 路径 | 规模 |
|---|---|---|
| 直接声明 | [pyproject.toml](file:///d:/LiuHao-AI-OS/pyproject.toml#L14-L248) | 48 个运行期依赖；extras：tests(3)、deploy-closure(2)、ops(1)、security(3)、google(1)、migrations(1)、taskiq-redis(1)、dbos(1) |
| 核心库声明 | [libs/liuhao-core/pyproject.toml](file:///d:/LiuHao-AI-OS/libs/liuhao-core/pyproject.toml) | tenacity、pybreaker、prometheus-client、structlog、pydantic-settings（其中 tenacity/pybreaker 未进入根锁） |
| 锁定（实际使用） | [requirements.txt](file:///d:/LiuHao-AI-OS/requirements.txt) | 210 个 `==` pin，由 `uv pip compile pyproject.toml` 生成（文件头第 1-2 行） |
| 锁定（已失效） | [uv.lock](file:///d:/LiuHao-AI-OS/uv.lock) | 223 条 entry / 221 个唯一名；缺 `pywebview`、`bcrypt`、`lxml`、`pycron`、`pypdf`、`python-docx`、`taskiq`、`taskiq-dependencies`，以及 9 个未进 requirements 的 extras（fakeredis/lupa/pytest-timeout/playwright/selenium/argon2-cffi/pyotp/taskiq-redis/dbos）；本机未安装 `uv` |
| 开发环境实测 | `.venv` | 271 个分发 |
| 随包实测 | `dist/payload/app/runtime/Lib/site-packages` | 93 个分发 |

关键差异：

- `pyproject.toml` 声明 48 个，`requirements.txt` 只漏了 **1 个运行期依赖**：`pywebview>=5.0.0`（[pyproject.toml:146](file:///d:/LiuHao-AI-OS/pyproject.toml#L146)），`uv.lock` 同样缺。
- extras 共 **9 条**未进 `requirements.txt`（tests：fakeredis/lupa/pytest-timeout；deploy-closure：playwright/selenium；security：argon2-cffi/pyotp；taskiq-redis；dbos），`uv.lock` 这 9 条与 `pywebview` 全部缺。
- `.venv` 与 `requirements.txt`：**39 个同名版本不一致**，另有 61 个已安装但不在锁里。典型：`numpy 2.5.2 vs 1.26.4`、`scipy 1.18.1 vs 1.13.1`、`protobuf 5.29.6 vs 6.33.6`、`wrapt 1.17.3 vs 2.4.0`、`click 8.4.2 vs 8.5.0`（完整列表见附录 A）。`.venv` 的 `pip check` 通过，只说明内部约束可满足，**不代表与锁一致**。
- 随包与锁：**7 个版本漂移**（anyio 4.15.0/4.15.1、click 8.4.2/8.5.0、googleapis-common-protos 1.75.0/1.75.3、google-auth 2.58.0/2.57.1、google-genai 2.23.0/2.22.0、platformdirs 4.11.8/4.11.7、protobuf **5.29.6/6.33.6**）；122 个 pin 未随包、61 个随包不在锁里。
- 声明面与分发面：`pyproject` 48 个运行期直接依赖中 **18 个未随包**（pywebview、structlog、python-json-logger、mem0ai、langgraph、langchain/-core/-community/-openai、langfuse、arize-phoenix、redis、qdrant-client、hvac、pydantic-monty-client、pytest、pytest-asyncio、hypothesis），详见 DEP-19。

### 1.2 Node

| 项目 | 生产依赖 | 开发依赖 | lock 节点 | 审计（prod/dev） |
|---|---|---|---|---|
| `apps/console/console` | react 19.2.8、react-dom 19.2.8、ws 8.21.3 | vite 8.3.6、typescript ~6.0.2、vitest 5.1.0、jsdom 25.0.1、oxlint 等 11 个 | 165（prod 5） | 0 / 0 |
| `frontend` | react 18.3.1、react-dom 18.3.1 | vite **5.4.x**、tailwindcss 3.4.x、typescript 5.6.x、postcss、autoprefixer 等 8 个 | 181（prod 6） | 3（1 high 2 moderate）；生产 0 |
| `sdk/javascript/liuhao-sdk` | axios 1.21.0 | eslint **8.50**、@typescript-eslint **6.x**、jest 29、ts-jest 29、rimraf **5.x**、typescript 5.2 | 421（prod 28） | 15（6 high 2 moderate 7 low）；生产 0 |
| `eap/01-browser-click-through` | 无 | @playwright/test | 3 | 0 |

### 1.3 重复依赖 / 多套同类库

- Python HTTP 栈（随包）：`httpx 0.28.1` + `httpx2 2.12.0` + `httpcore 1.0.9` + `httpcore2 2.12.0` + `requests 2.34.2` + `aiohttp 3.14.4` + `urllib3 2.8.0` + `truststore 0.10.4`（8 个）。`httpx2/httpcore2` 是 openai 3.13 的新一代栈，`httpx/httpcore` 是 `src/ai/providers.py` 自建客户端所需，二者并存属上游分裂，非本项目引入。
- Python Google SDK：`google-genai 2.23.0`（业务使用）+ `google-generativeai 0.8.6`（业务**显式拒绝**，见 DEP-05），同时随包。
- YAML/JSON：随包仅 PyYAML 6.0.3；开发环境另有 `ruamel.yaml 0.19.1`、`ormsgpack`、`jsonpatch`（未随包、未登记）。
- 同名多版本：`uv.lock` 里 `numpy 2.4.6`（L2934）与 `2.5.2`（L3018）、`scipy 1.17.1`（L4825）与 `1.18.1`（L4901）并存；单一 venv 内不存在同名多版本。
- Node：仓库内两套 React（console 19 / frontend 18）、两套 Vite（8.3.6 / 5.4.x）、两套 TypeScript（6.0.x / 5.6.x）。

---

## 2. 许可合规

### 2.1 随包（93 个）许可分布

MIT 28、Apache-2.0 23、BSD-3-Clause 15、Apache 2.0（写法差异）7、PSF-2.0 2、PSF 1；另含 MPL-2.0（certifi）、MPL-2.0 AND (Apache-2.0 OR MIT)（orjson，双许可）、ZPL-2.1（RestrictedPython）、Unlicense（email-validator）；`taskiq-dependencies 1.5.7` 元数据缺 License 字段。**无 GPL/AGPL/SSPL/Elastic**。

- `packaging/licenses/` 共 18 个文本，覆盖运行期非 Python 资产（Tcl/Tk、Tix、SQLite、libiconv、libffi、expat、zlib、Unicode、MSVC runtime、React-MIT、pywin32/adodbapi、libmpdec、LGPL-SOURCE-OFFER 等）。
- `packaging/third_party_notices.py --app dist/payload/app --check dist/payload/app/THIRD-PARTY-NOTICES.txt` **通过**（RC=0），生成表 93 个包与 payload 实测一致；`fastmcp-slim`、`opentelemetry-util-http` 的许可证文本位于 `dist-info/licenses/`（文件名不含 license 关键字，需按目录识别），`pywin32` 由 `packaging/licenses/pywin32-BSD-3-Clause.txt` + `adodbapi-LGPL-2.1.txt` 覆盖。
- 许可证声明不明：`taskiq-dependencies 1.5.7` METADATA 无 License 字段（包内有 LICENSE 文件），notices 只能写“见随包文本”。

### 2.2 开发环境里的受限许可证（未随包）

| 组件 | 许可证 | 来源 | 是否随包 |
|---|---|---|---|
| arize-phoenix 20.11.0 / arize-phoenix-client 等 | Elastic-2.0 | `pyproject.toml:44` 声明、`requirements.txt:41` pin | 否 |
| ldap3 2.9.1 | LGPL v3 | phoenix 传递依赖 | 否 |
| psycopg / psycopg-binary | LGPL-3.0-only | `[dbos]` extra | 否 |

### 2.3 `oss-registry.yaml` 与实际依赖的一致性

- 登记册 **38 个组件**；随包 **93 个** → **68 个随包组件未登记**（含 `requests`、`pyjwt`、`python-dotenv`、`anyio`、`bcrypt`、`certifi`、`protobuf`、`google-genai`、`google-generativeai`、`aiohttp`、`opentelemetry-exporter-otlp` 全家桶、`taskiq-dependencies`、`tenacity`、`watchfiles`、`websockets` 等）。
- 反向：登记册里 13 个组件未随包（`alembic`、`arize-phoenix`、`hvac`、`hypothesis`、`langchain`、`langfuse`、`langgraph`、`mem0ai`、`pytest`、`pytest-asyncio`、`qdrant-client`、`redis`、`structlog`）。
- `pyproject.toml` 声明的 48 个直接依赖中有 **25 个未登记**（如 `pywebview`、`pyjwt`、`requests`、`anyio`、`opentelemetry-exporter-otlp`、`python-json-logger`、`langchain-core`、`pydantic-monty-client`，以及全部 extras 项）。
- 校验逻辑只遍历登记册：`scripts/verify_oss_licenses.py:122` `for comp in components`，未安装的组件只 `[skip]`（[verify_oss_licenses.py:129-139](file:///d:/LiuHao-AI-OS/scripts/verify_oss_licenses.py#L129-L139)）。因此“实际随包/新引入的组件从来没被检查”，26 个 `[ok]` 只代表登记册自洽。
- 登记册自身对受限许可有书面接受（`arize-phoenix` Elastic-2.0，见 [oss-registry.yaml:145-150](file:///d:/LiuHao-AI-OS/oss-registry.yaml#L145-L150)），这一点合规，但“声明为运行期依赖却从不随包”属清单不一致。

### 2.4 直接依赖许可表（实测；版本为随包或开发环境实测）

| 依赖 | 声明 | 实测版本 | 许可证 | 随包? |
|---|---|---|---|---|
| openai | >=1.0.0 | 3.13.0 | Apache-2.0 | 是 |
| anthropic | >=0.25.0 | 1.3.0 | MIT | 是 |
| httpx | >=0.27.0 | 0.28.1 | BSD-3-Clause | 是 |
| pydantic | >=2.7.0 | 2.13.5 | MIT | 是 |
| pydantic-settings | >=2.3.0 | 2.15.0 | MIT | 是 |
| structlog | >=24.1.0 | 26.1.0 | MIT OR Apache-2.0 | 否 |
| python-json-logger | >=2.0.7 | 4.2.0 | BSD-2-Clause | 否 |
| python-dotenv | >=1.0.0 | 1.2.3 | BSD-3-Clause | 是 |
| prometheus-client | >=0.19.0 | 0.26.0 | Apache-2.0 AND BSD-2-Clause | 是 |
| opentelemetry-api | >=1.22.0 | 1.44.0 | Apache-2.0 | 是 |
| opentelemetry-sdk | >=1.22.0 | 1.44.0 | Apache-2.0 | 是 |
| opentelemetry-exporter-otlp | >=1.22.0 | 1.44.0 | Apache-2.0 | 是 |
| opentelemetry-instrumentation-fastapi | >=0.43b0 | 0.65b0 | Apache-2.0 | 是 |
| fastapi | >=0.110.0 | 0.141.1 | MIT | 是 |
| uvicorn | >=0.29.0 | 0.52.4 | BSD-3-Clause | 是 |
| pywin32 | >=306 (win32) | 311 | PSF | 是 |
| pytest | >=8.0.0 | 9.1.1 | MIT | 否 |
| pytest-asyncio | >=0.23.0 | 1.4.0 | Apache-2.0 | 否 |
| hypothesis | >=6.100 | 6.168.0 | MPL-2.0 | 否 |
| pyyaml | >=6.0.1 | 6.0.3 | MIT | 是 |
| mem0ai | >=2.0.20 | 2.0.20 | Apache-2.0 | 否 |
| langgraph | >=0.2.0 | 1.2.11 | MIT | 否 |
| langchain | >=0.3.0 | 1.4.0 | MIT | 否 |
| langchain-openai | >=0.2.0 | 1.6.2 | MIT | 否 |
| langchain-community | >=0.3.0 | 0.4.2 | MIT | 否 |
| langfuse | >=4.0.0 | 4.15.2 | MIT | 否 |
| arize-phoenix | >=3.0.0 | 20.11.0 | **Elastic-2.0** | 否 |
| mcp | >=2.0,<3.0 | 2.2.0 | MIT | 是 |
| fastmcp-slim | ==4.0.3 | 4.0.3 | Apache-2.0 | 是 |
| hvac | >=2.0.0 | 2.4.0 | Apache-2.0 | 否 |
| cryptography | >=42.0.0 | 50.0.1 | Apache-2.0 OR BSD-3-Clause | 是 |
| redis | >=5.0.0 | 8.1.0 | MIT | 否 |
| qdrant-client | >=1.9.0 | 1.19.0 | Apache-2.0 | 否 |
| aiosqlite | >=0.20.0 | 0.22.1 | MIT | 是 |
| RestrictedPython | >=8.0 | 8.5 | ZPL-2.1 | 是 |
| requests | >=2.28.0 | 2.34.2 | Apache-2.0 | 是 |
| sqlalchemy | >=2.0.0 | 2.0.52 | MIT | 是 |
| starlette | >=0.36.3 | 1.6.0 | BSD-3-Clause | 是 |
| packaging | >=23.0 | 26.3 | Apache-2.0 OR BSD-2-Clause | 是 |
| pyjwt | >=2.15.0 | 2.15.1 | MIT | 是 |
| langchain-core | >=0.3.0 | 1.6.3 | MIT | 否 |
| psutil | >=5.9.0 | 7.2.2 | BSD-3-Clause | 是 |
| pydantic-monty-client | >=0.0.20 | 0.0.21 | MIT | 否 |
| taskiq | >=0.11.0 | 0.13.0 | MIT | 是 |
| pypdf | >=5.0.0 | 6.21.4 | BSD-3-Clause | 是 |
| python-docx | >=1.1.0 | 1.2.0 | MIT | 是 |
| anyio | >=4.0.0 | 4.15.0 | MIT | 是 |
| pywebview | >=5.0.0 | 6.2.1 | BSD | **否** |

extras：fakeredis BSD-3、lupa MIT、pytest-timeout MIT、playwright Apache-2.0、selenium（未安装）、websockets BSD-3（随包）、argon2-cffi MIT、bcrypt Apache-2.0（随包）、pyotp MIT、google-genai Apache-2.0（随包）、alembic MIT、taskiq-redis（未安装）、dbos MIT。

---

## 3. 漏洞扫描（真实输出摘要 + 可触发性判定）

### 3.1 Python — 随包环境（最重要的分发面）

命令：`pip-audit --path dist/payload/app/runtime/Lib/site-packages`（隔离 venv 中的 pip-audit 2.10.1）

```
Found 1 known vulnerability in 1 package
opentelemetry-api 1.44.0  CVE-2026-114603 / GHSA-xcq7-p3pf-p88h  fix=1.45.1
  TraceState.from_header 对入站 W3C tracestate 头使用无上界可选空白前缀的正则，未先做长度限制（ReDoS）
```

可触发性：**当前仅版本命中**。`src` 中没有任何 `FastAPIInstrumentor` / ASGI 中间件 / `propagator.extract` / `tracestate` 解析调用（全局 `TracerProvider` 已设置，但未接入入站请求提取），入站头部不会进入 `TraceState.from_header`；一旦后续启用已随包的 `opentelemetry-instrumentation-fastapi` 并做分布式上下文提取，即变为可触发。修复只需 1.44.0 → 1.45.1。

### 3.2 Python — 锁定集（`requirements.txt`）

- 在 CPython 3.12 下全量解析成功（210 个 pin，numpy/scipy 均有 cp312 轮子）：
  ```
  Found 7 known vulnerabilities in 3 packages
  pydantic-ai-slim 2.40.0  PYSEC-2026-4285 / CVE-2026-107286 / GHSA-6fqq-452j-qhrp  fix=2.53.0
  pydantic-ai-slim 2.40.0  PYSEC-2026-4286 / CVE-2026-107290 / GHSA-fpf4-vwcp-v4hp  fix=2.44.0
  pydantic-ai-slim 2.40.0  PYSEC-2026-4284 / CVE-2026-107291 / GHSA-4x9p-g9wm-8q7f  fix=2.44.0
  pydantic-ai-slim 2.40.0  PYSEC-2026-4290 / CVE-2026-107287 / GHSA-v36g-jcw9-x7cw  fix=2.52.0
  pydantic-ai-slim 2.40.0  PYSEC-2026-4291 / CVE-2026-107289 / GHSA-vmxc-h2x2-jmf3  fix=2.44.0
  opentelemetry-api 1.44.0 CVE-2026-114603 / GHSA-xcq7-p3pf-p88h               fix=1.45.1
  strawberry-graphql 0.327.0 PYSEC-2026-4302 / CVE-2026-107727 / GHSA-m952-2w3f-6r8h fix=0.327.2
  ```
- 在 CPython 3.13 下解析失败：`pip-audit -r requirements.txt` 触发 `numpy==1.26.4` 源码构建，`meson` 报“Unknown compiler(s)”后 `metadata-generation-failed`；`pip download numpy==1.26.4 --only-binary=:all:` 在 3.13 下 “no matching distribution”。根因是 numpy 1.26.4/scipy 1.13.1 无 cp313 轮子，而 `requires-python = ">=3.11"`（[pyproject.toml:8](file:///d:/LiuHao-AI-OS/pyproject.toml#L8)）未限制上限。
- 可触发性：`opentelemetry-api` 见 3.1（当前不可触发）；`pydantic-ai-slim` / `strawberry-graphql` 仅由 phoenix 链路带入、`src` 不 import、也不随包 → 不可触发（开发/CI 暴露）。

### 3.3 Python — 开发环境（`.venv`，非随包）

```
Found 19 known vulnerabilities in 4 packages（去重后 13 条）
opentelemetry-api 1.44.0   CVE-2026-114603（同上）
pip 25.0.1                 6 条（GHSA-4xh5-x5gv-qwph / GHSA-6vgw-5pg2-w6jp / GHSA-58qw-9mgm-455v /
                          GHSA-jp4c-xjxw-mgf9 / GHSA-wf93-45jw-7689 / GHSA-qwm4-qh6w-59xr）
pydantic-ai-slim 2.43.0    5 条（GHSA-6fqq-452j-qhrp / GHSA-fpf4-vwcp-v4hp / GHSA-4x9p-g9wm-8q7f /
                          GHSA-v36g-jcw9-x7cw / GHSA-vmxc-h2x2-jmf3）
strawberry-graphql 0.327.0 1 条（GHSA-m952-2w3f-6r8h）
```

可触发性：`src` 不 import `pydantic_ai` / `strawberry`（仅由 arize-phoenix 链路带入），且都不随包；`pip` 只是构建工具、不在 payload。→ **不可触发（开发环境噪声）**。

### 3.4 npm

| 项目 | 结果 | 可触发性 |
|---|---|---|
| console | 0（prod 0 / dev 0） | — |
| eap | 0 | — |
| frontend | 3（1 high + 2 moderate）：`vite <=6.4.3`（8 条公告，含优化依赖 `.map` 路径穿越 GHSA-4w7w-66w2-5vf9、launch-editor Windows UNC/NTLM 系列、跨域中间件执行等）、`esbuild <=0.24.2`（GHSA-67mh-4wv8-2f99）、`postcss-selector-parser <6.1.5`（GHSA-rj75-hqrm-r3gf） | 全部 `direct=false`（除 vite 本身为开发依赖）→ 只在**本机开发服务器/构建期**暴露，不随包、不可被终端用户触发；但 Windows 开发机上的 launch-editor/UNC 公告属真实开发期风险 |
| sdk | 15（6 high / 2 moderate / 7 low）：`@typescript-eslint/*` 6.16–7.5（minimatch ReDoS）、`minimatch 9.0.0–11.0.0`、`glob`、`rimraf 4.2–6.1.3`、`jest/ts-jest`、`deepmerge` | SDK `files` 只发布 `dist/**/*` + README（[package.json:7-10](file:///d:/LiuHao-AI-OS/sdk/javascript/liuhao-sdk/package.json#L7-L10)），全部为开发依赖；`npm audit --omit=dev` = 0，生产依赖仅 axios 1.21.0（无公告） |

---

## 4. 停止维护 / 替代方案

| 组件 | 现状 | 证据 | 替代 | 替换成本 |
|---|---|---|---|---|
| google-generativeai 0.8.6 | 最后发布 2025-12-16，Google 已弃用；随包但与 protobuf>=6 冲突 | PyPI JSON；payload 实测；[pyproject.toml:202-206](file:///d:/LiuHao-AI-OS/pyproject.toml#L202-L206) 已声明移除 | google-genai 2.23.0（已随包、已使用） | **低**：删旧包 + 重建 payload |
| ldap3 2.9.1 | 最后发布 2021-07-18（>5 年），LGPL v3 | PyPI JSON；仅 `.venv`（phoenix 传递） | python-ldap / bonsai；更优是移除 phoenix | **中**（若随 phoenix 一并移除则低） |
| numpy 1.26.4 / scipy 1.13.1（锁内） | 约 2 年前的 pin；无 cp313 轮子；实际由 phoenix 链路引入 | [requirements.txt:342](file:///d:/LiuHao-AI-OS/requirements.txt#L342)、[:641](file:///d:/LiuHao-AI-OS/requirements.txt#L641) | 2.x 系列（若保留 phoenix）；更好是连同 phoenix 一起从运行期锁中剔除 | **中**（涉及 phoenix 取舍） |
| eslint 8（SDK dev） | 官方已 EOL（最新 10.14.0），@typescript-eslint 6（最新 8.72） | `npm view` | eslint 10 + typescript-eslint 8 + jest 30 + rimraf 6 | **中**（lint 规则迁移） |
| arize-phoenix 20.8.0（锁内） | Elastic-2.0，最新 21.0.0，且从不随包 | PyPI JSON；registry acknowledged | 不随包即可；或整体移除 | 低（移除）/ 中（实际启用） |

---

## 5. 安装包体积 Top 10（随包 site-packages 90.8 MB）

| # | 组件 | 大小 | 运行期是否真的用到 | 证据 |
|---|---|---|---|---|
| 1 | cryptography 50.0.1 | 9.97 MB | 是（加密/JWT/密钥） | `src/security/` 主路径 |
| 2 | SQLAlchemy 2.0.52 | 7.76 MB | 是（知识/记忆 ORM） | `src/knowledge`、`src/memory` |
| 3 | openai 3.13.0 | 7.33 MB | 是（OpenAI provider） | `src/ai/providers.py` |
| 4 | google-ai-generativelanguage 0.6.15 | **7.01 MB** | **否**：仅旧版 `google-generativeai` 的传递依赖，而业务已显式拒绝该 SDK | [providers.py:2058-2064](file:///d:/LiuHao-AI-OS/src/ai/providers.py#L2058-L2064) |
| 5 | lxml 6.1.3 | 5.82 MB | 是（python-docx 解析 .docx） | python-docx 传递 |
| 6 | pydantic_core 2.46.5 | 5.20 MB | 是 | pydantic 运行时 |
| 7 | Pygments 2.21.0 | 4.41 MB | 间接（rich/fastmcp CLI 渲染） | rich 传递 |
| 8 | pywin32 311 | 4.24 MB | 是（桌面托盘/服务） | `src/desktop` |
| 9 | anthropic 1.3.0 | 3.93 MB | 是（Anthropic provider） | `src/ai/providers.py` |
| 10 | google-genai 2.23.0 | 3.67 MB | 条件使用（配置 Gemini provider 时延迟导入） | [providers.py:2058](file:///d:/LiuHao-AI-OS/src/ai/providers.py#L2058) |

Top10 合计 59.34 MB = 随包 site-packages 的 65.4%。紧随其后：`fastmcp-slim 4.0.3` 2.90 MB、`pydantic 2.13.5` 1.81 MB、`pypdf 6.21.4` 1.70 MB、`protobuf 5.29.6` 1.55 MB、`python-docx 1.2.0` 1.48 MB。

参考成品体积：`dist/installer/LiuHao-Setup-0.1.1.exe` = 28,866,678 bytes（约 27.5 MiB，压缩后）；其他历史构建在 28.6–43.7 MB 区间。注意当前 `dist/payload` 为 8/30 构建，与最新安装器不一定是同一输入。

---

## 6. 版本锁定与可复现性

### 6.1 Python

- 有锁：`requirements.txt`（210 pin，hash 未启用；无 `--require-hashes`）；另有 `uv.lock`，但**已失效**且本机无 `uv` 可执行文件，无法使用。
- 声明 vs 锁：`pywebview` 声明但未锁；extras 9 条未锁；锁与随包 7 处版本漂移；`.venv` 与锁 39 处不一致。
- 安装可复现性：**取决于解释器次版本**。CPython 3.12 下 numpy 1.26.4 / scipy 1.13.1 有 cp312 轮子（`pip download` 成功）；CPython 3.13 下两者无轮子，`pip install -r` 会退回源码构建并失败（`pip-audit` 实测 `metadata-generation-failed`）。仓库没有 `.python-version` / `runtime.txt` / `requires-python` 上限。
- 结论：`pip install -r requirements.txt` 在目标 3.12 上“能装出锁内版本”，但不保证与当前 payload 一致（payload 不是从该锁构建）。

### 6.2 Node

- 3 个项目均有 `package-lock.json`（lockfileVersion 3）；`npm ci --dry-run --ignore-scripts` 在 console / frontend / SDK 全部通过（exit 0），说明 `package.json` 与 lock 同步、锁可复现同一版本集合。
- 未验证 tarball 完整性（`--dry-run` 不落盘、不校验解包）；`node_modules` 现状：console/frontend 已安装，SDK 未安装（lock 可独立安装）。
- `apps/console/console/package-lock.json` 在审计时被其他并行任务修改（未提交），本报告按当时文件读取。

---

## 7. 条目清单（DEP-01 ～ DEP-19）

> 状态口径：CONFIRMED（未修复）/ OPEN / ACCEPTED RISK / BLOCKED / NOT REPRODUCED。本次为只读，无 FIXED 项。

### DEP-01 锁定集对解释器次版本不可复现（numpy/scipy 无 cp313 轮子）
- **模块**：Python 依赖 / 构建
- **文件:行号**：[pyproject.toml:8](file:///d:/LiuHao-AI-OS/pyproject.toml#L8)（`requires-python = ">=3.11"` 无上限）；[requirements.txt:342](file:///d:/LiuHao-AI-OS/requirements.txt#L342)（`numpy==1.26.4`）；[requirements.txt:641](file:///d:/LiuHao-AI-OS/requirements.txt#L641)（`scipy==1.13.1`）
- **表现**：CPython 3.13 上 `pip install -r requirements.txt` 解析失败（numpy 走 sdist，meson 找不到编译器）；CPython 3.12 正常（轮子可下载）。仓库无 Python 版本固定文件。
- **是否复现**：**已复现**。`pip-audit -r requirements.txt`（隔离 venv，3.13）→ `pip_audit._virtual_env: internal pip failure ... metadata-generation-failed`；`.venv\Scripts\python -m pip download numpy==1.26.4 --no-deps --only-binary=:all:` → Successfully downloaded；3.13 同命令 → no matching distribution。
- **根因**：numpy 1.26.x 不支持 3.13；`requires-python` 未封顶；无 `.python-version`；锁里保留 2 年前的 numpy/scipy（由 phoenix 链路带入）。
- **影响面**：CI / 新机器 / 用户按 README 用系统 Python 安装；构建中断或装出不同版本。
- **安全/数据风险**：低（构建失败非静默降级），但会导致“装不上/装错版本”的不确定性。
- **修复方案**：固定 CPython 3.12（新增 `.python-version` 或 CI matrix + `requires-python` 上限 `<3.13`），或升级 numpy/scipy 并重锁；从 requirements 移除 phoenix 链路可一并消除 numpy/scipy。
- **需要改的文件**：`pyproject.toml`、`requirements.txt`、CI 配置（新增 `.python-version`）
- **验证方法**：在 3.12 与 3.13 各跑 `pip install --dry-run -r requirements.txt`；3.13 应明确失败或不再允许。
- **等级**：P2（若发布流程只用 3.12 则降为 P3） **状态**：OPEN

### DEP-02 锁定集与随包集严重漂移（protobuf 5 vs 6、122 缺 / 61 多）
- **模块**：打包 / Python 依赖
- **文件:行号**：[packaging/prepare_release_venv.ps1:63](file:///d:/LiuHao-AI-OS/packaging/prepare_release_venv.ps1#L63) 期望按 `requirements.txt` 建独立 venv；[packaging/stage_payload.py:788-807](file:///d:/LiuHao-AI-OS/packaging/stage_payload.py#L788-L807) `_measure_distributions()` 按“真实 import 图”在 `sys.executable` 环境里测包，[stage_payload.py:1330](file:///d:/LiuHao-AI-OS/packaging/stage_payload.py#L1330) 只复制测到的 dist
- **表现**：随包 93 个 vs 锁 210 个；122 个 pin 未随包、61 个随包不在锁内；7 个版本漂移，其中 `protobuf 5.29.6`（锁 6.33.6）、`click 8.4.2`（锁 8.5.0）；随包有 `google-generativeai`（锁里没有）；随包 runtime 时间戳 2026-08-30 早于锁更新。
- **是否复现**：**已复现**（脚本比对 `requirements.txt` × `dist/payload/app/runtime/Lib/site-packages/*.dist-info`；`pip-audit --path` 也读到 93 个分发）。根因方向由“payload 不是从 requirements venv 构建”与“按 import 图测量”共同支撑；未逐次复跑构建脚本（只读边界）。
- **根因**：构建用了仓库 `.venv`（含旧包/降级 protobuf）而非 `requirements` 独立 venv；`_measure_distributions` 只复现启动期 import 图，非全量安装集；构建产物未在锁变更后重建。
- **影响面**：安装器实际内容不可由锁推导；SBOM / 许可清单 / 漏洞结论都基于一个“历史环境”；升级与回滚不可控。
- **安全/数据风险**：中。protobuf 5.29.6 与锁内 6.33.6 不一致，锁上安全修复不会进入分发；同时随包多出无人使用的 SDK（DEP-05）。
- **修复方案**：发布构建强制 `PythonExe` 指向由 `requirements.txt` 新建的干净 venv，并在 stage 时 `pip check` + 生成“锁 vs 随包”差异报告作为门禁；删除旧 payload 重新构建。
- **需要改的文件**：`packaging/prepare_release_venv.ps1`、`packaging/stage_payload.py`（测量/校验）、CI/发布脚本
- **验证方法**：重建后比对 `pip freeze`（构建 venv）与 payload `*.dist-info` 名称+版本，期望 0 漂移；`pip check` 在 payload 内通过。
- **等级**：P1 **状态**：OPEN

### DEP-03 `pywebview` 声明为默认桌面后端但未锁定、未随包
- **模块**：桌面端 / Python 依赖
- **文件:行号**：[pyproject.toml:143-146](file:///d:/LiuHao-AI-OS/pyproject.toml#L143-L146)；`src/desktop/native_window.py:243` 延迟 `import webview`；`src/desktop/app.py` 捕获异常后回退浏览器
- **表现**：`dist/payload/app/runtime/python.exe -B -c "import webview"` → `ModuleNotFoundError: No module named 'webview'`；`requirements.txt` / `uv.lock` 均无 pywebview；随包 93 个中无 pywebview。
- **是否复现**：**已复现**（payload 自带 python.exe 实测 ImportError，退出码 1）。
- **根因**：`pyproject` 新增声明（工作树未提交改动）后未运行 `uv pip compile` 重生成锁；payload 从旧环境构建；`stage_payload._MEASURE_CODE`（[stage_payload.py:716-755](file:///d:/LiuHao-AI-OS/packaging/stage_payload.py#L716-L755)）未 `import webview`，即使装了也可能被 import 图剪掉。
- **影响面**：默认“原生窗口”形态失效；用户双击后回退到浏览器 + 警告，产品默认体验不成立。
- **安全/数据风险**：低（有回退），但会误导验收（进程能起、模式不对）。
- **修复方案**：把 `pywebview>=5.0.0` 写入 requirements/uv.lock；在 `_MEASURE_CODE` 显式 `import webview`（真实延迟导入依赖同 pycron 类问题）；重建 payload。
- **需要改的文件**：`requirements.txt`、`uv.lock`、`packaging/stage_payload.py`
- **验证方法**：payload 内 `python -B -c "import webview; print(webview.__version__)"` 成功；桌面启动日志不再出现“无法创建原生窗口”。
- **等级**：P1 **状态**：OPEN

### DEP-04 随包 taskiq 缺少其声明的 `pycron` 依赖
- **模块**：任务排程 / 打包
- **文件:行号**：[requirements.txt:504](file:///d:/LiuHao-AI-OS/requirements.txt#L504)（`pycron==3.3.0`）；payload `taskiq-0.13.0.dist-info/METADATA` 声明 `Requires-Dist: pycron>=3.0.0`；`taskiq/cli/scheduler/run.py:9` `import pycron`
- **表现**：payload 内 `import pycron` → ModuleNotFoundError；`import taskiq` / `LabelScheduleSource` 仍可用（pycron 仅在 CLI scheduler 路径使用）。
- **是否复现**：**已复现**（payload python 实测 `import pycron` 失败；METADATA 已核对）。
- **根因**：`_measure_distributions` 只记录实际 import 到的模块，pycron 属 taskiq 的 CLI 延迟导入，未被测量 → 未复制；`_MEASURE_CODE` 已为同类延迟依赖手工补了 9 个 import，但漏了 pycron（[stage_payload.py:726-755](file:///d:/LiuHao-AI-OS/packaging/stage_payload.py#L726-L755)）。
- **影响面**：当前产品用 `src/tasks/local_scheduler`（非 taskiq CLI），**默认路径不触发**；一旦使用 `taskiq scheduler` / label schedule，运行即失败；发行包元数据不自洽（`pip check` 在 payload 内会报缺依赖）。
- **安全/数据风险**：低；但“声明的依赖缺失”会污染依赖审计与 SBOM。
- **修复方案**：`_MEASURE_CODE` 增加 `import pycron`（或改为“按 Requires-Dist 闭包补齐”而非仅 import 图）；重建后跑 `pip check`。
- **需要改的文件**：`packaging/stage_payload.py`、`requirements.txt`（保持 pin）
- **验证方法**：payload 内 `import pycron` 成功；`python -m pip check`（若带 pip）或脚本校验 `Requires-Dist` 全部可导入。
- **等级**：P2 **状态**：OPEN

### DEP-05 已弃用的 `google-generativeai` 随包，并把 protobuf 压到 5.x
- **模块**：AI Provider / 打包 / 体积
- **文件:行号**：`src/ai/providers.py:2058-2064`（只用 `google.genai`，显式拒绝 `google-generativeai`）；[pyproject.toml:202-206](file:///d:/LiuHao-AI-OS/pyproject.toml#L202-L206)（声明已移除）；随包 `google_generativeai-0.8.6.dist-info`、`google_ai_generativelanguage-0.6.15.dist-info`、`protobuf-5.29.6.dist-info`
- **表现**：随包存在 0.8.6（PyPI 最后发布 2025-12-16）+ 0.6.15（7.01 MB），而锁 pin 是 `protobuf==6.33.6`、随包是 5.29.6；业务代码不 import 该 SDK。
- **是否复现**：**已复现**（payload 分发列表 + 版本比对 + `src` 全局 import 检索）。
- **根因**：payload 来自含旧 SDK 的 `.venv`（DEP-02 同源）；旧 SDK 要求 `protobuf<6`，其存在会迫使解析器降级 protobuf 或直接冲突（pyproject 注释已记录 ResolutionImpossible）。
- **影响面**：+约 7.5 MB 死重；protobuf 与锁/安全基线脱节；未来重装时可能再次触发解析冲突。
- **安全/数据风险**：中低（无业务引用，不会误用旧 SDK；但版本基线不可控）。
- **修复方案**：从构建环境移除 `google-generativeai` 与 `google-ai-generativelanguage`，按 requirements 重建；确认 protobuf 回到 6.33.6（或统一到新基线）。
- **需要改的文件**：无源码改动；`packaging/*`（构建校验）、重建 `dist/payload`
- **验证方法**：重建后 payload 无 `google_generativeai`；`protobuf` 版本 == 锁；`import google.genai` 正常。
- **等级**：P2 **状态**：OPEN

### DEP-06 随包 opentelemetry-api 1.44.0 的 ReDoS（CVE-2026-114603）
- **模块**：可观测性 / Python 依赖
- **文件:行号**：[requirements.txt:375](file:///d:/LiuHao-AI-OS/requirements.txt#L375)；随包 `opentelemetry_api-1.44.0.dist-info`
- **表现**：`TraceState.from_header` 对超长/构造的 tracestate 头做回溯正则；存在 CPU 放大风险。修复版本 1.45.1。
- **是否复现**：**已复现（扫描命中）**；`pip-audit --path payload/site-packages` 输出 1 vulnerability。**运行期利用未复现**：`src` 未接入入站上下文提取（无 `FastAPIInstrumentor` / `propagator.extract` / tracestate 解析）。
- **根因**：版本落后一个补丁；随包环境与锁一致（1.44.0）。
- **影响面**：若今后开启 FastAPI 自动埋点并做 W3C 上下文提取，任意构造 tracestate 的请求可造成 CPU 消耗（不是数据泄露）。
- **安全/数据风险**：可用性（DoS），当前不可触发。
- **修复方案**：`opentelemetry-api/sdk/exporter` 统一升到 1.45.1+ 并重锁；随包重建。
- **需要改的文件**：`requirements.txt`、`uv.lock`、重建 payload
- **验证方法**：`pip-audit --path ...` 无该 CVE；保留一个构造 tracestate 的回归用例（若接入 instrumentation）。
- **等级**：P2（可触发前置条件满足时升 P1） **状态**：OPEN

### DEP-07 开发环境漏洞（pip / pydantic-ai-slim / strawberry），不随包
- **模块**：开发/CI 依赖
- **文件:行号**：[requirements.txt:531](file:///d:/LiuHao-AI-OS/requirements.txt#L531)、[:674](file:///d:/LiuHao-AI-OS/requirements.txt#L674)；`.venv` 实测 pip 25.0.1
- **表现**：`pip-audit --path .venv` → 19 条（去重 13）；锁内同样命中 pydantic-ai-slim 2.40.0 与 strawberry 0.327.0（由 phoenix 带入）。
- **是否复现**：**已复现**（扫描输出，见 3.3）。
- **根因**：phoenix 的观测/评测依赖长期未升级；构建工具 pip 非运行期但被扫描。
- **影响面**：开发机/CI 供应链；不进入安装包。
- **安全/数据风险**：低（不随包、业务不 import）。
- **修复方案**：重锁 pydantic-ai-slim≥2.44/2.53、strawberry≥0.327.2；CI 升级 pip≥26.1.2；长期移除 phoenix 可直接删除整条链路。
- **需要改的文件**：`requirements.txt`、`uv.lock`、CI
- **验证方法**：`pip-audit` 在开发环境 0 命中（或仅剩已书面接受项）。
- **等级**：P3 **状态**：OPEN

### DEP-08 frontend 构建链漏洞（vite 5 / esbuild / postcss-selector-parser）
- **模块**：前端构建
- **文件:行号**：[frontend/package.json:39](file:///d:/LiuHao-AI-OS/frontend/package.json#L39)（`vite ^5.4.10`）、[:37](file:///d:/LiuHao-AI-OS/frontend/package.json#L37)（tailwind 3）
- **表现**：`npm audit` → 3 条（1 high、2 moderate）：vite ≤6.4.3 含 8 个公告（路径穿越、`server.fs.deny` 绕过、launch-editor/UNC、任意命令执行等），esbuild ≤0.24.2，postcss-selector-parser <6.1.5。
- **是否复现**：**已复现**（`npm audit --json`，见 3.4）。
- **根因**：组件库工程停留在 vite 5（console 已用 8.3.6）。
- **影响面**：开发者在 Windows 上运行 `vite dev` 时的本机风险；构建期恶意 CSS 可造成 CPU 耗尽；不随包。
- **安全/数据风险**：开发期本机风险（NTLM 哈希泄露/命令执行类公告）；终端用户不可触发。
- **修复方案**：vite → ^8.3.6、@vitejs/plugin-react → 6.x、tailwind 4（或至少 vite 6.4.4+ 过渡），重生成 lock。
- **需要改的文件**：`frontend/package.json`、`frontend/package-lock.json`
- **验证方法**：`npm audit` 0（或仅剩 dev 已接受项）；`npm run build`、`npm test`。
- **等级**：P3 **状态**：OPEN

### DEP-09 SDK 开发工具链 15 条漏洞 + eslint 8 EOL
- **模块**：JS SDK 工程
- **文件:行号**：[sdk/javascript/liuhao-sdk/package.json:45-54](file:///d:/LiuHao-AI-OS/sdk/javascript/liuhao-sdk/package.json#L45-L54)
- **表现**：`npm audit` 15 条（6 high：@typescript-eslint 6.16–7.5 的 minimatch ReDoS；moderate：glob/rimraf；low：jest/deepmerge）；生产依赖 `axios 1.21.0` 0 命中。eslint 8 已 EOL（最新 10.14）。
- **是否复现**：**已复现**（`npm audit --json`；`npm audit --omit=dev` 0）。
- **根因**：SDK 工程工具链约 2023 年的版本，未升级。
- **影响面**：开发者 lint/test 环境；发布物 `dist/` 不含 devDeps（`files` 白名单）。
- **安全/数据风险**：低（不随发布物）。
- **修复方案**：eslint 10 + @typescript-eslint 8 + jest 30 + rimraf 6；`engines.node` 从 >=14 提到 >=18。
- **需要改的文件**：`sdk/javascript/liuhao-sdk/package.json`、lockfile
- **验证方法**：`npm audit` 0；`npm test` / `npm run lint` / `npm run build` 通过。
- **等级**：P3 **状态**：OPEN

### DEP-10 `oss-registry.yaml` 覆盖率 38/93，许可门禁存在系统性盲区
- **模块**：许可合规门禁
- **文件:行号**：[oss-registry.yaml](file:///d:/LiuHao-AI-OS/oss-registry.yaml)（38 条 components）；[verify_oss_licenses.py:122-139](file:///d:/LiuHao-AI-OS/scripts/verify_oss_licenses.py#L122-L139)（只遍历登记册、未安装即 skip）
- **表现**：68 个随包组件未登记；25 个 pyproject 直接依赖未登记；13 个登记但未随包（含 Elastic-2.0 的 phoenix）。`verify_oss_licenses.py` 只输出 26 个 `[ok]` + 1 个 `[review]`，不会发现未登记/未安装项。
- **是否复现**：**已复现**（脚本比对 registry × payload dist-info；实跑 verify 仅遍历 registry，见附录 B）。
- **根因**：登记册由“人工按需添加”维护，门禁以登记册为输入；没有“实际分发集必须全部登记”的反向校验。
- **影响面**：新增依赖、随包传递依赖永不被检查；许可证变更（如 Redis 8 → AGPL）只能靠人工记忆。
- **安全/数据风险**：合规风险（分发含未登记许可证），非运行安全。
- **修复方案**：以 payload `*.dist-info` 为输入生成/补齐登记册（至少 68 条），或让门禁双向校验：payload 每个 dist 必须有 registry 记录且许可证族在 allow/restricted 规则内；`third_party_notices` 已能自动覆盖，可复用其枚举。
- **需要改的文件**：`oss-registry.yaml`、`scripts/verify_oss_licenses.py`（只读建议，未改）
- **验证方法**：`verify_oss_licenses.py` 对 payload 跑“零未登记”通过；故意少登记一个包时门禁失败（红证）。
- **等级**：P2 **状态**：OPEN

### DEP-11 Elastic-2.0（arize-phoenix）声明为运行期依赖但从不随包
- **模块**：可观测性 / 许可
- **文件:行号**：[pyproject.toml:44](file:///d:/LiuHao-AI-OS/pyproject.toml#L44)；[requirements.txt:41](file:///d:/LiuHao-AI-OS/requirements.txt#L41)；[oss-registry.yaml:145-150](file:///d:/LiuHao-AI-OS/oss-registry.yaml#L145-L150)（已 acknowledged）；`src/adapters/observability/observability_adapter.py:34-41`（`try: import phoenix`，缺失则 `PHOENIX_AVAILABLE=False`，导出静默跳过）
- **表现**：锁里是 Elastic-2.0 的 phoenix 20.8.0（且 2 条 Phoenix 相关弃用说明），随包没有；启用导出时只打印“Phoenix not available, skipping export”。
- **是否复现**：**已复现**（registry 比对 + payload 缺失 + 代码分支阅读）。
- **根因**：依赖声明面未与“实际随包白名单”对齐；构建按 import 图剪枝恰好把它剪掉。
- **影响面**：形如“观测导出可选”的功能静默不可用；若改为按 requirements 全量随包，会带入 Elastic-2.0（source-available，禁止以托管服务形式提供）。
- **安全/数据风险**：合规风险（许可证约束），当前不分发故不触发。
- **修复方案**：明确取舍——要么从 `pyproject/requirements` 移除 phoenix（并用 Langfuse/自建 OTLP 替代），要么保留“永不随包”的显式构建断言 + 文档说明。
- **需要改的文件**：`pyproject.toml`、`requirements.txt`、`oss-registry.yaml`、构建门禁
- **验证方法**：payload 内不存在 `arize_phoenix-*.dist-info`；`verify_oss_licenses` 对 payload 通过；观测导出路径有明确日志/文档。
- **等级**：P2 **状态**：ACCEPTED RISK（许可已书面接受）＋清单不一致（OPEN）

### DEP-12 LGPL-3.0 / LGPL v3 仅存在于开发环境
- **模块**：许可
- **文件:行号**：`.venv` 实测 `ldap3 2.9.1`（LGPL v3）、`psycopg 3.x` 与 `psycopg-binary`（LGPL-3.0-only，来自 `[dbos]`）；[packaging/licenses/LGPL-2.1.txt](file:///d:/LiuHao-AI-OS/packaging/licenses/LGPL-2.1.txt)、[LGPL-SOURCE-OFFER.txt](file:///d:/LiuHao-AI-OS/packaging/licenses/LGPL-SOURCE-OFFER.txt) 覆盖随包的 libiconv/adodbapi
- **表现**：随包无 LGPL/GPL/AGPL；开发环境有 3 个 LGPL 分发。
- **是否复现**：**已复现**（`.venv` 元数据扫描）。
- **根因**：`[dbos]` extra 与 phoenix 链路只在开发机安装。
- **影响面**：默认分发包不受影响；若未来把 `[dbos]` 或 phoenix 纳入随包，需评估 LGPL 动态链接义务。
- **安全/数据风险**：合规。
- **修复方案**：保持 extras 不进随包；若启用 dbos，记录 LGPL 义务并保留源码获取途径。
- **需要改的文件**：无（文档/registry 备注）
- **验证方法**：payload 许可证扫描无 LGPL/GPL/AGPL。
- **等级**：P3 **状态**：ACCEPTED RISK

### DEP-13 随包 MPL-2.0（certifi、orjson）未登记
- **模块**：许可
- **文件:行号**：payload `certifi-2026.10.23.dist-info`、`orjson-3.12.0.dist-info`；registry 仅登记 `orjson`（certifi 未登记）
- **表现**：MPL-2.0 属文件级 copyleft，未修改源文件时不触发公开义务；`certifi` 为纯 MPL-2.0 且未进登记册；`orjson` 为 MPL-2.0 与 Apache/MIT 双许可（可整体按 Apache-2.0 处理），已在登记册。
- **是否复现**：**已复现**（license 扫描 + registry 比对）。
- **根因**：DEP-10 的同一覆盖缺口。
- **影响面**：若有人修改 certifi 的 MPL 文件并分发，需公开对应文件源码。
- **安全/数据风险**：合规（低）。
- **修复方案**：登记 certifi 并注明“未修改、仅文件级 copyleft”；notices 已包含 MPL 文本。
- **需要改的文件**：`oss-registry.yaml`
- **验证方法**：registry 覆盖率 93/93；`third_party_notices --check` 继续通过。
- **等级**：P3 **状态**：OPEN

### DEP-14 重复依赖 / 多套同类库
- **模块**：依赖结构
- **文件:行号**：[uv.lock:2934](file:///d:/LiuHao-AI-OS/uv.lock#L2934)、[:3018](file:///d:/LiuHao-AI-OS/uv.lock#L3018)（numpy 双版本）；[:4825](file:///d:/LiuHao-AI-OS/uv.lock#L4825)、[:4901](file:///d:/LiuHao-AI-OS/uv.lock#L4901)（scipy 双版本）；随包 HTTP 栈 8 个；[frontend/package.json:28](file:///d:/LiuHao-AI-OS/frontend/package.json#L28)（React 18）vs console React 19
- **表现**：见 1.3。
- **是否复现**：**已复现**（锁/随包/工程文件扫描）。
- **根因**：上游 SDK 分裂（openai→httpx2；phoenix→旧 Google/评测栈）+ 两个前端工程各自维护 React/Vite/TS 版本。
- **影响面**：体积、攻击面、升级分裂；CVE 需修两套。
- **安全/数据风险**：低。
- **修复方案**：优先移除 `google-generativeai`；console 与 frontend 统一 React 19（frontend 组件库 peerDeps 已放宽到 >=18，升级成本可控）；httpx/httpx2 并存属上游硬依赖，保留但在 SBOM 注释。
- **需要改的文件**：`frontend/package.json`、`requirements.txt`
- **验证方法**：重复项清单收敛（同名多版本 0；Google SDK 1 套）。
- **等级**：P3 **状态**：OPEN

### DEP-15 `uv.lock` 已失效且本机无 `uv`
- **模块**：版本锁定
- **文件:行号**：[uv.lock](file:///d:/LiuHao-AI-OS/uv.lock)（223 entries；缺 pywebview / bcrypt / lxml / pycron / pypdf / python-docx / taskiq / taskiq-dependencies 与全部 extras；numpy/scipy 各 2 个版本）
- **表现**：`uv --version` 不存在；`uv.lock` 的内容与当前 `pyproject`/`requirements` 均不一致；CI 未使用它。
- **是否复现**：**已复现**（`uv --version` 失败；lock 解析比对）。
- **根因**：锁未随依赖变更重生成；团队实际用 requirements，但旧 uv.lock 未删除。
- **影响面**：误导读者以为有可复现锁；两套“锁”互相矛盾。
- **安全/数据风险**：低（流程混乱）。
- **修复方案**：只保留一个真源。推荐：保留 `requirements.txt`（当前构建脚本使用），删除 `uv.lock`；或引入 uv 并以其为唯一真源、CI 用 `uv sync --frozen`。
- **需要改的文件**：`uv.lock`（删除或重生成）、CI、README
- **验证方法**：真源锁可在一台干净机器装出与 payload 一致集合；CI 有 `--frozen`/hash 校验。
- **等级**：P2 **状态**：OPEN

### DEP-16 开发环境 `.venv` 与锁漂移（39 处）
- **模块**：开发环境
- **文件:行号**：`.venv`（271 个分发）vs [requirements.txt](file:///d:/LiuHao-AI-OS/requirements.txt)（210 pin）
- **表现**：39 个版本不一致（numpy、scipy、protobuf、wrapt、click、alembic、langchain-core、langfuse、jsonschema、boto3 等），61 个未登记进锁的分发（多为 dev 工具/旧包）；`pip check` 通过。
- **是否复现**：**已复现**（importlib.metadata × requirements 比对，附录 A）。
- **根因**：`.venv` 长期原地升级/安装 extras，未按锁重建；payload 又复用了它（DEP-02）。
- **影响面**：本机测试结果与锁不一致；`pip check` 给人“环境健康”的错觉。
- **安全/数据风险**：中低（测试环境与发布基线不一致）。
- **修复方案**：按 `requirements.txt` 重建 `.venv`；CI 增加“`pip freeze` 与锁 diff”门禁。
- **需要改的文件**：无源码；脚本/CI
- **验证方法**：重建后 0 版本漂移（除显式 extras）。
- **等级**：P3 **状态**：OPEN

### DEP-17 `taskiq-dependencies` 许可证元数据缺失
- **模块**：许可声明
- **文件:行号**：payload `taskiq_dependencies-1.5.7.dist-info/METADATA`（无 License 字段；包内 LICENSE 文本存在）
- **表现**：notices 只能输出“见随包文本”；registry 未登记该组件。
- **是否复现**：**已复现**（元数据扫描；PyPI JSON `license=""`）。
- **根因**：上游打包未写 License 元数据。
- **影响面**：机器可读许可清单出现 `MISSING` 一项；不影响随包分发合法性（有文本）。
- **安全/数据风险**：合规（低）。
- **修复方案**：人工确认上游 LICENSE（taskiq 组织）后在 registry/notices 显式标注，不修改上游包。
- **需要改的文件**：`oss-registry.yaml`（标注）
- **验证方法**：notices 无 `MISSING` 行（允许 `见随包文本` 且 registry 有记录）。
- **等级**：P4 **状态**：OPEN

### DEP-18 令牌/密钥排查
- **模块**：配置
- **文件:行号**：`C:\Users\Administrator\.npmrc`（仅 registry 配置，无 `_authToken`）；仓库内无 `.npmrc`/`.pypirc`；依赖声明文件中无内嵌 token
- **表现**：未发现依赖域令牌/密钥。
- **是否复现**：**静态判断**（只读检查上述文件）。
- **等级**：P4 **状态**：NOT REPRODUCED（无问题）

### DEP-19 18 个“运行期直接依赖”未随包（声明面 ≠ 分发面）
- **模块**：依赖声明 / 打包
- **文件:行号**：[pyproject.toml:14-248](file:///d:/LiuHao-AI-OS/pyproject.toml#L14-L248)；实测脚本比对 `pyproject.dependencies` × payload `*.dist-info`
- **表现**：未随包清单 = `arize-phoenix`、`hvac`、`hypothesis`、`langchain`、`langchain-community`、`langchain-core`、`langchain-openai`、`langfuse`、`langgraph`、`mem0ai`、`pydantic-monty-client`、`pytest`、`pytest-asyncio`、`python-json-logger`、`pywebview`、`qdrant-client`、`redis`、`structlog`（18/48）。
- **是否复现**：**已复现**（比对脚本输出；`import pywebview` 实测失败；`src/ai/langgraph_workflow.py:27-29` 有 `LANGGRAPH_AVAILABLE` 降级开关、`src/knowledge/memory.py:48` 有 `mem0_available()` 降级开关）。
- **根因**：`stage_payload` 按启动 import 图取包（DEP-02 同根因）；`pyproject` 把“可选集成”和“桌面后端”全部写进主依赖，而不是 extras，导致声明面远大于实际分发面。另有 4 个测试类依赖（pytest/pytest-asyncio/hypothesis）本不该出现在主依赖。
- **影响面**：内置记忆→mem0、LangGraph 工作流、Redis/Qdrant 生产后端、Langfuse/Phoenix 导出等能力在安装包里**静默降级为不可用**；依赖清单无法用于判断“安装包能做什么”。用户可见但不报错，验收时容易被误判为“功能已具备”。
- **安全/数据风险**：中（功能缺失而非崩溃；但会误导用户与验收结论）。
- **修复方案**：把真正必随的能力（至少 pywebview）移入锁定与 import 图；可选集成改为 extras + 运行时能力探测；测试依赖移到 `[tests]`；对“未随包但被声明”的项在发布门禁里必须显式 allowlist（附理由）。
- **需要改的文件**：`pyproject.toml`、`requirements.txt`、`packaging/stage_payload.py`、`oss-registry.yaml`
- **验证方法**：发布包启动自检输出能力矩阵（mem0/langgraph/redis/qdrant/langfuse/webview 的 available 状态）；门禁断言“主依赖集合 ⊂ 随包集合，或位于显式 allowlist”。
- **等级**：P1 **状态**：OPEN

---

## 8. 修复顺序建议（最该先修的 5 条）

1. **DEP-02 + DEP-19**：用 `requirements.txt` 重建独立发布 venv 并重打 payload；把“可选集成”移出主依赖，发布门禁要求“主依赖 ⊂ 随包 或 显式 allowlist + 能力探测”。它同时消除 DEP-04/05/06 的验证障碍。
2. **DEP-03**：补 `pywebview` 进 requirements/uv.lock 与 `_MEASURE_CODE`，重建后实测 `import webview`。这是用户可直接感知的默认形态缺陷。
3. **DEP-06 + DEP-07**：把 opentelemetry 升到 1.45.1+、pydantic-ai-slim ≥2.44、strawberry ≥0.327.2，重新扫描归零（成本最低、直接消掉随包唯一 CVE）。
4. **DEP-10**：把 `oss-registry.yaml` 覆盖到随包 93/93，并让门禁以实际分发集为输入（否则许可证风险永远只能靠人工）。
5. **DEP-01**：固定 CPython 3.12（`.python-version` + `requires-python <3.13` 或升级 numpy/scipy），让“按锁安装”在任何机器上结果一致。

---

## 附录 A：`.venv` 与 `requirements.txt` 版本漂移（39 条）

```
alembic 1.19.2→1.20.0            anyio 4.15.1→4.15.0            arize-phoenix 20.8.0→20.11.0
arize-phoenix-client 3.4.0→3.5.0  arize-phoenix-evals 3.6.0→3.8.0  arize-phoenix-sqlean 0.1.1→0.1.2
bashkit 0.17.1→0.18.0            boto3 1.43.89→1.43.93          botocore 1.43.89→1.43.93
caio 0.12.2→0.12.4               click 8.5.0→8.4.2              cyclopts 4.24.0→4.25.2
exceptiongroup 1.3.1→1.2.2       google-auth 2.57.1→2.58.0      google-genai 2.22.0→2.23.0
googleapis-common-protos 1.75.3→1.75.0  jsonschema 4.26.0→4.25.1  langchain-core 1.6.2→1.6.3
langchain-openai 1.6.0→1.6.2     langfuse 4.15.1→4.15.2         langsmith 0.12.1→0.12.4
logfire-api 5.0.0→5.1.0          narwhals 2.25.0→2.26.0         numpy 1.26.4→2.5.2
openinference-instrumentation-openai 0.1.58→0.1.60  openinference-instrumentation 0.1.61→0.1.63
openinference-semantic-conventions 0.1.35→0.1.37  platformdirs 4.11.7→4.11.8  posthog 7.47.0→7.45.3
protobuf 6.33.6→5.29.6           pydantic-ai-slim 2.40.0→2.43.0  pydantic-graph 2.40.0→2.43.0
regex 2026.9.3→2026.9.10         scikit-learn 1.9.0→1.9.1        scipy 1.13.1→1.18.1
tqdm 4.70.0→4.70.1               tzdata 2026.3→2026.4           uuid_utils 0.17.0→0.17.1
wrapt 2.4.0→1.17.3
```

另有 61 个“已安装但不在 requirements.txt”的分发（开发工具/旧链路残留）。

## 附录 B：关键命令与输出摘要

```powershell
# 1) Python 漏洞（随包）
pip-audit --path dist\payload\app\runtime\Lib\site-packages
# → Found 1 known vulnerability in 1 package: opentelemetry-api 1.44.0 CVE-2026-114603 (fix 1.45.1)

# 2) Python 漏洞（开发环境）
pip-audit --path .venv\Lib\site-packages
# → Found 19 known vulnerabilities in 4 packages（opentelemetry-api / pip / pydantic-ai-slim / strawberry-graphql）

# 3) 锁在 3.13 下不可安装（真实失败）
pip-audit -r requirements.txt        # venv created from CPython 3.13
# → internal pip failure ... numpy 1.26.4 构建失败 metadata-generation-failed
# 3.12 下同一命令解析成功并完成审计：
pip-audit -r requirements.txt        # venv created from CPython 3.12
# → Found 7 known vulnerabilities in 3 packages（210 个 pin 全部解析）
# 3.12 下同版本轮子可下载：
.venv\Scripts\python -m pip download numpy==1.26.4 --no-deps --only-binary=:all:   # Successfully downloaded numpy
.venv\Scripts\python -m pip download scipy==1.13.1 --no-deps --only-binary=:all:   # Successfully downloaded scipy

# 4) 随包完整性实测
dist\payload\app\runtime\python.exe -B -c "import pycron"   # ModuleNotFoundError: No module named 'pycron'
dist\payload\app\runtime\python.exe -B -c "import webview"  # ModuleNotFoundError: No module named 'webview'

# 5) 通知/许可
packaging\third_party_notices.py --app dist\payload\app --check dist\payload\app\THIRD-PARTY-NOTICES.txt  # RC=0
.venv\Scripts\python scripts\verify_oss_licenses.py   # 只遍历 registry（26 ok + 1 review），未覆盖未登记组件

# 6) npm
npm audit --json                      # console: 0；frontend: 3（vite/esbuild/postcss-selector-parser）；sdk: 15（全 dev）；eap: 0
npm audit --omit=dev --json           # sdk: 0
npm ci --dry-run --ignore-scripts     # console / frontend / sdk 全部 exit 0
```

## 附录 C：随包 93 个分发的名字与版本（实测）

```
aiohappyeyeballs 2.7.1, aiohttp 3.14.4, aiosignal 1.4.0, aiosqlite 0.22.1, annotated-doc 0.0.4,
annotated-types 0.8.0, anthropic 1.3.0, anyio 4.15.0, attrs 26.1.0, bcrypt 5.0.0, certifi 2026.10.23,
cffi 2.1.0, charset-normalizer 3.4.5, click 8.4.2, cryptography 50.0.1, docstring-parser 0.18.0,
email-validator 2.3.0, fastmcp-slim 4.0.3, fastapi 0.141.1, frozenlist 1.9.0,
google-ai-generativelanguage 0.6.15, google-api-core 2.30.0, google-auth 2.58.0, google-genai 2.23.0,
google-generativeai 0.8.6, googleapis-common-protos 1.75.0, greenlet 3.5.5, h11 0.16.0, h2 4.3.0,
hpack 4.2.0, httpcore 1.0.9, httpcore2 2.12.0, httpx 0.28.1, httpx2 2.12.0, hyperframe 6.1.0,
idna 3.18, jiter 0.15.0, lxml 6.1.3, mcp 2.2.0, mcp-types 2.2.0, multidict 6.8.0,
opentelemetry-api 1.44.0, opentelemetry-sdk 1.44.0, opentelemetry-exporter-otlp 1.44.0,
opentelemetry-exporter-otlp-proto-common 1.44.0, -grpc 1.44.0, -http 1.44.0,
opentelemetry-instrumentation 0.65b0, -asgi 0.65b0, -fastapi 0.65b0, -requests 0.65b0, -threading 0.65b0,
opentelemetry-proto 1.44.0, opentelemetry-semantic-conventions 0.65b0, opentelemetry-util-http 0.65b0,
orjson 3.12.0, packaging 26.3, platformdirs 4.11.8, prometheus-client 0.26.0, propcache 0.5.1,
protobuf 5.29.6, psutil 7.2.2, pydantic 2.13.5, pydantic-core 2.46.5, pydantic-settings 2.15.0,
Pygments 2.21.0, PyJWT 2.15.1, pypdf 6.21.4, python-docx 1.2.0, python-dotenv 1.2.3,
python-multipart 0.0.21, pywin32 311, PyYAML 6.0.3, requests 2.34.2, RestrictedPython 8.5, rich 15.0.0,
sniffio 1.3.1, SQLAlchemy 2.0.52, sse-starlette 3.4.0, starlette 1.6.0, taskiq 0.13.0,
taskiq-dependencies 1.5.7, tenacity 9.1.4, truststore 0.10.4, typing-extensions 4.16.0,
typing-inspection 0.4.2, urllib3 2.8.0, uvicorn 0.52.4, watchfiles 1.1.1, websockets 16.1.1,
yarl 1.24.0, zstandard 0.26.0
```

> 上表是 2026-11-08 凌晨首次体检快照（93 个）。当天并行打包线补齐桌面后端后又增加
> `pywebview 6.2.1 / bottle 0.13.4 / proxy_tools 0.1.0 / pythonnet 3.2.1 / clr_loader 0.3.1 /
> pycparser 3.0` 6 个，随包集变为 99 个；第 9 章按 99 个复核。

## 9. 修复记录（依赖域修复线 · 2026-11-08）

本节是本轮修复的落地记录，状态以此为准；第 7 章条目里未被本节覆盖的部分仍然成立。

### 9.1 门禁：输入从「登记册」改成「实际分发集」

改动文件：[scripts/verify_oss_licenses.py](file:///d:/LiuHao-AI-OS/scripts/verify_oss_licenses.py)

- 输入三选一，脚本第一行打印实际使用的输入，杜绝「不知道在验什么」：
  - `--dist-path <site-packages|venv>`：**发布门禁**模式，逐项读 `*.dist-info/METADATA`；
  - `--env`：CI 开发环境自检（含 dev extras，明确不等于随包集）；
  - 无参数：仓库内 `dist/payload/.../site-packages` 存在则等同 `--dist-path`，否则回退 `--env`。
- 双向校验：
  - 随包有、登记表无 → **FAIL**；
  - 登记表有、随包没有 → 打印 `[DRIFT]`，不阻断（「声明了但从不分发」不构成分发风险）；
  - 许可证按「族」比对（MIT / MIT License / MIT-0 视为同族），不再要求字面完全一致；
  - 受限许可证无 `acknowledged` → FAIL；无法归类的许可证 → FAIL；
  - `dist-info` 缺 `METADATA`（payload 正在重建/不完整）在发布模式下 → FAIL，避免半成品 payload 假绿。
- 许可证来源全部实测：`License-Expression` → `License` classifier → `License` 短字段 →
  都没有时读包内 `LICENSE*`/`licenses/*` 文本按已知许可证标记判定（`taskiq-dependencies 1.5.7`
  就是元数据无字段、包内为 MIT 文本，门禁按文本判定为 MIT）。
- 门禁输入与规模（2026-11-08 01:1x 快照）：
  - 随包实际分发 **99** 个（`dist/payload/app/runtime/Lib/site-packages`）；
  - `oss-registry.yaml` 组件 **112** 个（原 38 + 本次实测补 74）；
  - 随包且已登记 **99 / 99**；随包未登记 **0**；登记未随包 **13**（drift，见 9.2）。

三态证据（同一天连续验证；期间并行打包线也在改 payload，每步数字见下）：

```
# ① 未补齐时（93 随包 vs 38 登记）：判红
[FAIL] 实际分发集存在 6 项阻止项： bottle / clr_loader / proxy_tools / pycparser / pythonnet / pywebview

# ② 补齐后：判绿
实际分发 99 个｜登记 112 个｜随包且已登记 99 个｜随包未登记 0 个｜登记未随包（drift）13 个
[GREEN] 实际分发集内每个组件都已登记，且许可证与登记一致

# ③ 故意删掉 certifi 一条登记项（--registry 指向临时副本）：必须 FAIL
removed 'certifi'; components=111
[FAIL] 实际分发集存在 1 项阻止项： - certifi 2026.10.23（许可证实测：MPL-2.0）
EXIT=1
```

CI 同步：[ci.yml](file:///d:/LiuHao-AI-OS/.github/workflows/ci.yml) 的许可证步骤显式写成
`--env`，并在注释里写清「CI 只有开发环境，不是随包集；发布门禁必须 `--dist-path`」。

### 9.2 登记册补齐（来源全是实测）

- 74 条新条目由 `dist-info/METADATA` 实测生成（名称、版本、许可证、`top_level.txt` 导入名），
  每条 `action` 注明版本与「src 是否直接 import」，没有凭印象写的条目。
- 新增 6 个桌面后端组件（来自并行打包线当天补齐的随包集）：
  `pywebview 6.2.1（BSD License）/ bottle 0.13.4 / proxy_tools 0.1.0 / pythonnet 3.2.1 / clr_loader 0.3.1 / pycparser 3.0`。
- 仍为 drift 的 13 个（登记但当前不随包，不阻断，需人确认）：
  `arize-phoenix、langgraph、langfuse、mem0ai、qdrant-client、redis、hvac、structlog、pytest、pytest-asyncio、hypothesis、…`
  —— 其中多数就是 DEP-19「运行期直接依赖未随包」，仍归打包线修。

### 9.3 opentelemetry-api 升到安全基线

- `requirements.txt` 12 行同步升级（必须整族一起走，sdk 1.45.x 硬依赖 api 与 semconv 同档）：
  `api/sdk/exporter-otlp/-proto-common/-proto-grpc/-proto-http/proto` → `1.45.1`；
  `instrumentation/-asgi/-fastapi/semantic-conventions/util-http` → `0.66b1`。
- `pyproject.toml` 四个 floor 抬到 `>=1.45.1` / `>=0.66b1`，避免裸装回退到 1.44。
- 隔离 venv 实测（未污染仓库环境）：`pip check` → `No broken requirements found.`；
  `api 1.45.1 sdk 1.45.1 instr 0.66b1 semconv 0.66b1`；OTLP HTTP exporter 可 import。
- 漏洞复扫：`pip-audit --path <该 venv>` 中 **opentelemetry 命中 0**（该 venv 只有 pip 自身 6 条）。
- 「源码里没有 tracestate 解析」复核**仍成立**：全仓 `.py` 无 `TraceState.from_header`、
  无 `opentelemetry.propagate`、无 `FastAPIInstrumentor`；`src/observability/models.py` 只有一个
  `trace_state` 字符串字段（存取/透传），不做头解析。升级后连「仅版本命中」也不再存在。

### 9.4 Python 版本：选「收窄上界」而不是升级 numpy/scipy

- `pyproject.toml`：`requires-python = ">=3.11,<3.13"`；新增仓库根 `.python-version` = `3.12`。
- 理由（低风险优先）：3.13 失败的根因是 numpy 1.26.4 / scipy 1.13.1 无 cp313 轮子（走 sdist 后
  因无 C 编译器 `metadata-generation-failed`），而这两个版本被 arize-phoenix 链路牵着；升级
  numpy/scipy 要重解整条 phoenix 依赖并可能改动数值行为，风险远大于把解释器收窄到 CI 已在跑的
  3.11/3.12。验证：`'3.13' in SpecifierSet('>=3.11,<3.13')` → `False`，`'3.12'` → `True`。

### 9.5 google-generativeai：声明面已移除，残留是 payload 侧

- `pyproject.toml` / `requirements.txt` / `uv.lock` 均无 `google-generativeai`、
  无 `google-ai-generativelanguage`（仅 pyproject 有一条历史注释）。
- 全仓 grep：`src/` 无 `google.generativeai` / `google.ai.generativelanguage` 导入；
  `src/ai/providers.py` 明确拒绝该 SDK。
- 随包导入链实测：`google.genai` 可 import；`google.generativeai` **import 失败**
  （缺 `proto-plus`），即残留包本身不可用，不是「能跑但没用」而是「已损坏的死重」。
- 回归兜底：`scripts/verify_dependency_declaration_surface.py` PASS —— 任何人再 import
  `google.generativeai` 都会因「未声明依赖」判红，不会静默复活。
- 7.01 MB 的移除发生在 payload 重建时，属打包线（DEP-02/19），本轮不动 `dist/**`。

### 9.6 uv.lock：已重生成到与 pyproject 一致

- 用隔离 venv 里的 `uv`（未安装进仓库环境）执行 `uv lock`：258 个 entry（原 223），
  覆盖 `pywebview` 与全部 9 组 extras（`fakeredis/lupa/pytest-timeout/playwright/selenium/
  argon2-cffi/pyotp/taskiq-redis/dbos`）。
- `uv lock --check` → exit 0（与 `pyproject.toml` 一致）。
- **口径说明**：`requirements.txt` 是发布/打包的唯一安装口径（打包线按它建 venv）；
  `uv.lock` 是开发解析口径。两者是两次独立解析，版本会不同（例如 numpy：requirements
  `1.26.4`，uv.lock `2.x`）。这不是新的「骗人锁」——uv.lock 不再缺包——但后续若要单一真源，
  应择一删除；本轮按任务书选「重生成」，不删。

### 9.7 边界、阻塞与遗留

- 未改：`packaging/stage_payload.py`、`packaging/third_party_notices.py`、
  `packaging/PAYLOAD-CONTENT.md`；未改 `requirements.txt` 里
  `pywebview / bottle / proxy_tools / pythonnet / clr_loader` 相关行；未 `git add` / commit。
- **BLOCKED（外部）**：`scripts/release/freeze_checks.py deps` 报
  `有模块无法映射到发行包：['CLR']`。`pythonnet 3.2.1` 是并行打包线当天的产物，
  `CLR` 由 `pythonnet.pth` 提供、不在 `top_level.txt`，属打包/冻结检查侧，非本轮改动引入。
- **payload 当前不完整（外部）**：01:10 之后 `dist/payload/.../site-packages` 稳定在
  99 个 `dist-info`，其中 **22 个是空目录**（按字母序截断：aiohappyeyeballs … frozenlist，
  连 `METADATA` 都没有），20 分钟未恢复 → 不是瞬时竞态，是被中断的拷贝。
  后果：新门禁在发布模式判红（`dist-info 缺少 METADATA … 不能代表实际分发集`，这是预期行为，
  不允许半成品 payload 假绿）；`packaging/third_party_notices.py --check` 同样红
  （`缺少许可证文本：licenses/proxy_tools-BSD.txt` 等）。须由打包线重做 payload 后再复跑
  9.1 的 ②③ 两态；`dist/**` 不在本轮边界内。

