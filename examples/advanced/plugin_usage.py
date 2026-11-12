"""
插件使用 - LiuHao AI OS 高级示例

本示例演示插件系统的使用：
1. 加载和注册插件
2. 多插件协同工作
3. 插件生命周期管理
4. 插件注册表使用

运行方式：
    python examples/advanced/plugin_usage.py
"""

import sys
import os
import time
from typing import List, Dict, Any, Optional, Type
from dataclasses import dataclass, field
from enum import Enum

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from plugins.base import (
    Plugin,
    PluginMetadata,
    PluginStatus,
    PluginError,
    PluginLoadError,
    PluginInitError,
    PluginStartError,
)


# ======================================================================
# 示例插件 1: 日志插件
# ======================================================================

class LoggingPlugin(Plugin):
    """
    日志插件 - 提供统一的日志记录功能。

    功能：
    - 分级日志输出
    - 日志格式化
    - 日志持久化
    """

    def __init__(self):
        metadata = PluginMetadata(
            name="logging_plugin",
            version="1.0.0",
            description="提供统一的日志记录和管理功能",
            author="LiuHao Team",
            license="MIT",
            scopes=["logging", "observability"],
            resource_types=["log_entry"],
            auto_start=True,
        )
        super().__init__(metadata)
        self.logs: List[Dict[str, Any]] = []
        self.log_level = "INFO"

    def initialize(self, config: Dict[str, Any] = None) -> bool:
        """初始化插件。"""
        self.status = PluginStatus.INITIALIZED
        if config:
            self.log_level = config.get("log_level", "INFO")
        self._log("INFO", "LoggingPlugin 初始化完成")
        return True

    def start(self) -> bool:
        """启动插件。"""
        self.status = PluginStatus.STARTED
        self._log("INFO", "LoggingPlugin 已启动")
        return True

    def stop(self) -> bool:
        """停止插件。"""
        self._log("INFO", "LoggingPlugin 正在停止")
        self.status = PluginStatus.STOPPED
        return True

    def reload(self) -> bool:
        """重新加载插件配置。"""
        self._log("INFO", "LoggingPlugin 重新加载")
        return True

    # 插件功能方法
    def _log(self, level: str, message: str):
        """记录日志。"""
        entry = {
            "timestamp": time.time(),
            "level": level,
            "message": message,
            "plugin": self.metadata.name,
        }
        self.logs.append(entry)

    def info(self, message: str):
        """记录 INFO 级别日志。"""
        if self._level_enabled("INFO"):
            self._log("INFO", message)

    def warn(self, message: str):
        """记录 WARN 级别日志。"""
        if self._level_enabled("WARN"):
            self._log("WARN", message)

    def error(self, message: str):
        """记录 ERROR 级别日志。"""
        if self._level_enabled("ERROR"):
            self._log("ERROR", message)

    def debug(self, message: str):
        """记录 DEBUG 级别日志。"""
        if self._level_enabled("DEBUG"):
            self._log("DEBUG", message)

    def _level_enabled(self, level: str) -> bool:
        """检查日志级别是否启用。"""
        levels = ["DEBUG", "INFO", "WARN", "ERROR"]
        current_idx = levels.index(self.log_level) if self.log_level in levels else 1
        target_idx = levels.index(level) if level in levels else 1
        return target_idx >= current_idx

    def get_logs(self, level: str = None, limit: int = 100) -> List[Dict[str, Any]]:
        """获取日志记录。"""
        logs = self.logs
        if level:
            logs = [l for l in logs if l["level"] == level]
        return logs[-limit:]


# ======================================================================
# 示例插件 2: 缓存插件
# ======================================================================

