"""配置管理器 for LiuHao AI OS.

提供统一的配置加载、验证和优先级管理。
支持以下配置来源（优先级从高到低）:
1. 环境变量
2. .env 文件
3. configs/ 目录下的配置文件
4. 默认值
"""

import os
import json
from pathlib import Path
from typing import Any, Dict

project_dir = r'D:\LiuHao-AI-OS'
CONFIG_DIR = Path(project_dir) / "configs"
ENV_FILE = Path(project_dir) / ".env"

# 配置优先级（从高到低）
CONFIG_PRIORITY = [
    "environment_variables",  # 最高优先级
    "env_file",
    "config_files",
    "defaults",  # 最低优先级
]


class ConfigError(Exception):
    """配置错误"""
    pass


class ConfigManager:
    """配置管理器单例"""

    _instance = None
    _configured = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._configured:
            self._configs: Dict[str, Any] = {}
            self._env_configs: Dict[str, Any] = {}
            self._file_configs: Dict[str, Any] = {}
            self._final_config: Dict[str, Any] = {}
            self._validation_schema: Dict[str, Any] = {}
            self._initialize()
            ConfigManager._configured = True

    def _initialize(self):
        """初始化配置管理器"""
        try:
            self._load_env_file()
            self._load_environment_variables()
            self._load_config_files()
            self._merge_configs()
            self._validate_configs()
        except Exception as e:
            # 如果配置加载失败，使用最小化默认配置
            print(f"[ConfigManager] 配置加载警告: {e}")
            self._load_defaults()

    def _load_env_file(self):
        """加载 .env 文件"""
        if ENV_FILE.exists():
            with open(ENV_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip().strip('"').strip("'")
                        self._env_configs[key] = value

    def _load_environment_variables(self):
        """加载环境变量（优先级高于 .env 文件）"""
        # 获取所有以 LIUHAO_ 或相关前缀开头的环境变量
        # 或者项目特定的环境变量
        relevant_vars = [
            'AI_PROVIDER_TYPE', 'AI_PROVIDER_KEY', 'AI_PROVIDER_MODEL',
            'APP_ENV', 'DATABASE_URL', 'LOG_LEVEL', 'METRICS_PERSIST',
            'SECRET_KEY', 'JWT_SECRET'
        ]

        for var in relevant_vars:
            val = os.environ.get(var)
            if val is not None:
                self._env_configs[var] = val

    def _load_config_files(self):
        """加载 configs/ 目录下的配置文件"""
        if not CONFIG_DIR.exists():
            return

        # 加载所有.yml和.json配置文件
        for config_file in sorted(CONFIG_DIR.glob("*.yml")) + sorted(CONFIG_DIR.glob("*.json")):
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    content = f.read()

                if config_file.suffix == '.json':
                    file_config = json.loads(content)
                elif config_file.suffix == '.yml':
                    # 简单的YAML解析（仅支持基本键值对）
                    import re
                    file_config = {}
                    for match in re.finditer(r'(\w+):\s*"([^"]*)"', content):
                        file_config[match.group(1)] = match.group(2)
                    # 也尝试不带引号的值
                    for match in re.finditer(r'(\w+):\s*(\S+)', content):
                        key, value = match.group(1), match.group(2)
                        if key not in file_config:
                            file_config[key] = value

                # 文件名作为键前缀
                file_key = config_file.stem
                self._file_configs[file_key] = file_config
            except Exception as e:
                print(f"[ConfigManager] 加载配置文件 {config_file} 失败: {e}")

    def _merge_configs(self):
        """合并配置（优先级：环境变量 > .env > configs > defaults）"""
        # 从低优先级到高优先级合并
        self._configs = {}

        # 1. 默认配置（最低优先级）
        self._configs.update(self._get_defaults())

        # 2. configs/ 目录配置
        self._configs.update(self._file_configs)

        # 2. .env 文件配置
        self._configs.update(self._env_configs)

        # 3. 环境变量（最高优先级）
        # 这里的 os.environ 已经是最高优先级，无需合并
        # 但我们仍然记录它们以便查询

        self._final_config = dict(self._configs)

    def _get_defaults(self) -> Dict[str, Any]:
        """获取默认配置"""
        return {
            "ai_provider_type": "openai",
            "ai_provider_model": "gpt-4o-mini",
            "ai_provider_key": "",
            "app_env": "development",
            "log_level": "INFO",
            "metrics_persist": "0",
            "temperature": "0.7",
            "max_tokens": "1000",
        }

    def _validate_configs(self):
        """验证配置完整性"""
        required_keys = [
            "ai_provider_type",
            "ai_provider_model",
        ]

        missing = []
        for key in required_keys:
            if key not in self._final_config or self._final_config[key] in [None, ""]:
                missing.append(key)

        if missing:
            raise ConfigError(f"缺少必要配置: {', '.join(missing)}")

    def get(self, key: str, default: Any = None) -> Any:
        """获取配置值"""
        return self._final_config.get(key, default)

    def get_all(self) -> Dict[str, Any]:
        """获取所有配置"""
        return dict(self._final_config)

    def reload(self):
        """重新加载配置"""
        self._instance = None
        ConfigManager._configured = False
        self._instance = ConfigManager()
        return self._instance._final_config

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def __contains__(self, key: str) -> bool:
        return key in self._final_config


# 全局单例实例
_config_manager: ConfigManager = None


def get_config() -> ConfigManager:
    """获取配置管理器单例"""
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager()
    return _config_manager


def init_config():
    """初始化配置（在应用启动时调用）"""
    global _config_manager
    _config_manager = ConfigManager()
    return _config_manager


# 便捷函数
def get(key: str, default: Any = None) -> Any:
    """快捷方式：获取配置值"""
    return get_config().get(key, default)


def has(key: str) -> bool:
    """快捷方式：检查配置键是否存在"""
    return key in get_config()


# 在导入模块时自动初始化（可选）
# 这会在import时运行init_config()
# 取消下行注释以启用
# init_config()
