"""``stage_payload`` 的 config 排除清单必须真的生效。

背景：``"production"`` 在清单里躺了很久，但匹配器只认完整文件名或
``*.ext``，它只命中了同名**目录**，``production.yaml`` 一直在进包。这类
"写进去但永远匹配不上"的静默失效比明面错误更危险，所以这里做三层断言：

1. 每个非防御性条目必须能匹配 ``config/`` 里的真实文件/目录；
2. 真实的 ``_copy_config`` 组装产物里不得出现任何一个被排除目标；
3. 最危险的几个文件名逐个点名，防止"清单里没写，断言就自然通过"。
"""

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "stage_payload_config_excludes", REPO / "packaging" / "stage_payload.py"
)
stage = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = stage
_SPEC.loader.exec_module(stage)

CONFIG_SRC = REPO / "config"


def _matches(name: str, pattern: str) -> bool:
    """与 ``_copy_config`` 的匹配规则保持一致。"""
    if pattern.startswith("*."):
        return name.endswith(pattern[1:])
    return name == pattern


def test_every_non_defensive_exclude_matches_a_real_config_entry():
    names = [path.name for path in CONFIG_SRC.iterdir()]
    dead = [
        pattern
        for pattern in stage.CONFIG_EXCLUDES
        if pattern not in stage.CONFIG_EXCLUDES_DEFENSIVE
        and not any(_matches(name, pattern) for name in names)
    ]
    assert not dead, (
        "这些排除项在 config/ 里匹配不到任何文件/目录，等于静默失效："
        f"{dead}（若确属防御性条目，请登记到 CONFIG_EXCLUDES_DEFENSIVE）"
    )


def test_production_yaml_is_excluded_by_exact_name():
    assert (CONFIG_SRC / "production.yaml").is_file(), "前提：仓库里有 production.yaml"
    matched = [p for p in stage.CONFIG_EXCLUDES if _matches("production.yaml", p)]
    assert matched, (
        "production.yaml 没有被任何排除项命中；注意 'production' 只能匹配同名目录"
    )


def test_real_config_assembly_contains_no_excluded_target(tmp_path):
    app_dir = tmp_path / "app"
    out = app_dir / "config"
    stage._copy_config(CONFIG_SRC, out)

    top_level = [path.name for path in out.iterdir()]
    for pattern in stage.CONFIG_EXCLUDES:
        leaked = [name for name in top_level if _matches(name, pattern)]
        assert not leaked, f"排除项 {pattern!r} 未生效，产物里仍有：{leaked}"

    # 点名断言：即使清单将来漏写，也不允许这些文件出现在产物里。
    for name in (
        "production.yaml",
        "docker.env",
        "auth_secrets.json",
        "human_identities.json",
        "production",
        "external_anchor",
    ):
        assert not (out / name).exists(), f"{name} 不应出现在组装出的 config/ 里"

    # 私钥/证书不能以任何深度随包。
    assert list(out.rglob("*.pem")) == []
    assert list(out.rglob("*.key")) == []

    # 产物本身要能通过构建时的硬拦截。
    stage._assert_payload_hygiene(app_dir)
