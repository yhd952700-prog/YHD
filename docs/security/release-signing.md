# 发布包签名（Ed25519）维护者手册

> 适用对象：**负责发布新版本的维护者**，以及要给客户端配置更新源的人。
> 相关代码：`scripts/ops/sign_release_manifest.py`、`src/upgrade/sources.py`、
> `src/upgrade/downloader.py`、`src/upgrade/manager.py`。
> 状态口径：工具与校验链路**已实现并有用例**；真实公网 CDN / 证书链仍属
> 「已改未端到端验证」（见 `docs/known-gaps.md` §4）。

---

## 1. 为什么远端升级必须签名

远端更新源是**供应链边界**。攻击者只要能改写 CDN 上的一个文件，就能让所有
客户端安装他构建的程序；而用户点「检查更新」时，期望的正是"信任的维护者发布
了新版本"。

只做 SHA256 不够：清单和包都放在同一个远端，攻击者可以**同时替换包和清单里的
`sha256`**，哈希校验照样通过。因此远端链路要求四件事同时成立：

| 防线 | 作用 | 代码位置 |
| --- | --- | --- |
| 强制 HTTPS | 防传输途中被替换 | `validate_remote_url()` |
| 拒绝私网/回环、禁跟随 30x | 防 SSRF 与"公网地址跳到内网/另一台主机" | `validate_remote_url()` / `_NoRedirectHandler` |
| 清单 SHA256 固定 | 防远端把清单本身换掉 | `HttpManifestSource(manifest_sha256=...)` |
| 发布包 Ed25519 签名 + 可信公钥 | 防远端把包换成另一个"哈希也自洽"的包 | `download_release()` / `verify_signature()` |

所以：**远端（HTTP/HTTPS）更新源在没有签名或没有配置可信公钥时会被直接拒绝下载**，
不是警告、不是降级，而是 `SignatureError`：

- 清单没有 `signature` → 「远端发布包必须带 Ed25519 签名…已拒绝下载。」
- 客户端没配公钥 → 「远端发布包必须配置可信 Ed25519 公钥…已拒绝下载。」
- 签名不匹配（包被换/密钥不对）→ 「签名验证失败：发布包与签名不匹配，可能被篡改」

本地目录源（`LIUHAO_UPGRADE_RELEASE_DIR` / `--release-dir`）是**本机维护路径**，
不强制签名：它读的是你本机磁盘上的文件，不存在"远端被替换"的攻击面。未签名时
界面/CLI 会明确显示「仅哈希校验，无签名」，绝不冒充已验证。

---

## 2. 生成密钥对（只做一次）

```powershell
python scripts/ops/sign_release_manifest.py keygen
```

默认写到（仓库之外）：

- Windows：`%LOCALAPPDATA%\LiuHao\upgrade-signing\`
- 其它系统：`~/.config/liuhao/upgrade-signing/`

产物：

| 文件 | 内容 | 去向 |
| --- | --- | --- |
| `release-signing.ed25519` | 私钥（机器绑定封装） | **只留在发布者本机**，不进仓库、不进网盘、不进 CI 日志 |
| `release-signing.pub.pem` | 公钥 PEM | 可分发 / 配置到客户端 |
| `release-signing.pub.b64` | 公钥 raw base64 | 适合放进环境变量 `LIUHAO_UPGRADE_PUBLIC_KEY` |

工具会打印**公钥指纹（SHA256 hex）**。请把它记进发布台账（例如
`docs/operations/release-signing-ledger.md` 或你团队的密钥台账），轮换时靠它确认
"客户端现在信任的到底是哪把公钥"。

可选参数：

- `--key-dir <目录>`：换位置（仍会被工具拒绝写进仓库目录）；
- `--force`：覆盖已存在的私钥。**这会让所有旧发布包无法再用旧钥匙签名**，
  除非你已经在做公钥轮换，否则不要用。

### 2.1 私钥是怎么保护的

私钥文件不是裸 PEM，而是项目既有的 machine-bound 形态：

- Windows + DPAPI 可用 → `LIUHAO-DPAPI-1:<base64(DPAPI 密文)>`，**绑定到当前
  Windows 用户**；换机器/换用户解不开（`cross-machine`）。
- 非 Windows（开发/CI）→ 显式 `LIUHAO-RAW-1:<base64(原文)>`，只是格式标记，
  **不是加密**；这种环境不要用来签正式发布包。
- Windows 上 DPAPI 不可用时工具直接报错，**不会退化成裸文件**。

工具只用独立 `purpose`（`liuhao-upgrade-release-signing-v1`）做 DPAPI entropy，
同一台机器上的其它密钥解不开这把私钥。

**事故处理**：私钥文件泄露 = 立刻停止用这把钥匙签包 → 走 §5 的公钥轮换 →
删除泄露私钥（本机 `--force` 重新生成或直接删文件）→ 检查已发布包的签名记录。

---

## 3. 给发布清单签名

前提：发布目录里已经有 ZIP，`LIUHAO_RELEASE.json` 里的 `sha256` / `size` 与实际
文件一致。

```powershell
python scripts/ops/sign_release_manifest.py sign `
  --manifest dist\release\LIUHAO_RELEASE.json `
  --artifact-dir dist\release
