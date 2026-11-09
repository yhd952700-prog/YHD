#!/usr/bin/env python
"""校验开源组件许可证，输入是**实际分发集**而不是登记册本身。

为什么需要它：许可证会变（Redis v8 -> AGPLv3、Elasticsearch -> SSPL），而版本号
不会告诉你这件事。旧版脚本只遍历 `oss-registry.yaml`：登记册里没写、但实际随包
的组件永远不会被检查，于是门禁永远绿。本脚本把方向翻过来 —— 先枚举「实际分发
集」，再逐项要求它在登记册里存在，做**双向**校验。

两个方向：
  1. 随包有、登记表没有 → **FAIL**（出货含未登记组件，许可证无人审过）；
  2. 登记表有、随包没有 → 打印 `[drift]`（声明面与分发面不一致，供人确认；
     不阻断，因为「声明但从不随包」本身不构成分发风险）。

输入（三选一，脚本会把实际用的输入打印在第一行）：
  --dist-path PATH   指定一个 site-packages 或 venv 根目录（随包目录优先）；
                     这是**发布门禁**该用的模式：随包有未登记项即判红。
  --env              使用当前解释器已安装的分发（CI 的 `pip install -e .[tests]`
                     环境含 dev extras，不等于随包集；此模式下「随包未登记」
                     只告警不判红，许可证不一致 / 受限无 acknowledged 仍判红）。
  默认               仓库内 `dist/payload/app/runtime/Lib/site-packages` 存在时
                     用它（等同 --dist-path），否则回退 --env 并打印说明。

判定（对实际分发集）：
  1. 登记册存在且可解析；
  2. 每个实际分发都必须在登记册里（否则 FAIL）；
  3. 实测许可证与登记值按「许可证族」一致（容忍 MIT / MIT License 写法差异）；
  4. 受限许可证（AGPL/GPL/LGPL/SSPL/Elastic-2.0/BSL...）必须带 `acknowledged`；
  5. 既不在允许名单也不在受限名单的许可证，同样需要 `acknowledged`。

用法：
    python scripts/verify_oss_licenses.py --dist-path dist/payload/app/runtime/Lib/site-packages
    python scripts/verify_oss_licenses.py --env

退出码：0 = 通过；1 = 实际分发集存在未登记组件或许可证风险。
"""
from __future__ import annotations

import argparse
import importlib.metadata as md
import re
import sys
from dataclasses import dataclass
from email.parser import HeaderParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402  (必须在 sys.path 引导之后)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY = REPO_ROOT / "oss-registry.yaml"
DEFAULT_PAYLOAD = REPO_ROOT / "dist" / "payload" / "app" / "runtime" / "Lib" / "site-packages"


def normalise_name(name: str) -> str:
    """PEP 503 名称归一化（大小写、-/_/. 差异统一）。"""
    return re.sub(r"[-_.]+", "-", (name or "").strip()).lower()


def normalise(text: str) -> str:
    return "".join(ch for ch in (text or "").lower() if ch.isalnum())


# 顺序重要：必须在 gpl 之前匹配 agpl / lgpl，否则 AGPL 会被误判成 GPL。
_FAMILIES = (
    "agpl",
    "lgpl",
    "gpl",
    "sspl",
    "elastic",
    "openrail",
    "ccbync",
    "apache",
    "bsd",
    "mit",
    "mpl",
    "isc",
    "psf",
    "unlicense",
    "cc0",
    "zpl",
    "bsl",
)


def family(text: str) -> str:
    """把任意许可证写法归一化到它的族；无法识别则回退为原字符串。"""
    flat = normalise(text)
    if not flat:
        return ""
    # 长写法的全称先折叠成族标记，否则会整体匹配失败（如 PSF 的官方全称）。
    _PHRASES = (
        ("pythonsoftwarefoundation", "psf"),
        ("mozillapublic", "mpl"),
        ("zopepublic", "zpl"),
        ("gnuaffero", "agpl"),
        ("gnulesser", "lgpl"),
        ("gnugeneral", "gpl"),
    )
    for phrase, fam in _PHRASES:
        if phrase in flat:
            return fam
    for fam in _FAMILIES:
        if fam in flat:
            return fam
    return flat


def lic_compatible(declared: str, actual: str) -> bool:
    """族相同即视为兼容，例如 'Apache-2.0' 与 'Apache Software License'。"""
    d, a = normalise(declared), normalise(actual)
    if not d or not a:
        return False
    if d in a or a in d:
        return True
    return family(declared) == family(actual) and family(declared) != ""


