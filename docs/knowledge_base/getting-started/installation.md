# 安装指南

本指南将帮助你在不同环境下安装和配置 LiuHao AI OS（鎏灏 X）。

---

## 系统要求

### 最低配置

| 组件 | 最低要求 | 推荐配置 |
|------|---------|---------|
| 操作系统 | Windows 10 / macOS 12 / Ubuntu 20.04 | Windows 11 / macOS 14 / Ubuntu 22.04 |
| Python | 3.11+ | 3.12+ |
| Node.js | 18+（仅驾驶舱需要） | 20+ |
| 内存 | 8 GB RAM | 16 GB+ RAM |
| 磁盘空间 | 5 GB | 20 GB+ SSD |
| GPU | 可选（加速本地模型） | NVIDIA GPU 8GB+ VRAM |

### 软件依赖

- **Ollama**（本地 LLM 推理，必需）
- **SQLite**（默认数据库，Python 内置）
- **PostgreSQL 15+**（可选，生产环境推荐）
- **Redis**（可选，缓存和消息队列）
- **Docker**（可选，容器化部署）

---

## 快速安装

### 方式一：Windows 一键启动（最简单）

适合 Windows 用户快速体验。

1. **克隆仓库**

```bash
git clone https://github.com/your-org/LiuHao-AI-OS.git
cd LiuHao-AI-OS
```

2. **安装 Ollama**

下载并安装 [Ollama](https://ollama.com/)，然后拉取默认模型：

```bash
ollama pull qwen2.5:3b
ollama pull nomic-embed-text
```

3. **一键启动**

双击仓库根目录的 `start-liuhao.bat`，或在命令行执行：

```bash
.venv\Scripts\python.exe scripts\start_liuhao.py
```

启动器会等待健康探针通过后打印：

```
鎏灏已就绪
  驾驶舱    http://127.0.0.1:5173
  API       http://127.0.0.1:8080     文档 http://127.0.0.1:8080/docs
  健康探针  http://127.0.0.1:8080/v1/health
```

### 方式二：pip 安装

```bash
# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# 或
.venv\Scripts\activate    # Windows

# 安装 LiuHao AI OS
pip install liuhao-ai-os

# 初始化配置
liuhao init
```

> **注意**：pip 包发布以官方 PyPI 为准。开发阶段推荐从源码安装。

### 方式三：Docker 部署

```bash
# 克隆仓库
git clone https://github.com/your-org/LiuHao-AI-OS.git
cd LiuHao-AI-OS

# 使用 docker-compose 启动完整栈
docker-compose -f docker-compose.yml -f docker-compose.infra.yml up -d

# 查看服务状态
docker-compose ps
```

Docker 部署包含以下服务：

| 服务 | 端口 | 说明 |
|------|------|------|
| liuhao-gateway | 8080 | API 网关 |
| liuhao-console | 5173 | 驾驶舱前端 |
| postgres | 5432 | PostgreSQL 数据库 |
| redis | 6379 | Redis 缓存 |
| ollama | 11434 | Ollama 推理服务 |

### 方式四：源码安装（开发者）

```bash
# 克隆仓库
git clone https://github.com/your-org/LiuHao-AI-OS.git
cd LiuHao-AI-OS

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# 或
.venv\Scripts\activate    # Windows

# 安装依赖
pip install -r requirements.txt

# 安装项目（开发模式）
pip install -e .

# 安装 Node.js 依赖（驾驶舱）
cd apps/console/console
npm install
cd ../../..
```

---

## 配置基础

### 环境变量

复制 `.env.example` 为 `.env` 并根据需要修改：

```bash
cp .env.example .env
```

### 核心配置项

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DATABASE_URL` | `sqlite:///./liuhao_ai_os.db` | 数据库连接 URL |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama 服务地址 |
| `DEFAULT_MODEL` | `qwen2.5:3b` | 默认使用的 LLM 模型 |
| `EMBEDDING_MODEL` | `nomic-embed-text` | 嵌入模型名称 |
| `MEMORY_BACKEND` | `sqlite` | 记忆存储后端 |
| `GATEWAY_HOST` | `127.0.0.1` | 网关监听地址 |
| `GATEWAY_PORT` | `8080` | 网关监听端口 |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `JWT_SECRET` | （自动生成） | JWT 签名密钥 |

### 配置文件

主配置通过 `src/config_manager.py` 管理，支持以下优先级（从高到低）：

1. 环境变量
2. `.env` 文件
3. 配置文件（`config/*.json`）
4. 代码默认值

---

## 首次运行验证

### 1. 检查健康状态

```bash
curl http://127.0.0.1:8080/v1/health
```

预期响应：

```json
{
  "status": "healthy",
  "version": "3.0.0",
  "timestamp": "2026-10-07T12:00:00Z"
}
```

### 2. 检查就绪状态

```bash
curl http://127.0.0.1:8080/v1/ready
```

预期响应：

```json
{
  "ready": true,
  "checks": {
    "database": "ok",
    "model_gateway": "ok",
    "memory_kernel": "ok",
    "identity_kernel": "ok"
  }
}
```

### 3. 访问 API 文档

打开浏览器访问 `http://127.0.0.1:8080/docs`，可以看到 Swagger UI 形式的完整 API 文档。

### 4. 访问驾驶舱

打开浏览器访问 `http://127.0.0.1:5173`，进入 LiuHao AI OS 驾驶舱控制台。

### 5. 运行冒烟测试

```bash
python scripts/smoke_mvp.py
```

预期输出：`9/9 tests passed`

---

## 常见问题排查

### 问题：Ollama 连接失败

**症状**：启动时报错 `Failed to connect to Ollama`

**解决方案**：

```bash
# 1. 确认 Ollama 正在运行
ollama serve

# 2. 确认模型已下载
ollama list

# 3. 如果端口不同，修改环境变量
# OLLAMA_BASE_URL=http://localhost:11434
```

### 问题：端口被占用

**症状**：启动时报错 `Address already in use`

**解决方案**：

```bash
# Windows 查看占用端口的进程
netstat -ano | findstr :8080

# 修改端口
# GATEWAY_PORT=9090
```

### 问题：数据库初始化失败

**症状**：报错 `Database connection failed`

**解决方案**：

1. 确认 SQLite 数据库文件所在目录可写
2. 如使用 PostgreSQL，确认服务已启动且连接参数正确
3. 检查 `DATABASE_URL` 格式是否正确

### 问题：依赖安装失败

**症状**：`pip install` 报错

**解决方案**：

```bash
# 升级 pip
pip install --upgrade pip

# 使用国内镜像源
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# Windows 上如遇 C 扩展编译问题，安装对应 wheel 版本
```

### 问题：内存不足

**症状**：模型加载时报错 `Out of Memory`

**解决方案**：

1. 使用更小的模型（如 `qwen2.5:1.5b` 代替 `qwen2.5:7b`）
2. 增加系统虚拟内存/交换空间
3. 降低并发请求数

### 问题：Docker 容器启动失败

**症状**：`docker-compose up` 后容器立即退出

**解决方案**：

```bash
# 查看容器日志
docker-compose logs liuhao-gateway

# 检查端口冲突
docker-compose ps

# 重新构建镜像
docker-compose build --no-cache
```

---

## 后续步骤

安装完成后，建议继续阅读：

- [快速上手指南](quickstart.md) - 创建你的第一个智能体
- [核心概念](../core-concepts/agents.md) - 了解 LiuHao AI OS 的核心概念
- [架构概览](../architecture/overview.md) - 深入了解系统架构
