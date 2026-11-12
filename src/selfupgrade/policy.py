"""自升级策略：目标目录白名单、失败上限、以及「改了就拒绝出包」的显式清单。

Boss 要求这份清单能一眼看到，所以它放在 ``release-lock/policy.json`` 的
``package_refusal`` 字段里，代码只负责读取和执行，不在这里藏规则。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import paths


class PolicyError(RuntimeError):
    pass


@dataclass
class Policy:
    auto_upgrade_enabled: bool = False
    require_boss_approval: bool = True
    max_consecutive_failures: int = 3
    target_install_dir: str = str(paths.DEFAULT_TEST_INSTALL)
    target_data_dir: str = str(paths.DEFAULT_TEST_DATA)
    smoke_port: int = paths.DEFAULT_TEST_PORT
    stop_timeout_seconds: int = 60
    package_refusal: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not 1 <= self.max_consecutive_failures <= 10:
            raise PolicyError("max_consecutive_failures 必须在 1..10")
        if not 1 <= self.smoke_port <= 65535:
            raise PolicyError("smoke_port 必须在 1..65535")
        if not 10 <= self.stop_timeout_seconds <= 600:
            raise PolicyError("stop_timeout_seconds 必须在 10..600")
        # 目标目录必须是测试目录；这里会抛 UnsafeTargetError。
        paths.assert_safe_test_target(self.target_install_dir, self.target_data_dir)
        if not self.package_refusal.get("lock_files"):
            raise PolicyError("policy.json 缺少 package_refusal.lock_files（拒绝出包清单）")

    def to_dict(self) -> dict[str, Any]:
        return {
            "auto_upgrade_enabled": self.auto_upgrade_enabled,
            "require_boss_approval": self.require_boss_approval,
            "max_consecutive_failures": self.max_consecutive_failures,
            "target_install_dir": self.target_install_dir,
            "target_data_dir": self.target_data_dir,
            "smoke_port": self.smoke_port,
            "stop_timeout_seconds": self.stop_timeout_seconds,
            "package_refusal": self.package_refusal,
        }


def load_policy(lock_dir: Path | None = None) -> Policy:
    lock_dir = Path(lock_dir or paths.LOCK_DIR)
    path = lock_dir / "policy.json"
    if not path.is_file():
        raise PolicyError(f"缺少 {path}")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    policy = Policy(
        auto_upgrade_enabled=bool(data.get("auto_upgrade_enabled", False)),
        require_boss_approval=bool(data.get("require_boss_approval", True)),
        max_consecutive_failures=int(data.get("max_consecutive_failures", 3)),
        target_install_dir=str(data.get("target_install_dir", paths.DEFAULT_TEST_INSTALL)),
        target_data_dir=str(data.get("target_data_dir", paths.DEFAULT_TEST_DATA)),
        smoke_port=int(data.get("smoke_port", paths.DEFAULT_TEST_PORT)),
        stop_timeout_seconds=int(data.get("stop_timeout_seconds", 60)),
        package_refusal=dict(data.get("package_refusal", {})),
    )
    policy.validate()
    return policy
