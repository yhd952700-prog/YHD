# 开源技术雷达 · 第 3 路：安全、合规与隔离

- 调研对象：鎏灏 LIUHAO AI OS（`D:\LiuHao-AI-OS`）
- 调研日期：2026-11-06（GitHub star / 最近提交均为当日快照）
- 方法：先读本仓库源码建立能力清单 → 在 GitHub 检索候选 → 逐个打开仓库源码目录、读取 LICENSE 文件原文、用 OSV 查询已公开漏洞
- 证据口径：只写实际读到的内容。查不到的写"未获取"，不做推断式断言

---

## 1. 该域能力现状表

> 判断标准：文件存在 + 有真实实现逻辑。仅"有文件"不算能力。

| # | 能力 | 现状 | 关键文件 | 实际缺口 |
|---|------|------|----------|----------|
| 1 | JWT 签发/校验、访问令牌与刷新令牌 | 有 | `src/security/jwt_handler.py` | — |
| 2 | JWT 吊销（持久化 + TTL） | 有 | `src/security/jwt_handler.py`（`RevocationStore`，SQLite `revoked_tokens` 表） | 无按用户批量吊销策略说明 |
| 3 | 会话管理、TOTP MFA、登录失败跟踪 | 有 | `src/security/authentication.py`（`SessionManager`/`TotpMfaProvider`/`LoginAttemptTracker`） | 无 WebAuthn、无 OIDC/SSO（单机产品可接受） |
| 4 | RBAC | 有 | `src/security/authorization.py`（`RbacManager`/`PermissionRegistry`）、`src/security/rbac.py`、`src/security/rbac_store.py` | — |
| 5 | ABAC（默认 DENY） | 有 | `src/security/abac.py`（`ABACEngine`）、`src/security/authorization.py`（`SimpleAbacPolicy`）、`src/gateway/policy.py` | 策略表达式为自研 DSL，无可视化编辑/策略测试器 |
| 6 | API Key（scope + 文件权限收紧） | 有 | `src/security/api_keys.py` | 无自动轮换调度验证 |
| 7 | 限流、输入清洗 | 有 | `src/security/rate_limit.py`、`src/security/input_sanitizer.py` | — |
| 8 | 对称/非对称加密、口令哈希 | 有 | `src/security/encryption.py`（AES-256-GCM、RSA、Argon2id、HKDF/PBKDF2、Fernet） | 未用 OS 凭据库保护主密钥 |
| 9 | 密钥管理/轮换 | 有（文件级） | `src/security/encryption.py`（`KeyManager`/`InMemoryKeyManager`）、`src/security/rotation.py` | 主密钥来源为环境变量/文件，主密钥本身无硬件/OS 保护 |
| 10 | 凭据加密存储 | 有 | `src/security/secret_store.py`（AES-256-GCM + Argon2id KDF，仅返回脱敏密钥） | 同上：根密钥保护弱于 OS Keychain |
| 11 | Vault Transit 可选后端 | 有（带离线回退） | `src/security/vault_crypto.py`、`src/integrations/vault/` | 依赖外部 Vault，默认不使用 |
| 12 | 安全态势自检 | 有 | `src/security/posture.py`、`src/security/c2_arming.py` | — |
| 13 | 哈希链审计（seq / prev_hash） | 有 | `src/kernels/audit/__init__.py`（`audit_events` + `chain_state` 锚 + `UNIQUE(seq)` + 写者租约） | 运行时链完整性在仓库内自述为 UNVERIFIED（HC-01） |
| 14 | 增量/分段验证（累计 link_hash + checkpoint） | 有 | `src/kernels/audit/verification.py` | checkpoint 依赖本仓库代码复算，无第三方独立实现 |
| 15 | Merkle 包含证明 / 签名树头（RFC 6962 数学） | 有 | `src/kernels/audit/external_anchor.py`、`src/kernels/audit/inclusion_proof.py` | `ExternalLogTransport` 是抽象占位（`NotImplementedError`），只有本地参考日志 |
| 16 | 透明日志（运营方独立签名、只追加） | 有（本地模型） | `src/kernels/audit/transparency_anchor.py` | 仍是本机自建日志，不是真实公共日志；"独立"仅密钥分离 |
| 17 | RFC 3161 可信时间戳 | 有 | `src/security/evidence/rfc3161_provider.py`、`rfc3161_cms.py` | 未配置公共 TSA 作为默认锚 |
| 18 | TPM 证明 | 有（可选） | `src/security/evidence/tpm_provider.py` | 未验证在目标 Windows 机型可用 |
| 19 | 根信任 / 双签名 / 故障隔离与恢复 | 有 | `src/kernels/audit/root_of_trust.py`、`dual_signature.py`、`fencing.py`、`recovery.py`、`durability.py` | 未接入实时审计主链路 |
| 20 | 独立验证工具 | 有（构建方解耦） | `scripts/independent_verification.py` | 自哈希 `record_hash` 明确"不防篡改"，且复用的是本仓库脚本 |
| 21 | 加密操作审计 | 有（内存） | `src/security/audit_logger.py` | 仅内存哈希链，进程重启即丢，明确声明非审计级证据 |
| 22 | 多后端沙箱 | 有 | `src/plugins/sandbox/backends/`（`subprocess_backend.py` / `docker_backend.py` / `gvisor.py` / `monty_backend.py` / `restricted_python_backend.py`） | Windows 桌面实际可用的"真隔离"后端缺位（见 §3.2） |
| 23 | 隔离保证裁决（fail-closed） | 有 | `src/plugins/sandbox/assurance.py`（`GENUINE_ISOLATION_BACKENDS`、HIGH/CRITICAL 硬拒） | 依赖后端可用性，Windows 上常退化为 subprocess |
| 24 | 许可证门禁 | 有（CI 中阻断） | `scripts/verify_oss_licenses.py` + `oss-registry.yaml`（allow / restricted / acknowledged 三档）；`ci.yml:375` 直接执行、无 `\|\| true` | 是否为分支保护的 required check 未验证 |
| 25 | 依赖漏洞扫描 | 有（非阻断） | `.github/workflows/ci.yml`（Trivy `--exit-code 0`、pip-audit `|| true`） | HIGH/CRITICAL 不阻断 |
| 26 | 静态安全扫描 | 有（仅 Semgrep 阻断） | CI：Bandit `--severity-level high` 仅产出 SARIF；Semgrep `.semgrep/liuhao.yml` 带 `--error` 才真正阻断（Semgrep 为 LGPL-2.1，作为独立工具使用，不入依赖） | Bandit findings 不阻断发布 |
| 27 | SBOM | 无 | 检索 `cyclonedx/syft/spdx/sbom` 仅命中 `oss-ecosystem/` 调研管线与 `plugins/license/license_plugin.py`，**无生成脚本** | 完全缺位 |
| 28 | 构建可复现 / 产物签名 / 溯源 | 无 | 检索 `cosign` / `sigstore` / `slsa` 无构建签名实现；`provenance` / `attest` 命中均为审计链或告警配置的无关注释 | 完全缺位 |
| 29 | 依赖锁定 | 有文件 | `uv.lock`（约 1.18 MB）、`requirements.txt` | CI 用 `pip install -e .`，未 `--require-hashes` / `uv sync --frozen`，锁文件未参与校验 |
| 30 | GDPR 数据主体权利流程 | 有（进程内） | `src/compliance/gdpr.py`（访问/更正/擦除/可携带/反对、同意、处理记录、DPIA、泄露通知） | 未见持久化与真实删除链路；`erase_data` 作用于内存登记数据 |
| 31 | 一键删除用户数据 | 部分 | `src/api/memory_api.py`（`DELETE /{memory_id}`） | 只覆盖 memory 单条，无"全量导出 + 全量清除"编排 |
| 32 | 提示注入/越狱检测 | 有（正则） | `src/ai/governance.py`（`ThreatDetector`、`PROMPT_INJECTION`、`jailbreak` 正则）、`src/security/input_sanitizer.py` | 规则型，易绕过；无模型侧检测 |
| 33 | 内容安全 / 输出审核 | 弱 | `src/evaluation/red_team.py`、`src/evaluation/metrics.py`（`toxicity_score` 关键词法） | 评测用途，未接入对话运行时；无输出审核/脱敏 |
| 34 | PII 检测 | 无 | — | 缺位（仅 `encryption.py` 有敏感字段加密，无识别） |

