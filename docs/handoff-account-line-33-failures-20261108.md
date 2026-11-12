# 交接单：`tests/gateway` 33 个失败的成因（给账号线）

- 交接时间：2026-11-08
- 提交方：启动/体积审计线
- 接收方：账号线（`src/gateway/accounts.py` 的未提交改动）
- 前置声明：**本单没有改你们的任何代码**，只做取证。下面每条都可在本机复跑。

## 0. 结论页（一页版 · 谁改、改哪、怎么验）

**根因归档（已钉死，不再重新论证）**：这 33 个失败不是产品回归。测试把
`boot_report.probe_writable()` 的探测目录指到了**仓库工作树内部**，探针文件写得进去、
却删不掉——删除被 `tests/conftest.py` 的破坏性操作守卫拒绝；`probe_writable` 把
"删除被拒"如实报成"目录不可写"，于是断言 `data_writable=True` 的用例全红。

**决定**：产品侧不为"读不了仓库"放宽容错。`probe_writable` 的判据就是"真的能删"，
放宽会同时放过 Windows 上"文件被占用/只读"这类真实故障。**修在测试侧。**

| 谁 | 改哪个文件 | 具体改法 | 改完怎么验 |
| --- | --- | --- | --- |
| 测试侧（账号线可代交） | 33 个断言 `data_writable=True` 的用例（完整名单在 `D:\_audit\gw-failures-20261108.log`） | 把 `data_dir`/`log_dir` 换成 `tmp_path` 派生目录；**不要**给 `probe_writable` 加容错 | `pytest tests/gateway -q` → `0 failed`；`pytest tests/test_boot_report.py -q` 全绿；`startup_baseline.py` 的 `/v1/ready` 里 `data_writable` 为 true |
| 测试侧（确实要在仓库内造探针的门禁用例） | 同上 | 改用 `allow_repo_deletion` fixture 显式登记（一次一用、必须写理由），不要扩大守卫规则 | 该用例单跑通过，且日志里不再出现 `护栏拒绝` |
| 产品侧 | `src/observability/boot_report.py` | **不改**（`probe_writable` 保持"真的能删才算可写"） | 后续任何"删除失败也当可写"的补丁一律拒绝 |
| conftest 守卫 | `tests/conftest.py#L905-L1039` | **本单不动**。该文件正被另一条线改：`PERSONAL_CONTEXT_DB_PATH` 隔离已落地在 `#L254`（`mem_dir / "personal_context.db"`），守卫段与它无关。若要在守卫上加白名单，等那批落地后另开单，避免混提交 | `git status tests/conftest.py` 确认与本单改动不混在一起 |

**归零判据**：`pytest tests/gateway -q` 从 `33 failed` 变为 `0 failed`；
`tests/test_boot_report.py` 全绿；`gw-failures-20261108.log` 里的名单逐条消失。

> 另外：之前全量复核里唯一剩下的 `test_data_portability::test_memory_export_…`
> 已被另一条线修掉（根因是仓库根遗留 `personal_context.db` 被老画像迁移读入，
> 已在 `conftest.py` 补 `PERSONAL_CONTEXT_DB_PATH` 隔离）。本机单跑已 `1 passed in 5.61s`
> （2026-11-08 复核），**不要把它算进这 33 条**。

---

## 1. 一句话结论

那 33 个失败**不是产品回归**，是测试把 `boot_report.probe_writable()` 的探测目标指到了
**仓库工作树内部**，写入成功、删除被 `tests/conftest.py` 的破坏性操作守卫拒绝；
`probe_writable` 把"删除被拒"如实报成"目录不可写"，于是断言 `data_writable=True` 的用例全红。

## 2. 机制：一条完整的因果链（已在当前代码上复现）

