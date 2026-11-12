# 开源技术雷达 · 第 7 路：合规、供应链与内容安全

- 调研对象：`D:\LiuHao-AI-OS`（鎏灏 LIUHAO AI OS）
- 调研日期：2026-11-06
- 调研范围：隐私与数据治理、SBOM / 可复现构建 / 依赖签名 / SLSA、许可证合规自动化、依赖漏洞与 CVE、模型输出内容安全、审计链第三方验证、无障碍（WCAG / VPAT）
- 方法：先读现有代码 → 建候选池 → 逐个打开仓库目录树与 LICENSE 文件 → 查 OSV/Advisory → 再判定
- 去重声明：本路与第 3 路 [security-compliance.md](./security-compliance.md)、第 8 路 [data-network.md](./data-network.md) 存在重叠。本文件**只做增量判定**，凡第 3 路已深评的候选（presidio、cyclonedx-python、osv-scanner、pip-audit、syft、scancode、ORT、in-toto、TUF、Rekor、merkle、keyring）本文件只给结论与差异点，不重复抄录。
- 边界遵守：本次只新增本文件；未改其他文件；未 git 提交；未跑测试；未启动服务；未引入任何第三方代码。

**状态图例**：🟢 已实现且可用 / 🟡 部分实现或有明确缺陷 / 🔴 缺失或名不副实

**事实与推断分离**：文中"未获取"表示本次未能打开或确认；带 `需实测` 的结论表示必须在本机验证后才可写入发布说明。CVE/GHSA 编号均来自本次 osv.dev 检索结果，发布前应复检。

---

## 0. 结论摘要（先给判断）

1. **审计链的真实缺口不是"没有 Merkle"，而是"没有外部运营方"**。`src/kernels/audit/` 已实现哈希链、Merkle 包含证明、RFC 6962 树与签名树头（STH）、RFC 3161 时间戳与 TSA 仲裁。`external_anchor.py` 甚至在 docstring 里自己承认：`ExternalLogTransport` 是抽象占位，唯一落地的 `LocalReferenceLog` 是"自证"日志。因此第三方验证的落地方案应是"补 transport + 打包离线验证器"，而不是重写密码学。
2. **SBOM + GPL/AGPL 门禁的最小方案约 1–2 人日可落地**，且对单机桌面产品零运行时成本：SBOM 只在构建期产出并随包发布，门禁只在 CI 跑。真正的成本在"策略定义"（哪些包属于工具、哪些属于分发依赖），不在工具。
3. **当前许可证门禁有一个结构性漏洞**：`scripts/verify_oss_licenses.py` 只遍历"已安装 + 已登记在 `oss-registry.yaml`"的组件，**未登记的新依赖不会报错**。所以"拦截 GPL/AGPL"目前靠人肉登记，不靠自动化。
4. **内容安全目前是"输入侧规则 + 正则脱敏"，没有输出侧审核**。`src/knowledge/pii.py` 存在死代码（`re.sub` 结果被丢弃）与过度脱敏（所有首字母大写词都被当人名）。在补本地分类器之前，先把这两处修掉，收益远大于引入模型。
5. **隐私合规的真正阻塞项不是缺条款，而是三处"名不副实"**：`src/cli/backup_commands.py` 是假数据（`bkp-*`、固定 42 MB、随机 sha256）；`src/compliance/gdpr.py` 纯内存且未接 API；`src/backup/backup_manager.py` 的 `encryption_enabled` 只是标志位。任何"支持导出/删除/加密备份"的对外表述都必须先修这三处。

---

## 1. 该域能力现状表（附文件路径）

### 1.1 能力矩阵

| # | 能力 | 已有实现（文件路径） | 状态 | 实测结论 / 缺口 |
|---|------|----------------------|------|------------------|
| 1 | 许可证门禁 | `scripts/verify_oss_licenses.py`、`oss-registry.yaml` | 🟡 | 基于 `importlib.metadata` 遍历**已安装**包，只校验**已登记**组件；未登记新依赖静默通过；不读锁文件、不扫源码树、不产出 SBOM |
| 2 | SBOM 生成 | 无 | 🔴 | 仓库内无 CycloneDX/SPDX 产物；CI 无 SBOM 步骤 |
| 3 | 依赖锁定 | `uv.lock`（约 1.18 MB）、`requirements.txt` | 🟡 | `requirements.txt` 由 `uv pip compile` 生成但**无 `--hash`**；CI 用 `pip install -e .`，**不消费 `uv.lock`** |
| 4 | 可复现构建 | `Dockerfile`、`.github/workflows/ci.yml` | 🔴 | 基础镜像未按 digest 固定；无 `SOURCE_DATE_EPOCH`；无 build provenance |
| 5 | 依赖签名 / 来源证明 | 无 | 🔴 | 无 cosign / sigstore / in-toto / SLSA 产物 |
| 6 | 漏洞扫描 | `ci.yml` 的 dependency-audit job（`pip_audit … \|\| true`）；镜像扫描 Trivy `--exit-code 0`（ci.yml:141） | 🟡 | **两条都非阻断**。注意：`pip-audit 2.10.1` 实测**没有** `--exit-code` 参数，CI 注释已写明；无 CVE 台账、无到期跟踪 |
| 6b | 阻断型静态检查（不是 CVE） | `ci.yml` 的 Architecture Gate（Semgrep `--error`，ci.yml:1222–1251） | 🟢 | 本仓库**唯一会阻断**的静态检查；且已建立"LGPL-2.1 工具只以独立进程调用、不进分发物"的**许可边界先例**，可直接复用到本路策略 |
| 7 | PII 检测与脱敏 | `src/knowledge/pii.py`、`src/knowledge/security.py` | 🟡 | 正则型；`_detect_email/_detect_phone/_detect_address/_detect_name` 的 `re.sub` 结果被丢弃（无效计算）；姓名正则把所有大写开头词当人名（过度脱敏）；**无中文手机号/身份证/银行卡/地址规则**；`filter_content` 的 `except Exception: return text` 是 fail-open |
| 8 | 输出侧内容安全 | `src/ai/governance.py`（11 类威胁的输入侧正则）、`src/security/input_sanitizer.py`（HTML/SQL/命令注入防护） | 🟡/🔴 | 只有输入侧；无 LLM 输出的毒性/越界/自伤/违法内容审核；无输出 PII 二次脱敏 |
| 9 | 日志脱敏 | `src/security/audit_logger.py`（`redact_secrets`，约 110–123 行） | 🟢 | 按 key 名（secret/key/token/password/passphrase）替换为 `[REDACTED]`，已实际调用 |
| 10 | 审计哈希链 | `src/kernels/audit/hash_chain.py`、`verification.py`、`chain_integrity.py`、`genesis.py`、`retention.py` | 🟢 | 链式哈希、创世、保留期、完整性校验均在 |
| 11 | Merkle 包含证明 | `src/kernels/audit/inclusion_proof.py` | 🟢 | 本地可生成/校验 |
| 12 | RFC 6962 树 + 签名树头 | `src/kernels/audit/external_anchor.py` | 🟡 | 数学（RFC 6962 root / inclusion proof / STH 验签）按模块 docstring 声明"real and tested"（本次未运行测试）；但 `ExternalLogTransport` 三个方法均 `NotImplementedError`，唯一实现 `LocalReferenceLog` 自标 `self_attested=True` |
| 13 | 透明日志锚定 | `src/kernels/audit/transparency_anchor.py` | 🟡 | 用独立键模拟"独立透明权威"的哈希链日志；**不是真实第三方运营日志**；文档已诚实标注 |
| 14 | RFC 3161 时间戳 | `src/kernels/audit/trusted_timestamp.py`、`src/security/evidence/rfc3161_provider.py` | 🟢/🟡 | 支持 TSA 仲裁/高可用（见 `ADR-tsa-quorum-ha.md`）；**未接入审计记录发布流程** |
| 15 | 独立验证脚本 | `scripts/independent_verification.py` | 🟢 | 子进程重跑证据脚本并记录 stdout sha256；**docstring 明确声明 `record_hash` 非防篡改**（"hash lives INSIDE the record"），措辞诚实 |
| 16 | 数据导出/备份（真实实现） | `src/backup/backup_manager.py` | 🟡 | 有元数据/SHA-256/校验/恢复/保留；`encryption_enabled` 仅标志位、**未真正加密**；文本按 UTF-8 读取后 gzip+JSON，会损坏 SQLite `.db`/`-wal`/二进制（第 8 路已详述） |
| 17 | 数据导出（CLI 暴露面） | `src/cli/backup_commands.py` | 🔴 | 类注释为 "In-memory backup manager"；`_seed_sample_data()` 造 3 条 `bkp-*` 假备份；`create_backup` 里 `size_bytes = 1024*1024*42`、sha256 随机 |
| 18 | GDPR / 数据主体请求 | `src/compliance/gdpr.py` | 🔴 | `_consent_records`/`_data_subject_requests`/`_processing_activities`/`_dpia_records` 全在内存；`src/api` 内无 privacy/consent/export/delete 路由 |
| 19 | SOC 2 控制 | `src/compliance/soc2.py` | 🔴 | 控制、测试、证据均为内存 dict，不落盘，属"演示级"，未实现 |
| 20 | 隐私声明 | 仅有 `docs/knowledge_base/24-security/gdpr-compliance-guide.md` | 🔴 | 仓库内 `**/PRIVACY*.md|txt` 检索结果为空；无随包/首启展示的隐私声明 |
| 21 | 静态数据加密 | `src/security/encryption.py`、`secret_store.py`、`vault_crypto.py` | 🟡 | secret 走 AES-256-GCM；**主数据库是明文 SQLite**，`sqlcipher`/`PRAGMA key` 在 `src` 内无匹配 |
| 22 | 无障碍 | `apps/console/console/src`（29 个文件、约 291 处 `aria-/role=/alt=/tabIndex/onKeyDown/label`）、`scripts/ops/audit_console_readability.py` | 🟡 | 有手工可读性脚本（对比度/字号/焦点/键盘），**无 axe/WCAG 门禁、无 VPAT/ACR** |
| 23 | 前端依赖锁定 | `apps/console/console/package.json`、`package-lock.json` | 🟢 | CI 用 `npm ci`；但无 `npm audit` 阻断、无前端 SBOM |

