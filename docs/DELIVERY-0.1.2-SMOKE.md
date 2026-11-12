# 鎏灏 AI OS 0.1.2 · 发布冒烟交付说明（定稿）

- 状态：**本版已发布；冒烟 4 条已完成；遗留 6 条进入 0.1.2.1 / 0.1.3**
- 冒烟对象：发布页产物 `LiuHao-Setup-0.1.2.exe`，29,428,128 B，SHA256 `6443A1AB03FEF2F9B0C6FA4F7825D041FF64C6D07793ECCB1503E84141FD796B`
- 安装位置：`D:\LiuHao`；数据目录：`D:\LiuHaoData`（复用，未清空、未改配置）
- 本轮约定：不改产品代码；所有界面结论均来自安装版客户端截图，不用接口回放替代

---

## 一、本轮通过项

| # | 项目 | 证据 |
|---|---|---|
| 1 | 发布页产物可下载、可安装、可启动 | 下载件哈希与发布页公示逐字节一致；安装 exit=0；应用窗口正常拉起（`LiuHao AI OS · 鎏灏智能中枢`） |
| 2 | 复用已有数据目录，账号未被新建 | `D:\LiuHao\data-dir.txt`=`D:\LiuHaoData`；`config\human_identities.json` 中账号仍为 `lhsmoke0110`；界面左下显示「鎏灏冒烟01」 |
| 3 | 模型配置 + 真实对话（接口侧） | 模型 `deepseek-smoke`（provider `deepseek` / model `deepseek-chat` / 端点 `https://api.deepseek.com/v1`，`is_default=true`，`api_key_status=ok`，Key 显示为 `sk-b15e****b47c`）。真实对话：客户端 **1604 ms**、网关 **1.4548 s**、助手内部 **1204.6 ms**；`trace_id=6071f386-4f7f-4b1f-b89c-7d206068f299`；回复前 40 字「我是鎏灏智能中枢（LiuHao AI OS）——由十源 DNA 统一形成的 AI」 |
| 4 | 界面可用（本轮新增的真界面证据） | ① 已登录入口 → ② 主界面 → ③ 工作台对话发问并收到回复 → ④ 503 的界面呈现，四组截图见第六节 |
| 5 | 卸载可执行、无残留冲突 | 两次卸载均 exit=0；快捷方式、开始菜单、卸载注册表项、`HKCU\Software\LiuHao` 均已清除；`D:\LiuHaoData` 完整保留（370 文件 / 78.9 MB） |

> 第 3 条与第 4 条的区别必须说清：第 3 条是**接口侧**的真实模型调用（有耗时与 trace），第 4 条是**界面上**真的发了一句话并看到回复（有截图）。两者不是同一份证据，也没有互相顶替。

## 二、如实说明

1. **`/v1/ready` 配好 Key 后仍然 503 —— 按判定口径是真 bug。** 阻断项不是模型，而是 `kernel_policy_enforcement`（`unarmed_production`）：安装脚本只写 `APP_ENV=production`，全套安装脚本没有任何地方设置 `LIUHAO_KERNEL_POLICY_ENFORCE`，所以安装版的 readiness 永远是 503。已进入 0.1.2.1。
2. **`/v1/ready` 的 503 响应没有 `reason` 字段。** 契约里只有 `status` / `checks[]` / `errors[]`，人话在 `user_message` 与 `errors[]` 中；且未鉴权探针会做脱敏，环境变量名被抹掉，第三方只看响应体拿不到「为什么」。已进入 0.1.2.1。
3. **首次设置向导没有恢复码步骤。** 向导 5 页（数据目录 / 管理员账号 / 默认模型 / 完成前确认 / 自检结果）全文没有「恢复码」；注册走 `register_human_identity`，不生成恢复码。结论：**不是「本版有意为之的告警」，是「新装用户没有恢复路径」**。
4. **新装用户默认没有可带走的恢复码。** 补设之前，「换机忘密」不成立。补设路径（实物界面，非推测）：**我的 → 恢复码 → 轮换**；轮换后旧码立即失效，明文只显示一次，可下载 `liuhao-recovery-<account>.json`。同机自救可用（`recovery.self_service=true`），跨机必须先补设。
5. **本机 `dist\freeze-0.1.2\installer\LiuHao-Setup-0.1.2.exe` 不是发布产物。** 36,948,984 B，SHA256 `E92D364F53B4A4109D848D4EA2FB15B016DA427FF97E9FAB2F259B63577798ED`，签名有效（`CN=鎏灏智能中枢, O=LiuHao AI OS, C=CN`，指纹 `AE398F094E7662E3B55A49F3BD728D4F742E5A16`），ProductVersion 0.1.2。差异来源：该轮出包**没有跑 prune**（payload 缺 `prune-import-ledger.jsonl`），`runtime\Lib\site-packages` 多出 pythonwin 8.88 MB、PyWin32.chm 2.52 MB、win32comext 2.31 MB、zstandard 1.31 MB、isapi/adodbapi 0.27 MB，lxml 大 2.72 MB，压缩后正好多约 7.5 MB。**本文件第六节所有截图均来自发布页 6443A1AB 那份安装**，不是这份。
6. **API Key 全程只在界面录入。** 由应用自身加密落盘（AES-256-GCM，`key_source=file` / `key_protection=dpapi-current-user`），接口只回掩码；日志/配置/临时文件/命令行/提交信息中均无明文（`sk-[A-Za-z0-9]{20,}` 命中 0，`model_registry.sqlite3` 二进制搜 `sk-` 命中 0）。本说明一律写作 `sk-b15e****b47c`。

