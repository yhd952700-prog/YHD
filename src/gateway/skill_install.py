"""技能包安装：zip / 目录 / 单文件的**安全落盘**，以及卸载与导出。

为什么单独一层
--------------
``skill_import`` 只负责"读懂一份清单"，不碰磁盘；``skill_runtime`` 只负责
"跑已经落盘的脚本"。中间这段最容易出事：解压、路径穿越、解压炸弹、半成品
残留、覆盖失败丢旧版本。这里把这段单独做严。

硬约束（与设计稿一致，不做"看起来支持"）
--------------------------------------
1. **防路径穿越**：每个条目规范化后必须是纯相对路径；``..``、绝对路径、盘符、
   符号链接（含 Windows reparse point）、控制字符、保留设备名一律**拒绝整包**，
   并报出是哪个条目。
2. **防解压炸弹**：条目数、单文件、解压后总量都有上限；边解边计数，超了立刻
   中止并删掉半成品。
3. **先暂存后提交**：全部解到 ``<data>/skills/.staging-*``，校验通过后才整体
   改名进 ``<data>/skills/<skill_id>``，绝不边解边写最终位置。
4. **失败回滚**：覆盖安装时先把旧目录改名成 ``.backup-*``；新目录就位失败就
   把旧目录改回来。任何异常都不会留下"半装好"的技能。

诚实边界
--------
* 这里做的是**安装期**的文件校验；脚本**运行期**的隔离在 ``skill_runtime``，
  那是低完整性 + Job Object 的"受限执行"，不是沙箱。
* 文件扩展名白名单只能挡住明显可执行的投递（exe/dll/bat/lnk…），挡不住
  一个 ``.py`` 里写任意代码；能不能跑由 ``runtime.entrypoints`` 与 Boss 的
  首次授权决定。
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import tempfile
import uuid
import zipfile
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .skill_runtime import packages_dir

# ---------------------------------------------------------------------- #
# 上限
# ---------------------------------------------------------------------- #
MAX_ENTRIES = 2000
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_PATH_CHARS = 240
MAX_DEPTH = 10

#: 允许落盘的文件类型。文本、脚本、常见数据/图片；其余一律拒绝。
_ALLOWED_SUFFIX = frozenset(
    {
        # 文档 / 清单
        ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
        ".csv", ".tsv", ".xml", ".html", ".htm", ".css", ".sql",
        # 脚本（由 runtime.entrypoints 决定是否可跑）
        ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".ps1",
        # 资源
        ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".pdf",
    }
)

#: 这些即使白名单漏了也永不落盘。
_BLOCKED_SUFFIX = frozenset(
    {
        ".exe", ".dll", ".sys", ".com", ".scr", ".msi", ".msp", ".cpl",
        ".bat", ".cmd", ".lnk", ".vbs", ".vbe", ".wsf", ".wsh", ".hta",
        ".jar", ".class", ".pif", ".reg", ".ps2", ".ps2xml", ".msc",
    }
)

_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")

#: 包内被认作"清单"的文件（相对路径，大小写不敏感）。
_MANIFEST_NAMES = ("SKILL.md", "skill.json")


class SkillInstallError(ValueError):
    """安装失败。消息面向用户，必须能直接展示，并指明是哪个条目。"""


def _fail(message: str) -> None:
    raise SkillInstallError(message)


def _normalize_relpath(raw: str) -> str:
    """把压缩包里的条目名收敛成安全的包内相对路径（``/`` 分隔）。"""
    if not isinstance(raw, str) or not raw.strip():
        _fail("压缩包里有空文件名条目，拒绝安装。")
    if _CONTROL_RE.search(raw):
        _fail(f"条目「{raw}」的文件名包含控制字符，拒绝安装。")
    path = raw.replace("\\", "/")
    if path.startswith("/"):
        _fail(f"条目「{raw}」是绝对路径，拒绝安装。")
    if _DRIVE_RE.match(path):
        _fail(f"条目「{raw}」带盘符，拒绝安装。")
    if path.endswith("/"):
        path = path.rstrip("/")
    if not path:
        _fail(f"条目「{raw}」规范化后为空，拒绝安装。")
    parts = path.split("/")
    if len(parts) > MAX_DEPTH:
        _fail(f"条目「{raw}」目录层级超过 {MAX_DEPTH} 层，拒绝安装。")
    if len(path) > MAX_PATH_CHARS:
        _fail(f"条目「{raw}」路径超过 {MAX_PATH_CHARS} 字符，拒绝安装。")
    for part in parts:
        if part in ("", ".", ".."):
            _fail(f"条目「{raw}」包含 . / .. / 空路径段，拒绝安装。")
        stem = part.rsplit(".", 1)[0].strip().lower()
        if part.rstrip(" .") != part:
            _fail(f"条目「{raw}」的文件名以空格或点结尾，拒绝安装。")
        if stem in _WINDOWS_RESERVED:
            _fail(f"条目「{raw}」使用了 Windows 保留名，拒绝安装。")
    return "/".join(parts)


def _check_suffix(relpath: str) -> None:
    suffix = os.path.splitext(relpath)[1].lower()
    if suffix in _BLOCKED_SUFFIX:
        _fail(f"条目「{relpath}」是 {suffix} 可执行文件，技能包不允许携带，拒绝安装。")
    if suffix not in _ALLOWED_SUFFIX:
        allowed = "、".join(sorted(_ALLOWED_SUFFIX))
        _fail(
            f"条目「{relpath}」的类型「{suffix or '无扩展名'}」不在允许清单内"
            f"（允许：{allowed}），拒绝安装。"
        )


def _is_link_or_reparse(path: str) -> bool:
    """符号链接 / junction / reparse point 一律视为链接。"""
    try:
        if os.path.islink(path):
            return True
        stat = os.stat(path, follow_symlinks=False)
    except OSError:
        return False
    attributes = getattr(stat, "st_file_attributes", 0)
    return bool(attributes & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT


def _inside(root: str, target: str) -> bool:
    root_abs = os.path.abspath(root).rstrip("\\/") + os.sep
    return os.path.abspath(target).startswith(root_abs)


def _sha256_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 256), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _walk_files(root: str) -> List[str]:
    out: List[str] = []
    for current, dirs, names in os.walk(root, followlinks=False):
        for name in list(dirs):
            if _is_link_or_reparse(os.path.join(current, name)):
                _fail(f"目录「{os.path.join(current, name)}」是符号链接，拒绝安装。")
        for name in names:
            full = os.path.join(current, name)
            if _is_link_or_reparse(full):
                _fail(f"文件「{full}」是符号链接，拒绝安装。")
            out.append(os.path.relpath(full, root).replace("\\", "/"))
    return out


def _hash_files(root: str, files: Iterable[str]) -> Dict[str, str]:
    return {rel: _sha256_file(os.path.join(root, rel)) for rel in sorted(files)}


def compute_package_hash(hashes: Dict[str, str]) -> str:
    payload = "".join(f"{rel}\0{digest}\n" for rel, digest in sorted(hashes.items()))
    return _sha256_bytes(payload.encode("utf-8"))


# ---------------------------------------------------------------------- #
# 暂存包
# ---------------------------------------------------------------------- #
class StagedPackage:
    """已经解到临时目录、但还没进最终位置的一个技能包。

    用法::

        with stage_upload(name, data) as staged:
            staged.commit(skill_id, overwrite=True)
            ... 写技能登记 ...
        # 块内抛异常 → 自动回滚到安装前状态
    """

    def __init__(
        self,
        staging_root: str,
        origin: str,
        fmt: str,
        main_file: str,
        hint: str = "",
    ) -> None:
        self._staging_root = staging_root
        self.origin = origin
        self.format = fmt
        self.main_file = main_file
        self.hint = hint
        self.files: List[str] = []
        self.hashes: Dict[str, str] = {}
        self.package_hash = ""
        self._committed = False
        self._target = ""
        self._backup = ""

    @property
    def manifest_name(self) -> str:
        """给 ``parse_upload`` 用的文件名：带上包目录名，便于缺 ``name`` 时兜底。"""
        return f"{self.hint}/{self.main_file}" if self.hint else self.main_file

    # ---------------------------------------------------------------- #
    def __enter__(self) -> "StagedPackage":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:  # noqa: D105
        if exc_type is not None or not self._committed:
            self.rollback()
        return False

    # ---------------------------------------------------------------- #
    def read_text(self) -> str:
        path = os.path.join(self._staging_root, self.main_file)
        try:
            with open(path, "r", encoding="utf-8-sig") as handle:
                return handle.read()
        except OSError as exc:
            raise SkillInstallError(f"读取技能清单失败：{exc}") from exc

    def finalize(self, entrypoints: Optional[List[Dict[str, Any]]] = None) -> None:
        """统计文件、算哈希，并把每个入口脚本的 sha256 绑进去。"""
        self.files = sorted(_walk_files(self._staging_root))
        if not self.files:
            _fail("技能包里没有任何文件，拒绝安装。")
        self.hashes = _hash_files(self._staging_root, self.files)
        self.package_hash = compute_package_hash(self.hashes)
        for entry in entrypoints or []:
            script = str(entry.get("script") or "")
            if script not in self.hashes:
                _fail(
                    f"runtime 声明的脚本「{script}」不在技能包里，"
                    "拒绝安装；请检查脚本路径是否相对包根目录。"
                )
            entry["script_sha256"] = self.hashes[script]

    # ---------------------------------------------------------------- #
    def commit(self, skill_id: str, *, overwrite: bool = False) -> str:
        """把暂存目录整体改名到 ``<data>/skills/<skill_id>``。"""
        target = os.path.join(packages_dir(), skill_id)
        os.makedirs(packages_dir(), exist_ok=True)
        if os.path.exists(target):
            if not overwrite:
                _fail(f"已经存在技能包目录「{skill_id}」，未选择覆盖，安装已中止。")
            backup = f"{target}.backup-{uuid.uuid4().hex[:8]}"
            try:
                os.replace(target, backup)
            except OSError as exc:
                raise SkillInstallError(f"备份旧技能包失败：{exc}") from exc
            self._backup = backup
        try:
            os.replace(self._staging_root, target)
        except OSError as exc:
            if self._backup:
                try:
                    os.replace(self._backup, target)
                    self._backup = ""
                except OSError:  # pragma: no cover - 极端情况
                    pass
            raise SkillInstallError(f"技能包落位失败：{exc}") from exc
        self._target = target
        self._committed = True
        if self._backup:
            shutil.rmtree(self._backup, ignore_errors=True)
            self._backup = ""
        return target

    def rollback(self) -> None:
        """回到安装前状态：删新目录、还原旧目录、清暂存。"""
        shutil.rmtree(self._staging_root, ignore_errors=True)
        if self._committed and self._target:
            shutil.rmtree(self._target, ignore_errors=True)
        if self._backup and os.path.isdir(self._backup):
            try:
                os.replace(self._backup, self._target or "")
            except OSError:  # pragma: no cover - 尽力还原
                shutil.rmtree(self._backup, ignore_errors=True)
        self._backup = ""
        self._committed = False


def _new_staging() -> str:
    os.makedirs(packages_dir(), exist_ok=True)
    return tempfile.mkdtemp(prefix=".staging-", dir=packages_dir())


def _detect_content_root(root: str) -> Tuple[str, str, str]:
    """找出真正的包根目录、清单文件名与格式。

    允许 zip 里再套一层同名目录（``my-skill/SKILL.md``），但只允许**一层**，
    避免把结构不明的东西猜成技能。
    """
    for name in _MANIFEST_NAMES:
        if os.path.isfile(os.path.join(root, name)):
            fmt = "agent-skill" if name.lower() == "skill.md" else "liuhao-json"
            return root, name, fmt
    children = [
        child for child in os.listdir(root)
        if not child.startswith(".") and os.path.isdir(os.path.join(root, child))
    ]
    loose = [
        child for child in os.listdir(root)
        if not os.path.isdir(os.path.join(root, child)) and not child.startswith(".")
    ]
    if len(children) == 1 and not loose:
        inner = os.path.join(root, children[0])
        for name in _MANIFEST_NAMES:
            if os.path.isfile(os.path.join(inner, name)):
                fmt = "agent-skill" if name.lower() == "skill.md" else "liuhao-json"
                return inner, name, fmt
    names = " / ".join(_MANIFEST_NAMES)
    _fail(f"技能包里没有找到 {names}，无法识别为一个技能，拒绝安装。")
    raise AssertionError  # pragma: no cover - _fail 一定抛错


# ---------------------------------------------------------------------- #
# 三种来源：单文件 / 目录 / zip
# ---------------------------------------------------------------------- #
def _stage_single_file(filename: str, data: bytes) -> StagedPackage:
    suffix = os.path.splitext(filename)[1].lower()
    if suffix not in (".md", ".json"):
        _fail(f"单文件安装只接受 .md 或 .json（收到「{filename}」）。")
    if len(data) > MAX_FILE_BYTES:
        _fail(
            f"文件「{filename}」超过单文件上限 {MAX_FILE_BYTES // (1024 * 1024)} MB，拒绝安装。"
        )
    staging = _new_staging()
    try:
        # 单文件包保留原文件名：技能名可能来自文件名，不能强行改成 SKILL.md。
        normalized = filename.replace("\\", "/")
        hint = os.path.dirname(normalized).strip("/")
        main = _normalize_relpath(os.path.basename(normalized))
        _check_suffix(main)
        with open(os.path.join(staging, main), "wb") as handle:
            handle.write(data)
        staged = StagedPackage(
            staging,
            filename or main,
            "agent-skill" if suffix == ".md" else "liuhao-json",
            main,
            hint=hint,
        )
        staged.finalize()
        return staged
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def stage_upload(filename: str, data: bytes) -> StagedPackage:
    """按扩展名分派：``.zip`` 走安全解包，``.md`` / ``.json`` 走单文件。"""
    suffix = os.path.splitext(filename or "")[1].lower()
    if suffix == ".zip":
        return _stage_zip(filename, data)
    if suffix in (".md", ".json"):
        return _stage_single_file(filename, data)
    _fail(
        f"不支持「{filename or '未命名文件'}」：技能安装接受 .zip（技能包）、"
        ".md（Agent Skills）、.json（技能清单）或一个目录。"
    )
    raise AssertionError  # pragma: no cover


def _stage_zip(filename: str, data: bytes) -> StagedPackage:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SkillInstallError(f"「{filename}」不是有效的 zip 文件：{exc}") from exc

    staging = _new_staging()
    total = 0
    count = 0
    try:
        infos = archive.infolist()
        if len(infos) > MAX_ENTRIES:
            _fail(
                f"压缩包有 {len(infos)} 个条目，超过上限 {MAX_ENTRIES}，"
                "疑似解压炸弹，已中止并清理半成品。"
            )
        for info in infos:
            # 符号链接（Unix 模式下 mode 的高 16 位是 0o120000）
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                _fail(f"条目「{info.filename}」是符号链接，拒绝整包安装。")
            if info.flag_bits & 0x1:
                _fail(f"条目「{info.filename}」是加密条目，拒绝整包安装。")
            if info.is_dir():
                continue
            rel = _normalize_relpath(info.filename)
            _check_suffix(rel)
            count += 1
            if count > MAX_ENTRIES:
                _fail(f"压缩包条目数超过上限 {MAX_ENTRIES}，已中止并清理半成品。")
            target = os.path.join(staging, *rel.split("/"))
            if not _inside(staging, target):
                _fail(f"条目「{info.filename}」解压后落到了技能目录之外，拒绝整包安装。")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            written = 0
            try:
                with archive.open(info) as source, open(target, "wb") as sink:
                    while True:
                        chunk = source.read(64 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        total += len(chunk)
                        if written > MAX_FILE_BYTES:
                            _fail(
                                f"条目「{info.filename}」解压后超过单文件上限 "
                                f"{MAX_FILE_BYTES // (1024 * 1024)} MB，已中止并清理半成品。"
                            )
                        if total > MAX_TOTAL_BYTES:
                            _fail(
                                f"压缩包解压后总量超过 {MAX_TOTAL_BYTES // (1024 * 1024)} MB，"
                                "已中止并清理半成品。"
                            )
                        sink.write(chunk)
            except SkillInstallError:
                raise
            except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
                _fail(f"解压条目「{info.filename}」失败：{exc}")
            if _is_link_or_reparse(target):
                _fail(f"条目「{info.filename}」解压后是链接，拒绝整包安装。")

        root, main_file, fmt = _detect_content_root(staging)
        hint = ""
        if root != staging:
            hint = os.path.basename(root)
            # 套了一层的包：把内层内容整体上移，删掉空壳目录。
            for name in os.listdir(root):
                shutil.move(os.path.join(root, name), os.path.join(staging, name))
            shutil.rmtree(root, ignore_errors=True)
        staged = StagedPackage(staging, filename, fmt, main_file, hint=hint)
        staged.finalize()
        return staged
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    finally:
        archive.close()


def stage_directory(source: str) -> StagedPackage:
    """从一个本机目录暂存技能包；链接、越界、超限一律拒绝。"""
    source = os.path.abspath(source)
    if not os.path.isdir(source):
        _fail(f"目录「{source}」不存在。")
    if _is_link_or_reparse(source):
        _fail(f"目录「{source}」是链接，请选择真实目录。")
    staging = _new_staging()
    total = 0
    count = 0
    try:
        for current, dirs, names in os.walk(source, followlinks=False):
            for name in list(dirs):
                full = os.path.join(current, name)
                if _is_link_or_reparse(full):
                    _fail(f"目录「{full}」是符号链接，拒绝安装。")
                if name.startswith("."):
                    dirs.remove(name)
            for name in names:
                full = os.path.join(current, name)
                if name.startswith("."):
                    continue
                if _is_link_or_reparse(full):
                    _fail(f"文件「{full}」是符号链接，拒绝安装。")
                rel = os.path.relpath(full, source).replace("\\", "/")
                rel = _normalize_relpath(rel)
                _check_suffix(rel)
                count += 1
                if count > MAX_ENTRIES:
                    _fail(f"目录文件数超过上限 {MAX_ENTRIES}，已中止并清理半成品。")
                size = os.path.getsize(full)
                if size > MAX_FILE_BYTES:
                    _fail(
                        f"文件「{rel}」超过单文件上限 "
                        f"{MAX_FILE_BYTES // (1024 * 1024)} MB，已中止并清理半成品。"
                    )
                total += size
                if total > MAX_TOTAL_BYTES:
                    _fail(
                        f"目录总量超过 {MAX_TOTAL_BYTES // (1024 * 1024)} MB，"
                        "已中止并清理半成品。"
                    )
                target = os.path.join(staging, *rel.split("/"))
                if not _inside(staging, target):
                    _fail(f"文件「{rel}」落到了技能目录之外，拒绝安装。")
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copy2(full, target)
        root, main_file, fmt = _detect_content_root(staging)
        hint = ""
        if root != staging:
            hint = os.path.basename(root)
            for name in os.listdir(root):
                shutil.move(os.path.join(root, name), os.path.join(staging, name))
            shutil.rmtree(root, ignore_errors=True)
        staged = StagedPackage(staging, source, fmt, main_file, hint=hint)
        staged.finalize()
        return staged
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def stage_path(path: str) -> StagedPackage:
    """本机路径安装：目录按目录走，``.zip`` / ``.md`` / ``.json`` 按文件读。"""
    path = os.path.abspath(os.path.expanduser(path))
    if os.path.isdir(path):
        return stage_directory(path)
    if not os.path.isfile(path):
        _fail(f"路径「{path}」既不是文件也不是目录。")
    suffix = os.path.splitext(path)[1].lower()
    if suffix == ".zip":
        with open(path, "rb") as handle:
            return stage_upload(os.path.basename(path), handle.read())
    if suffix in (".md", ".json"):
        with open(path, "rb") as handle:
            return stage_upload(os.path.basename(path), handle.read())
    _fail(f"不支持的文件类型「{suffix or '无扩展名'}」，请选择目录、.zip、.md 或 .json。")


# ---------------------------------------------------------------------- #
# 卸载 / 导出
# ---------------------------------------------------------------------- #
def uninstall(skill_id: str) -> bool:
    """删除技能包目录；返回是否真的删掉了东西。"""
    target = os.path.join(packages_dir(), skill_id)
    if not _inside(packages_dir(), target) or not os.path.isdir(target):
        return False
    shutil.rmtree(target, ignore_errors=True)
    return not os.path.isdir(target)


def installed_dir(skill_id: str) -> Optional[str]:
    target = os.path.join(packages_dir(), skill_id)
    return target if os.path.isdir(target) else None


def export_zip(skill_id: str, archive_name: Optional[str] = None) -> Tuple[bytes, str]:
    """把一个已安装技能打包成 zip，返回 ``(bytes, filename)``。"""
    root = installed_dir(skill_id)
    if not root:
        _fail(f"技能「{skill_id}」没有已安装的包目录，无法导出。")
    files = _walk_files(root)
    if len(files) > MAX_ENTRIES:
        _fail(f"技能包文件数超过 {MAX_ENTRIES}，拒绝导出。")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for rel in files:
            full = os.path.join(root, rel)
            archive.write(full, arcname=f"{skill_id}/{rel}")
    name = archive_name or f"{skill_id}.zip"
    return buffer.getvalue(), name