### 1.2 已经扎实、不要重复造轮子的部分

- `src/kernels/audit/` 的密码学原语（链、Merkle、RFC 6962、STH、双签、RFC 3161、root of trust、genesis、retention、verifier registry）**不要替换**。外部工具只能在"transport / 运营 / 打包"层补。
- `src/security/audit_logger.py` 的 key-based 脱敏**已可用**，不要为了"上一个脱敏库"而替换它；只在需要"值级脱敏"（如正文中的身份证号）时补 PII 检测。
- `scripts/independent_verification.py` 的自我诚实（承认哈希非防篡改）应作为**写作与验收范式**保留。
- `.github/workflows/ci.yml` 的 Architecture Gate 已经跑通"**第三方 copyleft 工具只作独立进程调用**"这条路（Semgrep 为 LGPL-2.1，注释明确禁止 fork/内置/进分发物）。本路对 `reuse-tool`（GPL-3）、`opentimestamps-client`（LGPL-3）的判定**直接沿用这一先例**，不需要重新发明规则。

### 1.3 真实缺口（按严重度）

| 严重度 | 缺口 | 证据 |
|--------|------|------|
| P0 | CLI 导出的备份是假数据 | `src/cli/backup_commands.py` 的 `_seed_sample_data`、固定 42 MB、随机 sha256 |
| P0 | 许可证门禁对"未登记新依赖"无覆盖 | `scripts/verify_oss_licenses.py` 以 `oss-registry.yaml` 为遍历源 |
| P0 | `pip-audit` 用 `\|\| true`、Trivy 用 `--exit-code 0`，CVE 不阻断 | `.github/workflows/ci.yml` 的 dependency-audit（约 177–189 行）/ image audit（141 行） |
| P1 | 无 SBOM、无签名、无 provenance、锁文件未被 CI 消费 | 同 #2/#3/#5 |
| P1 | PII 正则死代码 + 无中文规则 + fail-open | `src/knowledge/pii.py`、`src/knowledge/security.py` |
| P1 | 无输出侧内容审核 | 仅有 `src/ai/governance.py`（输入侧） |
| P1 | 外部审计验证只有抽象接口 | `src/kernels/audit/external_anchor.py` 的 `ExternalLogTransport` |
| P1 | GDPR/SOC2 内存态、未接 API（均未实现） | `src/compliance/*.py`、`src/api` 无相关路由 |
| P2 | 无隐私声明、无无障碍门禁/VPAT | 文件检索为空 |
| P2 | 明文 SQLite | `src` 内无 `PRAGMA key` / SQLCipher |

---

## 2. 候选清单（按"值得引入程度"排序）

### 2.1 排序总表

| 排名 | 仓库 | Star | 最近提交 | 语言 | 许可证（以 LICENSE 为准） | 维护 | 结论 |
|------|------|------|----------|------|--------------------------|------|------|
| A1 | `CycloneDX/cyclonedx-python` | 399 | 2026-11-04 | Python | Apache-2.0 | 活跃 | 建议引入（SBOM 生成） |
| A2 | `raimon49/pip-licenses` | 378 | 2026-11-05 | Python | MIT | 活跃 | 建议引入（许可证清单） |
| A3 | `google/osv-scanner` | 11,398 | 2026-11-05 | Go | Apache-2.0 | 活跃 | 建议引入（CVE/漏洞） |
| A4 | `messense/nh3` | 404 | 2026-10-27 | Rust + PyO3 | MIT | 活跃 | 建议引入（HTML 消毒，替 bleach） |
| A5 | `data-privacy-stack/presidio` | 11,741 | 2026-11-02 | Python | MIT | 活跃 | 建议引入（PII，含中文扩展点） |
| A6 | `dequelabs/axe-core` | 7,757 | 2026-11-04 | JavaScript | MPL-2.0 | 活跃 | 建议引入（无障碍门禁） |
| A7 | `interlynk-io/sbomqs` | 318 | 2026-11-03 | Go | Apache-2.0 | 活跃 | 建议引入（SBOM 质量门禁） |
| A8 | `sigstore/sigstore-python` | 338 | 2026-10-29 | Python | Apache-2.0 | 活跃 | 条件引入（签名真实发布物） |
| B1 | `CycloneDX/cyclonedx-node-npm` | 151 | 2026-11-03 | JavaScript | 未获取 | 活跃 | 条件引入（前端 SBOM） |
| B2 | `pypa/pip-audit` | 1,389 | 2026-10-30 | Python | Apache-2.0 | 活跃 | 已在用，改阻断 |
| B3 | `transparency-dev/tessera` | 255 | 2026-10-29 | Go | Apache-2.0 | 活跃 | 条件引入（外部日志运营） |
| B4 | `in-toto/witness` | 615 | 2026-10-26 | Go | Apache-2.0 | 活跃 | 观察（provenance 策略） |
| B5 | `slsa-framework/slsa-verifier` | 352 | 2026-08-07 | Go | Apache-2.0 | 中（默认分支异常） | 观察 |
| B6 | `jsx-eslint/eslint-plugin-jsx-a11y` | 3,633 | 2026-01-06 | JavaScript | MIT | 中 | 条件引入（静态 a11y） |
| B7 | `IBMa/equal-access` | 786 | 2026-10-30 | JS/Java | Apache-2.0 | 活跃 | 观察（WCAG 引擎备选） |
| B8 | `unitary/toxic-bert` | HF 229 likes | 未获取 | PyTorch | Apache-2.0（模型卡） | 低（研究模型） | 条件引入（英文） |
| B9 | `thu-coai/roberta-base-cold` | HF 44 likes | 未获取 | PyTorch | **未获取（模型卡未标）** | 低 | 条件引入（中文，需先定许可） |
| B10 | `sqlcipher/sqlcipher` | 7,323 | 2026-10-27 | C | BSD-3-Clause 风格（ZETETIC） | 活跃 | 条件引入（静态库加密） |
| B11 | `restic/restic` | 36,981 | 2026-11-01 | Go | BSD-2-Clause | 活跃 | 条件引入（加密备份，外置 CLI） |
| B12 | `aquasecurity/trivy` | 38,996 | 2026-11-04 | Go | Apache-2.0 | 活跃 | 已在用，改阻断 |
| B13 | `kubernetes-sigs/bom` | 476 | 2026-11-05 | Go | Apache-2.0 | 活跃 | 观察（SPDX 生成） |
| B14 | `microsoft/sbom-tool` | 2,091 | 2026-09-21 | C# | MIT | 活跃 | 不引入（Windows 二进制体积） |
| B15 | `pypa/twine` | 1,792 | 2026-10-14 | Python | Apache-2.0 | 活跃 | 条件引入（PyPI 证明） |
| B16 | `sigstore/cosign` | 6,411 | 2026-11-05 | Go | Apache-2.0 | 活跃 | 观察（容器/发布签名） |
| C1 | `fsfe/reuse-tool` | 595 | 2026-11-01 | Python | **GPL-3.0-or-later** | 活跃 | 仅 CLI，不作依赖 |
| C2 | `opentimestamps/opentimestamps-client` | 433 | 2026-10-27 | Python | **LGPL-3.0-or-later** | 活跃 | 仅 CLI，不作依赖 |
| C3 | `aboutcode-org/license-expression` | 74 | 2026-05-26 | Python | 未获取 | 中 | 观察（SPDX 表达式解析） |
| C4 | `GoogleChrome/lighthouse` | 31,019 | 2026-11-04 | JavaScript | Apache-2.0 | 活跃 | 仅 CI 参考 |
| C5 | `protobom/protobom` | 339 | 2026-11-04 | Go | Apache-2.0 | 活跃 | 不引入（Go 生态） |

> 说明：Star 与提交时间取自 ungh.cc 镜像（GitHub API 已限流），与 GitHub 页面可能有数分钟偏差；LICENSE 判定以仓库内 LICENSE 文件为准，取不到的一律记"未获取"。

---

### 2.2 A 级候选：字段 + 九问

#### A1. CycloneDX/cyclonedx-python

- 仓库：https://github.com/CycloneDX/cyclonedx-python
- Star：399 ｜ 最近提交：2026-11-04 ｜ 语言：Python
- 许可证：Apache-2.0（LICENSE 文件）｜ 维护：活跃（CycloneDX 官方组织）
- 测试覆盖：仓库含 `tests/`（覆盖率数字未获取）
- 已知公告：osv.dev `PyPI/cyclonedx-python` 无结果
- 核心源码目录：`cyclonedx_py/`（`environment.py`、`requirements.py`、`pipenv.py`、`poetry.py`、`parser/`）、`tests/`、`docs/`

