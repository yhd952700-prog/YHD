"""
Deployment Models for LiuHao AI OS

Defines the data model for deployment configurations, Docker images, and release pipelines.
Provides standardized interfaces for different deployment targets and strategies.
"""

from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List
from enum import Enum, auto
from datetime import datetime


class DeploymentTarget(Enum):
    """Deployment target enumeration."""
    DOCKER = "docker"
    KUBERNETES = "kubernetes"
    DOCKER_COMPOSE = "docker_compose"
    VPS = "vps"  # Virtual Private Server


class DeploymentStrategy(Enum):
    """Deployment strategy enumeration."""
    BLUE_GREEN = "blue_green"      # 蓝绿部署
    CANARY = "canary"              # 金丝雀部署
    ROLLING = "rolling"            # 滚动更新
    RECREATE = "recreate"          # 重新创建


class DeploymentStatus(Enum):
    """Deployment status enumeration."""
    PENDING = "pending"      # 等待中
    BUILDING = "building"    # 构建中
    IN_PROGRESS = "in_progress" # 进行中
    SUCCESS = "success"      # 成功
    FAILED = "failed"        # 失败
    CANCELLED = "cancelled"  # 取消


@dataclass
class DockerConfig:
    """Docker configuration for containerized deployment."""
    image_name: str
    tag: str = "latest"
    dockerfile_path: str = "Dockerfile"
    build_args: Dict[str, str] = field(default_factory=dict)
    registry: Optional[str] = None  # e.g., "registry.hub.docker.com"
    push_to_registry: bool = False
    port_mapping: Dict[str, str] = field(default_factory=lambda: {"8080": "8080"})
    environment_vars: Dict[str, str] = field(default_factory=dict)
    health_check: Optional[Dict[str, Any]] = None
    resources: Optional[Dict[str, str]] = None  # e.g., {"memory": "512m", "cpu": "0.5"}


@dataclass
class HealthCheckConfig:
    """Health check configuration."""
    test: str                              # curl -f http://localhost:8080/health || exit 1
    interval: str = "30s"                # 检查间隔
    timeout: str = "10s"                 # 超时时间
    retries: int = 3                     # 连续失败重试次数
    start_period: str = "10s"            # 启动后等待时间


@dataclass
class DeploymentConfig:
    """Overall deployment configuration."""
    target: DeploymentTarget
    strategy: DeploymentStrategy = DeploymentStrategy.ROLLING
    docker_config: Optional[DockerConfig] = None
    health_check: Optional[HealthCheckConfig] = None
    environment: Dict[str, str] = field(default_factory=dict)
    secrets: Dict[str, str] = field(default_factory=dict)  # sensitive values
    resources: Dict[str, str] = field(default_factory=dict)
    autoscaling: Optional[Dict[str, Any]] = None


@dataclass
class ReleaseRecord:
    """Release/deployment record for traceability."""
    id: str
    version: str
    commit_hash: str
    deployed_at: float
    deployed_by: str
    config: DeploymentConfig
    status: DeploymentStatus
    logs: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class PipelineStep:
    """CI/CD pipeline step."""
    name: str
    status: str = "pending"  # pending, running, completed, failed
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    output: Optional[str] = None
    error: Optional[str] = None


@dataclass
class DeploymentPipeline:
    """CI/CD pipeline configuration."""
    id: str
    name: str
    description: str
    stages: List[str] = field(default_factory=lambda: ["build", "test", "deploy"])
    steps: Dict[str, List[PipelineStep]] = field(default_factory=dict)
    triggers: Dict[str, Any] = field(default_factory=dict)  # webhook, schedule, etc.
    status: str = "stopped"  # stopped, running, completed, failed
    created_at: float = field(default_factory=datetime.now().timestamp)


# Convenience functions


def default_docker_config(image_name: str) -> DockerConfig:
    """Create default Docker config for a given image name."""
    return DockerConfig(
        image_name=image_name,
        tag="latest",
        port_mapping={"8080": "8080"},
        health_check=HealthCheckConfig(
            test="curl -f http://localhost:8080/health || exit 1",
            start_period="10s",
        ),
    )


def default_deployment_config(target: DeploymentTarget) -> DeploymentConfig:
    """Create default deployment config for a given target."""
    return DeploymentConfig(
        target=target,
        health_check=HealthCheckConfig(),
    )


# ==================== Export Functions ====================


def create_deployment_config(
    target: DeploymentTarget,
    strategy: DeploymentStrategy = DeploymentStrategy.ROLLING,
    **kwargs: Any,
) -> DeploymentConfig:
    """Create a deployment configuration."""
    config = DeploymentConfig(
        target=target,
        strategy=strategy,
        **kwargs,
    )
    return config


def default_docker_config(image_name: str) -> DockerConfig:
    """Create default Docker config for a given image name."""
    return DockerConfig(
        image_name=image_name,
        tag="latest",
        port_mapping={"8080": "8080"},
        health_check=HealthCheckConfig(
            test="curl -f http://localhost:8080/health || exit 1",
            start_period="10s",
        ),
    )


def default_health_check() -> HealthCheckConfig:
    """Create default health check config."""
    return HealthCheckConfig()


def create_pipeline(pipeline_id: str, name: str) -> DeploymentPipeline:
    """Create a deployment pipeline."""
    return DeploymentPipeline(id=pipeline_id, name=name)