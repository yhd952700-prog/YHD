"""
Infra Helm Charts for LiuHao AI OS

Provides Helm Charts for all LiuHao AI OS components:
- API Gateway
- Message Bus
- Model Registry
- Workflow Engine
- Security Components

Each chart includes:
- Deployment configuration
- Service endpoints
- Resource requests/limits
- Health checks
- Environment variable configuration
"""

# Chart metadata
chart_version = "1.0.0"
app_name = "liuhao-ai-os"
app_description = "LiuHao AI Operating System"
app_icon = "https://liuhao-ai-os.example.com/icon.png"
app_kind = "application"
app_category = "AI OS"
app_annotations = {
    "metadata.ibm.com/alb-ingress-enabled": "true",
    "app.kubernetes.io/part-of": "liuhao-ai-os",
    "app.kubernetes.io/managed-by": "helm",
}

# Supported components
COMPONENTS = {
    "gateway": {
        "replicaCount": 2,
        "serviceType": "LoadBalancer",
        "resources": {
            "limits": {"cpu": "500m", "memory": "512Mi"},
            "requests": {"cpu": "100m", "memory": "256Mi"},
        },
        "env": {
            "REDIS_HOST": "redis-master",
            "REDIS_PORT": "6379",
            "MODEL_REGISTRY_URL": "http://model-registry:8080",
        },
    },
    "message-bus": {
        "replicaCount": 1,
        "serviceType": "ClusterIP",
        "resources": {
            "limits": {"cpu": "250m", "memory": "256Mi"},
            "requests": {"cpu": "50m", "memory": "128Mi"},
        },
    },
    "model-registry": {
        "replicaCount": 1,
        "serviceType": "ClusterIP",
        "resources": {
            "limits": {"cpu": "250m", "memory": "256Mi"},
            "requests": {"cpu": "50m", "memory": "128Mi"},
        },
    },
    "workflow-engine": {
        "replicaCount": 2,
        "serviceType": "LoadBalancer",
        "resources": {
            "limits": {"cpu": "500m", "memory": "1Gi"},
            "requests": {"cpu": "100m", "memory": "512Mi"},
        },
    },
    "security": {
        "replicaCount": 1,
        "serviceType": "ClusterIP",
        "resources": {
            "limits": {"cpu": "100m", "memory": "128Mi"},
            "requests": {"cpu": "50m", "memory": "64Mi"},
        },
    },
}


# ==================== Gateway Chart ====================

gateway_chart = {
    "apiVersion": "v2",
    "name": f"{app_name}-gateway",
    "type": "application",
    "description": "LiuHao AI OS API Gateway - Unified entry point for all services",
    "version": chart_version,
    "appVersion": "1.0.0",
    "keywords": ["ai", "os", "gateway", "api"],
    "home": "https://liuhao-ai-os.example.com",
    "icon": app_icon,
    "annotations": app_annotations,
    
    "dependencies": [],
    
    "maintainers": [
        {
            "name": "LiuHao AI OS Team",
            "email": "team@liuhao-ai-os.example.com",
        },
    ],
    
    "keywords": ["ai", "os", "gateway", "api"],
    
    "sources": [
        "https://charts.liuhao-ai-os.example.com",
    ],
    
    "version": chart_version,
    
    "appVersion": "1.0.0",
    
    "dependencies": [
        {
            "name": "redis",
            "version": "19.0.0",
            "repository": "https://charts.bitnami.com/bitnami",
        },
        {
            "name": "postgresql",
            "version": "14.5.0",
            "repository": "https://charts.bitnami.com/bitnami",
        },
    ],
    
    "values": {
        "replicaCount": 2,
        
        "image": {
            "repository": "liuhao-ai-os/gateway",
            "pullPolicy": "IfNotPresent",
            "tag": "latest",
        },
        
        "service": {
            "type": "LoadBalancer",
            "port": 80,
        },
        
        "resources": {
            "limits": {"cpu": "500m", "memory": "1Gi"},
            "requests": {"cpu": "100m", "memory": "512Mi"},
        },
        
        "nodeSelector": {},
        
        "tolerations": [],
        
        "affinity": {},
        
        "env": {
            "REDIS_HOST": "redis-master",
            "REDIS_PORT": "6379",
            "MODEL_REGISTRY_URL": "http://model-registry:8080",
            "JWT_SECRET": "change-me",
            "API_KEY_MANAGER": "true",
        },
        
        "envFrom": [],
        
        "ports": [
            {
                "name": "http",
                "containerPort": 80,
                "protocol": "TCP",
            },
        ],
        
        "probe": {
            "liveness": {
                "enabled": True,
                "path": "/v1/health",
                "initialDelaySeconds": 30,
                "periodSeconds": 10,
            },
            "readiness": {
                "enabled": True,
                "path": "/v1/ready",
                "initialDelaySeconds": 10,
                "periodSeconds": 5,
            },
        },
        
        "strategy": {
            "type": "RollingUpdate",
            "rollingUpdate": {
                "maxSurge": 1,
                "maxUnavailable": 0,
            },
        },
    },
}