九问：
1. 是否已有同类功能：无（`scripts/verify_oss_licenses.py` 只做许可证校验，不产出 SBOM）。
2. 当前实现不足：无组件清单、无依赖传递关系、无 SBOM 文件，审查方无法离线知道"这个版本装了什么"。
3. 候选解决什么：从已安装环境或 `requirements.txt` 生成 CycloneDX 1.x JSON/XML，含 purl、hash、依赖关系。
4. 值得借鉴的模块：`cyclonedx_py/environment.py`（如何从 `importlib.metadata` 抽组件与依赖）、`cyclonedx_py/parser/`（多输入源抽象）。
5. 能否独立封装：能，作为构建期 CLI 依赖，不进运行时。
6. 是否破坏现有接口：否。只新增 CI 步骤与 `dist/sbom.json` 产物；不改 `oss-registry.yaml` 结构即可先跑通。
7. 许可证：Apache-2.0，允许使用与修改；非 GPL/AGPL，可安全作为构建依赖。
8. 安全风险与维护成本：构建期引入供应链面（本身是扫描工具）；维护低（官方组织、半年一次升级）。风险点：从环境生成时会包含 dev 依赖，需显式过滤。
9. 集成/回归测试：CI 增加 `cyclonedx-py environment -o dist/sbom.json`，断言 ① JSON schema 通过 ② 组件数 ≥ 锁定依赖数 ③ 每个组件有 `name/version/purl` ④ 与 `uv.lock` 抽样比对 3 个包版本一致。回归用 golden 文件（组件排序规范化后 diff）。

#### A2. raimon49/pip-licenses

- 仓库：https://github.com/raimon49/pip-licenses
- Star：378 ｜ 最近提交：2026-11-05 ｜ 语言：Python
- 许可证：MIT（LICENSE 文件）｜ 维护：活跃
- 测试覆盖：仓库含测试目录（数值未获取）
- 已知公告：osv.dev `PyPI/pip-licenses` 无结果
- 核心源码目录：单文件 `piplicenses.py`（约 41 KB）+ `tests/`

九问：
1. 已有同类功能：部分有——`scripts/verify_oss_licenses.py` 自己做 license 判定。
2. 不足：它依赖人工维护的 `oss-registry.yaml`；许可证名来自包的元数据字符串，未经 SPDX 归一化，遇到 `GPL-2.0-or-later OR MIT`、`LICENSE` 空值等就不可靠。
3. 解决什么：一次性导出全部已安装包的名称/版本/许可证/首页，作为机器可读清单喂给门禁脚本，替代"人工登记"。
4. 值得借鉴：`piplicenses.py` 中 `get_packages()` 的元数据回退链（License → Trove Classifier → 文件名推断）；`--format json` 输出结构。
5. 能否独立封装：能，构建期 CLI。
6. 是否破坏接口：否；但会让门禁从"遍历 registry"变成"遍历 SBOM + registry 做策略"，需同步修改 `verify_oss_licenses.py`（这是行为变更，需评审）。
7. 许可证：MIT，允许使用与修改；非 copyleft。
8. 风险与成本：Trove classifier 推断可能误判（把 `License :: OSI Approved :: GNU General Public License v3` 判成 GPL，方向正确但需人工复核边界）；维护低。
9. 测试：构造 5 个 fixture 包（MIT、GPL-3.0-only、AGPL-3.0、双许可、无元数据），断言分类结果；回归断言"新增依赖若为 GPL/AGPL 必须出现在 violations 列表"。

#### A3. google/osv-scanner

- 仓库：https://github.com/google/osv-scanner
- Star：11,398 ｜ 最近提交：2026-11-05 ｜ 语言：Go
- 许可证：Apache-2.0（LICENSE 文件）｜ 维护：活跃（Google）
- 测试覆盖：`internal/` 与 `pkg/` 内有测试；数值未获取
- 已知公告：osv.dev `Go/github.com/google/osv-scanner` 本次未检出
- 核心源码目录：`cmd/osv-scanner/`、`internal/`、`pkg/`、`actions/`

九问：
1. 已有同类功能：部分有——CI 已用 `pip-audit` + Trivy，但都 `--exit-code 0`。
2. 不足：无锁文件全量扫描（`pip-audit` 依赖已安装环境）、无 CVE 台账、无到期跟踪、非阻断。
3. 解决什么：直接扫 `uv.lock`/`package-lock.json`/SBOM，输出 OSV 格式，可在 CI 阻断并可生成基线。
4. 值得借鉴：`pkg/lockfile/`（多生态锁文件解析）、`pkg/reporter/`、`actions/`（GitHub Action 封装方式）。
5. 能否独立封装：能，CI 二进制或 Docker 调用，不入运行时。
6. 是否破坏接口：否；但若改为阻断需调 CI 退出码，可能让历史遗留 CVE 阻塞主干，应先用 baseline。
7. 许可证：Apache-2.0，允许使用与修改；非 copyleft。
8. 风险与成本：OSV 库查询需网络（CI 可接受）；离线场景需 `--offline` 预置库（体积成本）。维护低。
9. 测试：用含已知漏洞的锁文件 fixture（如刻意 pin 一个旧版本）断言返回非零；再用干净锁文件断言零；回归检查 baseline 文件格式稳定。

#### A4. messense/nh3

- 仓库：https://github.com/messense/nh3
- Star：404 ｜ 最近提交：2026-10-27 ｜ 语言：Rust + PyO3（Python 绑定）
- 许可证：MIT（LICENSE 文件）｜ 维护：活跃
- 测试覆盖：仓库含 `tests/`；数值未获取
- 已知公告：osv.dev `PyPI/nh3` 无结果（对比 `bleach` 有 6 条且含"无修复"）
- 核心源码目录：`src/lib.rs`（约 37.8 KB，ammonia 封装）、`nh3.pyi`、`tests/`

九问：
1. 已有同类功能：部分有——`src/security/input_sanitizer.py` 用 `html.escape` + 标签/属性黑名单。
2. 不足：黑名单型 XSS 过滤易被新向量绕过；无"允许列表 + URL scheme 白名单"的成熟实现。
3. 解决什么：基于 ammonia（Rust）的允许列表 HTML 消毒，输出侧渲染模型结果时防 XSS。
4. 值得借鉴：`src/lib.rs` 的默认允许标签/属性集合，以及 URL scheme 与 `rel="noopener noreferrer"` 处理。
5. 能否独立封装：能，`sanitize_html()` 单一函数，替换现有 `XssGuard.sanitize` 的调用点。
6. 是否破坏接口：低。保持"str → str"签名即可；但默认允许列表比现有黑名单更严格，可能删掉控制台需要的标签，需先枚举允许集。
7. 许可证：MIT，允许使用与修改；非 copyleft。
8. 风险与成本：引入 Rust 轮子（多平台 wheel 可用性需实测）；维护低。安全上明显优于 bleach（bleach 有未修复的 ReDoS）。
9. 测试：对 20 条 OWASP XSS 向量断言输出不含 `javascript:`/`onerror=`/`<script>`；对合法 markdown 渲染片段断言保留 `<p>/<strong>/<a href="https://...">`；回归用快照。

#### A5. data-privacy-stack/presidio

- 仓库：https://github.com/data-privacy-stack/presidio（原 microsoft/presidio）
- Star：11,741 ｜ 最近提交：2026-11-02 ｜ 语言：Python
- 许可证：MIT（LICENSE 文件）｜ 维护：活跃
- 测试覆盖：各子包含 `tests/`；数值未获取
- 已知公告：osv.dev `PyPI/presidio-analyzer` 无结果
- 核心源码目录：`presidio-analyzer/`（`analyzer/recognizer_registry/`、`analyzer/analyzer_engine.py`）、`presidio-anonymizer/`、`presidio-structured/`、`presidio-image-redactor/`

九问：
1. 已有同类功能：有——`src/knowledge/pii.py`（正则型 `PIIDetector` + `redact_text`）。
2. 不足：死代码（检测函数丢弃 `re.sub` 结果）；姓名规则把所有大写词当人名；无中文 PII；无置信度/上下文消歧；`filter_content` fail-open。
3. 解决什么：可插拔 recognizer 注册表 + 置信度分数 + 多种 anonymizer 策略（replace/mask/hash/encrypt），并可用自定义 recognizer 补中文。
4. 值得借鉴：`RecognizerRegistry` 的注册/加载机制（自定义中文 recognizer 的挂载点）、`AnalyzerEngine` 的 `entities + score_threshold` 设计、`AnonymizerEngine` 的 operator 抽象。
5. 能否独立封装：能，作为 `PIIDetector` 的后端之一；保留现有纯正则路径作为"零依赖快速路径"。
6. 是否破坏接口：中等。`PIIDetector.detect(text)` 返回结构需扩展 `score/entity_type`；建议新增适配器而不是改旧返回，避免 RAG 管线回归。
7. 许可证：MIT，允许使用与修改；非 copyleft。
8. 风险与成本：默认模型（spaCy/transformers）体积大（数十 MB 至数百 MB），单机打包需裁剪；误报率需在本项目语料上实测（本次未测）。维护中低。
9. 测试：固定 50 条中文/英文样本，断言 ① 中文手机号/身份证可检出 ② 普通业务词不被误判 ③ `score_threshold` 调整后 precision/recall 单调变化；回归防"过度脱敏"（断言合法产品名不被替换）。