| 步 | 位置 | 发生了什么 |
| --- | --- | --- |
| 1 | `src/observability/boot_report.py#L143` | `tempfile.mkstemp(prefix=".liuhao-startup-probe-", dir=root)` 在目标目录里建探针文件 |
| 2 | `src/observability/boot_report.py#L149` | `os.remove(name)` 删除探针文件 |
| 3 | `tests/conftest.py#L993` → `#L974` | 守卫 `guarded_remove` → `_check` → `_guard_violation` 拒绝：**"路径位于仓库工作树内"** |
| 4 | `src/observability/boot_report.py#L134-L137` | `probe_writable` 捕获 `BaseException`，返回 `(False, exc)` |
| 5 | `src/observability/boot_report.py#L357-L358` | `emit_startup_check(data_dir=.../log_dir=...)` 把 `data_writable/log_writable` 写成 `False` |
| 6 | 调用该自检的用例 | `assert data_writable is True` 失败；诊断包里也会出现"数据目录不可写"的假告警 |

守卫的原话（复现输出，未改一字）：

```
护栏拒绝（fail-closed）：测试试图删除归属无法确认的路径。
  删除接口  : os.remove
  目录项路径: D:\LiuHao-AI-OS\data\_probe_guard_check\.liuhao-startup-probe-xxxxxxxx
  当前用例  : tests/gateway/...::test_...
  拒绝原因  : 路径位于仓库工作树内（或符号链接指向仓库内）
```

复现方法（30 秒，临时用例已删除，可自行重建）：

```python
from pathlib import Path
from src.observability.boot_report import probe_writable

def test_probe_repo_dir():
    ok, exc = probe_writable(Path(__file__).parents[2] / "data" / "probe_check")
    assert ok is True, exc          # ← 这里会红，异常就是上面那段守卫报错
```

同一份代码把目标换成 `tmp_path` 立刻变绿——**所以问题在"探针目标目录选在哪"，不在产品代码**。

## 3. 触发条件（谁把它踩响的）

只要满足任意一条就会红：

1. 用例把 `LIUHAO_DATA_DIR` / `log_dir` / 诊断包导出的目标设成仓库内路径（如 `REPO/"data"`、`REPO/"logs"`、`Path.cwd()/"data"`）；
2. 用例直接调用 `emit_startup_check()` / 诊断打包，且没把目标换成 `tmp_path` 或 `--basetemp`；
3. `boot_report` 用默认目录（相对 cwd）推导，测试的 cwd 恰好是仓库根。

反例：`tmp_path`、`tempfile.gettempdir()`、pytest `--basetemp` 声明的临时根都在放行名单里（`tests/conftest.py#L905-L1039` 的判据 1），所以同一批用例换目录就能过。

## 4. 当前状态：重跑已不复现

今天（2026-11-08 16:5x）在当前工作树上重跑：

```powershell
.venv\Scripts\python.exe -m pytest tests/gateway/test_account_scope_three_layers.py ^
  tests/gateway/test_account_backup_stores.py tests/gateway/test_account_wording.py ^
  tests/gateway/test_local_proof_handoff.py -q
.venv\Scripts\python.exe -m pytest tests/gateway/test_diagnostic_bundle.py ^
  tests/gateway/test_security_hardening.py -q
```

结果：`30 passed`（前四个文件，262.13s）；`53 passed`（后两个文件，90.49s）。六个文件全绿。

全量 `tests/gateway` 复核结果（同一工作树，串行跑完）：

```
1 failed, 383 passed, 1 warning in 869.97s (0:14:29)
FAILED tests/gateway/test_data_portability.py::test_memory_export_keeps_timestamps_and_import_is_idempotent
  AssertionError: 导出列表比期望多一项 ('system', 'verification_model: 真实模型')
```

结论：那 33 个失败是**你们 WIP 中途的瞬时状态**，随代码演进而消失；
现在全量只剩 1 个失败，**且与「33 失败」不是同一批问题**——

- 它不是 `data_writable` 断言，而是导出记忆里多出一条 `('system', 'verification_model: 真实模型')`
  记录（本机存在一条真实模型配置，被导出成 system 记忆）；