# ==================== Message Bus Chart ====================

message_bus_chart = {
    "apiVersion": "v2",
    "name": f"{app_name}-message-bus",
    "type": "application",
    "description": "LiuHao AI OS Message Bus - Redis Streams based Pub/Sub",
    "version": chart_version,
    "appVersion": "1.0.0",
    "keywords": ["ai", "os", "messaging", "pub-sub"],
    "home": "https://liuhao-ai-os.example.com",
    "icon": app_icon,
    "annotations": app_annotations,
    
    "dependencies": [
        {
            "name": "redis",
            "version": "19.0.0",
            "repository": "https://charts.bitnami.com/bitnami",
        },
    ],
    
    "values": {
        "replicaCount": 1,
        
        "image": {
            "repository": "bitnami/redis",
            "pullPolicy": "IfNotPresent",
            "tag": "7-alpine",
        },
        
        "auth": {
            "enabled": False,
        },
        
        "master": {
            "enabled": True,
            "rootPassword": "",
        },
        
        "port": 6379,
        
        "resources": {
            "limits": {"cpu": "250m", "memory": "256Mi"},
            "requests": {"cpu": "50m", "memory": "128Mi"},
        },
        
        "nodeSelector": {},
        
        "tolerations": [],
        
        "affinity": {},
    },
}


# ==================== GitOps Workflow (as text) ====================

gitops_workflow_yaml = """name: liuhao-ai-os-deploy
on:
  push:
    branches:
      - main
      - release/*
  pull_request:
    branches:
      - main

jobs:
  build:
    name: Build Docker Images
    runs-on: ubuntu-latest
    steps:
      - name: Checkout Code
        uses: actions/checkout@v3
      - name: Set up QEMU
        uses: docker/setup-qemu-action@v2
      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v2
      - name: Login to Docker Hub
        name: Login
        uses: docker/login-action@v3
        with:
          username: "${{ secrets.DOCKER_HUB_USERNAME }}"
          password: "${{ secrets.DOCKER_HUB_TOKEN }}"
      - name: Build and Push
        run: |
          docker build -t liuhao-ai-os/gateway:${{ github.sha }} .
          docker push liuhao-ai-os/gateway:${{ github.sha }}
  test:
    name: Run Tests
    runs-on: ubuntu-latest
    dependsOn: [build]
    steps:
      - name: Install Dependencies
        run: pip install -e .
      - name: Run Unit Tests
        run: pytest tests/ -v
      - name: Run Integration Tests
        run: pytest tests/integration/ -v
  deploy:
    name: Deploy to Kubernetes
    runs-on: ubuntu-latest
    dependsOn: [test]
    steps:
      - name: Install Kustomize
        uses: jakewharley/kubectl-install@v2
      - name: Deploy with Helm
        run: |
          helm upgrade --install liuhao-ai-os \\
            ./infra/helm/gateway \\
            --namespace liuhao-ai-os \\
            --create-namespace \\
            --set image.repository=liuhao-ai-os/gateway \\
            --set image.tag=${{ github.sha }}
"""