## 三、卸载与残留（分三句，不合并）

1. **保留许可文件是有意的**：`D:\LiuHao\licenses\`（19 个许可文本）+ `LICENSE` + `THIRD-PARTY-NOTICES.txt` + `sbom-cyclonedx.json` 在卸载后留在安装目录，是为履行 LGPL 源码提供与许可随包义务，不是漏删。
2. **`D:\LiuHao\run\instance.lock` 是脏残留**：它不属于上面那组文件，是运行期锁文件没有被卸载器回收，属待修项，已进入 0.1.2.1。
3. **`%LOCALAPPDATA%\LiuHao` 的 181 个文件是历史版本留的、不是本次安装造成**：本次安装前后文件数都是 181，数量未变；其中 5 个（`data\logs\liuhao.log`、`data\logs\error.log`、`data\logs\retention.json`、`data\audit_store.db-shm`、`data\temp-sweep.stamp.json`）在今天 06:42–06:44 被**另一条线的实例**写入（其日志里有 `POST /v1/accounts/recovery/reset`、`POST /v1/auth/login`），与本次安装使用的 `D:\LiuHaoData` 无关。

卸载结果：装错产物那次 exit=0；装发布产物这次 exit=0。卸载后 `D:\LiuHao` 残留 = 23 个文件 + `licenses`、`run` 两个目录。`D:\LiuHaoData`（370 文件 / 78.9 MB）与 `D:\LiuHaoBackups` 均保留。

## 四、遗留 6 条（已知 vs 待修）

| 现象 | 影响 | 归属 |
|---|---|---|
| 安装版 `/v1/ready` 恒 503（策略未 arm） | 探针永远降级；界面上只是一个「1 项依赖异常」计数 | 0.1.2.1 |
| 503 响应缺 `reason` 字段 | 第三方探针拿不到人话原因 | 0.1.2.1 |
| 首次设置向导无恢复码步骤 | 新装用户无可带走的恢复码，「换机忘密」不成立 | 0.1.2.1 |
| 卸载残留 `run\instance.lock` | 卸载后目录不干净 | 0.1.2.1 |
| 出包未断言 `prune-import-ledger.jsonl` | 可签出未裁剪产物（本轮 36.9 MB 事件），且无人报警 | 已修（脚本硬闸，见文末） |
| `dist\` 下同名 Setup 多份且哈希不同 | 无法判断哪份才是要发的 | 已修（同名不同哈希即失败，见文末） |

已发布这一版不因上述 6 条回退；本轮不再触碰产品代码，全部在 0.1.2.1 / 0.1.3 处理。

## 五、产物身份对照

| 产物 | 大小 | SHA256 | 用途 |
|---|---|---|---|
| 发布页 `LiuHao-Setup-0.1.2.exe` | 29,428,128 B | `6443A1AB03FEF2F9B0C6FA4F7825D041FF64C6D07793ECCB1503E84141FD796B` | **本轮截图与卸载均用此份** |
| 本机 `dist\freeze-0.1.2\installer\LiuHao-Setup-0.1.2.exe` | 36,948,984 B | `E92D364F53B4A4109D848D4EA2FB15B016DA427FF97E9FAB2F259B63577798ED` | 未 prune 的同名副本，保留待 0.1.2.1 复用，不对外 |

## 六、界面证据（发布页产物安装，`D:\LiuHao`）

截图目录：仓库内 `docs/evidence-0.1.2-smoke/`（本机原始目录 `ui-evidence/smoke-0.1.2-published/`，该目录被 `.gitignore` 忽略，故按 `docs/dpi-evidence/` 的既有约定入库）

1. **已登录入口** — `pub-01-loggedin.png`：「我的」页，左下账号「鎏灏冒烟01 / 我的工作空间」，行内有「账号安全」「恢复码」「系统设置」入口。（复用数据目录后直接进入已登录态，登录页不再出现；这是「已登录的注册/登录入口」，不是拿接口冒充。）
2. **主界面** — `pub-02-overview.png`：「总览」，AI 员工总数 2、审计事件 91、系统健康度 94%、顶部小字「13/14 项依赖正常 · 1 项依赖异常」。
3. **对话（含问句与回复）** — `pub-03-typed.png` 与 `pub-03-chat-reply.png`：工作台「直接对话」，默认模型显示 `deepseek-smoke`；
   - 我发出的问句：`用一句话说明你现在能帮我做什么`
   - 助手回复：`我能帮你查系统和记忆、看审计日志、做纯计算、读写工作区文件、记录偏好与提醒，并在不确定时直说做不到。`
   - 后端对应记录：`POST /v1/chat/stream 200 0.2955s trace_id=0b5b508d-b158-47d7-b189-4b4e91893215`（06:42:04）
4. **503 的界面呈现** — `pub-04-status.png` 与 `pub-04-notification.png`：
   - **不是全局红条，也不是完全不显示，更不是「有轻微提示」能带过的。** 总览右上只有一个「1 项依赖异常」小计数；进「系统状态」页才看到「依赖健康」列表里唯一红项：`策略执行 — 策略强制部分可用，请到诊断页查看技术细节`，右侧红色徽标「降级」；其余 13 项「正常」。
   - 右上铃铛显示「1 条」，点开是「依赖不健康（1 项）→ 策略执行：策略强制部分可用，请到诊断页查看技术细节」。
   - 原样表述：界面上只有这一句红字 + 一个红色「降级」徽标，外加总览「1 项依赖异常」和铃铛「1 条」——**用户能看出有东西不对，但没有任何一句告诉他该怎么处理，也没有告诉他这不影响使用。**「请到诊断页查看技术细节」对普通用户就是死胡同。
   - 0.1.2.1 要把这段文案改成回答三个问题：**能做什么 / 要不要处理 / 怎么处理**。这是第 ① 条 bug 的严重度落点。

## 七、`D:\cache\temp` 可再生中间目录清单（只列，不删）

统计口径：排除 `verify-*`、`pytest-base-*`、`package-*`；候选目录 **36,779** 个。

| 前缀 / 目录 | 数量或体积 |
|---|---|
| `tmp*` | 9,834 个 |
| `liuhao_hypothesis_*` | 4,575 个 |
| `liuhao-upgrade-download-*` | 2,023 个 |
| `p08_HC-*` | 1,970 个 |
| `liuhao-retention-cold-*` | 816 个 |
| `lh_ws_art_*` / `lh_aud_art_*` | 700 / 700 个 |
| `f1_pollution_*` / `sec_registry_*` | 169 / 150 个 |
| `liuhao-smoke-*` / `liuhao_pool_probe_*` | 37 / 8 个（各约 3.1 MB / 0.2 MB） |
| `pytest-of-Administrator` | 4,221.5 MB |
| `release-0.1.2-3e816d9c` | 311.8 MB |
| `liuhao-release-site` | 231.4 MB |
| `liuhao-head-src` | 125.5 MB |
| `release-page` / `evidence-run11-artifacts` | 28.1 MB / 28.1 MB |
| `is-94FYA2SMCG-uninstall.tmp` | 4.2 MB |
| `liuhao-e2e-BASELINE-HEAD-*` / `liuhao-e2e-CURRENT-*` | 4.9 MB / 4.5 MB |

两棵出包副本（`dist\freeze-0.1.2` 与 worktree 快照 `D:\LiuHaoBackups\worktree-snapshot-20261108-054720`）按要求**不删**。

---

附：本轮未改任何 `src/` 产品代码。`scripts/release/` 的出包硬闸已落地（仅改这两支脚本，已用演练跑过通过/拒绝两条路径）：

1. `package-frozen.ps1` 断言 payload 里必须有非空 `prune-import-ledger.jsonl`，缺失即 FAIL；并额外断言**解包后的成品树**里也有该台账——签走的是 exe，只查 payload 目录挡不住「签了另一棵树」。
2. `FREEZE-RECORD.json` 新增 `payload` 段（`file_count` / `bytes` / `size_mib` / `sha256` / `prune_ledger`）；`verify_publish_source.py` 在 `--require-record` 下缺这段即拒发，给了 `--payload-dir` 就按同一算法逐项复核实物树（两侧算法已对同一棵树互验：10232 文件 / 150,134,123 字节 / `47EF61B6…9247`）。
3. `package-frozen.ps1` 收尾扫 `dist\` 下所有非归档 `LiuHao-Setup-*.exe`：**同名不同哈希即 FAIL**，并列出每份的路径/字节/哈希，提示移到 `dist\_archive\` 或改名；`_archive` 不参与计数（与 `verify_publish_source.py` 的归档口径一致）。

演练结果：记录与 payload 一致 → `RESULT: PASS`；篡改记录字节数 → `RESULT: FAIL（拒绝发布）`；payload 缺台账 → `RESULT: FAIL（拒绝发布）`。