class CachePlugin(Plugin):
    """
    缓存插件 - 提供内存缓存功能。

    功能：
    - Key-Value 缓存
    - TTL 过期机制
    - LRU 淘汰策略
    - 缓存统计
    """

    def __init__(self):
        metadata = PluginMetadata(
            name="cache_plugin",
            version="2.1.0",
            description="高性能内存缓存，支持 TTL 和 LRU 淘汰",
            author="LiuHao Team",
            license="Apache-2.0",
            scopes=["caching", "performance"],
            resource_types=["cache_entry"],
            auto_start=True,
        )
        super().__init__(metadata)
        self._cache: Dict[str, Dict[str, Any]] = {}
        self.max_size = 1000
        self.hits = 0
        self.misses = 0

    def initialize(self, config: Dict[str, Any] = None) -> bool:
        """初始化插件。"""
        if config:
            self.max_size = config.get("max_size", 1000)
        self.status = PluginStatus.INITIALIZED
        return True

    def start(self) -> bool:
        """启动插件。"""
        self.status = PluginStatus.STARTED
        return True

    def stop(self) -> bool:
        """停止插件，清空缓存。"""
        self._cache.clear()
        self.status = PluginStatus.STOPPED
        return True

    def get(self, key: str) -> Optional[Any]:
        """
        获取缓存值。

        Args:
            key: 缓存键

        Returns:
            缓存值，如果不存在或已过期则返回 None
        """
        if self.status != PluginStatus.STARTED:
            raise PluginError(f"插件未启动，当前状态: {self.status}")

        entry = self._cache.get(key)
        if entry is None:
            self.misses += 1
            return None

        # 检查是否过期
        if entry["expires_at"] and time.time() > entry["expires_at"]:
            del self._cache[key]
            self.misses += 1
            return None

        # 更新访问时间
        entry["last_accessed"] = time.time()
        entry["access_count"] += 1
        self.hits += 1

        return entry["value"]

    def set(self, key: str, value: Any, ttl_seconds: int = None) -> bool:
        """
        设置缓存值。

        Args:
            key: 缓存键
            value: 缓存值
            ttl_seconds: 过期时间（秒），None 表示永不过期

        Returns:
            是否成功
        """
        if self.status != PluginStatus.STARTED:
            raise PluginError(f"插件未启动，当前状态: {self.status}")

        # 如果达到容量上限，执行 LRU 淘汰
        if len(self._cache) >= self.max_size and key not in self._cache:
            self._evict_lru()

        self._cache[key] = {
            "value": value,
            "created_at": time.time(),
            "last_accessed": time.time(),
            "access_count": 0,
            "expires_at": time.time() + ttl_seconds if ttl_seconds else None,
        }
        return True

    def delete(self, key: str) -> bool:
        """删除缓存项。"""
        if key in self._cache:
            del self._cache[key]
            return True
        return False

    def clear(self) -> int:
        """清空所有缓存，返回清除的条目数。"""
        count = len(self._cache)
        self._cache.clear()
        return count

    def _evict_lru(self):
        """LRU 淘汰：删除最近最少使用的条目。"""
        if not self._cache:
            return

        # 找到最早访问的条目
        oldest_key = min(
            self._cache.keys(),
            key=lambda k: self._cache[k]["last_accessed"]
        )
        del self._cache[oldest_key]

    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计信息。"""
        total = self.hits + self.misses
        hit_rate = self.hits / total if total > 0 else 0
        return {
            "size": len(self._cache),
            "max_size": self.max_size,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": hit_rate,
            "total_requests": total,
        }


# ======================================================================
# 示例插件 3: 通知插件
# ======================================================================

class NotificationPlugin(Plugin):
    """
    通知插件 - 提供多渠道消息通知功能。

    功能：
    - 多渠道通知（邮件、短信、Webhook）
    - 通知模板管理
    - 发送状态追踪
    """

    def __init__(self):
        metadata = PluginMetadata(
            name="notification_plugin",
            version="1.2.0",
            description="多渠道消息通知服务",
            author="LiuHao Team",
            license="MIT",
            scopes=["notification", "communication"],
            resource_types=["notification", "template"],
            requires_internet=True,
        )
        super().__init__(metadata)
        self.channels: Dict[str, bool] = {}
        self.templates: Dict[str, str] = {}
        self.sent_count = 0
        self.failed_count = 0

    def initialize(self, config: Dict[str, Any] = None) -> bool:
        """初始化插件。"""
        if config:
            channels = config.get("channels", {})
            self.channels = channels

        # 内置默认模板
        self.templates = {
            "welcome": "欢迎使用 {app_name}，{username}！",
            "alert": "【告警】{message}",
            "success": "【成功】{message}",
        }

        self.status = PluginStatus.INITIALIZED
        return True

    def start(self) -> bool:
        """启动插件。"""
        # 检查必要的渠道配置
        if not self.channels:
            self.channels = {"console": True}  # 默认启用控制台
        self.status = PluginStatus.STARTED
        return True

    def stop(self) -> bool:
        """停止插件。"""
        self.status = PluginStatus.STOPPED
        return True

    def send(self, channel: str, recipient: str,
             message: str, template: str = None,
             context: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        发送通知。

        Args:
            channel: 发送渠道
            recipient: 接收者
            message: 消息内容
            template: 模板名称
            context: 模板上下文变量

        Returns:
            发送结果
        """
        if self.status != PluginStatus.STARTED:
            raise PluginError("插件未启动")

        # 应用模板
        if template and template in self.templates:
            template_str = self.templates[template]
            if context:
                for key, value in context.items():
                    template_str = template_str.replace("{" + key + "}", str(value))
            message = template_str

        # 发送（模拟）
        success = True
        error = None

        if not self.channels.get(channel, False):
            success = False
            error = f"渠道 {channel} 未启用"
            self.failed_count += 1
        else:
            self.sent_count += 1

        return {
            "success": success,
            "channel": channel,
            "recipient": recipient,
            "message": message,
            "error": error,
            "timestamp": time.time(),
        }

    def add_template(self, name: str, template: str):
        """添加通知模板。"""
        self.templates[name] = template

    def get_stats(self) -> Dict[str, Any]:
        """获取发送统计。"""
        return {
            "sent": self.sent_count,
            "failed": self.failed_count,
            "templates": len(self.templates),
            "channels": list(self.channels.keys()),
        }