# --------------------------------------------------------------------------- 分发集
@dataclass
class Dist:
    name: str
    version: str
    license: str
    path: Path | None = None


_LICENSE_FILE_MARKERS = (
    ("MIT", ("mit license", "permission is hereby granted, free of charge")),
    ("Apache-2.0", ("apache license", "apache software license")),
    ("BSD-3-Clause", ("redistribution and use in source and binary forms",)),
    ("MPL-2.0", ("mozilla public license",)),
    ("PSF-2.0", ("python software foundation license",)),
    ("ZPL-2.1", ("zope public license",)),
    ("Unlicense", ("this is free and unencumbered software",)),
    ("LGPL-3.0", ("gnu lesser general public license",)),
    ("GPL-3.0", ("gnu general public license",)),
    ("AGPL-3.0", ("gnu affero general public license",)),
    ("Elastic-2.0", ("elastic license",)),
)


def _license_from_distinfo(dist_info: Path) -> str:
    """只有 METADATA 缺 License 字段时才用：从随包许可证文本里实测许可证族。

    `taskiq-dependencies 1.5.7` 就是这种情况（元数据无字段，包内有 MIT 文本）。
    这里只做「文本 -> 已知许可证」的机械判定，不臆测。
    """
    texts = []
    for pat in ("licenses/*", "LICENSE*", "COPYING*"):
        for p in dist_info.glob(pat):
            if p.is_file():
                try:
                    texts.append(p.read_text(encoding="utf-8", errors="replace").lower())
                except OSError:
                    continue
    blob = "\n".join(texts)
    for label, markers in _LICENSE_FILE_MARKERS:
        if any(m in blob for m in markers):
            return label
    return ""


