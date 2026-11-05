"""DBOS pilot —— **默认关闭**，只验证一件事：杀进程后重启能否续跑。

## 开关

* 环境变量 `LIUHAO_DBOS_PILOT=1`（或 true/yes/on）才启用；未设置时 CLI 直接
  以退出码 2 结束。
* 只做试点，不接入 API / worker / 调度主链路；`src/resilience/` 的生产引用
  仍然为零（本文件是独立 CLI）。

## 依赖评估（dbos 3.4.0，2026-11-05 实测）

* `pip install dbos` 会无条件拉 `psycopg[binary]`：它在 `dbos` 的 top-level
  `Requires-Dist` 里，**是硬 pip 依赖**，不能通过 extra 规避。
* 但 **不需要 Postgres 服务**：`system_database_url=sqlite:///...` 时 DBOS 用
  SQLAlchemy async + `aiosqlite`，完全本地跑通（本项目已依赖 aiosqlite）。
* 副作用：多装 psycopg + 二进制 wheel（数 MB）；DBOS 会在**独立** system
  sqlite 文件里建自己的内部表（几十张），不碰应用的 AuditStore / 业务库。
* 结论：作为**可选 extra**（`pip install -e ".[dbos]"`）可接受；不设成运行时
  必装依赖，不默认启用。

## 试点结论（`--kill-at` 实测）

第一次运行在 step 2 内 `os._exit(17)` 硬杀；第二次以同一 workflow id 重启：
`DBOS.launch()` 自动恢复 pending workflow，step 1 不重跑（步骤日志只有一份），
step 2/3 续跑到 `workflow-complete`。成功续跑。

注意 DBOS 的恢复依赖 `application_version` + workflow id 稳定：两者变化会被
当成不同的持久化命名空间，恢复不会发生。本模块用固定 id + 固定版本。
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict, Optional

ENV_SWITCH = "LIUHAO_DBOS_PILOT"
DEFAULT_WORKFLOW_ID = "liuhao-dbos-pilot"


def is_enabled() -> bool:
    return os.environ.get(ENV_SWITCH, "").strip().lower() in {"1", "true", "yes", "on"}


def run_pilot(
    db_dir: str,
    kill_at_step: int = 0,
    steps: int = 3,
    workflow_id: str = DEFAULT_WORKFLOW_ID,
) -> Dict[str, Any]:
    """Run the DBOS pilot in the current process.

    ``kill_at_step`` hard-kills the process *inside* that step, which is the
    crash we need to prove recovery against. Returns the workflow result dict.
    """
    if not is_enabled():
        raise RuntimeError(
            f"DBOS pilot is disabled; set {ENV_SWITCH}=1 to run it explicitly."
        )

    # 延迟导入：默认安装（未装 dbos extra）时本模块仍可被 import。
    try:
        from dbos import DBOS, DBOSConfig, SetWorkflowID
    except ImportError as exc:  # pragma: no cover - 取决于可选依赖
        raise RuntimeError(
            "DBOS pilot needs the optional dependency: "
            'pip install -e ".[dbos]"'
        ) from exc

    os.makedirs(db_dir, exist_ok=True)
    sys_db = os.path.join(db_dir, "dbos_system.db")
    step_log = os.path.join(db_dir, "pilot_steps.log")

    config = DBOSConfig(
        name="liuhao_dbos_pilot",
        system_database_url=f"sqlite:///{sys_db}",
        application_version=os.environ.get("LIUHAO_DBOS_PILOT_APP_VERSION", "pilot-v1"),
        executor_id=os.environ.get("LIUHAO_DBOS_PILOT_EXECUTOR_ID", "liuhao-pilot"),
    )
    DBOS(config=config)

    @DBOS.step()
    def step(index: int) -> str:
        if kill_at_step == index:
            print(f"DBOS pilot: HARD KILL inside step {index}", flush=True)
            os._exit(17)  # 不经 atexit / DBOS destroy，模拟进程被硬杀
        with open(step_log, "a", encoding="utf-8") as fh:
            fh.write(f"step{index}\n")
        return f"step{index}-done"

    @DBOS.workflow()
    def workflow() -> str:
        for index in range(1, steps + 1):
            step(index)
        return "workflow-complete"

    DBOS.launch()
    try:
        with SetWorkflowID(workflow_id):
            handle = DBOS.start_workflow(workflow)
        result = handle.get_result()
        return {
            "workflow_id": handle.workflow_id,
            "result": result,
            "step_log": step_log,
            "system_db": sys_db,
        }
    finally:
        DBOS.destroy()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="liuhao-dbos-pilot", description=__doc__)
    parser.add_argument("--db-dir", required=True, help="directory for DBOS system db + step log")
    parser.add_argument(
        "--kill-at",
        type=int,
        default=0,
        help="hard-kill inside step N (0 = do not kill); used to prove restart resume",
    )
    parser.add_argument("--steps", type=int, default=3)
    parser.add_argument("--workflow-id", default=DEFAULT_WORKFLOW_ID)
    args = parser.parse_args(argv)

    if not is_enabled():
        print(
            f"DBOS pilot is DISABLED by default; set {ENV_SWITCH}=1 to enable.",
            flush=True,
        )
        return 2

    outcome = run_pilot(
        args.db_dir,
        kill_at_step=args.kill_at,
        steps=args.steps,
        workflow_id=args.workflow_id,
    )
    print(f"DBOS pilot result: {outcome['result']} (workflow {outcome['workflow_id']})", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