#### A6. dequelabs/axe-core

- 仓库：https://github.com/dequelabs/axe-core
- Star：7,757 ｜ 最近提交：2026-11-04 ｜ 语言：JavaScript
- 许可证：MPL-2.0（LICENSE 文件）｜ 维护：活跃（Deque）
- 测试覆盖：`test/` 大量用例；数值未获取
- 已知公告：osv.dev `npm/axe-core` 仅返回恶意仿冒包 `axe-core-scanner`（MAL-2025-41281），**不是本包**；本包未见公告
- 核心源码目录：`lib/`（`checks/`、`rules/`、`commons/`）、`build/`、`doc/`

九问：
1. 已有同类功能：部分有——`scripts/ops/audit_console_readability.py` 用 CDP 检查对比度/字号/焦点/键盘。
2. 不足：不是 WCAG 全套规则；无 CI 门禁；无 VPAT/ACR 证据；无自动化回归。
3. 解决什么：覆盖 WCAG 2.x A/AA 的自动化子集（名称/角色/对比度/表单标签/ARIA 合法性），可输出 violations JSON。
4. 值得借鉴：`lib/rules/` 的规则元数据（`wcag` tag、`impact`、`helpUrl`）——可直接映射到 VPAT 条目。
5. 能否独立封装：能，作为前端 devDependency + `vitest` 集成。
6. 是否破坏接口：否，测试层新增。
7. 许可证：MPL-2.0（文件级 copyleft）。**不改 axe-core 源文件即可商用**；若要改其文件，改动文件需以 MPL 公开。对闭源分发安全，但需在合规报告标注。
8. 风险与成本：只能覆盖自动化可检项（约 30–50% 规则），不能宣称"通过 WCAG"；维护低。
9. 测试：在 `apps/console/console` 增加 `vitest` + `axe-core` 对 5 个关键页面（聊天、设置、知识库、审计、备份）跑 `runOnly: {type:'tag', values:['wcag2a','wcag2aa','wcag21aa']}`；门禁设为"critical/serious 为 0"，moderate 记录不阻断；回归保存基线 violations。

#### A7. interlynk-io/sbomqs

- 仓库：https://github.com/interlynk-io/sbomqs
- Star：318 ｜ 最近提交：2026-11-03 ｜ 语言：Go
- 许可证：Apache-2.0（LICENSE 文件）｜ 维护：活跃
- 测试覆盖：`pkg/` 含测试；数值未获取
- 已知公告：osv.dev `Go/.../sbomqs` 无结果
- 核心源码目录：`cmd/`、`pkg/`（评分与规范检查）、`docs/`

九问：
1. 已有同类功能：无。
2. 不足：即使生成了 SBOM，也没有"这个 SBOM 是否够用"的判定。
3. 解决什么：按 NTIA 最小元素 / BSI TR-03183 / OpenChain Telco 等打分，输出分数与缺失字段。
4. 值得借鉴：`pkg/` 的 profile 定义与评分权重——可只取"必需字段检查"而不引入整套评分哲学。
5. 能否独立封装：能，CI 二进制。
6. 是否破坏接口：否。
7. 许可证：Apache-2.0，允许使用与修改。
8. 风险与成本：标准版本会漂移，需锁工具版本；维护低。
9. 测试：对自产 SBOM 断言分数 ≥ 阈值；对"删掉 supplier 字段"的畸形 SBOM 断言分数下降并列出缺失项。

#### A8. sigstore/sigstore-python

- 仓库：https://github.com/sigstore/sigstore-python
- Star：338 ｜ 最近提交：2026-10-29 ｜ 语言：Python
- 许可证：Apache-2.0（LICENSE 文件）｜ 维护：活跃
- 测试覆盖：`test/`；数值未获取
- 已知公告：本次未检出针对该包的 OSV 条目
- 核心源码目录：`sigstore/`（`sign/`、`verify/`、`_cli/`）、`test/`

九问：
1. 已有同类功能：无（`src/kernels/audit/dual_signature.py` 是自有密钥签名，不是发布物签名）。
2. 不足：发布包与二进制无法被外部验证"是谁构建的、是否被改过"。
3. 解决什么：keyless（OIDC）或密钥模式对发布物签名，生成 `.sig`/bundle，供下游 `sigstore verify` 离线/在线校验。
4. 值得借鉴：`sigstore/sign/` 的 bundle 生成、`sigstore/verify/` 的证书链 + Rekor 校验路径。
5. 能否独立封装：能，发布流水线专用。
6. 是否破坏接口：否，只加发布产物。
7. 许可证：Apache-2.0，允许使用与修改。
8. 风险与成本：keyless 依赖 OIDC 与网络（Rekor/Fulcio）；离线/内网发布不可用，需回退到密钥模式；维护中。
9. 测试：CI 签名一个 release artifact → 用 `sigstore verify identity` 校验者身份与证书 SAN → 篡改 1 字节断言失败。

---

### 2.3 B 级候选：字段 + 九问（压缩版）

#### B1. CycloneDX/cyclonedx-node-npm
- 字段：151 star｜2026-11-03｜JavaScript｜LICENSE **未获取**｜活跃｜目录 `.github, bin, src, tests`（目录来自 ungh 树，LICENSE 未取到）｜OSV npm 未检出
- 九问：①无（前端无 SBOM）②前端依赖只在 `package-lock.json` ③为 `apps/console/console` 生成 CycloneDX ④`src/` 的 lockfile 解析 ⑤可独立为 npm 脚本 ⑥不改接口 ⑦**许可证未获取，使用前必须补读 LICENSE**；若为 Apache-2.0 与 CycloneDX 生态一致 ⑧维护低 ⑨对 `package-lock.json` 生成后断言组件数 ≥ 直接+传递依赖数，并对 `react` 版本抽样比对。

#### B2. pypa/pip-audit
- 字段：1,389 star｜2026-10-30｜Python｜Apache-2.0（LICENSE，10,174 字节）｜活跃｜`pip_audit/_service/osv.py`、`_format/cyclonedx.py`｜OSV PyPI 未检出
- 九问：①已有（CI dependency-audit，约 ci.yml:177–189）②`pip_audit … || true` 不阻断、只扫已安装环境（注意 pip-audit 2.10.1 无 `--exit-code` 参数）③可直接消费 `requirements.txt` 并输出 CycloneDX ④`_service/osv.py` 的批量查询与 `_format/cyclonedx.py` ⑤已独立 ⑥改为阻断会改变 CI 行为，需 baseline ⑦Apache-2.0 安全 ⑧误报/无修复 CVE 会阻塞发布，需 `--ignore-vuln` 白名单与到期复核 ⑨fixture 漏洞包断言非零退出 + 干净包断言零。

#### B3. transparency-dev/tessera
- 字段：255 star｜2026-10-29｜Go｜Apache-2.0｜活跃｜`api/ client/ cmd/ ctonly/ deployment/`｜OSV Go 未检出（本次）
- 九问：①部分有：`src/kernels/audit/external_anchor.py` 有 RFC 6962 与 STH，但 `ExternalLogTransport` 抽象未实现 ②"外部日志"目前是本地自证 ③提供可自运营的 tile-based 透明日志（含 witness 模型），可作真实第三方/独立运营日志 ④`api/` 的 tile 读取与 `client/` 的 proof 交互最值得借鉴 ⑤能，作独立服务 ⑥不改现有内核，只需实现 transport ⑦Apache-2.0 允许 ⑧运营成本高（存储、监控、witness 协调），单机产品不建议自建，除非有合规刚需 ⑨用本地单节点起日志，提交 root → 取 inclusion/consistency proof → 篡改后断言拒绝。

#### B4. in-toto/witness
- 字段：615 star｜2026-10-26｜Go｜Apache-2.0｜活跃｜`cmd/` 等（原 `testifysec/witness` 已迁移，旧仓库 0 star 为存根）｜OSV 未检出
- 九问：①无 ②无构建步骤级 provenance ③按 in-toto 布局记录每步产物与签名 ④`cmd/` 的策略验证流程 ⑤能，CI 独立工具 ⑥不改接口 ⑦Apache-2.0 ⑧Go 工具链 + 密钥管理成本，单一桌面产品收益低 ⑨对构建步骤生成 attestation，篡改产物断言验证失败。

#### B5. slsa-framework/slsa-verifier
- 字段：352 star｜2026-08-07｜Go｜Apache-2.0｜维护中（默认分支名异常：`Hayden-IO-patch-1`，说明分支治理需注意）｜`actions/ cli/`｜OSV 未检出
- 九问：①无 ②无法验证构建来源等级 ③校验 GitHub Actions 生成的 provenance 是否满足 SLSA ④`cli/` 的 verifier 分层 ⑤能，CI ⑤不改接口 ⑦Apache-2.0 ⑧只在 GitHub Actions 构建时有效，本地构建无意义 ⑨用官方 release 的 provenance 断言通过，替换 artifact 断言失败。

