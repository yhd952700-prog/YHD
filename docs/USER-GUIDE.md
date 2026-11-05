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
- 本次构建的安装包：**未重跑整机冒烟**（构建使用了 `-SkipSmoke`）。
  上一轮旧包（34,199,800 字节）曾完成「静默安装 25.3 秒 → 8.4 秒页面可用 →
  发消息 2.4 秒收到回复」；本次包为 43,746,172 字节，需在最终发布前补验。
- 卸载器的「保留数据 / 二次确认删除」实机行为：交互式卸载**未验证**；静默卸载
  （`/VERYSILENT`）实测会停在保留数据的确认框，需人工点选。
- 真实厂商 Key：本流未复验；本流用本地 OpenAI 兼容桩验证了真实 HTTP 请求路径
  （发消息 2.4 秒收到回复）。网关流报称已用真实 DeepSeek Key 做过同链路演练，
  但本流没有其原始输出。
- 升级：发布清单的生成与读取**已验证**（`up_to_date` / `update_available` /
  `invalid_manifest` 三种结果都实测过）；「下载 → 校验 → 备份 → 应用 → 回滚」
  未在安装版上实跑。
- 代码签名：**未实现**，安装包与升级包都会提示「未知发布者」。
