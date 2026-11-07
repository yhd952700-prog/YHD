"""覆盖升级清理：程序目录里「新版已不随包」的旧配置必须被删掉。

0.1.1 之前 ``config/production.yaml`` 一直在包里，打包清单虽已排除，升级后
仍留在程序目录，等于「排除了又被带回来」。``scripts/purge_installed_config.py``
按与打包清单相同的规则清理，并由安装器 ``[Run]`` 调用。
"""

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


purge_module = _load(
    "purge_installed_config", REPO / "scripts" / "purge_installed_config.py"
)
stage = _load("stage_payload_for_purge", REPO / "packaging" / "stage_payload.py")


def test_purge_list_matches_packaging_excludes_minus_user_secrets():
    assert set(purge_module.CONFIG_EXCLUDES) == (
        set(stage.CONFIG_EXCLUDES) - set(purge_module.PRESERVED)
    ), "清理清单必须与 packaging/stage_payload.CONFIG_EXCLUDES 同步（去掉用户凭据）"
    assert purge_module.PRESERVED == ("auth_secrets.json", "human_identities.json")


def test_purge_removes_excluded_but_keeps_secrets_and_new_files(tmp_path):
    app = tmp_path / "LiuHao"
    (app / "runtime").mkdir(parents=True)
    config = app / "config"
    config.mkdir()

    (config / "production.yaml").write_text("old: true\n", encoding="utf-8")
    (config / "production").mkdir()
    (config / "production" / "deploy.ps1").write_text("", encoding="utf-8")
    (config / "docker.env").write_text("X=1\n", encoding="utf-8")
    (config / "auth_secrets.json.lock").write_text("", encoding="utf-8")
    (config / "site.pem").write_text("", encoding="utf-8")

    # 必须保留：用户凭据 + 新版仍随包的配置。
    (config / "auth_secrets.json").write_text("{}", encoding="utf-8")
    (config / "human_identities.json").write_text("{}", encoding="utf-8")
    (config / "logging.yaml").write_text("level: INFO\n", encoding="utf-8")

    removed = purge_module.purge(app)

    assert sorted(removed) == sorted(
        ["production", "production.yaml", "docker.env", "auth_secrets.json.lock", "site.pem"]
    )
    assert not (config / "production.yaml").exists()
    assert not (config / "production").exists()
    assert (config / "auth_secrets.json").is_file()
    assert (config / "human_identities.json").is_file()
    assert (config / "logging.yaml").is_file()


def test_purge_refuses_unknown_root(tmp_path):
    target = tmp_path / "not-an-install"
    (target / "config").mkdir(parents=True)
    (target / "config" / "production.yaml").write_text("x\n", encoding="utf-8")

    assert purge_module.main(["--root", str(target)]) == 2
    assert (target / "config" / "production.yaml").is_file()

    # 正常安装根：退出码 0，并清掉 production.yaml。
    (target / "LiuHao.bat").write_text("@echo off\n", encoding="ascii")
    assert purge_module.main(["--root", str(target)]) == 0
    assert not (target / "config" / "production.yaml").exists()
