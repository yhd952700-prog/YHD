# 5 分钟跑起来

面向「我现在就想把贾维斯用起来」的人。Windows 口径，Git Bash / PowerShell 均可。

## 最短路径

**双击**仓库根目录的 `start-liuhao.bat` —— 完事。

**第一次启动会先弹一个黑窗口**（命令行向导）问管理员账号和登录密码；模型可以选「跳过」。
答完它才会继续启动；第二次起不再问。

命令行等价：

```bash
cd D:\Jarvis-AI-OS
.venv\Scripts\python.exe scripts\start_liuhao.py
```

浏览器会自动打开 `http://127.0.0.1:8080`。完成。

> 默认是**单端口模式**：网关直接服务已构建的 `apps/console/console/dist`。若仓库里还没有 `dist`，
> 启动器会报 `[FAIL]` 并提示用 `--vite` 跑开发双进程（那时前端在 `5173`）。

## 分步（开发双进程模式，仅前端调试需要）

```bash
# 1. 网关
cd D:\Jarvis-AI-OS
.venv\Scripts\python.exe -m uvicorn src.gateway.main:app --host 127.0.0.1 --port 8080

# 2. 控制台前端（新开一个终端）
cd D:\Jarvis-AI-OS\apps\console\console
node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5173
```

## 确认它真的活着

```bash
curl --noproxy '*' http://127.0.0.1:8080/v1/health
```

期望：`{"status":"ok","timestamp":...}`

> `curl` 后的 `--noproxy '*'` **不能省**。本机 env 里有代理，不加会把 localhost
> 也送去代理，报 `upstream connect failed`——那不是服务挂了。

## 试一次真实对话

```bash
curl --noproxy '*' -X POST http://127.0.0.1:8080/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"你好，用一句话介绍你自己","session_id":"smoke"}'
```

期望（约 5 秒，走本机 Ollama `qwen2.5:3b` 真实推理）：

```json
{"reply":"我是贾维斯，一个由十源DNA统一形成的AI操作系统人格…","status":"completed","turn":1,…}
```

## 跑一次端到端冒烟

```bash
.venv\Scripts\python.exe scripts\smoke_mvp.py
```

## 停止

在启动器终端按 `Ctrl+C`。启动器会把 uvicorn 和 vite 子进程一起关掉，不留孤儿进程。

## 常见故障

| 现象 | 原因与处理 |
| --- | --- |
| `upstream connect failed` | curl 走了代理，加 `--noproxy '*'` |
| 启动器报「未找到 node」 | Node.js 没装或不在 PATH |
| 启动器报「没找到 vite/bin/vite.js」 | 前端依赖没装：在 `apps/console/console` 下 `npm install` |
| 网关起来了但对话很慢/失败 | Ollama 没起或没模型：`ollama pull qwen2.5:3b` |
| `docker compose up` 报 daemon | 本机 Docker daemon 没运行，**走上面的原生方式** |
| `/v1/policy/*` 返回 401 | 刻意设计，需要审批令牌：`scripts/issue_console_token.py --list` |
| 审批中心没有可签发的主体 | 未登记人类身份（C-7 正向白名单）：<br>`scripts/register_human_identity.py --principal <name>` |