### 已在使用、无需重复引入

- `pypa/pip-audit`（Apache-2.0，CI 已用）、`pyca/cryptography`（Apache-2.0/BSD-3，核心依赖）、`RestrictedPython`（ZPL-2.1，已在白名单）、`PyJWT` 生态（MIT）。
- `sigstore-python`（Apache-2.0）未引入；`pyca/pynacl`（Apache-2.0）未引入，现有 `cryptography` 已覆盖同能力。

---

## 2. 候选清单（按"值得引入程度"排序）

排序口径：**A = 建议尽快落地**；**B = 值得评估、按场景接入**；**C = 只做参考/工具，不进产品**；**D = 明确不建议**。

| 排序 | 仓库 | Star | 最近提交 | 语言 | 许可证（以 LICENSE 原文为准） | 维护状态 | 结论 |
|------|------|------|----------|------|------------------------------|----------|------|
| 1 | [bytecodealliance/wasmtime](https://github.com/bytecodealliance/wasmtime) | 18,827 | 2026-11-05 | Rust | Apache-2.0 WITH LLVM-exception（LICENSE 含例外条款，Cargo.toml 同） | 活跃（v50.0.1，2026-10-29） | A |
| 2 | [CycloneDX/cyclonedx-python](https://github.com/CycloneDX/cyclonedx-python) | 399 | 2026-11-04 | Python | Apache-2.0 | 活跃 | A |
| 3 | [anchore/syft](https://github.com/anchore/syft) | 9,839 | 2026-11-04 | Go | Apache-2.0 | 活跃（v1.56.0，2026-11-02） | A（镜像/发布物） |
| 4 | [transparency-dev/merkle](https://github.com/transparency-dev/merkle) | 42 | 2026-10-19 | Go | Apache-2.0 | 活跃（tag 停在 v0.0.2 / 2023-05，主干仍在提交） | A（算法参考） |
| 5 | [sigstore/rekor](https://github.com/sigstore/rekor) | 1,231 | 2026-11-04 | Go | Apache-2.0 | 活跃（v1.5.5，2026-10-21） | B+（协议参考） |
| 6 | [jaraco/keyring](https://github.com/jaraco/keyring) | 1,516 | 2026-10-29 | Python | MIT（`pyproject.toml` 中 `license = "MIT"`；仓库根无独立 LICENSE 文件） | 维护中（v25.7.0，2025-11-16） | B+ |
| 7 | [data-privacy-stack/presidio](https://github.com/data-privacy-stack/presidio) | 11,742 | 2026-11-02 | Python | MIT | 活跃（2.2.366，2026-10-27） | B+ |
| 8 | [google/osv-scanner](https://github.com/google/osv-scanner) | 11,413 | 2026-11-05 | Go | Apache-2.0 | 活跃 | B |
| 9 | [apache/casbin-pycasbin](https://github.com/apache/casbin-pycasbin) | 1,779 | 2026-11-04 | Python | Apache-2.0 | 活跃 | B |
| 10 | [google/certificate-transparency-go](https://github.com/google/certificate-transparency-go) | 1,207 | 2026-11-04 | Go | BSD-3-Clause（LICENSE 为 Go Authors BSD 文本；API 报 NOASSERTION 系多许可证文件） | 活跃 | B（算法参考） |
| 11 | [theupdateframework/python-tuf](https://github.com/theupdateframework/python-tuf) | 1,731 | 2026-11-05 | Python | Apache-2.0（LICENSE 原文确认） | 活跃 | B（有自动更新才用） |
| 12 | [FiloSottile/age](https://github.com/FiloSottile/age) | 24,211 | 2026-08-29 | Go | BSD-3-Clause | 活跃 | B（备份加密） |
| 13 | [smallstep/certificates](https://github.com/smallstep/certificates) | 9,038 | 2026-11-03 | Go | Apache-2.0 | 活跃 | B（多设备 mTLS 才用） |
| 14 | [google/nsjail](https://github.com/google/nsjail) | 4,186 | 2026-10-28 | C++ | Apache-2.0 | 活跃 | B（仅 Linux 部署） |
| 15 | [in-toto/in-toto](https://github.com/in-toto/in-toto) | 1,055 | 2026-08-27 | Python | Apache-2.0（LICENSE 原文确认） | 活跃 | C+（发布溯源） |
| 16 | [anchore/grype](https://github.com/anchore/grype) | 13,188 | 2026-11-04 | Go | Apache-2.0 | 活跃 | C+（与 Trivy 重叠） |
| 17 | [google/trillian](https://github.com/google/trillian) | 3,761 | 2026-11-02 | Go | Apache-2.0 | 活跃 | C（架构参考） |
| 18 | [aboutcode-org/scancode-toolkit](https://github.com/aboutcode-org/scancode-toolkit) | 2,662 | 2026-11-02 | Python | Apache-2.0（develop 分支 `apache-2.0.LICENSE`；另含 CC-BY-4.0 数据许可） | 活跃 | C（本地 CLI 扫描） |
| 19 | [NVIDIA-NeMo/Guardrails](https://github.com/NVIDIA-NeMo/Guardrails) | 7,436 | 2026-11-05 | Python | Apache-2.0（LICENSE.md 含 SPDX 标识） | 活跃 | C（设计参考） |
| 20 | [oss-review-toolkit/ort](https://github.com/oss-review-toolkit/ort) | 2,117 | 2026-11-05 | Kotlin | Apache-2.0 | 活跃 | C（JVM，太重） |
| 21 | [getsops/sops](https://github.com/getsops/sops) | 23,624 | 2026-11-03 | Go | MPL-2.0 | 活跃 | C（文件级 copyleft，仅外部工具） |
| 22 | [authelia/authelia](https://github.com/authelia/authelia) | 29,563 | 2026-11-05 | Go | Apache-2.0 | 活跃 | D（服务型 IdP） |
| 23 | [ory/kratos](https://github.com/ory/kratos) | 13,948 | 2026-07-29 | Go | Apache-2.0 | 活跃但节奏放缓 | D（服务型 IdP） |
| 24 | [openfga/openfga](https://github.com/openfga/openfga) | 6,094 | 2026-11-05 | Go | Apache-2.0 | 活跃 | D（独立授权服务） |
| 25 | [guardrails-ai/guardrails](https://github.com/guardrails-ai/guardrails) | 7,585 | 2026-11-05 | Python | Apache-2.0 | 活跃（v0.11.0，2026-08-14） | D（见 §4） |
| 26 | [protectai/llm-guard](https://github.com/protectai/llm-guard) | 3,219 | 2026-07-08 | Python | MIT | **已归档**（API `archived=true`） | D |
| 27 | [protectai/rebuff](https://github.com/protectai/rebuff) | 1,531 | 2024-08-07 | TypeScript | Apache-2.0 | **已归档**（2024-08 后无提交） | D |
| 28 | [meta-llama/PurpleLlama](https://github.com/meta-llama/PurpleLlama) | 4,466 | 2026-10-30 | Python | **Llama 3.2 Community License**（非 OSI 许可） | 活跃 | D |
| 29 | [codenotary/immudb](https://github.com/codenotary/immudb) | 9,049 | 2026-10-21 | Go | **BUSL-1.1**（LICENSE 为 MariaDB Business Source License 文本） | 活跃 | D |
| 30 | [hashicorp/vault](https://github.com/hashicorp/vault) | 36,468 | 2026-11-05 | Go | **BUSL-1.1**（同上文本） | 活跃 | D |
| 31 | [opentimestamps/opentimestamps-client](https://github.com/opentimestamps/opentimestamps-client) | 433 | 2026-10-27 | Python | **LGPL-3.0**（LICENSE 原文确认） | 活跃 | D（协议可参考，客户端不可依赖） |
| 32 | [containers/bubblewrap](https://github.com/containers/bubblewrap) | 9,379 | 2026-10-14 | C | **LGPL-2.1**（COPYING 原文确认） | 活跃 | D（Linux 专属） |
| 33 | [google/gvisor](https://github.com/google/gvisor) | 19,876 | 2026-11-05 | Go | Apache-2.0 | 活跃（release-20261102.0） | D（Windows 不可用；Linux 才评估） |

**测试覆盖（实际获取情况）**：以上仓库均未在本次调研中运行测试或生成覆盖率。仅有可观察证据：`wasmtime` 根目录有 `fuzz/`、`supply-chain/`（cargo-vet 审计）、`deny.toml`；`transparency-dev/merkle` 的 `compact/` 有 `node_fuzz_test.go`，README 带 codecov 徽章（数值未获取）；`rekor` 有 `tests/`、`syft` 有 `test/`、`nsjail` 有 `tests/`、`gvisor` 有 `test/`、`trillian` 有 `integration/` 与 `testonly/`。无证据的一律记为"未获取"。

**已知安全公告**（OSV PyPI 查询，2026-11-06）：

| 包 | 结果 |
|----|------|
| `guardrails-ai` | 9 条，含 `GHSA-xmpw-2vmm-p4p6`（0.10.1 供应链投毒）、`MAL-2026-3607`（恶意包）、`GHSA-r6hf-g5x6-7pv9`（Hub 安装机制代码注入，影响 ≤0.6.7）、`GHSA-w392-75q8-vr67`（RCE，修复 0.5.10）、`GHSA-f8hx-f4xw-c646`（XXE，修复 0.5.0） |
| `keyring` | 6 条（`GHSA-8867-vpm3-g98g`、`GHSA-p3h7-3c45-qj4v`、`GHSA-p86x-652p-6385`、`PYSEC-2012-8`、`PYSEC-2019-181` 等，多为历史项） |
| `sigstore` | 4 条（`GHSA-hhfg-fwrw-87w7`、`GHSA-hm8f-75xx-w2vr`、`PYSEC-2026-1923/1924`） |
| `in-toto` | 3 条（`GHSA-jjgp-whrp-gq8m`、`GHSA-wc64-c5rv-32pf`、`PYSEC-2023-63`） |
| `presidio-analyzer` / `presidio-anonymizer` | 0 |
| `pycasbin` | 0 |
| `cyclonedx-python-lib` | 0 |
| `llm-guard` | 0 |
| `pip-audit` | 0 |

Go/Rust 仓库（rekor、syft、gvisor、trillian、wasmtime、nsjail）的 RustSec / GitHub Advisory **未查询**，列入 §5。

---

## 2.1 每个候选的九问速答

> 缩写：①已有同类？②现有不足 ③候选解决什么 ④值得借鉴的源码 ⑤可独立封装？⑥破坏接口？⑦许可证 ⑧风险/成本 ⑨测试怎么写

### 1) bytecodealliance/wasmtime（A）
1. **无**（`src/plugins/sandbox/backends/` 无 wasm 后端）
2. 桌面唯一"真隔离"选项缺失；`subprocess` 自述非隔离；`gvisor` 在 Windows 不可用
3. 提供跨平台、能力式（capability-based）隔离，带 `consume_fuel`/`epoch_interruption`/`max_wasm_stack` 资源上限（已在 `crates/wasmtime/src/config.rs` 核对到这三个 API）
4. `crates/wasmtime/src/config.rs`（资源限制）、`crates/wasi*`（能力白名单）、`fuzz/`（模糊测试思路）
5. **是**：实现 `SandboxBackendBase`，新增 `wasm` 后端
6. 不改：仅新增后端 + `SandboxBackendType` 枚举值
7. Apache-2.0 WITH LLVM-exception → 允许使用/修改/分发
8. 需把"要执行的代码"编译为 WASM；纯 Python 脚本需 CPython-WASM（重）。运行时应作为独立进程/独立 wheel
9. 无限循环→fuel 耗尽；超内存→实例化失败；文件读→WASI 未授权拒绝；网络→无 socket 能力；与现有 `assurance.py` 的 HIGH/CRITICAL 硬拒联动

### 2) CycloneDX/cyclonedx-python（A）
1. **无** SBOM
2. 发布物无法回答"这次发布里到底有哪些组件"
3. 从已安装环境/锁文件生成 CycloneDX JSON
4. `cyclonedx_py/_internal/environment.py`、`cyclonedx_py/_internal/requirements.py`（生成器入口）
5. **是**：CI 步骤 + 发布附件
6. 不改运行时
7. Apache-2.0
8. 工具版本需固定并校验哈希；SBOM 会暴露依赖清单（可接受）
9. SBOM 通过 CycloneDX schema 校验；组件数与 `uv.lock` 一致；许可证字段与 `oss-registry.yaml` 对齐

### 3) anchore/syft（A，镜像场景）
1. **无**
2. Docker 镜像层内的组件/许可证无清单
3. 目录、容器镜像 → SPDX/CycloneDX
4. `syft/`（cataloger 组织方式）、`schema/`
5. **是**：CI/发布脚本独立二进制
6. 不改
7. Apache-2.0
8. 二进制 ~几十 MB；需锁定版本 + 校验和
9. 对构建出的镜像跑 syft，断言关键组件存在；与 `pip-audit` 结果交叉比对

### 4) transparency-dev/merkle（A，算法参考）
1. **有**（`src/kernels/audit/inclusion_proof.py`、`external_anchor.py` 的 RFC 6962 实现）
2. 自研实现只用 SHA-256、无跨实现互验、无 tile/compact 证明格式
3. 提供经过广泛使用的 proof/compact/rfc6962 实现作为**对照物**
4. `proof/`、`rfc6962/`、`compact/`（证明与紧凑编码）
5. **是**（只读参考，重写为 Python 或作为测试向量来源）
6. 不改（若替换实现则需回归）
7. Apache-2.0
8. 只读参考几乎零成本；直接引入 Go 依赖不可行
9. 用相同叶子集生成根/证明，与 Python 实现逐字节比对；篡改任一叶子必须验签失败

### 5) sigstore/rekor（B+，协议参考）
1. **有**（`transparency_anchor.py` 的本地日志 + `inclusion_proof.py`）
2. 没有运营方独立的真实公共日志；`ExternalLogTransport` 仍是抽象
3. 提供"签名条目 + 日志包含证明 + 一致性证明"的成熟协议形态
4. `pkg/api`（`entries.go` 的 `signEntry`/`logEntryFromLeaf`：条目规范化与签名）、`internal/trillianclient`（日志存储客户端）、`tests/`
5. **可参考**；自托管不合适桌面（README 明确 v2 走 tile-based log + Trillian-Tessera，需要服务与对象存储）
6. 不改：实现 `ExternalLogTransport` 即可
7. Apache-2.0
8. 自托管成本高；公共实例的可用性/配额未验证
9. 模拟 transport 的签名/包含/一致性验证；负例：替换叶子、截断日志、伪造 SET

### 6) jaraco/keyring（B+）
1. **无** OS 凭据库（`src/security/secret_store.py` 用文件/环境变量主密钥）
2. 主密钥与密文同机同权限，窃取文件即可解密
3. 用 Windows Credential Manager / macOS Keychain / Secret Service 保存包裹密钥
4. `keyring/backends/`（`Windows.py`/`macOS`/`SecretService.py`/`fail.py`：后端选择与降级）、`keyring/core.py`
5. **是**：在 `secret_store.py` 加一个 KeyProvider
6. 不改：密文格式可保留，仅换主密钥来源
7. MIT（`pyproject.toml` 声明，仓库根无独立 LICENSE 文件）
8. 仓库根无独立 LICENSE 文件（仅 `pyproject.toml` 声明），需确认安装后元数据可被许可证门禁识别；历史 6 条 advisories；无后端时必须 fail-closed 而非降级明文
9. 写入→重启进程→读取；卸载后端→明确失败；断言不落明文

### 7) data-privacy-stack/presidio（B+）
1. **无** PII 识别（只有字段级加密）
2. 无法在写入记忆/知识库前识别并脱敏手机号、身份证、邮箱、卡号
3. 规则 + NLP 的 PII 识别与匿名化
4. `presidio-analyzer/presidio_analyzer/recognizer_registry/`、`presidio-anonymizer/`
5. **是**：独立预处理模块，挂在记忆写入/日志导出前
6. 不改存储结构（产物是脱敏后的文本）
7. MIT
8. 模型体积大（桌面打包变重）；中文识别需自定义 recognizer；建议只用规则型 recognizer
9. 样本集（手机号/身份证/邮箱/银行卡）召回与误报基线；脱敏幂等；原文不落库

### 8) google/osv-scanner（B）
1. 有 `pip-audit`（CI 非阻断）
2. 只覆盖 Python；无统一 OSV 口径
3. 单一二进制扫锁文件/镜像，输出 OSV
4. `internal/`（锁文件解析）、`pkg/`（输出格式）
5. **是**：CI 工具
6. 不改
7. Apache-2.0
8. 与 pip-audit/Trivy 功能重叠，避免三套并行
9. 与 pip-audit 结果比对；构造已知 CVE 的锁文件断言能命中

### 9) apache/casbin-pycasbin（B）
1. **有**（`abac.py` + `authorization.py` + `gateway/policy.py`）
2. 自研策略 DSL 无标准模型、无策略单测框架
3. RBAC/ABAC/带域 RBAC 的标准策略引擎
4. `casbin/model/`（策略模型解析）、`casbin/enforcer.py`
5. **是**（可只用于策略求值）
6. 会替换现有求值路径 → 有破坏风险；需双跑对账
7. Apache-2.0
8. 引入第二套权限语义 = 双写风险；当前规模不值得
9. 用现有策略集做等价性对账（新旧引擎决策一致率 100%）

### 10) google/certificate-transparency-go（B，算法参考）
1. **有**（`external_anchor.py` 的 RFC 6962）
2. 无 CT 式公开日志客户端、无一致性证明互验
3. 成熟 CT 客户端/日志实现
4. `client/`、`trillian/`、`x509/`
5. **是**（参考）
6. 不改
7. BSD-3-Clause（LICENSE 为 Go Authors BSD；仓库含多许可证文件故 API 报 NOASSERTION）
8. 无
9. 与 `merkle` 同：向量对照 + 篡改负例

### 11) theupdateframework/python-tuf（B）
1. **无** 更新元数据签名
2. 便携包/自动更新既无防降级，亦无防篡改
3. TUF 元数据（root/targets/snapshot/timestamp）
4. `tuf/ngclient/`（客户端更新流程）、`tuf/api/metadata.py`
5. **是**，但需要元数据仓库 + 密钥托管
6. 不改现有 API
7. Apache-2.0（LICENSE 原文确认）
8. 只有做自动更新才值得；否则用签名 + 校验和即可
9. 回滚攻击（旧版本元数据）必须拒绝；过期元数据必须拒绝；密钥轮换路径

### 12) FiloSottile/age（B）
1. **无** 备份/导出加密格式
2. 审计导出包、备份文件无独立加密容器
3. 现代、简单的文件加密
4. `internal/stream/`（`stream.go`：流式 AEAD）
5. **是**：只作为外部 CLI 或独立格式
6. 不改
7. BSD-3-Clause
8. Go 二进制分发；Python 侧需调用 CLI
9. 加解密往返；错误口令必须失败；大文件流式不 OOM

### 13) smallstep/certificates（B）
1. **无** 本地 CA / mTLS
2. 手机 PWA/多设备访问只能靠共享口令或裸 HTTPS
3. 内部 ACME CA + 设备证书
4. `authority/`（`provisioner/`、`authorize.go`、`policy.go`、`provisioners.go`）
5. **是**（独立服务）
6. 不改
7. Apache-2.0
8. 多一个常驻服务 + 证书生命周期；单机单用户场景收益低
9. 签发→吊销→续期；未授权 provisioner 必须拒绝

### 14) google/nsjail（B，仅 Linux）
1. 有 Docker / gVisor 后端
2. Linux 部署缺少轻量 namespace 隔离选项
3. 单进程级 namespace/seccomp 沙箱
4. `contain.cc`、`sandbox.cc`、`mnt_newapi.cc`、`net.cc`、`config.proto`（namespace/seccomp/挂载/网络）
5. **是**（外部二进制后端）
6. 新增后端，不改接口
7. Apache-2.0
8. 仅 Linux；需 root/CAP_SYS_ADMIN；Windows 无效
9. 网络默认关闭；文件系统只读；超时必须杀进程组

### 15) in-toto/in-toto（C+）
1. **无** 构建链路证明
2. 发布物无法证明"由谁在什么步骤构建"
3. 供应链步骤级 attestation
4. `in_toto/`（layout/link 元数据）
5. **是**（发布流程工具）
6. 不改
7. Apache-2.0（LICENSE 原文确认）
8. 单机产品收益低；3 条历史 advisories
9. 生成 link 元数据并验签；篡改步骤必须失败

### 16) anchore/grype（C+）
1. 有 Trivy + pip-audit
2. 无镜像层 SBOM 驱动的漏洞匹配
3. SBOM → 漏洞匹配
4. `grype/`（匹配器）
5. **是**（CI）
6. 不改
7. Apache-2.0
8. 与 Trivy 重叠
9. 用 syft SBOM 作为输入，断言已知 CVE 命中

### 17) google/trillian（C）
1. **有**（本地透明日志模型）
2. 无生产级 Merkle 日志服务
3. 通用 Merkle 日志后端
4. `server/`、`storage/`、`merkle/`
5. 结论：**不封装**（服务化成本 > 收益）
6. —
7. Apache-2.0
8. 需要 MySQL/存储集群
9. 仅作架构参考，不写集成测试

### 18) aboutcode-org/scancode-toolkit（C）
1. 有 `verify_oss_licenses.py`（基于已安装元数据）
2. 无法扫描源码内嵌许可证/复制代码
3. 文件级许可证与版权扫描
4. `src/scancode/`（许可证检测器）、`src/licensedcode/`
5. **是**（本地 CLI，不进运行时）
6. 不改
7. Apache-2.0（`develop` 分支 `apache-2.0.LICENSE`；数据另含 CC-BY-4.0）
8. 依赖重、首次索引慢；只作为发布前人工复核工具
9. 对 vendor/ 第三方目录抽样扫描，断言已知 GPL 片段命中

### 19) NVIDIA-NeMo/Guardrails（C）
1. **有** `ai/governance.py` 正则检测
2. 规则弱、无对话级策略编排
3. 声明式护栏 + 动作（拒绝/改写）
4. `nemoguardrails/rails/`（rail 执行）、`nemoguardrails/library/`
5. **能**，但依赖 LLM 二次调用与大量依赖
6. 会插入对话主链路 → 延迟与失败面扩大
7. Apache-2.0（LICENSE.md 含 SPDX 头）
8. 桌面产品过重；只借鉴"输入/输出 rail + 动作"的分层设计
9. 若引入：护栏触发→拒绝；护栏超时→fail-closed

### 20) oss-review-toolkit/ort（C）
1. 有 `verify_oss_licenses.py`
2. 无完整依赖树许可证义务分析（NOTICE/条款冲突）
3. 依赖树级许可证合规报告
4. `analyzer/`、`reporter/`
5. 结论：**不引入**（Kotlin/JVM + Gradle，体积与运维不匹配）
6. —
7. Apache-2.0
8. 高
9. 不写

### 21) getsops/sops（C）
1. 有 `secret_store.py`
2. 配置文件明文入 Git 的风险
3. 文件级加密（KMS/age/PGP）
4. `stores/`、`keyservice/`、`decrypt/`、`kms/`（密钥源与加解密抽象）
5. **是**（部署期外部工具）
6. 不改
7. MPL-2.0（文件级 copyleft：修改其文件须公开该文件）
8. 单机桌面凭据在 DB 中，Git 加密收益低
9. 仅部署仓库使用；断言未授权密钥无法解密

### 22) authelia/authelia（D）
1. **有**完整本地认证/授权
2. 缺 SSO/门户
3. 反向代理前的统一认证门户
4. `internal/`（会话与策略）
5. 不建议
6. 会替换认证入口（破坏性最大）
7. Apache-2.0
8. 多一个服务 + Redis + 配置复杂度；桌面无收益
9. 不写

### 23) ory/kratos（D）
1. **有**本地认证
2. 缺标准身份 API
3. 独立身份服务
4. `selfservice/`、`identity/`
5. 不建议
6. 替换认证体系
7. Apache-2.0
8. 服务 + 数据库 + 迁移成本
9. 不写

### 24) openfga/openfga（D）
1. **有** RBAC+ABAC
2. 缺关系型（ReBAC）授权
3. Zanzibar 风格关系授权
4. `pkg/server/`、`internal/`
5. 不建议
6. 需替换授权数据模型
7. Apache-2.0
8. 独立服务 + 存储；单用户桌面模型简单
9. 不写

### 25) guardrails-ai/guardrails（D）
1. 有正则检测
2. 无输出结构化校验
3. 结构化输出校验 + 护栏
4. `guardrails/`（validator 注册）
5. 可，但**不建议**
6. 插入主链路
7. Apache-2.0
8. **0.10.1 被投毒（GHSA-xmpw-2vmm-p4p6 / MAL-2026-3607）**，另有 RCE/XXE/代码注入历史；供应链信任已受损
9. 若未来重启评估：锁定版本 + 哈希 + 独立 venv 隔离

### 26) protectai/llm-guard（D）
1. 有正则检测
2. 检测面窄
3. 输入/输出扫描器集合
4. `llm_guard/input_scanners/`、`output_scanners/`
5. 可，但 **已归档**（`archived=true`）
6. 插入主链路
7. MIT
8. 无维护；依赖旧
9. 不写

### 27) protectai/rebuff（D）
1. 有正则检测
2. 无启发式注入检测
3. 注入检测（启发式 + 可选 LLM）
4. `server/`、`python-sdk/`（检测服务与 SDK）
5. 不建议
6. 插入主链路
7. Apache-2.0
8. 归档于 2024-08；TypeScript 服务 + 可选 LLM
9. 不写

### 28) meta-llama/PurpleLlama（D）
1. 有正则检测
2. 无模型级安全分类
3. Llama Guard 等安全模型
4. `Llama-Guard2/`、`LlamaFirewall/`、`CybersecurityBenchmarks/`
5. 不建议
6. —
7. **Llama 3.2 Community License（非 OSI）**，有使用限制
8. 权重体积 + GPU；许可证与"开源"定位冲突
9. 不写

### 29) codenotary/immudb（D）
1. 有 SQLite 哈希链
2. 无内建不可变 DB
3. 不可变 KV + Merkle 证明
4. `pkg/`、`embedded/`
5. 不建议
6. 会替换存储层
7. **BUSL-1.1**（LICENSE 为 MariaDB BSL 文本，非 OSI）
8. 许可与运维双重风险
9. 不写

### 30) hashicorp/vault（D）
1. 有 `vault_crypto.py`（可选）
2. 无完整密钥生命周期服务
3. 密钥管理服务
4. `vault/`（transit/seal）
5. 不建议自托管
6. 替换密钥层
7. **BUSL-1.1**
8. 服务 + 解封 + 高可用；桌面不可承受
9. 不写

### 31) opentimestamps/opentimestamps-client（D，协议可参考）
1. **无** 比特币锚定
2. 缺"无需信任任何运营方"的锚
3. 哈希提交到公共 calendar → 升级为比特币区块证明
4. `otsclient/`（提交/升级流程，**只读参考，不复制**）
5. 客户端本身不可依赖
6. —
7. **LGPL-3.0** → 与 `oss-registry.yaml` 的 `restricted_licenses` 冲突；Python 直接 import 会触发重链接义务
8. 公共 calendar 可用性未验证；确认延迟（数小时）
9. 若实现：自研最小提交器 + 已验证 proof 的离线校验；负例：伪造 proof 必须失败

### 32) containers/bubblewrap（D）
1. 有 Docker/gVisor
2. Linux 轻量隔离
3. namespace 沙箱
4. `bubblewrap.c`
5. 不建议
6. —
7. **LGPL-2.1**（COPYING 原文确认）
8. 仅 Linux；LGPL 分发义务
9. 不写

### 33) google/gvisor（D，桌面不可用）
1. **有**（`src/plugins/sandbox/backends/gvisor.py`）
2. `_build_runsc_cmd` 生成的命令与 runsc CLI 语义不符（见 §3.2），且 gVisor 需 Linux 5.6+
3. 用户态内核隔离
4. `runsc/cmd/run.go`（`run` 读取 OCI bundle）、`runsc/cmd/do.go`（`do` 直接执行命令）
5. 不建议在桌面修
6. 不改
7. Apache-2.0
8. Windows 永远不可用；Linux 下才值得修
9. 仅在 Linux CI 上跑：`runsc do` 执行命令、无网络、超时杀进程

---

## 3. 最值得的深评（Top 5）

### 3.1 sigstore/rekor + transparency-dev/merkle —— 审计链"第三方可验证"的关键拼图

**为什么是它**：鎏灏的 `external_anchor.py` 已经把 RFC 6962 的树、包含证明、签名树头（STH）验签写完了，缺的不是数学，而是**运营方之外的锚**和**不依赖本仓库代码的验证器**。rekor 提供"签名条目 + SET + 包含/一致性证明"的成熟协议形态，`transparency-dev/merkle` 提供可作对照的 proof 实现。

**值得借鉴的具体源码**
- `rekor/pkg/api/entries.go`：`signEntry()` 对**规范化后的 `LogEntryAnon`** 签名（即 SET / Signed Entry Timestamp 语义），把"条目已入日志"变成可离线验证的签名事实；`logEntryFromLeaf()` 展示了从日志叶子重建可验证条目（含 `LogIndex`、`IntegratedTime`、签名根）的完整流程。
- `rekor/internal/trillianclient`：日志存储客户端，对应 `ExternalLogTransport` 的"提交 + 取证明"职责边界。
- `rekor/tests/`：测试组织方式（目录结构与用例分层可作为参考；具体用例未逐条阅读）。
- `merkle/proof`、`merkle/rfc6962`、`merkle/compact`：证明生成与"紧凑范围"编码，可用于给导出包瘦身。

**能否独立封装**：可以。`src/kernels/audit/external_anchor.py` 的 `ExternalLogTransport` 已经是抽象边界，新增一个真实 transport 不需要改调用方。验证器应做成**单文件、零仓库依赖**（只依赖 `cryptography`），输入是导出包 + 公钥指纹。

**许可证**：两者均 Apache-2.0；只读参考 + 自写实现无义务；若直接引入 Go 代码则不适合 Python 运行时。

**风险与成本**：自托管 rekor v2 需要服务进程与对象存储（README 明确走 tile-based log + Trillian-Tessera），对单机桌面过重。**结论：只做协议参考；产品侧用 RFC 3161 公共 TSA 做第一层外部锚，公共 rekor 实例作为可选第二层。**

**测试怎么写**
- 向量测试：固定叶子集 → 根、包含证明、一致性证明逐字节对照 `merkle` 的输出。
- 负例：替换叶子 / 删除中间条目 / 截断日志 / 伪造 SET / 错误公钥 → 必须 fail-closed。
- 跨实现互验：Go 侧生成证明，Python 侧验证；反向再验一次。

### 3.2 bytecodealliance/wasmtime —— 桌面端唯一现实可行的"真隔离"

**先给结论：gVisor 这条路在 Windows 桌面是死的。**

已核对的源码事实（`google/gvisor`）：
- `runsc/cmd/run.go` 的 `Run` 从 `bundleDir` 读取 OCI spec（`specutils.ReadSpec`），即 `runsc run <container-id>` 期望的是**bundle 目录**，不是 `-- cmd`；
- 直接执行命令的是 `runsc/cmd/do.go`：`do [flags] <cmd> - runs a command`；
- `network` / `rootless` 在 `run.go` 中来自全局 `conf.Network` / `conf.Rootless`，不是 `run` 子命令参数；`run.go` 中未出现 `rlimit` 相关 flag。
- gVisor README 要求 **Linux 5.6+**。

对照本仓库 `src/plugins/sandbox/backends/gvisor.py` 的 `_build_runsc_cmd`：它把 `--platform`、`--network=none`、`--rootless`、`--rlimit=...` 放在 `run` 之后，并以 `-- <command>` 结尾 —— **子命令选错、全局 flag 位置错、命令传参方式错**。因此"大概率跑不通"可以升级为"确定跑不通"，且在 Windows 上 `is_available()` 恒为 False。

**关键连带风险**：`src/plugins/sandbox/assurance.py` 把 `GVISOR`/`DOCKER` 视为"真隔离"，而 `manager.py` 的默认顺序是 `GVISOR > SUBPROCESS`。在 Windows 桌面上，两者都不可用时，HIGH/CRITICAL 只能 fail-closed（拒绝执行）或按 `LIUHAO_SANDBOX_ENFORCE` 降级到 `subprocess`（已自述"不是隔离"）。也就是说：**桌面端的不可信代码，目前没有真正的隔离后端**。

**wasmtime 能补上什么**
- 跨平台（Windows/macOS/Linux 同一套语义）；WASI 能力白名单（不给 socket/文件就不给）；
- `crates/wasmtime/src/config.rs` 中确有 `consume_fuel(bool)`、`epoch_interruption(bool)`、`max_wasm_stack(usize)`，可用于 CPU/超时/栈上限；
- 有 `fuzz/` 与安全策略文档。

**代价**
- 纯 Python 脚本无法直接跑；需要把可执行单元编译成 WASM 模块（或接受 CPython-WASM 的重量）。
- 定位应写清楚：**"受限脚本/计算"用 WASM，"Python 表达式"继续用 RestrictedPython，"需要完整 Python 生态"才落到 Docker/Linux 服务器。**

**集成测试与回归**
- 死循环 → fuel 耗尽并终止（不挂死进程）；
- 大内存分配 → 实例化失败；
- 未授权文件路径 → WASI 拒绝；
- 无 socket 能力 → 连接失败；
- `assurance.py` 在 HIGH 档：WASM 可用时不降级，不可用时必须抛 `SandboxIsolationUnavailable`。

**沙箱自研 vs 接入的代价对照**

| 方案 | 覆盖平台 | 隔离强度 | 成本 | 结论 |
|------|----------|----------|------|------|
| 继续自研 subprocess + Job Object | Windows | 弱（资源限制，非安全边界） | 中 | 只能作为兜底并明确标注 |
| 修 gVisor | 仅 Linux | 强 | 低但**桌面无收益** | 只在 Linux CI/服务器修 |
| 接 Docker | Windows 需 Docker Desktop | 强 | 中高（用户装 Docker） | 服务器/高级用户可选 |
| 接 WASM（wasmtime） | 全平台 | 强（能力式） | 中 | **桌面首选** |
| nsjail/bubblewrap | 仅 Linux | 强 | 低 | Linux 部署可选 |

### 3.3 CycloneDX/cyclonedx-python + anchore/syft —— 供应链最小可落地

**现状**：`scripts/verify_oss_licenses.py` 已经能按 `oss-registry.yaml` 的 allow/restricted/acknowledged 三档扫描已安装发行版并阻断；CI 有 Trivy（`--exit-code 0`）与 pip-audit（`|| true`）。缺的是 **SBOM** 与 **锁文件参与校验**。

**最小落地四步（成本从低到高）**
1. **SBOM 作为发布产物**：CI 里 `cyclonedx-py environment -o sbom.cdx.json`；镜像场景用 `syft` 输出 CycloneDX。挂到 release artifact，不进入运行时依赖。
2. **许可证门禁升级为阻断**：`verify_oss_licenses.py` 已有退出码语义（受限且无 `acknowledged` 直接失败），确认它在 CI 中是 required job；`restricted_licenses` 已含 `AGPL-3.0/GPL-3.0/LGPL-3.0/SSPL/Elastic-2.0/BSL`。
3. **依赖锁定真正生效**：`uv.lock` 已有（约 1.18 MB），但 CI 用 `pip install -e .`。改为 `uv sync --frozen` 或 `pip install --require-hashes -r requirements.txt`，让锁文件成为唯一入口。
4. **漏洞扫描分级阻断**：pip-audit 对 HIGH/CRITICAL 阻断，LOW/MEDIUM 只报告；Trivy 同理（现在 `--exit-code 0` 等于只采集）。

**不建议现在做**：SLSA 等级认证、in-toto 全链路、TUF 更新仓库 —— 单机产品没有独立的构建平台与更新服务，投入产出比不成立。替代做法：发布说明里写死 `commit SHA + SBOM 哈希 + 安装包 SHA-256 + Ed25519 分离签名`，达到"可核验"即可。

**测试**
- SBOM schema 校验（CycloneDX JSON schema）；
- 组件集合与 `uv.lock` 对齐（数量 + 名称集合）；
- 人为在 `oss-registry.yaml` 放一个 GPL 组件 → 门禁必须失败；
- 人为改坏 `uv.lock` 哈希 → 安装必须失败。

### 3.4 data-privacy-stack/presidio —— 隐私与数据治理的最小增量

**现状**：`src/compliance/gdpr.py` 有完整的"数据主体权利"状态机（访问/更正/擦除/可携带/反对、同意、处理记录、DPIA、泄露通知），但看源码是**进程内实现**（各类 `__init__` 里维护字典/列表，未见持久化导入）；`src/api/memory_api.py` 只有单条 `DELETE /{memory_id}`，没有"全量导出 + 全量清除"的编排。PII 识别完全缺位。

**候选价值**：MIT；可完全离线；`presidio-analyzer` 的 recognizer 注册表可只启用规则型识别（手机号、身份证、邮箱、银行卡、IP）。

**代价与取舍**
- 全量 NLP 模型会让桌面安装包显著变重，中文识别效果也需自建 recognizer；
- **建议只引入规则型 recognizer + 自定义中文正则**，把 NLP 模型留作可选。

**落地位置**：记忆写入前（`memory_api` 的 `POST`/`batch`）、日志导出前、审计导出包生成前 → 统一一个 `redact(text)` 钩子。

**测试**
- 正样本：手机号/身份证/邮箱/银行卡/IP 全部命中；
- 负样本：订单号、时间戳不误伤；
- 幂等：二次脱敏结果不变；
- 原文不落库（查询 DB 断言）。

### 3.5 jaraco/keyring —— 把"主密钥"从文件搬到 OS 凭据库

**现状**：`src/security/secret_store.py` 的 AES-256-GCM + Argon2id KDF 是扎实的，但主密钥来自环境变量/文件（`src/security/encryption.py` 的 `KeyManager`）。攻击面很直接：**拿到数据目录即可解密 API Key**。

**候选价值**：用 Windows Credential Manager / macOS Keychain / Linux Secret Service 保存"包裹密钥"，密文格式不变，只换密钥来源。这是单机桌面产品最划算的一步安全提升。

**必须先解决的阻塞**
- 仓库根**没有独立 LICENSE 文件**，许可证只在 `pyproject.toml` 里以 `license = "MIT"` 声明；`verify_oss_licenses.py` 读取的是已安装发行版元数据，因此接线前需确认该包安装后元数据能正确带出 MIT，否则会被判为"无法识别"并要求 `acknowledged`。
- OSV 有 6 条历史 advisories（需逐条确认是否影响 25.7.0）。
- 维护节奏：最新版本 25.7.0 发布于 2025-11-16，仓库仍有提交。

**实现约束**
- 无后端时必须 **fail-closed**（拒绝启动并给出明确错误），绝不能降级为明文文件；
- 保留现有密文格式与解密路径（只替换 KEK 获取方式），避免数据迁移；
- 提供 `keyring` 不可用时的显式 CLI 逃生阀（例如导出恢复口令）。

**测试**
- 写入 → 结束进程 → 新进程读取成功；
- 模拟无后端 → 启动失败且不落明文；
- 迁移测试：旧文件密钥加密的数据，用新 KEK 包裹后可解密。

---

## 3.6 本路核心判断（汇总）

### (1) 审计链如何被第三方独立验证

**现有资产已经足够支撑 80%，缺的是"最后一个外部锚 + 一个独立验证器"。**

按成本从低到高：

1. **冻结导出格式（必须）**：定义 `Audit Evidence Bundle v1` —— 事件（seq / event_hash / link_hash / prev / payload 摘要）、`chain_state` 链头、checkpoint、STH、RFC 3161 token、锚公钥指纹、算法标识、导出时间。现有 `verification.py` 的 `link_hash` + checkpoint 正好适配。
2. **独立验证器（必须）**：单文件 Python（只依赖 `cryptography`），不 import 本仓库任何模块，重新计算链、校验 checkpoint、验 STH、验 TSA token，输出 PASS/FAIL + 根因。当前 `scripts/independent_verification.py` 是"构建方解耦"，不是"第三方独立"，且自述 `record_hash` 不防篡改。
3. **第一层外部锚（首选）**：RFC 3161 公共 TSA。`src/security/evidence/rfc3161_provider.py` + `rfc3161_cms.py` 已实现 CMS 验签，接一个公共 TSA 即得到"运营方之外的时间证明"，离线可验证，成本最低。
4. **第二层外部锚（可选）**：Sigstore Rekor 公共实例或自托管的只追加日志（协议参考 `rekor/pkg/api`、`pkg/log`）。自托管不适合桌面。
5. **信任发布（必须配套）**：锚公钥指纹写进 release notes / 官网；日志 URL 长期稳定；提供 RFC 6962 **一致性证明**证明"只追加"（`inclusion_proof.py` 已有基础）。
6. **不要做**：自建 Trillian/Rekor 集群；把"本地独立签名日志"（`transparency_anchor.py`）对外宣称"第三方验证"——源码已诚实标注它只是密钥分离的本地日志。

### (2) 沙箱层：继续自研还是接成熟方案

**混合，但要先砍掉桌面上的 gVisor 幻想。**

- 桌面（Windows 为主）：`RestrictedPython`（表达式级）+ **WASM（wasmtime）**（不可信脚本）+ `subprocess` 仅作兜底且明确标注"非隔离"。补充 Win32 Job Object 的资源限制（杀进程组、内存/CPU 上限），这属于自研但成本可控。
- Linux/服务器：Docker 后端保留；可选 `nsjail`/`gVisor` 强化。
- **不要修 gVisor 的桌面路径**：Windows 永远不可用；Linux 才需要，且修法明确（`runsc do` + 全局 flag 前置，或直接构造 OCI bundle）。
- 兼容性设计已具备：`SandboxBackendBase` + `assurance.py` 是现成的扩展点，新增 `wasm` 后端不改调用方。

### (3) 供应链安全最小可落地

见 §3.3 四步：**SBOM 产物 → 许可证门禁阻断 → 锁文件真正生效 → 漏洞扫描分级阻断**，外加"安装包 SHA-256 + 签名 + 发布说明写死 commit/SBOM 哈希"。TUF/SLSA/in-toto 暂缓。

### (4) 明确不适合单机桌面引入

见 §4。核心判据只有三条：**必须常驻服务 → 不做；许可证非 OSI/有传染性 → 不做；只在 Linux 生效 → 桌面上不做。**

---

## 4. 明确不建议引入的，及原因

| 候选 | 原因 | 替代方案 |
|------|------|----------|
| hashicorp/vault | **BUSL-1.1**（LICENSE 原文为 MariaDB BSL），非 OSI；需常驻服务 + 解封流程 + 高可用 | 继续用 `secret_store.py` + OS Keychain（keyring） |
| codenotary/immudb | **BUSL-1.1**；需替换整个存储层 | 现有 SQLite 哈希链 + 外部锚 |
| authelia / ory/kratos | 服务型 IdP（独立进程 + DB/Redis），单机单用户无收益；替换认证入口属破坏性改动 | 现有 JWT + TOTP + RBAC/ABAC |
| openfga/openfga | 独立授权服务 + 存储；单用户桌面的授权模型远未到 ReBAC 复杂度 | 现有 `abac.py` + `authorization.py` |
| google/trillian | 生产级 Merkle 日志服务，需 MySQL/存储集群 | 只做架构参考；用 RFC 3161 + 可选公共 rekor |
| oss-review-toolkit/ort | Kotlin/JVM + Gradle，体积与运维成本远超收益 | `verify_oss_licenses.py` + 可选 ScanCode CLI |
| aboutcode scancode 作为运行时依赖 | 依赖重、首次索引慢；应只作为发布前 CLI | 同上 |
| anchore/grype 与 Trivy 并存 | 功能重叠，三套扫描器口径不一 | 保留 Trivy + pip-audit，按需加 osv-scanner |
| NVIDIA NeMo Guardrails / guardrails-ai / llm-guard | 插入对话主链路带来延迟与失败面；guardrails-ai 有 0.10.1 **投毒**与 RCE/XXE 历史；llm-guard 已归档 | 保留 `ai/governance.py` 轻量检测 + 输出脱敏 |
| meta-llama/PurpleLlama | **Llama 3.2 Community License（非 OSI）**；权重体积 + GPU | 规则/小模型方案 |
| protectai/rebuff | 2024-08 后归档；TypeScript 服务 + 可选 LLM | 同上 |
| opentimestamps-client | **LGPL-3.0**，与 `oss-registry.yaml` 的 `restricted_licenses` 冲突；Python 直接 import 触发重链接义务 | 只参考其协议；优先 RFC 3161 |
| containers/bubblewrap | **LGPL-2.1** + 仅 Linux | Docker/nsjail（Linux）；WASM（桌面） |
| google/gvisor（桌面路径） | README 要求 Linux 5.6+；Windows 永不满足；现有命令构造错误（`run` 需 bundle，命令应走 `do`） | WASM（桌面）；Linux 下再修 |
| google/nsjail / firejail（桌面） | 仅 Linux | 同上 |
| sops | MPL-2.0 但收益低（桌面凭据在 DB，非 Git 配置文件） | 部署期可选 |
| hashicorp/vault 自托管（即使忽略许可） | 单机产品无高可用/解封运维能力 | OS Keychain |

---

## 5. 未验证项清单

1. **未运行任何候选的测试或构建**；未做完整源码审计，只读了 README / 目录 / LICENSE / 关键入口文件。
2. **未查询 Go/Rust 仓库的安全公告**：rekor、syft、grype、gvisor、trillian、wasmtime、nsjail、bubblewrap、smallstep、sops、age、authelia、kratos、openfga 的 GitHub Advisory / RustSec / CVE **未获取**。
3. **jaraco/keyring 的许可证只来自 `pyproject.toml` 的 `license = "MIT"`**；仓库根无独立 LICENSE 文件，未核对安装后元数据是否带出该字段。
4. **ScanCode 的代码许可证已读原文**（`develop` 分支 `apache-2.0.LICENSE` = Apache-2.0）；但同目录另有 `cc-by-4.0.LICENSE`（数据/规则库），两者的组合义务未逐条评估；GitHub API 仍报 NOASSERTION。
5. **gVisor 的 `_build_runsc_cmd` 结论基于源码语义**：已确认 `run` 读 bundle、`do` 执行命令、`network/rootless` 为全局 config、`run.go` 无 `rlimit` flag；但**未在真机运行 runsc 验证**（本机无 Linux gVisor 环境）。
6. **rekor 公共实例的可用性、配额、是否免费未验证**；rekor v2 的自托管依赖（对象存储/Trillian-Tessera）只读 README 描述。
7. **OpenTimestamps 公共 calendar 的可用性与确认延迟未验证**。
8. **wasmtime 的 Python 绑定在 Windows 的 wheel 可用性、版本对应关系未验证**（本次只核对了 Rust 侧 API 与许可证）。
9. **keyring 在 Windows 上实际落到 Credential Manager 的行为未验证**。
10. **presidio 的中文识别效果、模型体积、打包影响未测量**。
11. **未实际生成 SBOM**，也未验证 `cyclonedx-py` 与当前 `uv.lock` 的兼容性。
12. **guardrails-ai 0.11.0 是否已完全清除 0.10.1 的投毒影响未验证**（OSV 记录指向 0.10.1；其余 RCE/XXE 修复版本为 0.5.0/0.5.10/0.6.7）。
13. **immudb / vault 的 BUSL "Change Date" 与 Four-Year 变更条款未逐条阅读**（只确认 LICENSE 为 BSL 文本）。
14. **未验证 `uv.lock` 是否含完整哈希**（文件存在，未解析内容）。
15. **Star / 最近提交为 2026-11-06 单次快照**，会随时间变化。
16. **`verify_oss_licenses.py` 在 CI 中不带 `|| true`（`ci.yml:375`），脚本失败即 job 失败**；但该 job 是否为分支保护的 required check **未验证**（仓库内无相关信息）。
17. **`src/compliance/gdpr.py` 的持久化情况仅由源码结构判断**（未见 DB 导入），未查 API 是否有落库封装。