#### B6. jsx-eslint/eslint-plugin-jsx-a11y
- 字段：3,633 star｜2026-01-06｜JavaScript｜MIT｜维护中（近 10 个月有提交）｜仓库根 `src/`、`docs/`｜OSV npm 未检出
- 九问：①部分（手工 aria 属性，无静态规则）②无 CI 级静态检查 ③在编码期捕获 `alt` 缺失、`role` 误用、无键盘处理等 ④`src/rules/` 的规则实现可映射到项目 lint ⑤能，devDependency ⑥不改接口 ⑦MIT ⑧规则可能与现有 `oxlint` 冲突，需试跑；误报需按规则关闭 ⑨在 `oxlint` 之外挂 ESLint 跑 `src/`，断言新增违规为 0（基线快照）。

#### B7. IBMa/equal-access
- 字段：786 star｜2026-10-30｜JS/Java｜Apache-2.0｜活跃｜`accessibility-checker-engine/` 等｜OSV 未检出
- 九问：①同 A6 ②同 A6 ③提供更贴 WCAG 2.2 的规则引擎与报告格式 ④`accessibility-checker-engine/` 的规则与 VPAT 映射 ⑤能 ⑥不改接口 ⑦Apache-2.0 ⑧Java 依赖较重，前端集成复杂 ⑨与 axe 对同一页面跑，比较违规集差异，作为交叉验证而非双门禁。

#### B8. unitary/toxic-bert
- 字段：HF 229 likes｜最近更新未获取｜PyTorch/BERT｜模型卡 apache-2.0｜研究模型、维护低｜权重与 tokenizer｜HF 页面未见安全公告（**模型无 CVE 概念，需注意供应链：权重文件完整性**）
- 九问：①无输出侧审核 ②`src/ai/governance.py` 只有输入侧正则 ③英文毒性二分类（toxic/obscene/threat/insult/identity hate）④模型卡的分标签阈值用法 ⑤能，ONNX 本地推理 ⑥新增输出审核中间件，不改原接口 ⑦apache-2.0 允许 ⑧误杀率**未实测**；英文为主，中文不可用；CPU 推理延迟需实测 ⑨用 200 条标注样本测 precision/recall，门禁取"高置信才拦、其余标记"。

#### B9. thu-coai/roberta-base-cold
- 字段：HF 44 likes / 764 downloads 月｜更新未获取｜PyTorch｜**许可证未获取（模型卡未标注）**｜研究模型｜`config.json`、权重｜无 CVE 概念
- 九问：①无 ②无中文输出审核 ③中文冒犯性内容分类；模型卡自报 test accuracy 82.75、macro-F1 82.39（**以模型卡为准，本次未复现**）④输入模板与标签映射 ⑤能 ⑥同 B8 ⑦**许可证未知，不得直接商用分发**；须先向作者确认 ⑧许可风险 > 技术风险 ⑨同 B8，但语料必须本项目自建。

#### B10. sqlcipher/sqlcipher
- 字段：7,323 star｜2026-10-27｜C｜BSD-3-Clause 风格（ZETETIC LLC 声明）｜活跃｜`src/ ext/ autoconf/`｜OSV C 生态本次未查（未获取）
- 九问：①部分：secret 已 AES-256-GCM，但主库明文 ②`src` 无 `PRAGMA key` ③整库页级加密，防"拿到 .db 文件" ④`src/` 的页加密与密钥派生（不宜自研）⑤能，但要替换 SQLite 驱动/打包原生库 ⑥**会改变数据层**（连接串、迁移、备份格式），风险高 ⑦BSD 风格允许 ⑧Windows 打包与 CI 矩阵成本高；密钥轮换复杂 ⑨用临时库写入 → 关闭 → 用错误密钥打开断言失败 → 正确密钥断言成功；迁移回归全量读写。

#### B11. restic/restic
- 字段：36,981 star｜2026-11-01｜Go｜BSD-2-Clause｜活跃｜`cmd/ internal/`｜OSV Go 未查（未获取）
- 九问：①部分：`src/backup/backup_manager.py` 未真加密 ②同上 + 二进制损坏 ③加密、去重、增量备份 ④`internal/repository/` 的加密仓库格式 ⑤能，**但建议作为外置 CLI 可选后端**，不内嵌 ⑥不改现有接口，作为新 backend ⑦BSD-2 允许 ⑧依赖外部二进制、用户体验割裂 ⑨创建仓库 → 备份 → 篡改块断言恢复失败 → 正常恢复断言哈希一致。

#### B12. aquasecurity/trivy
- 字段：38,996 star｜2026-11-04｜Go｜Apache-2.0｜活跃｜`cmd/ pkg/ internal/ contrib/`｜OSV 未查（未获取）
- 九问：①已有（镜像扫描，`--exit-code 0`）②非阻断 ③扫容器/文件系统/SBOM 的 CVE ④`pkg/` 的扫描器注册 ⑤已独立 ⑥改阻断需处理无修复 CVE ⑦Apache-2.0 ⑧镜像体积大、DB 需联网更新 ⑨fixture 镜像断言非零退出。

#### B13. kubernetes-sigs/bom
- 字段：476 star｜2026-11-05｜Go｜Apache-2.0｜活跃｜`cmd/ internal/ pkg/`｜OSV 未检出
- 九问：①无 ②无 SPDX 输出 ③生成 SPDX 2.3 文档 ④`pkg/` 的 SPDX 构建 ⑤能 ⑥不改接口 ⑦Apache-2.0 ⑧Go 工具，对 Python 项目收益低于 cyclonedx ⑨对同一输入生成 SPDX 并断言 NTIA 必需字段齐全。

#### B14. microsoft/sbom-tool
- 字段：2,091 star｜2026-09-21｜C#/.NET｜MIT｜活跃｜`src/ samples/ pipelines/`｜OSV 未查（未获取）
- 九问：①无 ②无 SPDX SBOM ③生成 SPDX 2.2 ④`src/` 的 SPDX 序列化 ⑤能但需 .NET 运行时 ⑥不改接口 ⑦MIT ⑧单机产品多一套运行时，**不建议** ⑨同 B13。

#### B15. pypa/twine
- 字段：1,792 star｜2026-10-14｜Python｜Apache-2.0｜活跃｜`twine/commands/upload.py`、`twine/package.py`｜OSV 未检出
- 九问：①无 ②发布无 attestation ③PyPI 上传时附带 PEP 740 attestation ④`twine/package.py` 的 metadata 处理 ⑤能 ⑥不改接口 ⑦Apache-2.0 ⑧依赖 PyPI 与 OIDC，仅发布路径 ⑨上传 dry-run 断言 attestation 存在。

#### B16. sigstore/cosign
- 字段：6,411 star｜2026-11-05｜Go｜Apache-2.0｜活跃｜`cmd/`、`.github/`｜OSV Go 公告：GO-2026-5694（verify-blob-attestation 误报）、GHSA-w6c6-c85g-mmv6（medium）、GO-2026-4529 / GHSA-wfqv-66vq-46rm（跳过 tlog 时过期中间证书）、GO-2026-4309（接受任意有效 Rekor 条目）等，均"有修复版本"
- 九问：①无 ②发布物/镜像未签名 ③镜像与二进制签名、attestation ④`cmd/cosign/verify` 的校验链 ⑤能，发布流水线 ⑥不改接口 ⑦Apache-2.0 ⑧**历史公告多且涉及验证逻辑**，必须锁版本并跟踪；单机桌面产品收益取决于是否有容器分发 ⑨用固定版本签名 → 验签 → 篡改断言失败 → 过期证书场景回归。

---

### 2.4 C 级候选（仅观察 / 仅 CLI）

#### C1. fsfe/reuse-tool
- 字段：595 star｜2026-11-01｜Python｜**GPL-3.0-or-later**（`LICENSES/GPL-3.0-or-later.txt`）｜活跃｜`src/reuse/`、`tests/`｜OSV 未查
- 九问：①无 SPDX 文件头合规 ②源码文件无统一版权/许可头 ③扫描并生成 SPDX 头 ④`src/reuse/` 的 header 解析 ⑤**只能作为外部 CLI 调用**，不得 import 进运行时 ⑥不改接口 ⑦**GPL-3.0-or-later：作为独立工具调用（不链接、不修改分发）可接受；作为 Python 依赖 import 会传染，禁止** ⑧维护低但法律边界需法务确认 ⑨CI 调 `reuse lint` 断言失败文件数为 0；本项目是否强制 header 需先定策略。

#### C2. opentimestamps/opentimestamps-client
- 字段：433 star｜2026-10-27｜Python｜**LGPL-3.0-or-later**｜活跃｜`otsclient/`、`ots/`｜OSV 未查
- 九问：①无比特币锚定（`external_anchor.py` 只有抽象）②链锚仅本地 ③把哈希提交到比特币链，获得时间存在证明 ④`otsclient/` 的 `stamp`/`verify` 交互 ⑤**建议仅 CLI**，不 import ⑥不改接口 ⑦**LGPL-3：动态调用/独立进程可用；若修改其源码并分发需开源修改部分；静态链接/嵌入有争议，规避** ⑧依赖公共日历服务器与网络，验证需 Bitcoin 节点或区块头 ⑨用测试网/已知时间戳文件离线 `verify` 断言通过。

