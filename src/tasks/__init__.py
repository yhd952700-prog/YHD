"""贾维斯任务队列包（taskiq 集成）。

职责边界见 `src/tasks/broker.py` 的模块文档：**taskiq 只负责任务队列与
执行**，事件广播继续由 `src/distribution/msg_bus.py` 负责。两者不互换。

入口：

* `src/workers/runner.py` —— 消费队列的 worker（对应 supervisor 的
  `liuhao-worker`）。
* `src/workers/scheduler.py` —— 周期投递的调度器（对应
  `liuhao-scheduler`）。

默认 broker 是 SQLite（单机多进程可用，不要求 Redis/Postgres）。

单机的时间点执行 / 失败指数退避重试 / 死信由 `src/tasks/local_scheduler.py`
负责（SQLite 状态 + 审计事件），与 taskiq 的跨进程队列分工明确，不重复写调度。
"""

__all__ = []