```

工具会：

1. 按 `releases[]` 逐项定位 `artifact`（相对路径默认相对清单目录，也可用
   `--artifact-dir` 指定）；
2. 重算 SHA256 与 size，**与清单不一致就拒绝签名**（绝不会把另一份文件签成
   这份清单）；
3. 用私钥对**整包字节**签名，写入 `signature`（base64）与
   `signature_algorithm: "ed25519"`；
4. 给**清单自身**写 `manifest_signature`（覆盖除签名字段外的整份清单）与
   `sequence`（防重放序号，默认在已有值上 +1，也可用 `--sequence` 指定）；
5. 原子写回清单（不会留下半截 JSON）。

只签某个版本：`--version 0.2.0`（可重复）。签名完成后重新发布清单即可，**ZIP
本身不要再改动**（改了哈希就变，之前的签名也不再对应）。

> 包签名对象是发布包字节；清单签名对象是清单的规范化内容（键排序、紧凑 JSON、
> UTF-8，剔除 `manifest_signature` / `manifest_signature_algorithm`）。因此
> 清单里的 `notes`、`generated_at` 改动会让**清单签名**失效，必须重新签名；
> 而 `sha256` / `artifact` / 包内容任何一项改变都会让下载被拒。

`sequence` 规则：客户端持久化"已接受的最大序号"。**每次发布必须递增**；同序号
只允许内容完全一致的重复检查（幂等）。没有私钥就无法伪造更大的序号，旧清单
重放会被拒绝。

---

## 3.1 发布物命名与替换约定（不可复用文件名）

缓存窗口的故障不是"签名坏了"，而是"同一个文件名被覆盖成了不同字节"。约定：

1. **文件名一经发布，永不覆盖。** 新构建一律换名（`liuhao-<version>-build<N>.zip`
   或 `liuhao-<version>-<内容短哈希>.zip`），并同步更新清单 `artifact`。
2. **清单与产物成对替换。** 发布 = 新 zip + 新清单 + 新 `sequence` 的一次原子动作；
   只换其中一个就是事故。
3. **旧文件保留。** 不要删除旧 zip：仍被 CDN 缓存着旧清单的客户端会按旧 `artifact`
   下载，保留旧文件至少能让它们拿到与旧清单匹配的字节；删掉则直接 404。
4. `sequence` 只防"旧清单被重放"；它**不能**防"旧清单里的 artifact 文件名被新字节
   覆盖"——后者只能靠第 1 条。

为什么重要：GitHub Pages 等静态托管默认 `Cache-Control: max-age=600`，不同边缘
节点的刷新时间不同。在这段窗口里，客户端可能拿到旧清单 + 新 zip：下载校验会以
`发布包大小不符：清单 X 字节，实际 Y 字节（包被追加了内容或清单与包不匹配）`
（大小相同但字节不同则报 `SHA256 校验失败`）失败。已装同版本（`up_to_date`）的
客户端不会下载；**还在旧版本、看到"有更新"的客户端会撞上**。实测记录见
`docs/self-upgrade.md` 2.2.1 / 7.1。

---

## 4. 客户端怎么配置公钥

两种等价写法（代码里 `_decode_public_key` 同时接受 PEM 与 raw base64）：

```powershell
# 1) CLI：--public-key 可以直接给 PEM 文件路径，也可以给 PEM/base64 字符串
python -m src.upgrade check --manifest-url https://example.com/LIUHAO_RELEASE.json `
  --public-key C:\keys\release-signing.pub.pem