#### C3. aboutcode-org/license-expression
- 字段：74 star｜2026-05-26｜Python｜**LICENSE 未获取**｜中｜`src/license_expression/`｜OSV PyPI 未检出
- 九问：①`verify_oss_licenses.py` 用字符串匹配 ②SPDX 表达式（`OR/AND/WITH`）解析不健壮 ③规范化并解析 SPDX 表达式，判定 copyleft ④`src/license_expression/` 的表达式树 ⑤能 ⑥替换内部比较逻辑，需回归 ⑦**许可证未获取，先读 LICENSE 再决定** ⑧若为 Apache-2.0 风险低；否则评估 ⑨用 30 条真实 license 字符串断言解析与归类。

#### C4. GoogleChrome/lighthouse
- 字段：31,019 star｜2026-11-04｜JavaScript｜Apache-2.0｜活跃｜`core/`、`cli/`｜OSV 未查
- 九问：①部分（`audit_console_readability.py`）②无标准报告 ③可输出 a11y 分数与 HTML 报告 ④`core/audits/accessibility/` ⑤能，仅 CI ⑥不改接口 ⑦Apache-2.0 ⑧需 Chrome，CI 稳定性一般 ⑨对本地构建跑 `--only-categories=accessibility`，断言分数不下降。

#### C5. protobom/protobom
- 字段：339 star｜2026-11-04｜Go｜Apache-2.0｜活跃｜`api/ internal/ pkg/`｜OSV 未检出
- 九问：①无 ②SBOM 格式互转缺失 ③CycloneDX/SPDX 双向转换 ④`pkg/` 的节点模型 ⑤Go 库，Python 项目不适用 ⑥不改接口 ⑦Apache-2.0 ⑧跨语言集成成本 ⑨不适用。

---

## 3. 最值得的 5 个深评

### 深评 1：SBOM + GPL/AGPL 门禁的最小可落地方案（对应重点问题 1）

#### 现状事实
- 有门禁但靠人工登记：`scripts/verify_oss_licenses.py` 遍历 `oss-registry.yaml` 里**已登记**的组件，未登记的新依赖不会失败。
- 锁文件存在但未被消费：`uv.lock` 存在，CI 却是 `pip install -e .`；`requirements.txt` 无 `--hash`。
- 无 SBOM、无质量校验、无签名。

#### 目标
每次构建产出两份东西：`dist/sbom.cdx.json`（后端）与 `dist/sbom-frontend.cdx.json`，并在 CI 上跑一条策略：**分发依赖中出现 GPL/AGPL/SSPL → 阻断**。

#### 最小落地步骤（不改运行时）

| 步 | 动作 | 工具 | 产出 | 备注 |
|----|------|------|------|------|
| 0 | 冻结安装 | `uv sync --frozen` 或 `uv export --frozen` + `pip install --require-hashes` | 可复现环境 | **`uv export` 是否默认带 hash 需实测**；若不便，改用 `uv pip compile --generate-hashes` |
| 1 | 后端 SBOM | `cyclonedx-py environment -o dist/sbom.cdx.json` | CycloneDX JSON | 需排除 dev 组 |
| 2 | 前端 SBOM | `cyclonedx-npm`（B1） | CycloneDX JSON | **先确认 B1 的 LICENSE** |
| 3 | 许可证清单 | `pip-licenses --format json` | `dist/licenses.json` | 仅作交叉校验，不作为唯一事实源 |
| 4 | SPDX 归一化/解析 | 自研小函数或 `license-expression`（C3，待定许可） | 规范化 license 集合 | 处理 `OR/AND/WITH` 与 `LicenseRef-*` |
| 5 | 策略门禁 | 扩展现有 `verify_oss_licenses.py`：读 SBOM → 按 `scope`（runtime/build/tool）分类 → 命中策略即非零退出 | 复用现有脚本，改动面小 | **新增 `scope` 字段，不破坏旧 registry** |
| 6 | SBOM 质量 | `sbomqs score` | 分数 + 缺失字段 | 阈值先设"NTIA 必需字段齐全" |
| 7 | 归档 | 把两份 SBOM 作为 release artifact | 随包发布 | 运行时零成本 |

#### 成本（不含法务确认）
- 工程量：后端 SBOM + 门禁改造 **1–2 人日**；前端 SBOM + 容器镜像 SBOM **+1–2 人日**；CI 运行时间 **+2–4 分钟**。
- 维护：依赖升级时 SBOM 自动跟着变；**每季度复核一次 allowlist 与 scope 归属**，约 0.5 人日/季。
- 法律成本才是大头：需要一次性定义"哪些是工具（GPL CLI 可直接调用）、哪些是分发依赖（GPL 不可）"。这是策略问题，工具替不了。

#### 关键设计决定（必须写进策略，否则门禁会误杀）
1. **scope 三分类**：`runtime`（随产品分发）、`build`（仅构建期）、`tool`（独立进程调用的 CLI）。GPL 在 `tool` 下允许；`build` 下的 GPL 工具通常也允许（不产生衍生作品），但**要留证据**。
2. **GPL/AGPL 判定必须看 LICENSE 文件而不是包元数据**：元数据里的 `License` 字段空值/自定义字符串很常见。SBOM 里应保留 `licenseConcluded` 与 `licenseDeclared` 两个概念（CycloneDX 用 `licenses` 数组）。
3. **`LicenseRef-*` 与"未获取"必须默认阻断**，而不是默认放行。这一条能把 A1/B1 的"未获取"变成可执行动作。
4. **门禁读 SBOM，不读环境**：环境会漂移，SBOM 是可审计产物。

#### 验收（必须自动断言）
- 在 CI 注入一个假依赖（测试用 `GPL-3.0-only` 的本地 wheel），断言门禁退出码非 0。
- 删掉 SBOM 中一个组件的 `purl`，断言 `sbomqs` 分数下降且 CI 报告缺失项。
- 同一 commit 两次生成 SBOM，规范化排序后 `diff` 为空（防抖动）。

#### 明确不做什么
- 不自建 SBOM 生成器。
- 不引入 `microsoft/sbom-tool`（多一套 .NET 运行时）。
- 不把 SBOM 生成放进运行时（单机产品不需要）。

---

### 深评 2：审计链的第三方独立验证（对应重点问题 2）

#### 现状（必须先承认，否则会重复造轮子）
- 哈希链、Merkle 包含证明、RFC 6962 树与 STH、RFC 3161 时间戳、双重签名、root of trust 全都在 `src/kernels/audit/`。
- `src/kernels/audit/external_anchor.py` 的 docstring 已经写得比任何雷达报告都诚实：
  - "`ExternalLogTransport` is an ABSTRACT placeholder … raise `NotImplementedError`"；
  - "The ONLY concrete transport shipped … is `LocalReferenceLog` … it is NOT a real independently-operated transparency log"；
  - 自证结果 `self_attested=True`，且"Nothing here is active by default"。
- `scripts/independent_verification.py` 也明确说 `record_hash` 不是防篡改（哈希在记录内部，编辑者可重算）。
- 因此真实缺口是 **transport + 运营方 + 离线验证器打包**，不是密码学。

#### 外部审计者"不接触本机"要拿到什么
| 级别 | 需要外发的东西 | 外部能证明什么 | 不能证明什么 | 本项目现状 |
|------|----------------|----------------|--------------|-----------|
| L1 离线证据包 | 公钥（带外固定）、checkpoint/STH、每条事件的 inclusion proof、RFC3161 token | 事件在某个时刻已存在且未被改；链自洽 | 运营方是否隐藏了其他事件 | 原语齐全，**打包器缺失** |
| L2 透明日志 | 日志 URL/产物 + 日志公钥 + inclusion proof + consistency proof | 加上"日志未 equivocation"（不能对同一序号给两个根） | 日志运营方是否可信（取决于其治理） | `LocalReferenceLog` 自证；**无真实日志** |
| L3 外部锚定 | 把根哈希提交到独立时间源（RFC3161 TSA / 比特币） | 根在某个真实时间点存在 | 内容正确性 | RFC3161 有；OTS 未接 |

#### 推荐架构（成本最低、最不破坏现有结构）
1. **定一个 `AuditBundle` 格式**（本项目自定，JSON）：
   - `version`
   - `log_public_key`（或 key id）
   - `checkpoint { tree_size, root_hash, timestamp, signature }`
   - `proofs[] { event_id, leaf_hash, audit_path[], leaf_index }`
   - `rfc3161_tokens[]`
   - **只出哈希与证明，不出事件内容**；叶子用 `sha256(salt || canonical_event)`，salt 留在本机，防止字典攻击反推内容。
2. **实现 transport**：在 `ExternalLogTransport` 上实现一个真实后端。两个选项：
   - 自运营 `tessera`（B3）：可控、可离线，但运营与 witness 协调是真成本；
   - 接公共 Rekor：零运营，但依赖网络与第三方可用性，且 `cosign` 的历史公告显示验证逻辑曾经出过"接受任意 Rekor 条目"这类问题，必须锁版本。
   - **单机桌面产品的现实建议**：先做 L1（离线证据包）+ 可选 L2（用户/客户自建日志）。不要默认要求联网。
