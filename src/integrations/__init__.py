"""
Integrations Module for LiuHao AI OS

Provides:
- ORM integration with SQLAlchemy
- Cloud services integration (storage, functions, queues)
- Identity and access management
- Export functions for configuration creation
"""

from .orm_models import Base, BaseModel, ModelMixin, mapper_registry, SessionManager, create_table, drop_table, add_index, init_models
from .cloud_models import (
    CloudProvider, StorageType, FunctionType, QueueType,
    StorageConfig, StorageObject,
    FunctionConfig, FunctionInvocation,
    QueueConfig, Message, QueueMessageRecord,
    Permission, Role, User,
    create_storage_config, create_function_config, create_queue_config,
)

# Module-level references
__all__ = [
    "Base", "BaseModel", "ModelMixin", "mapper_registry",
    "SessionManager", "create_table", "drop_table", "add_index", "init_models",
    "CloudProvider", "StorageType", "FunctionType", "QueueType",
    "StorageConfig", "StorageObject",
    "FunctionConfig", "FunctionInvocation",
    "QueueConfig", "Message", "QueueMessageRecord",
    "Permission", "Role", "User",
    "create_storage_config", "create_function_config", "create_queue_config",
]
