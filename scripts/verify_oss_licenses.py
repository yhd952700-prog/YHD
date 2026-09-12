#!/usr/bin/env python
"""校验开源组件的许可证是否仍与 `oss-registry.yaml` 登记一致。

为什么需要它：许可证会变（Redis v8 -> AGPLv3、Elasticsearch -> SSPL、
VectorChord -> AGPL），而 pyproject/requirements 里的版本号不会告诉你这件事。
人工"记得看一眼"不是控制手段，会漂移。这个脚本把"每个外部组件的来源与许可证
已被登记、受限许可证已被书面接受"变成 CI 里的一条硬约束。

判定：
  1. 登记册存在且可解析；
  2. 已安装组件的实测许可证与登记值一致（宽松匹配，容忍 "MIT" / "MIT License"）；
  3. 落入受限许可证（AGPL/GPL/SSPL/Elastic-2.0/BSL...）的组件必须带
     `acknowledged` 理由，否则**构建失败**；
  4. 无法识别的许可证同样需要 acknowledged。

用法：
    .venv\\Scripts\\python.exe scripts\\verify_oss_licenses.py

退出码：0 = 全部通过；1 = 存在未登记的许可证风险。
"""
from __future__ import annotations

import importlib.metadata as md
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402  (必须在 sys.path 引导之后)

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY = REPO_ROOT / "oss-registry.yaml"


def installed_license(dist_name: str) -> str:
    """从包元数据读真实许可证。优先 License-Expression，回退 classifier。"""
    try:
        meta = md.metadata(dist_name)
    except md.PackageNotFoundError:
        return ""
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


def normalise(text: str) -> str:
    return "".join(ch for ch in (text or "").lower() if ch.isalnum())


# 包元数据里的许可证写法千奇百怪："Apache-2.0" / "Apache Software License" /
# "BSD-3-Clause" / "BSD License" 指的是同一个东西。按"许可证族"比较，
# 跨族的不一致（MIT vs GPL）依然会被抓出来 —— 那才是真正要报警的情况。
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
    "bsl",
)


def family(text: str) -> str:
    """把任意许可证写法归一化到它的族；无法识别则回退为原字符串。"""
    flat = normalise(text)
    if not flat:
        return ""
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


def main() -> int:
    if not REGISTRY.exists():
        print(f"[FAIL] 找不到登记册 {REGISTRY}")
        return 1

    with REGISTRY.open(encoding="utf-8") as fh:
        reg = yaml.safe_load(fh) or {}

    # 名单同样按"族"匹配，否则 'Apache Software License' 会被误判为不在允许名单里。
    allow = {family(x) for x in reg.get("allow_licenses") or []}
    restricted = {family(x) for x in reg.get("restricted_licenses") or []}
    components = reg.get("components") or []

    if not components:
        print("[FAIL] 登记册里没有任何组件")
        return 1

    failures: list[str] = []
    checked = skipped = 0

    for comp in components:
        name = comp.get("name")
        if not name:
            failures.append("登记册里有一条记录缺少 name")
            continue

        actual = installed_license(name)
        if not actual:
            skipped += 1
            print(f"  [skip] {name}（本机未安装，交由 CI 校验）")
            continue

        checked += 1
        declared = comp.get("license") or ""
        acknowledged = (comp.get("acknowledged") or "").strip()
        actual_family = family(actual)
        is_restricted = actual_family in restricted
        is_allowed = actual_family in allow

        if not lic_compatible(declared, actual):
            failures.append(
                f"{name}: 登记许可证 {declared!r} 与实测 {actual!r} 不一致 —— "
                f"许可证可能已变更，请人工确认后更新登记册"
            )
            continue

        if is_restricted and not acknowledged:
            failures.append(
                f"{name}: 受限许可证 {actual!r} 且没有 acknowledged 理由 —— "
                f"必须在登记册写明接受原因与约束，否则不得使用"
            )
            continue

        if not is_allowed and not is_restricted and not acknowledged:
            failures.append(
                f"{name}: 许可证 {actual!r} 既不在允许名单也不在受限名单，"
                f"无法判定 —— 请归类后登记"
            )
            continue

        tag = "review" if (is_restricted or not is_allowed) else "ok"
        print(f"  [{tag}] {name} = {actual}")

    print()
    print(f"已校验 {checked} 个组件，跳过 {skipped} 个（未安装）")

    if failures:
        print(f"[FAIL] 发现 {len(failures)} 项许可证风险：")
        for item in failures:
            print(f"  - {item}")
        return 1

    print("[GREEN] 所有已安装组件的许可证均已登记且可接受")
    return 0


if __name__ == "__main__":
    sys.exit(main())