3. **打包独立验证器**：一个**单文件、零项目依赖**的验证脚本（或独立小二进制），输入 `AuditBundle` + 公钥，输出 PASS/FAIL。它不能 import 本项目代码，否则"独立"不成立。可以借鉴 `external_anchor.py` 的验证数学，但**复制的是算法而非代码**，并且要写明来源与许可。
4. **锚定节奏**：按天/按千条事件生成 checkpoint 并对 checkpoint 做 RFC3161 时间戳；把 checkpoint hash 写进发布说明或公开文档，形成"运营方也无法悄悄改历史"的外部见证。
5. **诚实的 UI 文案**：`self_attested` 必须显示为"本机自证"，不得显示"第三方已验证"。这一条比任何技术都重要——现有代码已经做对了，别在 UI 层破坏它。

#### 成熟格式对照
- **RFC 6962 / CT**：已有实现（`external_anchor.py`），包含证明与 STH 都在；缺的是真实日志。
- **Tessera**：tile-based 日志，适合自运营；`api/` + `client/` 是 transport 实现的参考。
- **Rekor**：公共透明日志，适合"零运营"；但引入外部依赖与版本锁定成本。
- **RFC 3161**：已有，最适合做"时间锚"，成本最低，**优先接进 bundle**。
- **OpenTimestamps**：比特币锚定，最强外部性；但客户端 LGPL-3、依赖日历服务，**只建议作为外部 CLI 可选**。

#### 验收
- 用 bundle 在**另一台机器**（无本项目数据目录）跑独立验证器 → PASS。
- 篡改 `audit_path` 中一个哈希 → FAIL。
- 把 checkpoint 的 `tree_size` 减 1 → consistency 校验 FAIL。
- 用错误公钥 → FAIL。
- bundle 中不得出现事件正文（用正则断言不含业务字段名）。

---

### 深评 3：本地内容安全（对应重点问题 3）

#### 现状
- 输入侧：`src/ai/governance.py` 的 11 类威胁正则（prompt injection、credential theft、data exfiltration 等），HIGH/CRITICAL 阻断；`src/security/input_sanitizer.py` 做 SQLi/XSS/命令注入。
- 输出侧：**没有**。`src/knowledge/security.py` 的 `filter_content` 只是把文本交给 `pii.redact_text`，且异常时 `return text`（fail-open）。
- `src/knowledge/pii.py`：检测函数算了 `re.sub` 却丢弃结果；姓名正则把所有首字母大写词当人名；无中文规则。

#### 分层方案（按"性价比"排序）

| 层 | 做什么 | 依赖 | 成本 | 误杀风险 | 建议 |
|----|--------|------|------|----------|------|
| L0 | 修现有 PII 死代码；补中文规则（手机号、身份证、银行卡、地址关键词）；脱敏失败改为 fail-closed（对出站内容） | 无 | 0.5–1 人日 | 低 | **先做，收益最大** |
| L1 | HTML 消毒（`nh3`，允许列表） | MIT | 0.5 人日 | 低（可能删标签） | **必做** |
| L2 | PII 识别升级（`presidio` + 自定义中文 recognizer） | MIT，模型体积需裁剪 | 1–3 人日 | 中（需阈值调优） | 做，但保留纯正则快速路径 |
| L3 | 毒性/越界分类（英文 `toxic-bert`；中文 `roberta-base-cold`） | apache-2.0 / 许可未获取 | 2–5 人日 + 模型分发 | **未实测**，需本语料校准 | 条件做 |
| L4 | 本地 LLM 审核（Granite Guardian 8B / Llama Guard 8B） | 8B 模型 + 显存；Llama 许可是自定义社区许可 | 高 | 未实测 | **单机默认不引入** |

#### 策略建议（比模型选择更重要）
1. **默认"标记 + 脱敏"，不默认"静默拦截"**。静默拦截是误杀的主要投诉源；对凭据外泄、PII 外泄这类类别才硬拦。
2. **分级处置**：`critical`（凭据/PII 出站）→ 拦截；`high`（明显越界）→ 拦截并给出原因；`medium` → 脱敏 + 提示；`low` → 仅记录审计事件。
3. **用户可见可申诉**：被拦时显示命中的类别与证据片段（现有 `ThreatFinding.evidence` 正好可复用），并提供"我已确认"的升级路径（带审计事件）。
4. **误杀率必须自测，不许引用别家数字**：本次没有测，任何"误杀率 X%"都应写 `需实测`。做法：抽 200–500 条本项目真实问答，人工标注，算 precision/recall 与分标签阈值曲线；中文模型尤其要在本项目语料上测。
5. **模型分发合规**：模型权重是新的"依赖"——必须进 SBOM 或单独的 `models-manifest.json`，记录来源、版本、哈希、许可证。`roberta-base-cold` 许可证未获取，**未确认前不得随包分发**。
6. **fail-open vs fail-closed 要分类**：输入侧的注入检测可以 fail-open + 告警（避免整机不可用）；**出站 PII 脱敏必须 fail-closed**（宁可不出，不可泄露）。

#### 成本与性能（诚实版）
- `nh3`：Rust 扩展，单次消毒通常在亚毫秒级；具体 `需实测`。
- `presidio`：默认 spaCy 模型会显著增加安装体积；单机打包应只带 `en_core_web_sm` 或纯规则 recognizer，中文用自定义正则 + 可选小模型。
- `toxic-bert` / `COLD`：约 1.1 亿参数级别，CPU 上每句通常几十毫秒量级；**必须实测**，且应异步、可关闭、不阻塞首 token。
- 磁盘：模型权重是主要成本（数百 MB 量级），这是单机产品要认真权衡的点。

#### 验收
- 修 PII 后：中文手机号/身份证必检；普通产品名不被脱敏。
- `nh3`：OWASP XSS 向量全部被消毒；合法 HTML 保留。
- 分类器：用自建标注集出阈值曲线；CI 只跑"确定性规则"回归，模型评测单独跑（避免 CI 不稳定）。

---

### 深评 4：隐私合规——必须做 vs 可以跳过（对应重点问题 4）

> 本节是工程清单，不是法律意见；不引用具体法条编号。

#### 必须做（本地优先产品的"最小可信集"）

| # | 必须项 | 现状 | 落地动作 | 验收 |
|---|--------|------|----------|------|
| 1 | 默认本地、无遥测 | 需确认是否有外发 | 首次启动明示"数据不出本机"；遥测默认关闭且需显式同意 | 断网可完整使用；抓包无外联 |
| 2 | 数据清单（装了什么、存哪里） | 无 | 生成 `docs/privacy/data-inventory.md` + 设置页可见 | 清单覆盖 DB/日志/缓存/备份/模型 |
| 3 | 导出（机器可读） | 假数据（`src/cli/backup_commands.py`） | 删掉 `_seed_sample_data`；导出走真实数据；格式 JSON/JSONL + 附件 | 导出→导入往返一致；二进制不损坏 |
| 4 | 删除（含派生数据） | `gdpr.py` 纯内存 | 实现真实删除：DB 行、索引、向量、缓存、备份副本；返回删除报告 | 删除后检索不到；备份中也不残留 |
| 5 | 保留期与自动清理 | `retention.py`（审计） | 对日志/会话/临时文件定保留期 | 到期自动清理可验证 |
| 6 | 静态加密（至少密钥） | secret 已 AES-GCM；主库明文 | 短期：明确"主库未加密"并在文档声明；中期：SQLCipher 或字段级加密 | 拿到 .db 不含明文密钥 |
| 7 | 日志脱敏 | 🟢 `audit_logger.py` | 保持；补 PII 值级脱敏 | 注入测试：日志无 token/手机号 |
| 8 | 隐私声明 | 无 | 随包 `PRIVACY.md` + 首启展示 + 变更记录 | 文件存在且与实现一致 |
| 9 | 第三方组件与模型披露 | 无 | SBOM + 模型清单进隐私声明 | 每个外发/内置组件可追溯 |
| 10 | 安全联系渠道 | 需确认 | SECURITY.md / 邮箱 | 可达 |

#### 可以跳过或延后（不要堆砌）

| 项 | 为什么可以跳过 |
|----|----------------|
| Cookie 同意横幅 / 追踪 SDK 管理 | 无 Web 追踪、无广告 SDK；本地优先产品不适用 |
| 同意管理平台（CMP） | 没有多目的营销同意场景；用设置开关 + 审计即可 |
| 完整 DSAR 工单系统与 SLA 门户 | 单机产品由用户自己在应用内导出/删除即可，不需要工单流 |
| DPO 任命、DPIA 模板全套 | 无大规模系统性监控/敏感数据规模时属过度合规；保留"变更时评估"的轻量模板即可 |
| 跨境传输机制（SCC 等） | 默认不出境；若未来接云模型再补 |
| SOC 2 证据自动化（未实现） | 现有 `src/compliance/soc2.py` 是内存演示，**要么落盘要么删掉宣称**，不要假装有 |
| 每功能一份隐私影响评估 | 用"数据清单 + 变更检查表"替代 |

#### 三个"必须先修"的红线
1. `src/cli/backup_commands.py` 的假备份：任何"导出/备份"宣传都建立在它上面，必须先改。
2. `src/compliance/gdpr.py` 的内存态：不可作为"已实现数据主体权利"的证据；要么接 DB + API，要么在文档中降级表述。
3. `src/backup/backup_manager.py` 的 `encryption_enabled`：只是标志位。要么实现，要么在 UI 上不要显示"加密备份"。

---

### 深评 5：无障碍（WCAG / VPAT）

