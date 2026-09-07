"""结构化日志配置 for LiuHao AI OS."""

import logging
import sys
from pathlib import Path

# 确保日志目录存在
LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

# 日志文件路径
APP_LOG_FILE = LOG_DIR / "app.log"
ERROR_LOG_FILE = LOG_DIR / "error.log"
OPERATION_LOG_FILE = LOG_DIR / "operations.log"

# 结构化日志格式 (包含时间、级别、模块、信息、上下文)
STRUCTURED_LOG_FORMAT = (
    '%(asctime)s | %(levelname)-8s | %(name)s | %(funcName)s:%(lineno)d | '
    '%(message)s | context=%(context)s'
)

# 基础日志配置
LOGGING_CONFIG = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'standard': {
            'format': '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        },
        'structural': {
            'format': STRUCTURED_LOG_FORMAT,
        },
        'detailed': {
            'format': '%(asctime)s [%(levelname)s] %(name)s:%(filename)s:%(funcName)s:%(lineno)d %(message)s',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'stream': sys.stdout,
            'formatter': 'standard',
            'level': 'INFO',
        },
        'file': {
            'class': 'logging.FileHandler',
            'filename': str(APP_LOG_FILE),
            'formatter': 'structural',
            'level': 'INFO',
            'encoding': 'utf-8',
        },
        'error_file': {
            'class': 'logging.FileHandler',
            'filename': str(ERROR_LOG_FILE),
            'formatter': 'detailed',
            'level': 'ERROR',
            'encoding': 'utf-8',
        },
        'operation_file': {
            'class': 'logging.FileHandler',
            'filename': str(OPERATION_LOG_FILE),
            'formatter': 'structural',
            'level': 'INFO',
            'encoding': 'utf-8',
        },
    },
    'loggers': {
        'liuhao': {
            'handlers': ['console', 'file', 'error_file', 'operation_file'],
            'level': 'INFO',
            'propagate': False,
        },
        'liuhao.ai': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': False,
        },
        'liuhao.mcp': {
            'handlers': ['console', 'operation_file'],
            'level': 'INFO',
            'propagate': False,
        },
    },
    'root': {
        'handlers': ['console', 'file'],
        'level': 'INFO',
        'formatter': 'standard',
    },
}

# 上下文日志记录器
class ContextAdapter(logging.LoggerAdapter):
    """带上下文的日志记录器"""
    
    def process(self, msg, kwargs):
        # 添加上下文信息
        context = kwargs.get('extra', {}).get('context', {})
        if context:
            # 确保context以冒号结尾，便于拼接
            context_str = ' | ' + ' | '.join(f'{k}={v}' for k, v in context.items())
            msg = msg + context_str
        return msg, kwargs
    
    def debug(self, msg, **kwargs):
        kwargs.setdefault('extra', {}).setdefault('context', {})
        return super().debug(msg, **kwargs)
    
    def info(self, msg, **kwargs):
        kwargs.setdefault('extra', {}).setdefault('context', {})
        return super().info(msg, **kwargs)
    
    def warning(self, msg, **kwargs):
        kwargs.setdefault('extra', {}).setdefault('context', {})
        return super().warning(msg, **kwargs)
    
    def error(self, msg, **kwargs):
        kwargs.setdefault('extra', {}).setdefault('context', {})
        return super().error(msg, **kwargs)
    
    def critical(self, msg, **kwargs):
        kwargs.setdefault('extra', {}).setdefault('context', {})
        return super().critical(msg, **kwargs)


# 预定义的日志上下文键
CONTEXT_KEYS = {
    'provider': 'AI provider name',
    'model': 'Model name',
    'request_id': 'Request identifier',
    'session_id': 'Session identifier',
    'user_id': 'User identifier',
    'agent_id': 'Agent identifier',
    'mcp_server': 'MCP server name',
    'tool_name': 'Tool name being called',
    'operation': 'Operation being performed',
}

# 获取日志记录器的工厂函数
def get_logger(name: str = __name__, **context) -> ContextAdapter:
    """获取结构化日志记录器"""
    logger = logging.getLogger(name)
    # 确保logger已使用ContextAdapter包装
    # 这里简化处理，直接返回logger并通过调用者传递context
    return ContextAdapter(logger, {'context': context})


# 便捷的日志记录函数
def log_operation(operation: str, **context):
    """记录操作日志的便捷函数"""
    logger = get_logger('liuhao.operation')
    logger.info(f"Operation: {operation}", **context)


def log_provider_action(provider: str, model: str, operation: str, **context):
    """记录Provider操作日志的便捷函数"""
    logger = get_logger('liuhao.provider')
    logger.info(
        f"Provider: {provider}, Model: {model}, Operation: {operation}",
        provider=provider,
        model=model,
        operation=operation,
        **context
    )


# 初始化日志配置（导入时自动配置）
def setup_logging():
    """初始化日志配置"""
    logging.config.dictConfig(LOGGING_CONFIG)
    # 确保日志目录存在
    LOG_DIR.mkdir(exist_ok=True)
    print(f"日志系统初始化完成，日志保存位置: {LOG_DIR.absolute()}")
