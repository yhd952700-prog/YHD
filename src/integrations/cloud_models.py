"""
Cloud Services Integration Models for LiuHua AI OS

Defines the data model and utilities for cloud service integration:
- Object storage (S3/阿里云 OSS)
- Cloud functions (AWS Lambda/函数计算)
- Message queues (SQS/RocketMQ)
- Identity and access management
"""

from typing import Dict, Any, Optional, List, Union
from datetime import datetime
import uuid


class CloudProvider:
    """Cloud provider enumeration."""
    AWS = "aws"
    ALIYUN = "aliyun"
    TENCENTCLOUD = "tencentcloud"
    GOOGLECLOUD = "googlecloud"


class StorageType:
    """Object storage type."""
    S3 = "s3"
    OSS = "oss"  # 阿里云 OSS
    GCS = "gcs"  # Google Cloud Storage


class FunctionType:
    """Cloud function type."""
    LAMBDA = "lambda"  # AWS Lambda
    FC = "fc"  # 阿里云函数计算


class QueueType:
    """Message queue type."""
    SQS = "sqs"  # AWS SQS
    ROCKETMQ = "rocketmq"  # 虚线 RocketMQ


# ==================== Object Storage ====================

class StorageConfig:
    """Configuration for object storage."""
    
    def __init__(
        self,
        provider: str,
        bucket: str,
        region: str,
        access_key: str,
        secret_key: str,
        endpoint: Optional[str] = None,
    ):
        self.provider = provider
        self.bucket = bucket
        self.region = region
        self.access_key = access_key
        self.secret_key = secret_key
        self.endpoint = endpoint
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "provider": self.provider,
            "bucket": self.bucket,
            "region": self.region,
            "endpoint": self.endpoint,
        }


class StorageObject:
    """Represents an object in object storage."""
    
    def __init__(
        self,
        key: str,
        size: int,
        content_type: str,
        last_modified: datetime,
        etag: str,
        metadata: Optional[Dict[str, Any]] = None,
        owner: Optional[str] = None,
    ):
        self.key = key
        self.size = size
        self.content_type = content_type
        self.last_modified = last_modified
        self.etag = etag
        self.metadata = metadata or {}
        self.owner = owner
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "key": self.key,
            "size": self.size,
            "content_type": self.content_type,
            "last_modified": self.last_modified.isoformat() if self.last_modified else None,
            "etag": self.etag,
            "metadata": self.metadata,
            "owner": self.owner,
        }


# ==================== Cloud Functions ====================

class FunctionConfig:
    """Configuration for cloud function."""
    
    def __init__(
        self,
        provider: str,
        function_name: str,
        runtime: str,
        handler: str,
        timeout: int = 30,
        memory: int = 128,
        environment: Optional[Dict[str, Any]] = None,
    ):
        self.provider = provider
        self.function_name = function_name
        self.runtime = runtime
        self.handler = handler
        self.timeout = timeout
        self.memory = memory
        self.environment = environment or {}
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "provider": self.provider,
            "function_name": self.function_name,
            "runtime": self.runtime,
            "handler": self.handler,
            "timeout": self.timeout,
            "memory": self.memory,
            "environment": self.environment,
        }


class FunctionInvocation:
    """Represents a cloud function invocation."""
    
    def __init__(
        self,
        invocation_id: str,
        function_name: str,
        status: str,
        start_time: datetime,
        end_time: Optional[datetime],
        request_id: str,
        logs: Optional[str] = None,
        output: Optional[Any] = None,
        cost_ms: Optional[int] = None,
    ):
        self.invocation_id = invocation_id
        self.function_name = function_name
        self.status = status
        self.start_time = start_time
        self.end_time = end_time
        self.request_id = request_id
        self.logs = logs
        self.output = output
        self.cost_ms = cost_ms
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "invocation_id": self.invocation_id,
            "function_name": self.function_name,
            "status": self.status,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "request_id": self.request_id,
            "logs": self.logs,
            "output": str(self.output)[:100] if self.output else None,  # Truncate
            "cost_ms": self.cost_ms,
        }


# ==================== Message Queues ====================

class QueueConfig:
    """Configuration for message queue."""
    
    def __init__(
        self,
        provider: str,
        queue_name: str,
        region: str,
        access_key: str,
        secret_key: str,
        endpoint: Optional[str] = None,
    ):
        self.provider = provider
        self.queue_name = queue_name
        self.region = region
        self.access_key = access_key
        self.secret_key = secret_key
        self.endpoint = endpoint
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "provider": self.provider,
            "queue_name": self.queue_name,
            "region": self.region,
            "endpoint": self.endpoint,
        }


