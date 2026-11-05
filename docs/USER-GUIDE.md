# 使用指南（Windows 安装版）

## 一、首次启动

双击桌面快捷方式（或 `%LOCALAPPDATA%\LiuHao\app\LiuHao.bat`）。

1. 出现一个命令行窗口，显示「鎏灏首次运行向导」。
2. **第 1/2 步：建管理员账号**（仅当机器上还没有可登录身份时出现）
   输入登录名（如 `xin.hongda`）和密码。密码只存 PBKDF2 哈希在
   `data\config\auth_secrets.json`，不存明文。
3. **第 2/2 步：选 AI Provider**
   - `1` Ollama 本地：先安装 Ollama 并 `ollama pull qwen2.5:7b` 之类；向导会登记
     模型名，默认地址 `http://127.0.0.1:11434`。
   - `2` OpenAI 兼容云：依次填 `base_url`（如 `https://api.openai.com/v1`）、
     模型名（如 `gpt-4o-mini`）、API Key。API Key 用 AES-256-GCM 加密后写入
     `data\data\model_registry.sqlite3`，前端与日志只看到脱敏值。
   - `0` 跳过：先不配模型，之后在控制台「模型管理」里补。
4. 向导完成后网关启动，浏览器自动打开 `http://127.0.0.1:8080`。

> 已经配置过的机器再次启动会直接跳过向导，十几秒进入控制台。

## 二、日常使用

- **启动**：双击快捷方式 → 命令行窗口保持开启（关闭窗口 = 停止服务）。
- **登录**：浏览器打开后输入向导里创建的管理员账号密码。
- **聊天**：控制台左侧会话 → 新建会话 → 输入消息。回复来自你在向导里选的
  Provider；未配置模型时聊天会明确报错，不会伪造回复。
- **模型管理**：控制台「模型 / Provider」页可新增、测试连接、切换默认模型；
  修改即时生效，无需重启。
- **停止**：关闭命令行窗口，或在窗口里 `Ctrl+C`。

端口 8080 被占用时，启动器会自动换一个端口并在窗口里打印实际地址；以窗口里
打印的地址为准。

## 三、数据在哪、怎么备份

全部用户数据在 `%LOCALAPPDATA%\LiuHao\data`：

| 文件/目录 | 内容 |
| --- | --- |
| `config\human_identities.json` | 账号与角色 |
| `config\auth_secrets.json` | 登录凭据哈希 |
| `config\app.env` | JWT/会话密钥（首次生成，重启复用） |
| `config\integrity.key` | 身份注册表完整性签名密钥 |
| `data\model_registry.sqlite3` | Provider 与加密后的 API Key |
| `data\model_config.key` | 模型配置解密密钥 |
| `conversation_store.db` | 对话历史 |
| `audit_store.db` | 审计证据链 |
| `workspace\` | 员工、目标、项目、文件 |

备份：退出应用后，整体复制 `LiuHao\data` 目录即可；恢复时覆盖回同一路径。
注意 `model_config.key` 与 `model_registry.sqlite3` 必须一起备份，缺一个则
API Key 无法解密。

## 四、卸载

设置 → 应用 → 鎏灏 LiuHao AI OS → 卸载。

- 默认**只删程序**，`data` 目录完整保留，重装后账号/会话/模型配置仍在。
- 需要彻底清理时，卸载器会先问一次「是否同时删除你的数据」（默认否），
  选择「是」后再问一次，两次确认才删除，无法恢复。

## 五、升级

两种方式：

1. **安装包覆盖**：直接运行新版本安装包，安装到同一目录即可覆盖。数据目录不受影响。
2. **应用内检查更新**：发布目录里同时放 `LIUHAO_RELEASE.json` 与 `liuhao-<版本>.zip`
   后，用安装目录里的运行时检查：

```powershell
cd $env:LOCALAPPDATA\LiuHao\app
runtime\python.exe -m src.upgrade check --app-root . --release-dir D:\releases
# 也可以直接指向清单文件
runtime\python.exe -m src.upgrade check --app-root . --manifest D:\releases\LIUHAO_RELEASE.json
```

结果只有三种：「发现新版本 X」「已是最新版本 X」「无法检查更新（原因）」。没有更新源时
会明确报「未配置更新源」，不会假装「已是最新」。升级前系统会自动备份程序与数据快照，
失败自动回滚。

建议升级前先复制一份 `data` 目录。

## 六、常见问题

**Windows 提示「未知发布者」**
当前安装包未做代码签名，属预期。点「更多信息 → 仍要运行」。只从可信来源获取安装包。

**启动窗口一闪而过**
以命令行方式运行 `%LOCALAPPDATA%\LiuHao\app\LiuHao.bat` 查看完整报错；常见原因是
杀毒软件拦截了 `runtime\python.exe`，把安装目录加入白名单后重试。

**登录页进不去 / 忘记密码**
用安装目录里的运行时重设：

```powershell
cd $env:LOCALAPPDATA\LiuHao\app
runtime\python.exe scripts\register_human_identity.py --principal <你的登录名> --password-stdin --file $env:LOCALAPPDATA\LiuHao\data\config\human_identities.json --secrets-file $env:LOCALAPPDATA\LiuHao\data\config\auth_secrets.json
```

（运行前需设置 `LIUHAO_DATA_DIR=%LOCALAPPDATA%\LiuHao\data`；更简单的办法是
在应用里用管理员账号处理。）

**回复是 `Mock response to: ...`（不是报错）**
说明当前没有可用的真实模型，系统退回了演示模式。回控制台「模型管理」添加真实
模型并「设为默认」，或重跑向导（先确认身份与模型都已存在）。Ollama 场景先确认
`ollama list` 能看到模型、且 Ollama 服务在跑。

**端口冲突**
关闭占用 8080 的程序，或直接使用启动窗口打印的自动顺延端口。

**占用空间**
程序约 197.3 MB（未压缩，安装包约 41.7 MB）+ 数据目录（对话/审计随使用增长）。
不含任何模型；Ollama 模型单独占约 2GB 以上。

## 七、本轮验证与仍未验证的部分

- 安装后的开机自启（托盘）：**已验证**。注册表值指向安装目录内的
  `runtime\pythonw.exe` 与 `scripts\liuhao_desktop.py`，托盘窗口能创建；
  文件缺失时会拒绝写入并给出原因，不会写一个注定失败的登记项。
- 本次构建的安装包（43,756,495 字节，SHA256 `D8FD0BC9…4461`）：**已实跑安装冒烟**
  ——静默安装到临时目录、启动服务、取得控制台首页、注册并登录、接入本机
  Ollama，`POST /v1/chat` 拿到真实回复，再静默卸载；未点击渲染后的前端表单
  （登录/发消息走的是同一套 HTTP API，前端视觉冻结后再补一次 UI 点击回归）。
- 卸载器：静默卸载已验证——程序目录、桌面/开始菜单快捷方式、卸载注册表项移除，
  `%LOCALAPPDATA%\LiuHao\data` 原样保留；交互式「是否删除数据」的“删除”分支未验证。
- 真实模型回复：本轮冒烟用本机 Ollama `qwen2.5:3b` 实测——模型连通性探测通过
  （约 1.36 秒），`POST /v1/chat` 11.2 秒返回真实生成文本；云厂商 Key 未在本轮复验。
- 升级：发布清单的生成与读取**已验证**（`up_to_date` / `update_available` /
  `invalid_manifest` 三种结果都实测过）；「下载 → 校验 → 备份 → 应用 → 回滚」
  未在安装版上实跑。
- 代码签名：**未实现**，安装包与升级包都会提示「未知发布者」。