#### 现状
- 前端是 React 19 + Vite 的 Web 控制台（`apps/console/console`），**不是 Electron/Tauri 桌面壳**，所以 WCAG 2.2 AA 基本适用。
- 已有约 291 处 `aria-/role/label/tabIndex/onKeyDown`，说明有手工意识。
- 只有 `scripts/ops/audit_console_readability.py`（对比度/字号/焦点/键盘），不是 WCAG 规则集，也不阻断。

#### 建议的三层
1. **静态**：`eslint-plugin-jsx-a11y`（MIT）接到现有 lint（注意与 `oxlint` 并存的成本，先试跑）。
2. **运行时/组件级**：`vitest` + `axe-core`（MPL-2.0，不改源文件即可商用），对 5 个关键页面跑 `wcag2a/wcag2aa/wcag21aa`，**critical/serious = 0 阻断**。
3. **人工 + 证据**：键盘全流程、屏幕阅读器抽测、200% 缩放、减少动效（`prefers-reduced-motion`）；产出 ACR/VPAT 2.5 草稿（未获取官方模板，需自行获取模板再填）。

#### 现实边界
- 自动化只能覆盖一部分 WCAG 成功准则，**不能**因此宣称"符合 WCAG 2.2 AA"。
- 桌面软件（若未来上 Electron/Tauri）额外要看：原生菜单可键盘访问、窗口焦点管理、高对比度模式、系统字体缩放。当前 Web 控制台不涉及。
- 不建议引入 `pa11y`（LGPL-3.0），除非仅作独立 CLI 使用；`equal-access` 可作交叉验证，但 Java 依赖重。

#### 验收
- CI 中 axe 违规 serious+ 为 0；违规基线文件随版本更新需评审。
- 键盘-only 走完"登录→提问→查看审计→导出"四步，无焦点丢失。
- 对比度脚本与 axe 结果一致（避免两套标准打架）。

---

## 4. 明确不建议引入

| 候选 | 原因 | 替代 |
|------|------|------|
| `mozilla/bleach` | 2,764 star、Apache-2.0，但 OSV 有 6 条 PyPI 记录，含 `linkify(parse_email=True)` CPU 耗尽型 **无修复版本**（GHSA-g75f-g53v-794x），以及 `formaction` URI 绕过（GHSA-gj48-438w-jh9v，6.1 medium）、Unicode URI 绕过（GHSA-8rfp-98v4-mmr6） | `messense/nh3`（MIT，Rust/ammonia） |
| `pa11y/pa11y` | 4,590 star，但 **LGPL-3.0**；作为前端打包依赖有许可摩擦 | `axe-core`（MPL-2.0，不改源文件） |
| `fsfe/reuse-tool` 作为运行时依赖 | **GPL-3.0-or-later**；import 进 Python 运行时会造成许可传染 | 仅作 CI 外部 CLI（独立进程、不链接） |
| `opentimestamps-client` 作为内嵌依赖 | **LGPL-3.0-or-later**；内嵌/静态链接边界争议，且依赖公共日历服务 | 仅作可选外部 CLI，或先用已有 RFC3161 |
| `meta-llama/Llama-Guard-3-8B` / `PurpleLlama` | 许可是 Llama 3.1 Community License（**非 OSI 开源**，有商业限制与命名要求）；8B 对单机过重 | 规则 + 小分类器；如需 LLM 审核，单独评估许可 |
| `protectai/llm-guard` | 3,219 star、Apache-2.0，但**归档状态未在本次确认**；即便可用，其价值主要在云端/服务端 | 自建轻量输出审核中间件 |
| `ibm-granite/granite-guardian-3.0-8b` | 8B 模型，单机默认包体积/显存不可接受 | 仅在"高性能机器可选下载" |
| `opensbom-generator/spdx-sbom-generator` | 最近提交 2025-01-13，约 22 个月无更新；SPDX 生态演进快 | `cyclonedx-python` + `bom` |
| `LeapBeyond/scrubadub` | 最近提交 2023-09-01，约 3 年停滞 | `presidio` |
| `opendp/opendp` | 差分隐私库，成熟但对"单机个人助手"属于过度设计；且 LICENSE 本次未获取 | 不做差分隐私，先做最小化与本地化 |
| `microsoft/sbom-tool` | 需 .NET 运行时，为单机产品多一套运行时负担 | `cyclonedx-python` |
| `protobom` / `kubernetes-sigs/bom` | Go 生态，对 Python 主栈收益低 | 同上 |
| `sqlcipher` **直接替换主库** | 不是不能用，而是**改动面大**（驱动、迁移、备份格式、Windows 打包）；应作为中期项而非本周项 | 短期：字段级加密 + 明确声明 |

---

## 5. 未验证项清单

以下为本次**没有**确认的内容，任何引用都必须标注，不得写成结论。

### 5.1 许可证未获取（使用前必须补读 LICENSE）
- `CycloneDX/cyclonedx-node-npm`（B1）——raw LICENSE 未取到
- `aboutcode-org/license-expression`（C3）——raw LICENSE 与 PyPI JSON 均未取到
- `opendp/opendp`——LICENSE 与 PyPI JSON 均未取到
- `thu-coai/roberta-base-cold`（B9）——HF 模型卡未显示许可证字段
- `transparency-dev/merkle`——LICENSE 抓取为空（未列入候选，仅背景）

### 5.2 数值未获取
- 各候选的**测试覆盖率百分比**（仅确认存在 `tests/` 目录，未读取覆盖率报告）
- `toxicity` 类模型的**真实误杀率/漏检率**（本次未跑任何评估）
- 模型在 CPU 上的**实际延迟**（未实测）
- `pip-licenses` 对 SPDX 表达式的**实际归一化准确率**
- `sbomqs` 各 profile 的**具体评分权重**（仅知存在 profile）
- `nh3` 在本项目实际渲染片段上的**允许列表兼容性**（可能删掉控制台需要的标签）

### 5.3 未实际运行（受边界约束）
- 未运行 `cyclonedx-py`、`sbomqs`、`nh3`、`presidio`、`axe-core`、`osv-scanner`
- 未验证 `uv export` 是否默认带 hash（深评 1 的步 0 需实测）
- 未验证 SQLCipher 在 Windows 打包链路（wheel/原生库/迁移）
- 未验证 Tessera 单节点运营的资源占用与 witness 协调成本
- 未验证 `ExternalLogTransport` 实现到真实 Rekor 的端到端流程（现有代码只有抽象与本地参考日志）

### 5.4 检索范围限制
- 本次候选主要来自 GitHub（经 ungh.cc 镜像）；**GitLab、Codeberg、SourceForge 未系统检索**，"全球公开资源"覆盖不完整。
- GitHub REST API 触发限流，Star/时间可能滞后数分钟。
- OSV 查询使用 GET 列表接口，可能返回同名仿冒包（如 axe-core 返回 `axe-core-scanner`），已人工过滤；**发布前应对每条 GHSA/CVE 复检**。
- `cosign` 的多条历史公告仅记录标题级信息，未逐条确认影响本项目版本范围。

### 5.5 文档与流程未验证
- 是否存在对外遥测/外联（未抓包验证）
- `SECURITY.md` 与安全联系渠道是否存在（未检索）
- VPAT/ACR 官方模板（未获取，需从相应渠道获取后再填）
- 法务对"GPL 工具在 CI 中调用"的内部认定（本文只给工程判定，不构成法律意见）

---

## 6. 一页行动清单（按优先级）

| 优先级 | 动作 | 涉及文件 | 预计 |
|--------|------|----------|------|
| P0 | 删掉 CLI 假备份数据；导出走真实数据 | `src/cli/backup_commands.py` | 0.5 人日 |
| P0 | 门禁改为"读 SBOM + scope 策略"，未获取许可证默认阻断 | `scripts/verify_oss_licenses.py`、`oss-registry.yaml` | 1–2 人日 |
| P0 | CI 增加 SBOM 生成 + `sbomqs` 必需字段检查 | `.github/workflows/ci.yml` | 0.5 人日 |
| P0 | `pip-audit`（`\|\| true`）/ Trivy（`--exit-code 0`）改为 baseline 阻断 | `.github/workflows/ci.yml` | 0.5 人日 |
| P1 | 修 `src/knowledge/pii.py` 死代码 + 中文规则 + fail-closed | `src/knowledge/pii.py`、`security.py` | 1 人日 |
| P1 | 用 `nh3` 替换黑名单 XSS 消毒 | `src/security/input_sanitizer.py` | 0.5 人日 |
| P1 | 定义 `AuditBundle` + 打包独立验证器（L1） | `src/kernels/audit/`（新增打包层） | 2–3 人日 |
| P1 | 数据主体导出/删除接真实存储 | `src/compliance/gdpr.py` + API | 2–3 人日 |
| P2 | axe-core + 关键页面 WCAG 门禁 | `apps/console/console` | 1 人日 |
| P2 | 随包隐私声明 + 数据清单 | 新增 `PRIVACY.md` / `docs/privacy/` | 1 人日 |
| P2 | 本地内容分类器评估（先测误杀率再决定） | 评测脚本（不入库） | 3–5 人日 |
| P3 | 外部透明日志 transport（Tessera 或 Rekor） | `src/kernels/audit/external_anchor.py` | 5+ 人日 |
| P3 | SQLCipher / 加密备份后端 | 数据层 | 5+ 人日 |