# ==================== CLI Scaffold ====================

cli_scaffold = {
    "name": "liuhao-init",
    "description": "Initialize a new LiuHao AI OS project",
    "usage": "liuhao-init [options]",
    
    "options": [
        {
            "name": "project-name",
            "type": "string",
            "description": "Name of the new project",
            "required": True,
        },
        {
            "name": "template",
            "type": "string",
            "description": "Template to use (minimal, full, plugin)",
            "default": "full",
        },
        {
            "name": "output-dir",
            "type": "string",
            "description": "Output directory for project files",
            "default": ".",
        },
    ],
    
    "template_minimal": {
        "files": [
            "README.md",
            "liuhao_config.yaml",
            "src/core/__init__.py",
            "src/config_manager.py",
        ],
        "description": "Minimal LiuHao AI OS project template",
    },
    
    "template_full": {
        "files": [
            "README.md",
            "liuhao_config.yaml",
            "src/core/__init__.py",
            "src/config_manager.py",
            "src/security/__init__.py",
            "src/security/api_keys.py",
            "src/security/jwt_handler.py",
            "src/security/rbac.py",
            "src/gateway/main.py",
            "src/gateway/rate_limiter.py",
            "src/gateway/health.py",
            "src/plugins/__init__.py",
            "src/models/registry.py",
            "Dockerfile",
            "helm/Chart.yaml",
            "infra/gitops/workflow.yaml",
        ],
        "description": "Full LiuHao AI OS project template",
    },
    
    "template_plugin": {
        "files": [
            "README.md",
            "liuhao_config.yaml",
            "src/plugins/__init__.py",
            "src/plugins/base.py",
            "src/plugins/manager.py",
            "src/plugins/registry.py",
        ],
        "description": "LiuHao AI OS plugin project template",
    },
}


# ==================== Documentation Site ====================

docsite_config = {
    "name": "liuhao-docs",
    "description": "LiuHao AI OS Documentation Site",
    "generator": "mkdocs",
    "theme": "mkdocs-material",
    
    "pages": [
        {
            "name": "Home",
            "url": ".",
            "sections": [
                "Welcome to LiuHao AI OS",
                "Getting Started",
                "Quick Start Guide",
            ],
        },
        {
            "name": "Concepts",
            "url": "concepts/",
            "sections": [
                "Architecture Overview",
                "Core Principles",
                "Security Model",
            ],
        },
        {
            "name": "API Reference",
            "url": "api/",
            "auto_generate": True,
            "source": "src/gateway/openapi.yaml",
        },
        {
            "name": "Plugins",
            "url": "plugins/",
            "sections": [
                "Plugin Development Guide",
                "Plugin Marketplace",
                "Plugin API Reference",
            ],
        },
        {
            "name": "Workflow",
            "url": "workflow/",
            "sections": [
                "Workflow Engine Guide",
                "Checkpoint Management",
                "Execution Model",
            ],
        },
        {
            "name": "Deployment",
            "url": "deployment/",
            "sections": [
                "Helm Chart Reference",
                "GitOps Pipeline",
                "CLI Reference",
            ],
        },
        {
            "name": "Tutorials",
            "url": "tutorials/",
            "sections": [
                "Hello World Example",
                "Building a Custom Plugin",
                "Deploying to Kubernetes",
            ],
        },
    ],
    
    "plugins": [
        "mkdocs-jupyter",
        "mkdocstrings",
        "search",
    ],
    
    "extra_javascript": [
        "search/js/search.js",
    ],
    
    "extra_css": [
        "stylesheets/extra.css",
    ],
}