# ======================================================================
# 插件管理器
# ======================================================================

class PluginManager:
    """
    插件管理器 - 管理插件的生命周期。

    功能：
    - 插件注册与发现
    - 生命周期管理（加载 → 初始化 → 启动 → 停止 → 卸载）
    - 插件依赖管理
    - 状态监控
    """

    def __init__(self):
        self.plugins: Dict[str, Plugin] = {}
        self._start_order: List[str] = []

    def register(self, plugin: Plugin) -> bool:
        """
        注册一个插件。

        Args:
            plugin: 插件实例

        Returns:
            是否注册成功
        """
        name = plugin.metadata.name
        if name in self.plugins:
            raise PluginLoadError(f"插件 {name} 已存在")

        self.plugins[name] = plugin
        print(f"  📦 注册插件: {name} v{plugin.metadata.version}")
        return True

    def initialize_all(self, configs: Dict[str, Dict[str, Any]] = None) -> int:
        """
        初始化所有已注册的插件。

        Args:
            configs: 各插件的配置字典

        Returns:
            成功初始化的插件数量
        """
        configs = configs or {}
        count = 0

        for name, plugin in self.plugins.items():
            try:
                config = configs.get(name, {})
                plugin.initialize(config)
                count += 1
                print(f"  ✅ 初始化: {name}")
            except Exception as e:
                plugin.status = PluginStatus.ERROR
                print(f"  ❌ 初始化失败: {name} - {e}")

        return count

    def start_all(self) -> int:
        """
        启动所有已初始化的插件。

        Returns:
            成功启动的插件数量
        """
        count = 0

        for name, plugin in self.plugins.items():
            if plugin.status != PluginStatus.INITIALIZED:
                continue

            try:
                if plugin.start():
                    self._start_order.append(name)
                    count += 1
                    print(f"  🚀 已启动: {name}")
            except Exception as e:
                plugin.status = PluginStatus.ERROR
                print(f"  ❌ 启动失败: {name} - {e}")

        return count

    def stop_all(self) -> int:
        """
        按启动的逆序停止所有插件。

        Returns:
            成功停止的插件数量
        """
        count = 0

        # 逆序停止
        for name in reversed(self._start_order):
            plugin = self.plugins.get(name)
            if not plugin or plugin.status != PluginStatus.STARTED:
                continue

            try:
                plugin.stop()
                count += 1
                print(f"  ⏹️  已停止: {name}")
            except Exception as e:
                print(f"  ⚠️  停止异常: {name} - {e}")

        return count

    def get_plugin(self, name: str) -> Optional[Plugin]:
        """获取指定名称的插件。"""
        return self.plugins.get(name)

    def get_plugins_by_scope(self, scope: str) -> List[Plugin]:
        """按功能范围获取插件列表。"""
        return [
            p for p in self.plugins.values()
            if scope in p.metadata.scopes
        ]

    def get_status_report(self) -> Dict[str, Any]:
        """获取所有插件的状态报告。"""
        status_counts = {}
        plugin_statuses = []

        for name, plugin in self.plugins.items():
            status = plugin.status
            status_counts[status] = status_counts.get(status, 0) + 1
            plugin_statuses.append({
                "name": name,
                "version": plugin.metadata.version,
                "status": status,
                "scopes": plugin.metadata.scopes,
            })

        return {
            "total": len(self.plugins),
            "status_counts": status_counts,
            "plugins": plugin_statuses,
        }