- 它和启动自检 / conftest 守卫**无关**，即使按 §5 第 1 条修完那 33 个也不会消失；
- 归属建议：由账号线或记忆线单独判定"导出是否应过滤 `verification_model:` 前缀的系统记忆"，
  别把它并入 33 失败的工单，也别拿它当"33 失败仍存在"的证据。

## 5. 期望行为（三条可选，按推荐顺序）

1. **测试侧（推荐，零产品风险）**：把自检/诊断的探测目标统一换到 `tmp_path` 或 `--basetemp`
   声明的临时根。判据 1 会自动放行，`probe_writable` 返回 `True`，断言恢复。
2. **测试侧（确实需要写仓库内产物时）**：用 `allow_repo_deletion` fixture 显式登记并写理由，
   守卫会放行**一次**删除。适合"故意在仓库内造探针验证门禁"的用例。
3. **产品侧（不建议）**：给 `probe_writable` 加"删除失败不等于不可写"的容错。这会削弱
   "真的要能删"的判据（Windows 上文件被占用/只读正是靠这一步发现），不建议为测试让步。

## 6. 账号线自查清单（针对你们未提交的 `src/gateway/accounts.py`）

这份 diff 是 +147/−7，重点看这几处（行号为当前工作树）：

| 位置 | 改动 | 需要自查什么 |
| --- | --- | --- |
| `#L20` | 新增 `from ..common.atomic_write import StorageWriteError` | 文件落盘失败的语义是否在无写权限时触发 |
| `#L273-L327` | 新增 `_require_same_user()` / `_require_bootstrap_source()` | 开发模式（未设 `LIUHAO_TRUSTED_LAUNCH`）必须放行，别把本地测试锁死 |
| `#L334` `#L348` `#L384` | 三个 proof 门禁里插入 `_require_same_user(request)` | 用 `TestClient` 时 `request.client` 是 `testclient`，SID 反查必然拿不到 → 只能靠"非可信启动"分支放行 |
| `#L498-L524` | `register_root` 先取 proof、凭证写入挪进 `try`、异常时 `release_token`、成功才 `consume_token` | 交接令牌必须"失败可重试、成功一次性"，重复注册不应吃掉令牌 |
| `#L638-L643` | `set_self_registration` 捕获 `StorageWriteError` → 503 `ACCOUNT_SETTINGS_WRITE_FAILED` | 落盘失败要报"没保存"，不能 500/假成功 |
| `#L1031-L1093` | 新增 `GET /{principal}/usage/events` | 权限必须与 `/{principal}/usage` 一致（根看子树、成员只看 `actor=本人`），否则是越权读 |

## 7. 与本轮懒加载改动的关系

**无交集。** 本轮的改动只是 `src/security/vault_crypto.py`、`src/integrations/__init__.py`、
`src/kernels/security/__init__.py` 的导入方式，以及 `src/gateway/main.py` 的指标延迟加载；
没有触碰账号、身份、启动自检。若要自证，可把本轮改动 `git stash` 后重跑同一批用例——
上面 §4 那六个文件的重跑就是在这批改动**存在**的情况下全绿的（全量只剩的那 1 个失败
也与懒加载无关，是导出记忆内容问题），说明失败与懒加载无关。

---

附：取证脚本与原始输出

- 机制复现：临时用例（`probe_writable(REPO/"data"/"probe_check")`）→ `1 failed`，异常即 §2 的守卫原文；
  同一用例 `tmp_path` 分支 `1 passed`。用例已删除，仓库工作树无残留（`git status` 已核对）。
- 守卫定义：`tests/conftest.py#L905-L1039`；探针实现：`src/observability/boot_report.py#L131-L152`。
- 全量复核日志：`D:\_audit\gw-failures-20261108.log`（`pytest tests/gateway -q --tb=line`）。