def _metadata_license(meta) -> str:
    expr = (meta.get("License-Expression") or "").strip()
    if expr:
        return expr
    classifiers = [c for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    if classifiers:
        return classifiers[-1].split("::")[-1].strip()
    raw = (meta.get("License") or "").strip()
    if not raw or len(raw) > 80:  # 正文里塞整份许可证文本的包，无法机器判定
        return ""
    return raw.splitlines()[0].strip()


def _dist_from_distinfo(dist_info: Path) -> Dist | None:
    metadata = dist_info / "METADATA"
    if not metadata.is_file():
        # 发布线正在重建 payload 时会出现「有目录、没元数据」的瞬时状态；
        # 这不是分发组件，跳过并告警，避免把重建竞态误报成门禁缺陷。
        print(f"[WARN] 跳过没有 METADATA 的目录：{dist_info.name}")
        return None
    meta = HeaderParser().parsestr(metadata.read_text(encoding="utf-8", errors="replace"))
    lic = _metadata_license(meta) or _license_from_distinfo(dist_info)
    return Dist(
        name=meta.get("Name") or dist_info.name.split("-")[0],
        version=meta.get("Version") or "",
        license=lic,
        path=dist_info,
    )


def _dist_from_env(dist: md.Distribution) -> Dist:
    meta = dist.metadata
    dist_info = Path(str(getattr(dist, "_path", "") or ""))
    lic = _metadata_license(meta)
    if not lic and dist_info.is_dir():
        lic = _license_from_distinfo(dist_info)
    return Dist(
        name=meta.get("Name") or "",
        version=meta.get("Version") or "",
        license=lic,
        path=dist_info or None,
    )


def load_distributions(dist_path: Path | None, use_env: bool) -> tuple[list[Dist], str, list[str]]:
    """返回 (分发列表, 输入说明, 缺少 METADATA 的目录列表)。dist_path 优先，其次当前环境。"""
    if dist_path is not None:
        base = dist_path
        if (base / "Lib" / "site-packages").is_dir():
            base = base / "Lib" / "site-packages"
        if not base.is_dir():
            raise SystemExit(f"[FAIL] 分发目录不存在：{base}")
        dists, broken = [], []
        for p in sorted(base.glob("*.dist-info")):
            d = _dist_from_distinfo(p)
            (dists if d is not None else broken).append(d if d is not None else p.name)
        return dists, f"--dist-path {base}（枚举 {len(dists)} 个实际分发）", broken

    if use_env:
        dists = [
            _dist_from_env(d)
            for d in md.distributions()
            if normalise_name(d.metadata["Name"]) != "liuhao-ai-os"
        ]
        return (
            dists,
            f"当前解释器环境 {sys.executable}（枚举 {len(dists)} 个已安装分发；含 dev extras，非随包集）",
            [],
        )

    raise SystemExit("[FAIL] 未指定输入：用 --dist-path <site-packages|venv> 或 --env")


def main() -> int:
    parser = argparse.ArgumentParser(description="按实际分发集双向校验许可证")
    parser.add_argument("--dist-path", type=Path, default=None,
                        help="site-packages 目录或 venv 根目录（发布门禁用这个）")
    parser.add_argument("--env", action="store_true",
                        help="使用当前解释器已安装分发（CI 开发环境自检用）")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY,
                        help="登记册路径（默认仓库根 oss-registry.yaml）")
    args = parser.parse_args()

    if args.dist_path is None and not args.env:
        if DEFAULT_PAYLOAD.is_dir():
            args.dist_path = DEFAULT_PAYLOAD
        else:
            args.env = True

    registry = args.registry
    if not registry.is_file():
        print(f"[FAIL] 找不到登记册 {registry}")
        return 1

    reg = yaml.safe_load(registry.read_text(encoding="utf-8")) or {}
    allow = {family(x) for x in reg.get("allow_licenses") or []}
    restricted = {family(x) for x in reg.get("restricted_licenses") or []}
    components = reg.get("components") or []
    if not components:
        print("[FAIL] 登记册里没有任何组件")
        return 1

    try:
        dists, source, broken = load_distributions(args.dist_path, args.env)
    except SystemExit as exc:
        print(exc)
        return 1

    registered = {normalise_name(c.get("name") or ""): c for c in components if c.get("name")}
    strict = args.dist_path is not None

    print(f"门禁输入：{source}")
    print(f"登记册：{registry}（{len(components)} 个组件）")
    print()

    unregistered: list[str] = []
    license_failures: list[str] = []
    checked = 0

    for dist in dists:
        key = normalise_name(dist.name)
        comp = registered.get(key)
        if comp is None:
            unregistered.append(
                f"{dist.name} {dist.version}（许可证实测：{dist.license or '未声明'}）"
            )
            continue

        checked += 1
        declared = comp.get("license") or ""
        acknowledged = (comp.get("acknowledged") or "").strip()
        actual = dist.license
        actual_family = family(actual)
        is_restricted = actual_family in restricted
        is_allowed = actual_family in allow

        if not lic_compatible(declared, actual):
            license_failures.append(
                f"{dist.name}: 登记许可证 {declared!r} 与实测 {actual!r} 不一致 —— "
                f"许可证可能已变更，请人工确认后更新登记册"
            )
            continue

        if is_restricted and not acknowledged:
            license_failures.append(
                f"{dist.name}: 受限许可证 {actual!r} 且没有 acknowledged 理由 —— "
                f"必须在登记册写明接受原因与约束，否则不得分发"
            )
            continue

        if not is_allowed and not is_restricted and not acknowledged:
            license_failures.append(
                f"{dist.name}: 许可证 {actual!r} 既不在允许名单也不在受限名单，"
                f"无法判定 —— 请归类后登记"
            )
            continue

        tag = "review" if (is_restricted or not is_allowed) else "ok"
        print(f"  [{tag}] {dist.name} {dist.version} = {actual}")

    actual_names = {normalise_name(d.name) for d in dists}
    drift = [c["name"] for c in components if normalise_name(c["name"]) not in actual_names]
    blocking = (unregistered if strict else []) + license_failures
    if strict and broken:
        blocking += [
            f"dist-info 缺少 METADATA：{name}（payload 不完整或正在重建，不能代表实际分发集）"
            for name in broken
        ]

    print()
    print(f"实际分发 {len(dists)} 个｜登记 {len(components)} 个｜"
          f"随包且已登记 {checked} 个｜随包未登记 {len(unregistered)} 个｜"
          f"登记未随包（drift）{len(drift)} 个｜不完整 dist-info {len(broken)} 个")

    if unregistered and not strict:
        print(f"[WARN] 当前环境有 {len(unregistered)} 个未登记分发（--env 模式不判红，"
              "发布门禁请用 --dist-path）：")
        for item in unregistered:
            print(f"  - {item}")

    if drift:
        print("[DRIFT] 登记册有、实际分发集没有（声明面≠分发面，供人确认；不阻断）：")
        for name in drift:
            print(f"  - {name}")

    if blocking:
        print(f"[FAIL] 实际分发集存在 {len(blocking)} 项阻止项：")
        for item in blocking:
            print(f"  - {item}")
        return 1

    print("[GREEN] 实际分发集内每个组件都已登记，且许可证与登记一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