# ======================================================================
# 示例 1: 基本插件使用
# ======================================================================

def demo_basic_plugin_usage():
    """
    演示单个插件的基本使用。
    """
    print("=" * 60)
    print("  示例 1：单个插件使用 (缓存插件)")
    print("=" * 60)
    print()

    # 创建并初始化插件
    print("[1/4] 创建缓存插件...")
    cache = CachePlugin()
    print(f"       名称: {cache.metadata.name}")
    print(f"       版本: {cache.metadata.version}")
    print(f"       状态: {cache.status}")

    print("\n[2/4] 初始化插件...")
    cache.initialize({"max_size": 100})
    print(f"       状态: {cache.status}")

    print("\n[3/4] 启动插件...")
    cache.start()
    print(f"       状态: {cache.status}")

    print("\n[4/4] 使用插件功能...")
    # 设置缓存
    cache.set("user:1", {"name": "Alice", "age": 25}, ttl_seconds=300)
    cache.set("user:2", {"name": "Bob", "age": 30}, ttl_seconds=300)
    cache.set("config:theme", "dark")

    print(f"       设置了 3 个缓存项")

    # 获取缓存
    user1 = cache.get("user:1")
    user2 = cache.get("user:2")
    theme = cache.get("config:theme")
    missing = cache.get("nonexistent")

    print(f"       获取 user:1: {user1}")
    print(f"       获取 user:2: {user2}")
    print(f"       获取 config:theme: {theme}")
    print(f"       获取不存在的键: {missing}")

    # 统计信息
    stats = cache.get_stats()
    print(f"\n       缓存统计:")
    print(f"         大小: {stats['size']}/{stats['max_size']}")
    print(f"         命中: {stats['hits']}")
    print(f"         未命中: {stats['misses']}")
    print(f"         命中率: {stats['hit_rate']:.1%}")

    # 停止插件
    print("\n       停止插件...")
    cache.stop()
    print(f"       状态: {cache.status}")

    print()


# ======================================================================
# 示例 2: 多插件协同
# ======================================================================

def demo_multi_plugin_collaboration():
    """
    演示多个插件协同工作。

    场景：使用日志插件 + 缓存插件 + 通知插件构建一个数据处理流程
    """
    print("=" * 60)
    print("  示例 2：多插件协同工作")
    print("=" * 60)
    print()

    # 创建插件管理器
    print("[1/5] 创建插件管理器...")
    manager = PluginManager()
    print()

    # 注册插件
    print("[2/5] 注册插件...")
    logging_plugin = LoggingPlugin()
    cache_plugin = CachePlugin()
    notification_plugin = NotificationPlugin()

    manager.register(logging_plugin)
    manager.register(cache_plugin)
    manager.register(notification_plugin)
    print()

    # 配置
    print("[3/5] 配置并初始化插件...")
    configs = {
        "logging_plugin": {"log_level": "DEBUG"},
        "cache_plugin": {"max_size": 500},
        "notification_plugin": {"channels": {"console": True, "email": False}},
    }
    manager.initialize_all(configs)
    print()

    # 启动
    print("[4/5] 启动所有插件...")
    started = manager.start_all()
    print(f"       成功启动 {started} 个插件")
    print()

    # 使用插件协同工作
    print("[5/5] 插件协同工作演示...")
    print()

    # 获取插件实例
    logger = manager.get_plugin("logging_plugin")
    cache = manager.get_plugin("cache_plugin")
    notifier = manager.get_plugin("notification_plugin")

    # 模拟数据处理流程
    print("  模拟数据处理流程:")
    print()

    # 1. 记录开始日志
    logger.info("开始处理用户数据")

    # 2. 检查缓存
    cache_key = "processed_data:user_123"
    cached_data = cache.get(cache_key)

    if cached_data:
        logger.info(f"缓存命中: {cache_key}")
        result = cached_data
    else:
        logger.info(f"缓存未命中，处理数据...")
        # 模拟数据处理
        processed_data = {"user_id": 123, "score": 95, "level": "A"}
        cache.set(cache_key, processed_data, ttl_seconds=600)
        logger.info("数据处理完成，已写入缓存")
        result = processed_data

    # 3. 发送通知
    logger.info("发送处理完成通知")
    notifier.send(
        channel="console",
        recipient="admin",
        message="",
        template="success",
        context={"message": "用户数据处理完成"},
    )

    # 4. 打印统计
    print()
    print("  各插件状态:")

    log_stats = len(logger.get_logs())
    print(f"    📝 日志插件: {log_stats} 条日志")

    cache_stats = cache.get_stats()
    print(f"    💾 缓存插件: {cache_stats['size']} 项, "
          f"命中率 {cache_stats['hit_rate']:.0%}")

    notif_stats = notifier.get_stats()
    print(f"    📨 通知插件: 发送 {notif_stats['sent']} 条, "
          f"{len(notif_stats['templates'])} 个模板")

    # 状态报告
    print()
    report = manager.get_status_report()
    print(f"  插件总数: {report['total']}")
    for status, count in report['status_counts'].items():
        print(f"    {status}: {count}")

    # 停止所有插件
    print()
    print("  停止所有插件...")
    manager.stop_all()

    print()