class Message:
    """Represents a message in a queue."""
    
    def __init__(
        self,
        message_id: str,
        body: Any,
        headers: Optional[Dict[str, Any]] = None,
        delay_seconds: int = 0,
        expiration: Optional[int] = None,
        message_system_timestamp: Optional[datetime] = None,
    ):
        self.message_id = message_id
        self.body = body
        self.headers = headers or {}
        self.delay_seconds = delay_seconds
        self.expiration = expiration
        self.message_system_timestamp = message_system_timestamp or datetime.utcnow()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "message_id": self.message_id,
            "body": str(self.body)[:500] if self.body else None,  # Truncate
            "headers": self.headers,
            "delay_seconds": self.delay_seconds,
            "expiration": self.expiration,
            "message_system_timestamp": self.message_system_timestamp.isoformat()
            if self.message_system_timestamp
            else None,
        }


class QueueMessageRecord:
    """Record of a sent/received message."""
    
    def __init__(
        self,
        message_id: str,
        queue_name: str,
        status: str,  # sent, received, failed, deleted
        sent_time: Optional[datetime] = None,
        receive_count: int = 0,
        failure_reason: Optional[str] = None,
    ):
        self.message_id = message_id
        self.queue_name = queue_name
        self.status = status
        self.sent_time = sent_time or datetime.utcnow()
        self.receive_count = receive_count
        self.failure_reason = failure_reason
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "message_id": self.message_id,
            "queue_name": self.queue_name,
            "status": self.status,
            "sent_time": self.sent_time.isoformat() if self.sent_time else None,
            "receive_count": self.receive_count,
            "failure_reason": self.failure_reason,
        }


# ==================== Identity and Access Management ====================

class Permission:
    """Permission model."""
    
    def __init__(
        self,
        name: str,
        description: str = "",
        resource: str = "",
        action: str = "",
    ):
        self.name = name
        self.description = description
        self.resource = resource
        self.action = action
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "description": self.description,
            "resource": self.resource,
            "action": self.action,
        }


class Role:
    """Role model."""
    
    def __init__(
        self,
        name: str,
        description: str = "",
        permissions: Optional[List[Permission]] = None,
    ):
        self.name = name
        self.description = description
        self.permissions = permissions or []
    
    def add_permission(self, permission: Permission) -> None:
        """Add a permission to the role."""
        self.permissions.append(permission)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "description": self.description,
            "permissions": [p.to_dict() for p in self.permissions],
        }


class User:
    """User model with role-based access."""
    
    def __init__(
        self,
        user_id: str,
        username: str,
        email: str,
        roles: Optional[List[Role]] = None,
        is_active: bool = True,
        is_superuser: bool = False,
    ):
        self.user_id = user_id
        self.username = username
        self.email = email
        self.roles = roles or []
        self.is_active = is_active
        self.is_superuser = is_superuser
    
    def has_permission(self, resource: str, action: str) -> bool:
        """Check if user has a specific permission."""
        if self.is_superuser:
            return True
        
        for role in self.roles:
            for perm in role.permissions:
                if perm.resource == resource and perm.action == action:
                    return True
        return False
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "user_id": self.user_id,
            "username": self.username,
            "email": self.email,
            "is_active": self.is_active,
            "is_superuser": self.is_superuser,
            "roles": [r.to_dict() for r in self.roles],
        }


# ==================== Export Functions ====================

def create_storage_config(
    provider: str,
    bucket: str,
    region: str,
    access_key: str,
    secret_key: str,
    endpoint: Optional[str] = None,
) -> StorageConfig:
    """Create a storage configuration."""
    return StorageConfig(
        provider=provider,
        bucket=bucket,
        region=region,
        access_key=access_key,
        secret_key=secret_key,
        endpoint=endpoint,
    )


def create_function_config(
    provider: str,
    function_name: str,
    runtime: str,
    handler: str,
    timeout: int = 30,
    memory: int = 128,
    environment: Optional[Dict[str, Any]] = None,
) -> FunctionConfig:
    """Create a function configuration."""
    return FunctionConfig(
        provider=provider,
        function_name=function_name,
        runtime=runtime,
        handler=handler,
        timeout=timeout,
        memory=memory,
        environment=environment,
    )


def create_queue_config(
    provider: str,
    queue_name: str,
    region: str,
    access_key: str,
    secret_key: str,
    endpoint: Optional[str] = None,
) -> QueueConfig:
    """Create a queue configuration."""
    return QueueConfig(
        provider=provider,
        queue_name=queue_name,
        region=region,
        access_key=access_key,
        secret_key=secret_key,
        endpoint=endpoint,
    )