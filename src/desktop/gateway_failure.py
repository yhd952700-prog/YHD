"""网关启动失败的归因：读抓取日志尾部，说清 code=1/code=3 为什么退出。

为什么需要它
------------
Joseph 的真 Windows 电脑上报「网关进程提前退出 (code=1)」，但他不再发日志。
桌面壳原来只把退出码和一句「详见 logs/desktop-gateway.log」甩给报错页，
用户得自己翻日志、看英文 traceback。本模块在 ``_wait_health`` 发现子进程
提前退出时，立刻读已抓取的 ``desktop-gateway.log`` 尾部（``_GatewayLogCapture``
已脱敏落盘），按信号归类，给出一句中文原因 + 一句下一步动作 + 最多 6 行
去重脱敏后的关键日志行，拼进 ``last_error``，报错页直接展示。

分类顺序即优先级（先判具体的，再判笼统的）：
  端口绑定 → 实例锁 → 缺模块（``src`` 时分三种：文件缺 / 根不对 /
  启动上下文，见 ``classify_gateway_failure``）→ 导入失败 → 配置损坏 →
  磁盘满 → 杀毒拦截 → 数据目录不可写 → lifespan → 崩溃（负退出码）→
  解释器层失败（code=1 且日志无信号）→ 启动超时 → 未知。

实测过的退出码口径（沙箱，``src.desktop._runner``）：
  * 导入失败（坏 app / 缺依赖）→ 1，尾行 ``ModuleNotFoundError``；
  * lifespan 抛错 / 端口绑定失败 → 3（uvicorn STARTUP_FAILURE），
    尾行 ``Application startup failed`` / ``address already in use``；
  * 实例锁被占 → 3，stderr 一行 ``[instance] …``；
  * 日志配置损坏 → 1（``load_uvicorn_log_config`` 抛错，未进 uvicorn）。
  所以 code=1 专指「解释器还没跑起来就退了」，code=3 才是 uvicorn 层失败，
  两类都要能说清，不能只认 code=1。

安全：证据行逐行再过一次 :func:`redact_log_text`（抓取落盘时已脱敏过一次，
这里是第二道），行数与总长都有上限；Key、token 不会进报错页，日志证据行里
的绝对路径会被折叠成 ``<本地路径>/文件名``。但程序根与缺文件路径是**特意
印出来**的——那是用户自己本机的路径，只显示在他自己的报错页上，不发往
任何地方；没有它，``No module named 'src'`` 根本没法定案（v9 真机故障的
教训：文件明明在，报错页却只会说“缺少组件”，让人白重装）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..common.redaction import redact_log_text

#: 归因时读的日志尾部行数（证据再从中挑）。
MAX_TAIL_LINES = 80
#: 报错页上最多展示的关键行数。
MAX_EVIDENCE_LINES = 6
#: 单行证据最长字符数（traceback 行可能很长）。
MAX_EVIDENCE_CHARS = 200
#: 拼进 last_error 的全文上限（报错页是给人看的，不是日志转储）。
MAX_DETAIL_CHARS = 1500

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

#: 证据候选行的信号词（大小写不敏感的子串匹配）。
_ERROR_TOKENS = (
    "error",
    "fail",
    "traceback",
    "exception",
    "errno",
    "winerror",
    "denied",
    "permission",
    "not found",
    "no module",
    "no space",
    "virus",
    "already in use",
    "instance",
    "被占用",
    "失败",
    "无法",
    "不能",
    "拒绝",
    "损坏",
    "不存在",
)

_MODULE_RE = re.compile(r"No module named '([^']+)'")


def read_gateway_log_tail(path: Path | str | None, max_lines: int = MAX_TAIL_LINES) -> str:
    """读抓取日志的最后 ``max_lines`` 行；文件不存在/读不出时返回空串。"""
    if path is None:
        return ""
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return ""
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    return "\n".join(lines[-max(1, max_lines):])


def _clean_line(line: str) -> str:
    """去 ANSI 颜色、去首尾空白、脱敏、截断；空行返回空串。"""
    text = _ANSI_RE.sub("", line).strip()
    if not text:
        return ""
    return redact_log_text(text)[:MAX_EVIDENCE_CHARS]


def _pick_evidence(lines: list[str]) -> list[str]:
    """从尾部行里挑关键行：含错误信号的去重后取最后 6 行；没有信号时取最后 3 行。

    INFO 行（``Application startup complete`` 之类）不是错误，不进证据，
    否则报错页会被启动成功的噪音淹没。
    """
    cleaned = [_clean_line(line) for line in lines]
    cleaned = [line for line in cleaned if line]
    candidates: list[str] = []
    for line in cleaned:
        if line.startswith("INFO:"):
            continue
        lowered = line.lower()
        if any(token in lowered for token in _ERROR_TOKENS):
            if line not in candidates:
                candidates.append(line)
    if candidates:
        return candidates[-MAX_EVIDENCE_LINES:]
    # 没有错误信号时只留最后 3 行非 INFO 行；全是 INFO 说明日志里确实
    # 没有可用证据，返回空，不拿"启动成功"的噪音充数。
    non_info = [line for line in cleaned if not line.startswith("INFO:")]
    return non_info[-3:]


def _missing_module(log_text: str) -> str | None:
    """最后一个 ``No module named 'x'`` 里的模块名（最靠近失败点的那个）。"""
    found = _MODULE_RE.findall(log_text)
    return found[-1] if found else None


def classify_gateway_failure(
    exit_code: int | None,
    log_text: str,
    *,
    root: str | Path | None = None,
    runner_files_present: bool | None = None,
    missing_file: str | None = None,
) -> dict[str, Any]:
    """按日志尾部信号归类；返回 ``category/title/reason/action/evidence``。

    ``exit_code`` 为 None 表示进程还在但健康检查超时（不是提前退出）。

    ``root`` / ``runner_files_present`` / ``missing_file`` 是启动时已知的
    上下文（``GatewayService.start`` 的预检结果，经 ``_wait_health`` 传
    进来）：``No module named 'src'`` 只看日志分不出“文件缺了”还是“启动
    上下文问题”——v9 真机就是文件都在却报缺模块。分三种：

    * 程序根不存在 → ``bad_root``（印出实际 _root）；
    * 入口链缺文件 → ``missing_runner_file``（印出缺文件的完整路径）；
    * 文件都在 → ``module_src_context``（启动上下文问题，别让人白重装）；
    * 上下文未知 → 仍是 ``missing_module``，但原因里写清两种可能，不再
      只写“缺少组件 src，重装”。
    """
    text = "\n".join((log_text or "").splitlines()[-MAX_TAIL_LINES:])
    lowered = text.lower()
    evidence = _pick_evidence(text.splitlines())
    module = _missing_module(text)
    root_str = str(root) if root is not None else None

    if any(
        token in lowered
        for token in (
            "address already in use",
            "error while attempting to bind",
            "[winerror 10048]",
            "[errno 98]",
            "[errno 48]",
            "only one usage of each socket address",
            "端口被其它程序占用",
            "被其它程序占用，网关无法启动",
        )
    ):
        category, title = "port_bind", "端口被抢占"
        reason = "网关要绑的端口在启动瞬间被别的程序抢走了（启动前检查时还是空的）。"
        action = "已尝试自动换一个空闲端口；若仍失败，请关闭占用该端口的程序，或在任务管理器里结束它后重试。"
    elif any(
        token in lowered
        for token in (
            "instance lock is held",
            "instancelockbusy",
            "已经在运行",
        )
    ) or "[instance]" in text and "已经在运行" in text:
        category, title = "instance_lock", "已有贾维斯在运行"
        reason = "另一个贾维斯已经在运行，本次启动为避免两个进程同时写坏数据而退出。"
        action = "请先关闭已经运行的那个贾维斯，再重新打开；若确认没有在运行的贾维斯，删掉数据目录 run/ 下的 instance.lock 后重试。"
    elif module is not None and module in ("src", "src.desktop"):
        if root_str is not None and not Path(root_str).is_dir():
            category, title = "bad_root", "程序根目录不对"
            reason = (
                f"程序根目录不存在：{root_str}。"
                "桌面壳是从别处启动的（快捷方式指向旧位置、或安装路径与"
                "运行路径不一致），网关子进程到这个目录下找不到启动文件。"
            )
            action = "用桌面上的「贾维斯」快捷方式重新打开；仍失败就用同一个安装包重新安装。"
        elif runner_files_present is False:
            category, title = "missing_runner_file", "启动文件缺失"
            if missing_file:
                reason = (
                    f"启动文件缺失：{missing_file}。"
                    "安装不完整，或该文件被删、被杀毒软件隔离了，网关还没跑起来就退出了。"
                )
            else:
                reason = (
                    "启动文件缺失：网关启动入口链里有文件不在（启动前核对过），"
                    "安装不完整，或该文件被删、被杀毒软件隔离了，网关还没跑起来就退出了。"
                )
            action = (
                "用同一个安装包重新安装（或重新解压）补齐文件；若杀毒软件隔离过文件，"
                "先从隔离区恢复再重启。"
            )
        elif runner_files_present is True:
            category, title = "module_src_context", "启动时找不到 src 包"
            reason = (
                "网关子进程启动时找不到 src 包：安装包里的文件都在（启动前已核对过），"
                "问题出在启动上下文——子进程没找对程序根，或内嵌 Python 的启动环境不对。"
                "不是安装不完整，不用重装。"
            )
            action = "先重启贾维斯再试一次；仍失败就点「生成诊断包」发给技术支持，包里的日志会写明卡在哪一步。"
        else:
            category, title = "missing_module", "缺少组件"
            reason = (
                f"缺少组件 {module}：安装不完整、文件被删或被隔离，也可能是"
                "启动上下文问题（子进程没找对程序根）——只看日志分不出来。"
            )
            action = (
                "用同一个安装包重新安装（或重新解压）补齐文件；若杀毒软件隔离过文件，"
                "先从隔离区恢复再重启。若重装后仍报同一句，问题在启动上下文，"
                "请点「生成诊断包」发给技术支持。"
            )
    elif module is not None:
        category, title = "missing_module", "缺少组件"
        reason = f"缺少组件 {module}：安装不完整，或该文件被删、被隔离了，网关还没跑起来就退出了。"
        action = "用同一个安装包重新安装（或重新解压）补齐文件；若杀毒软件隔离过文件，先从隔离区恢复再重启。"
    elif any(
        token in lowered
        for token in (
            "importerror",
            "cannot import name",
            "dll load failed",
            "failed to import",
        )
    ):
        category, title = "import_error", "组件导入失败"
        reason = "网关启动时某个组件导入失败（文件损坏、缺运行库，或被安全软件拦截）。"
        action = "先重启电脑再试一次；仍失败就用同一个安装包重新安装；若杀毒软件报过毒，先从隔离区恢复。"
    elif any(
        token in lowered
        for token in (
            "日志配置",
            "logging.yaml",
            "while parsing",
            "yaml.parser",
            "yaml.scanner",
            "parsererror",
            "scannererror",
            "error in logging config",
            "dictconfig",
        )
    ):
        category, title = "config", "日志配置损坏"
        reason = "日志配置文件损坏，网关读不懂它就直接退出了。"
        action = "把 config/logging.yaml 恢复成安装包里的原版（或删掉它让程序重建），再重新打开贾维斯。"
    elif any(
        token in lowered
        for token in ("no space left on device", "[errno 28]", "磁盘空间不足", "disk full")
    ):
        category, title = "disk_full", "磁盘满了"
        reason = "磁盘满了，网关写不进日志和数据就退出了。"
        action = "清理磁盘释放空间，或把数据目录换到其它剩余空间充足的盘，再重试。"
    elif any(
        token in lowered
        for token in (
            "[winerror 225]",
            "contains a virus",
            "error_virus_infected",
            "because the file contains a virus",
        )
    ):
        category, title = "antivirus", "被安全软件拦截"
        reason = "安全软件拦截了网关（文件被判定含病毒或禁止访问），进程还没跑起来就被拦住了。"
        action = "打开杀毒软件，把贾维斯安装目录加入白名单、从隔离区恢复被隔离的文件，然后重新打开；不要直接关掉杀毒软件。"
    elif any(
        token in lowered
        for token in (
            "permissionerror",
            "permission denied",
            "[winerror 5]",
            "access is denied",
            "read-only file system",
            "[errno 30]",
            "[errno 13]",
            "无法创建单实例锁文件",
            "boot.instance_lock.failed",
            "boot.logdir.failed",
            "数据目录不可写",
            "数据目录无法创建",
        )
    ):
        category, title = "data_dir", "数据目录写不进去"
        reason = "数据目录写不进去（没权限、被其它程序锁住，或路径被同名文件挡住），网关没法落盘就退出了。"
        action = "给当前用户数据目录的写权限，或把数据目录换到可写的位置（例如 %LOCALAPPDATA%\\LiuHao\\data）；路径被同名文件挡住时先把那个文件改名或移走。"
    elif "application startup failed" in lowered or "lifespan" in lowered:
        category, title = "lifespan", "核心启动失败"
        reason = "网关核心启动失败（端口没问题，问题出在核心组件初始化）。"
        action = "先重启一次；仍失败就点「生成诊断包」发给技术支持，包里的日志会写明卡在哪一步。"
    elif exit_code is not None and exit_code < 0:
        category, title = "crash", "进程崩溃"
        reason = f"网关进程被系统强行终止（崩溃，退出码 {exit_code}），没留下可读的错误。"
        action = "重启电脑再试一次；反复出现就生成诊断包发给技术支持。"
    elif exit_code == 1:
        category, title = "interpreter", "启动器/解释器失败"
        reason = "网关还没跑起来就退出了：启动器或 Python 解释器本身出了问题（安装损坏、文件被隔离，或被安全软件拦住）。"
        action = "先重启电脑再试；仍失败就用同一个安装包重新安装；若杀毒软件报过毒，先从隔离区恢复。"
    elif exit_code is None:
        category, title = "timeout", "启动超时"
        reason = "网关进程还在，但限时内一直没通过健康检查（可能正在加载，也可能卡住了）。"
        action = "等一会儿再点「重新检查」；反复超时就生成诊断包，里面的日志会写明卡在哪一步。"
    else:
        category, title = "unknown", "原因不明"
        reason = "网关进程提前退出了，但日志里没有留下能定位原因的错误。"
        action = "请点「生成诊断包」发给技术支持；也可以先看 logs/desktop-gateway.log 的最后几行。"
    return {
        "category": category,
        "title": title,
        "reason": reason,
        "action": action,
        "evidence": evidence,
        # 启动上下文：报错页印出来（本机自己的路径，只显示在自己的报错页上）。
        "root": root_str,
        "missing_file": missing_file,
    }


def format_failure_detail(
    summary: dict[str, Any], *, exit_code: int | None
) -> str:
    """拼成报错页上的一段话：退出码 + 原因 + 下一步 + 程序根/缺文件 + 关键日志行。"""
    if exit_code is None:
        head = f"网关启动超时。{summary['reason']}"
    else:
        head = f"网关进程提前退出（code={exit_code}）。{summary['reason']}"
    parts = [head, f"下一步：{summary['action']}"]
    if summary.get("root"):
        parts.append(f"程序根：{summary['root']}")
    if summary.get("missing_file"):
        parts.append(f"缺文件：{summary['missing_file']}")
    evidence = [line for line in (summary.get("evidence") or []) if line]
    if evidence:
        parts.append("日志关键行：\n" + "\n".join(f"- {line}" for line in evidence))
    detail = "\n".join(parts)
    return detail[:MAX_DETAIL_CHARS]


def summarize_gateway_failure(
    exit_code: int | None,
    log_path: Path | str | None,
    *,
    root: str | Path | None = None,
    runner_files_present: bool | None = None,
    missing_file: str | None = None,
) -> dict[str, Any]:
    """读抓取日志尾部并归类；返回带 ``detail``（可直接进 ``last_error``）的摘要。

    ``root`` / ``runner_files_present`` / ``missing_file`` 见
    ``classify_gateway_failure``：``No module named 'src'`` 的三种情况
    靠它们区分，``detail`` 里也会印出程序根与缺文件路径。
    """
    tail = read_gateway_log_tail(log_path)
    summary = classify_gateway_failure(
        exit_code,
        tail,
        root=root,
        runner_files_present=runner_files_present,
        missing_file=missing_file,
    )
    summary["exit_code"] = exit_code
    summary["detail"] = format_failure_detail(summary, exit_code=exit_code)
    return summary


__all__ = [
    "MAX_DETAIL_CHARS",
    "MAX_EVIDENCE_CHARS",
    "MAX_EVIDENCE_LINES",
    "MAX_TAIL_LINES",
    "classify_gateway_failure",
    "format_failure_detail",
    "read_gateway_log_tail",
    "summarize_gateway_failure",
]