# ======================================================================
# 示例 3: 插件生命周期
# ======================================================================

def demo_plugin_lifecycle():
    """
    演示完整的插件生命周期。

    生命周期：LOADED → INITIALIZED → STARTED → STOPPED → UNLOADED
    """
    print("=" * 60)
    print("  示例 3：插件生命周期管理")
    print("=" * 60)
    print()

    plugin = LoggingPlugin()

    lifecycle = [
        ("LOADED", "插件已加载，等待初始化"),
        ("INITIALIZED", "初始化完成，配置已加载"),
        ("STARTED", "插件已启动，功能可用"),
        ("STOPPED", "插件已停止，资源已释放"),
    ]

    print("插件生命周期状态流转:")
    print()

    for i, (status, desc) in enumerate(lifecycle, 1):
        # 执行对应的操作
        if status == "INITIALIZED":
            plugin.initialize()
        elif status == "STARTED":
            plugin.start()
        elif status == "STOPPED":
            plugin.stop()

        bar_len = int(i / len(lifecycle) * 30)
        bar = "█" * bar_len + "░" * (30 - bar_len)
        print(f"  阶段 {i}: [{bar}] {status}")
        print(f"         {desc}")
        print(f"         当前状态: {plugin.status}")
        print()

    print("状态说明:")
    print("  LOADED       - 插件类已加载，但未初始化")
    print("  INITIALIZED  - 配置已加载，资源已分配")
    print("  STARTED      - 插件正常运行，可以调用功能")
    print("  STOPPED      - 插件已停止，功能不可用但可重启")
    print("  ERROR        - 插件遇到错误")
    print()

    # 演示重启
    print("演示插件重启:")
    print(f"  当前状态: {plugin.status}")
    print("  重新初始化...")
    plugin.initialize()
    print(f"  状态: {plugin.status}")
    print("  启动...")
    plugin.start()
    print(f"  状态: {plugin.status}")
    print("  ✅ 插件成功重启")

    print()


# ======================================================================
# 主函数
# ======================================================================

def main():
    """运行所有插件使用示例。"""
    print("\n" + "█" * 60)
    print("  LiuHao AI OS - 插件系统使用")
    print("█" * 60)

    try:
        # 示例 1: 基本使用
        demo_basic_plugin_usage()

        # 示例 2: 多插件协同
        demo_multi_plugin_collaboration()

        # 示例 3: 生命周期
        demo_plugin_lifecycle()

        print("=" * 60)
        print("  插件系统功能总结")
        print("=" * 60)
        print("""
  ✓ 标准插件接口
  ✓ 完整生命周期管理
  ✓ 插件注册与发现
  ✓ 多插件协同工作
  ✓ 按功能范围分类
  ✓ 状态监控与报告
  ✓ 配置化初始化
  ✓ 错误处理与容错
""")

    except Exception as e:
        print(f"\n❌ 运行出错: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