# 2) 环境变量（应用内每周检查 / API 使用；推荐用 base64，单行、无换行）
set LIUHAO_UPGRADE_PUBLIC_KEY=<release-signing.pub.b64 的内容>
```

远端源只需要再加一行清单地址：

```powershell
set LIUHAO_UPGRADE_MANIFEST_URL=https://example.com/LIUHAO_RELEASE.json
```

- **不再需要每次发布配置新的清单哈希。** 信任依据是长期不变（可轮换）的公钥：
  清单自带 `manifest_signature`，客户端用公钥验签后才接受，因此同一份配置可以
  跨版本一直用下去——这才是"自动升级"。
- `LIUHAO_UPGRADE_MANIFEST_SHA256` 仍保留，但退化为**可选首次引导**：配置了就
  额外校验清单哈希（适合离线安装包/受控环境把"第一次信任"钉死），不配置则完全
  依赖清单签名。它每次发布都会变，**不应**作为长期方案。
- **不要把 `LIUHAO_UPGRADE_MANIFEST_SHA256` 长期钉在旧值。** 它校验的是清单
  文件整字节哈希，每次重签（改 `sequence`/包哈希/`notes`）都会变。配了旧值时的
  真实现象是 `invalid_manifest`，文案「无法检查更新：发布清单无效（发布清单
  SHA256 与配置的固定值不一致（清单可能被篡改））」，且不会写水位线、不会下载。
  排障步骤见 `docs/self-upgrade.md` 2.2.10。
- 远端既没有固定哈希、清单又没签名 → `SignatureError`，检查被拒；不会静默放行。
- 公钥配错/格式坏 → `SignatureError`，不会静默降级。
- 客户端会在 `upgrade-state/manifest-trust.json` 记录已接受的最大 `sequence`、
  清单摘要与最高可信版本；该文件损坏/不可读时**拒绝检查**（防止丢了水位线被
  旧清单重放），删除它等于重新引导，需确认没有回滚需求。
- 本地目录源：`LIUHAO_UPGRADE_RELEASE_DIR=<目录>`；公钥可选，签名可选，
  但状态里会如实写「仅哈希校验，无签名」。本地源带签名且配置了公钥时同样真验签，
  坏签名会被拒绝。

---

## 5. 公钥轮换（换钥匙）

客户端**只信任一把公钥**（当前实现是单个 `public_key` 字符串，没有"同时配两把"
的开关）。因此轮换必须按"先让客户端信任新钥匙，再用新钥匙签包"的顺序：

1. **生成新密钥对**（可放到新目录，例如 `--key-dir %LOCALAPPDATA%\LiuHao\upgrade-signing-v2`），
   记录新公钥指纹；
2. **用旧私钥**发布一版"只更新信任锚"的客户端（配置里带上**新公钥**；这一版的
   包仍然由旧私钥签名，旧客户端能验证）；
3. 确认目标用户已完成这次升级（版本门控/发布说明写清最低版本）；
4. 之后所有新包改用**新私钥**签名；
5. 旧私钥退役：删除本机旧私钥文件、在台账里标记 revoked；如仍需向老客户端
   分发旧包，保留旧公钥与旧私钥直到最后一台客户端迁移完成。

注意事项：

- **不要在用户还没升级到"信任新公钥"的版本时就用新私钥签包**——老客户端会
  报「签名验证失败」，升级链直接断掉。
- 轮换后新私钥签的第一份清单，`sequence` 必须**大于客户端已记录的水位线**；
  签发工具对同一份清单文件默认自动 +1，跨文件/跨机器轮换时请显式用
  `--sequence <更大的值>`，否则客户端会以"sequence 回退/疑似重放"拒绝。
- 公钥指纹台账是唯一能事后回答"这个包当年是谁签的"的东西；请随发布记录保存。

---

## 6. 常见失败与排查

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `私钥已存在…` | 目录里已有私钥 | 确认不是要轮换后，加 `--force`；否则换 `--key-dir` |
| `拒绝把签名私钥写进仓库目录` | `--key-dir` 指到了仓库内 | 用默认目录或仓库外路径 |
| `这把私钥绑定在另一台机器…` | DPAPI 解不开（换机/换用户） | 回原机器导出公钥；确需换机走轮换流程 |
| `SHA256 与清单不一致，拒绝签名` | ZIP 构建后又被改动，或清单是旧哈希 | 重新构建并更新清单，再签 |
| `签名验证失败：发布包与签名不匹配` | 包被替换 / 公钥不是签名用的那把 | 核对公钥指纹；重新用正确私钥签名 |
| `发布包大小不符：清单 X 字节，实际 Y 字节` / `SHA256 校验失败：清单 …，实际 …` | 清单与 zip 不是同一构建：同名文件被覆盖，或 CDN 缓存窗口内旧清单配新 zip | 换唯一文件名、重签清单（`sequence` 递增），清单与产物成对发布；见 §3.1 |
| `发布清单 SHA256 与配置的固定值不一致（清单可能被篡改）` | `LIUHAO_UPGRADE_MANIFEST_SHA256` 仍钉着旧清单的哈希 | 清掉该变量改为依赖清单签名，或更新成当前清单的整文件哈希；见 `self-upgrade.md` 2.2.10 |
| `清单签名验证失败：清单内容与签名不匹配` | 签完之后又改了清单（`notes`/版本/包哈希） | 用同一把私钥重新 `sign`（会递增 sequence） |
| `清单 sequence 回退…疑似旧清单重放` | 重放了旧清单，或换钥后序号没接上 | 用当前私钥签更大的 `--sequence`；不要删除信任状态来"绕过" |
| `发布清单未通过可信校验…缺少 sequence` | 旧工具签的清单没有清单级签名/序号 | 用新版 `sign_release_manifest.py` 重新签名 |
| 远端检查直接 `SignatureError` | 清单无 `manifest_signature` 或客户端没配公钥 | 按 §3、§4 补齐 |
| `未配置更新源` | 一个源都没配 | 配置 `LIUHAO_UPGRADE_RELEASE_DIR` 或 `LIUHAO_UPGRADE_MANIFEST_URL` |

---

## 7. 诚实边界（不要把"签了"当成"绝对安全"）

- 签名证明**包由持有对应私钥的人发布**；它不证明构建机没被入侵、不证明源码
  没被投毒。构建环境隔离与源码审计是另外一层。
- 公钥是**信任锚**：谁能在客户端上改 `LIUHAO_UPGRADE_PUBLIC_KEY` 或改内置公钥，
  谁就能换掉整条信任链。配置面要按管理员权限保护。
- 本手册描述的链路已用本机真实密钥端到端跑通：签名 → 下载校验通过；篡改清单/
  签名/换公钥 → 拒绝；**不配置固定清单哈希**的签名清单可以自动检查并下载
  （`sequence` 防重放/防降级）。**真实公网 HTTPS + CDN + 证书链**仍未端到端验证，
  记录在 `docs/known-gaps.md` §4.3。
- 防重放水位线存在客户端本地状态目录，能挡住"旧签名清单/旧包"的网络重放，但
  挡不住能在客户端本机改 `upgrade-state` 与公钥配置的攻击者——那种权限已等价于
  管理员，属于另一层。签名的边界仍是"谁持有私钥、谁控制客户端信任锚"。
