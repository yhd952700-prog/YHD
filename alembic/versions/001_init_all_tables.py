"""init all tables

Revision ID: 001
Revises: 
Create Date: 2024-01-15 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ==================== Security Tables ====================
    
    # API Keys
    op.create_table(
        'api_keys',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('key_hash', sa.String(64), nullable=False, index=True),
        sa.Column('encrypted_key', sa.Text, nullable=False),
        sa.Column('scopes', sa.JSON, nullable=False, default='[]'),
        sa.Column('status', sa.String(50), nullable=False, default='active'),
        sa.Column('created_at', sa.DateTime, nullable=False, default=sa.func.now()),
        sa.Column('expires_at', sa.DateTime, nullable=True),
        sa.Column('last_used_at', sa.DateTime, nullable=True),
        sa.Column('usage_count', sa.Integer, nullable=False, default=0),
        sa.Column('rate_limit_rpm', sa.Integer, nullable=False, default=60),
        sa.Column('rate_limit_tpm', sa.Integer, nullable=False, default=10000),
        sa.Column('metadata', sa.JSON, nullable=False, default='{}'),
        sa.Column('rotated_from', sa.String(36), sa.ForeignKey('api_keys.id'), nullable=True),
        sa.Column('rotation_count', sa.Integer, nullable=False, default=0),
        sa.Column('is_deleted', sa.Boolean, nullable=False, default=False),
        sa.Column('deleted_at', sa.DateTime, nullable=True),
    )

    # JWT Tokens (for revocation list)
    op.create_table(
        'jwt_tokens',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('jti', sa.String(36), nullable=False, unique=True, index=True),
        sa.Column('token_type', sa.String(20), nullable=False),  # access, refresh
        sa.Column('subject', sa.String(255), nullable=False, index=True),
        sa.Column('issued_at', sa.DateTime, nullable=False, default=sa.func.now()),
        sa.Column('expires_at', sa.DateTime, nullable=False, index=True),
        sa.Column('revoked_at', sa.DateTime, nullable=True),
        sa.Column('revoked_reason', sa.String(255), nullable=True),
        sa.Column('metadata', sa.JSON, nullable=False, default='{}'),
        sa.Column('created_at', sa.DateTime, nullable=False, default=sa.func.now()),
    )

    # RBAC: Roles
    op.create_table(
        'rbac_roles',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('name', sa.String(100), nullable=False, unique=True, index=True),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('is_system', sa.Boolean, nullable=False, default=False),
        sa.Column('metadata', sa.JSON, nullable=False, default='{}'),
        sa.Column('created_at', sa.DateTime, nullable=False, default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=False, default=sa.func.now(), onupdate=sa.func.now()),
        sa.Column('is_deleted', sa.Boolean, nullable=False, default=False),
        sa.Column('deleted_at', sa.DateTime, nullable=True),
    )

    # RBAC: Permissions
    op.create_table(
        'rbac_permissions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('name', sa.String(100), nullable=False, unique=True, index=True),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('resource', sa.String(100), nullable=False, index=True),
        sa.Column('action', sa.String(50), nullable=False),
        sa.Column('metadata', sa.JSON, nullable=False, default='{}'),
        sa.Column('created_at', sa.DateTime, nullable=False, default=sa.func.now()),
    )
    op.create_index('ix_rbac_permissions_resource_action', 'rbac_permissions', ['resource', 'action'], unique=True)

    # RBAC: Role-Permission mapping
    op.create_table(
        'rbac_role_permissions',
        sa.Column('role_id', sa.String(36), sa.ForeignKey('rbac_roles.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('permission_id', sa.String(36), sa.ForeignKey('rbac_permissions.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('created_at', sa.DateTime, nullable=False, default=sa.func.now()),
    )

    # RBAC: Users
    op.create_table(
        'rbac_users',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('username', sa.String(100), nullable=False, unique=True, index=True),
        sa.Column('email', sa.String(255), nullable=False, unique=True, index=True),
        sa.Column('password_hash', sa.String(255), nullable=True),  # For local auth
        sa.Column('is_active', sa.Boolean, nullable=False, default=True),
        sa.Column('is_superuser', sa.Boolean, nullable=False, default=False),
        sa.Column('last_login_at', sa.DateTime, nullable=True),
        sa.Column('metadata', sa.JSON, nullable=False, default='{}'),
        sa.Column('created_at', sa.DateTime, nullable=False, default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=False, default=sa.func.now(), onupdate=sa.func.now()),
        sa.Column('is_deleted', sa.Boolean, nullable=False, default=False),
        sa.Column('deleted_at', sa.DateTime, nullable=True),
    )

    # RBAC: User-Role mapping
    op.create_table(
        'rbac_user_roles',
        sa.Column('user_id', sa.String(36), sa.ForeignKey('rbac_users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('role_id', sa.String(36), sa.ForeignKey('rbac_roles.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('created_at', sa.DateTime, nullable=False, default=sa.func.now()),
    )

    # Audit Logs
    op.create_table(
        'audit_logs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('event_type', sa.String(50), nullable=False, index=True),
        sa.Column('timestamp', sa.DateTime, nullable=False, default=sa.func.now(), index=True),
        sa.Column('source', sa.String(100), nullable=False),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('rbac_users.id'), nullable=True, index=True),
        sa.Column('session_id', sa.String(36), nullable=True, index=True),
        sa.Column('severity', sa.String(20), nullable=False, default='medium'),
        sa.Column('status', sa.String(20), nullable=False, default='success'),
        sa.Column('message', sa.Text, nullable=True),
        sa.Column('details', sa.JSON, nullable=False, default='{}'),
        sa.Column('request_id', sa.String(36), nullable=True, index=True),
        sa.Column('trace_id', sa.String(36), nullable=True, index=True),
        sa.Column('ip_address', sa.String(45), nullable=True),
        sa.Column('user_agent', sa.Text, nullable=True),
    )

    # ==================== Observability Tables ====================
    
    # Spans (Distributed Tracing)
    op.create_table(
        'spans',
        sa.Column('span_id', sa.String(36), primary_key=True),
        sa.Column('trace_id', sa.String(36), nullable=False, index=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('kind', sa.String(20), nullable=False, default='internal'),
        sa.Column('start_time', sa.BigInteger, nullable=False),
        sa.Column('end_time', sa.BigInteger, nullable=False),
        sa.Column('duration_ns', sa.BigInteger, nullable=True),
        sa.Column('attributes', sa.JSON, nullable=False, default='{}'),
        sa.Column('status', sa.String(20), nullable=False, default='unset'),
        sa.Column('status_message', sa.Text, nullable=True),
        sa.Column('parent_span_id', sa.String(36), nullable=True, index=True),
        sa.Column('trace_state', sa.Text, nullable=True),
        sa.Column('dropped_attributes_count', sa.Integer, nullable=False, default=0),
        sa.Column('events', sa.JSON, nullable=False, default='[]'),
        sa.Column('links', sa.JSON, nullable=False, default='[]'),
        sa.Column('created_at', sa.DateTime, nullable=False, default=sa.func.now()),
    )

    # Metrics
    op.create_table(
        'metrics',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('name', sa.String(255), nullable=False, unique=True, index=True),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('unit', sa.String(50), nullable=True),
        sa.Column('kind', sa.String(20), nullable=False, default='gauge'),
        sa.Column('aggregation_temporality', sa.String(20), nullable=False, default='delta'),
        sa.Column('metric_type', sa.String(20), nullable=False, default='double'),
        sa.Column('data_points', sa.JSON, nullable=False, default='[]'),
        sa.Column('created_at', sa.DateTime, nullable=False, default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=False, default=sa.func.now(), onupdate=sa.func.now()),
    )

    # Alerts
    op.create_table(
        'alerts',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('alert_type', sa.String(50), nullable=False, index=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('severity', sa.String(20), nullable=False, default='medium'),
        sa.Column('message', sa.Text, nullable=True),
        sa.Column('source', sa.String(100), nullable=False),
        sa.Column('metric_name', sa.String(255), nullable=True),
        sa.Column('threshold_value', sa.Float, nullable=True),
        sa.Column('threshold_operator', sa.String(10), nullable=True),
        sa.Column('state', sa.String(20), nullable=False, default='firing'),
        sa.Column('fired_at', sa.DateTime, nullable=False, default=sa.func.now(), index=True),
        sa.Column('resolved_at', sa.DateTime, nullable=True),
        sa.Column('tags', sa.JSON, nullable=False, default='{}'),
        sa.Column('extra', sa.JSON, nullable=False, default='{}'),
    )

    # ==================== Plugin System Tables ====================
    
    # Plugin Marketplace
    op.create_table(
        'plugins',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('name', sa.String(100), nullable=False, unique=True, index=True),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('plugin_type', sa.String(50), nullable=False, default='extension'),
        sa.Column('author', sa.String(100), nullable=True),
        sa.Column('homepage', sa.String(255), nullable=True),
        sa.Column('license', sa.String(100), nullable=True),
        sa.Column('keywords', sa.JSON, nullable=False, default='[]'),
        sa.Column('compatibility', sa.String(50), nullable=False, default='>=1.0.0'),
        sa.Column('entry_points', sa.JSON, nullable=False, default='{}'),
        sa.Column('tags', sa.JSON, nullable=False, default='[]'),
        sa.Column('status', sa.String(20), nullable=False, default='pending'),
        sa.Column('is_active', sa.Boolean, nullable=False, default=False),
        sa.Column('current_version_id', sa.String(36), nullable=True),
        sa.Column('metadata', sa.JSON, nullable=False, default='{}'),
        sa.Column('created_at', sa.DateTime, nullable=False, default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=False, default=sa.func.now(), onupdate=sa.func.now()),
        sa.Column('is_deleted', sa.Boolean, nullable=False, default=False),
        sa.Column('deleted_at', sa.DateTime, nullable=True),
    )

    op.create_table(
        'plugin_versions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('plugin_id', sa.String(36), sa.ForeignKey('plugins.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('version', sa.String(50), nullable=False),
        sa.Column('release_notes', sa.Text, nullable=True),
        sa.Column('changelog', sa.Text, nullable=True),
        sa.Column('upload_url', sa.String(500), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, default='pending'),
        sa.Column('released_at', sa.DateTime, nullable=True),
        sa.Column('file_size', sa.BigInteger, nullable=False, default=0),
        sa.Column('md5_hash', sa.String(32), nullable=True),
        sa.Column('metadata', sa.JSON, nullable=False, default='{}'),
        sa.Column('created_at', sa.DateTime, nullable=False, default=sa.func.now()),
    )

    # Plugin Sandbox
    op.create_table(
        'sandbox_executions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('plugin_id', sa.String(36), sa.ForeignKey('plugins.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('sandbox_id', sa.String(36), nullable=False, index=True),
        sa.Column('entry_point', sa.String(255), nullable=False),
        sa.Column('arguments', sa.JSON, nullable=False, default='[]'),
        sa.Column('working_directory', sa.String(500), nullable=True),
        sa.Column('environment_variables', sa.JSON, nullable=False, default='{}'),
        sa.Column('resource_limits', sa.JSON, nullable=False, default='{}'),
        sa.Column('timeout', sa.Integer, nullable=False, default=60),
        sa.Column('status', sa.String(20), nullable=False, default='pending'),
        sa.Column('start_time', sa.DateTime, nullable=True),
        sa.Column('end_time', sa.DateTime, nullable=True),
        sa.Column('exit_code', sa.Integer, nullable=True),
        sa.Column('error_message', sa.Text, nullable=True),
        sa.Column('output', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=False, default=sa.func.now(), onupdate=sa.func.now()),
    )

    op.create_table(
        'sandbox_results',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('execution_id', sa.String(36), sa.ForeignKey('sandbox_executions.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('success', sa.Boolean, nullable=False),
        sa.Column('output', sa.Text, nullable=True),
        sa.Column('error', sa.Text, nullable=True),
        sa.Column('execution_time', sa.Float, nullable=True),
        sa.Column('resource_usage', sa.JSON, nullable=False, default='{}'),
        sa.Column('created_at', sa.DateTime, nullable=False, default=sa.func.now()),
    )

    # Plugin Dependencies
    op.create_table(
        'plugin_dependencies',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('plugin_id', sa.String(36), sa.ForeignKey('plugins.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('dependency_name', sa.String(100), nullable=False),
        sa.Column('version', sa.String(50), nullable=True),
        sa.Column('version_range', sa.String(100), nullable=True),
        sa.Column('dependency_type', sa.String(20), nullable=False, default='hard'),
        sa.Column('optional', sa.Boolean, nullable=False, default=False),
        sa.Column('weak', sa.Boolean, nullable=False, default=False),
        sa.Column('metadata', sa.JSON, nullable=False, default='{}'),
        sa.Column('created_at', sa.DateTime, nullable=False, default=sa.func.now()),
    )

    op.create_table(
        'plugin_conflicts',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('plugin_id', sa.String(36), sa.ForeignKey('plugins.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('dependency_name', sa.String(100), nullable=False),
        sa.Column('conflicting_versions', sa.JSON, nullable=False, default='[]'),
        sa.Column('resolution', sa.String(50), nullable=True),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('suggested_fix', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, default=sa.func.now()),
    )

    # ==================== AI / Workflow Tables ====================
    
    op.create_table(
        'goals',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('description', sa.Text, nullable=False),
        sa.Column('priority', sa.String(20), nullable=False, default='medium'),
        sa.Column('status', sa.String(20), nullable=False, default='pending'),
        sa.Column('metadata', sa.JSON, nullable=False, default='{}'),
        sa.Column('created_at', sa.DateTime, nullable=False, default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=False, default=sa.func.now(), onupdate=sa.func.now()),
    )

    op.create_table(
        'tasks',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('goal_id', sa.String(36), sa.ForeignKey('goals.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('description', sa.Text, nullable=False),
        sa.Column('task_type', sa.String(50), nullable=False, default='general'),
        sa.Column('depends_on', sa.JSON, nullable=False, default='[]'),
        sa.Column('status', sa.String(20), nullable=False, default='pending'),
        sa.Column('assigned_agent', sa.String(100), nullable=True),
        sa.Column('result', sa.Text, nullable=True),
        sa.Column('error', sa.Text, nullable=True),
        sa.Column('priority', sa.Integer, nullable=False, default=0),
        sa.Column('metadata', sa.JSON, nullable=False, default='{}'),
        sa.Column('created_at', sa.DateTime, nullable=False, default=sa.func.now()),
        sa.Column('started_at', sa.DateTime, nullable=True),
        sa.Column('completed_at', sa.DateTime, nullable=True),
    )

    # Workflow Executions (LangGraph)
    op.create_table(
        'workflow_executions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('workflow_name', sa.String(100), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, default='pending'),
        sa.Column('input_data', sa.JSON, nullable=False, default='{}'),
        sa.Column('output_data', sa.JSON, nullable=True),
        sa.Column('error', sa.Text, nullable=True),
        sa.Column('checkpoint_data', sa.JSON, nullable=True),
        sa.Column('thread_id', sa.String(36), nullable=True, index=True),
        sa.Column('started_at', sa.DateTime, nullable=True),
        sa.Column('completed_at', sa.DateTime, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=False, default=sa.func.now(), onupdate=sa.func.now()),
    )

    # ==================== Knowledge / Memory ====================
    
    op.create_table(
        'memory_items',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('content', sa.Text, nullable=False),
        sa.Column('tier', sa.String(20), nullable=False, default='semantic'),
        sa.Column('metadata', sa.JSON, nullable=False, default='{}'),
        sa.Column('embedding', sa.JSON, nullable=True),  # Store as JSON array for simplicity
        sa.Column('created_at', sa.DateTime, nullable=False, default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=False, default=sa.func.now(), onupdate=sa.func.now()),
        sa.Column('access_count', sa.Integer, nullable=False, default=0),
        sa.Column('importance', sa.Float, nullable=False, default=0.5),
        sa.Column('tags', sa.JSON, nullable=False, default='[]'),
        sa.Column('user_id', sa.String(36), nullable=False, default='default', index=True),
        sa.Column('session_id', sa.String(36), nullable=True, index=True),
        sa.Column('agent_id', sa.String(36), nullable=True, index=True),
    )

    # ==================== Storage ====================
    
    op.create_table(
        'storage_entries',
        sa.Column('key', sa.String(500), primary_key=True),
        sa.Column('value', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=False, default=sa.func.now(), onupdate=sa.func.now()),
        sa.Column('tags', sa.JSON, nullable=False, default='{}'),
        sa.Column('ttl', sa.Float, nullable=True),
    )

    # ==================== Deployment / Config ====================
    
    op.create_table(
        'deployment_configs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('name', sa.String(100), nullable=False, unique=True, index=True),
        sa.Column('target', sa.String(50), nullable=False),
        sa.Column('strategy', sa.String(20), nullable=False, default='rolling'),
        sa.Column('docker_config', sa.JSON, nullable=False, default='{}'),
        sa.Column('health_check', sa.JSON, nullable=True),
        sa.Column('environment', sa.JSON, nullable=False, default='{}'),
        sa.Column('secrets', sa.JSON, nullable=False, default='{}'),
        sa.Column('resources', sa.JSON, nullable=False, default='{}'),
        sa.Column('autoscaling', sa.JSON, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=False, default=sa.func.now(), onupdate=sa.func.now()),
    )

    op.create_table(
        'deployment_releases',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('version', sa.String(50), nullable=False),
        sa.Column('commit_hash', sa.String(64), nullable=True),
        sa.Column('deployed_at', sa.DateTime, nullable=False, default=sa.func.now()),
        sa.Column('deployed_by', sa.String(100), nullable=True),
        sa.Column('config_id', sa.String(36), sa.ForeignKey('deployment_configs.id', ondelete='SET NULL'), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, default='pending'),
        sa.Column('logs', sa.Text, nullable=True),
        sa.Column('error_message', sa.Text, nullable=True),
    )

    # ==================== AI / Models ====================
    
    op.create_table(
        'ai_models',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('name', sa.String(100), nullable=False, unique=True, index=True),
        sa.Column('provider', sa.String(50), nullable=False),
        sa.Column('model', sa.String(100), nullable=False),
        sa.Column('capabilities', sa.JSON, nullable=False, default='{}'),
        sa.Column('cost_per_1k_tokens', sa.Float, nullable=True),
        sa.Column('max_concurrency', sa.Integer, nullable=False, default=10),
        sa.Column('is_active', sa.Boolean, nullable=False, default=True),
        sa.Column('metadata', sa.JSON, nullable=False, default='{}'),
        sa.Column('created_at', sa.DateTime, nullable=False, default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=False, default=sa.func.now(), onupdate=sa.func.now()),
    )

    op.create_table(
        'cost_tracking',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(36), nullable=False, index=True),
        sa.Column('model', sa.String(100), nullable=False, index=True),
        sa.Column('prompt_tokens', sa.Integer, nullable=False, default=0),
        sa.Column('completion_tokens', sa.Integer, nullable=False, default=0),
        sa.Column('total_tokens', sa.Integer, nullable=False, default=0),
        sa.Column('cost_usd', sa.Float, nullable=False, default=0.0),
        sa.Column('timestamp', sa.DateTime, nullable=False, default=sa.func.now(), index=True),
        sa.Column('metadata', sa.JSON, nullable=False, default='{}'),
    )
    op.create_index('ix_cost_tracking_user_id_timestamp', 'cost_tracking', ['user_id', 'timestamp'])
    op.create_index('ix_cost_tracking_model_timestamp', 'cost_tracking', ['model', 'timestamp'])

    # ==================== Devices / External ====================
    
    op.create_table(
        'device_adapters',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('name', sa.String(100), nullable=False, unique=True, index=True),
        sa.Column('type', sa.String(50), nullable=False),  # filesystem, browser, shell
        sa.Column('config', sa.JSON, nullable=False, default='{}'),
        sa.Column('status', sa.String(20), nullable=False, default='active'),
        sa.Column('last_health_check', sa.DateTime, nullable=True),
        sa.Column('metadata', sa.JSON, nullable=False, default='{}'),
        sa.Column('created_at', sa.DateTime, nullable=False, default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=False, default=sa.func.now(), onupdate=sa.func.now()),
    )

    # ==================== Configuration ====================
    
    op.create_table(
        'config_snapshots',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('config_data', sa.JSON, nullable=False, default='{}'),
        sa.Column('created_by', sa.String(36), nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, default=sa.func.now()),
    )

    # ==================== Deployment Releases ====================
    
    op.create_table(
        'deployments',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('version', sa.String(50), nullable=False),
        sa.Column('commit_hash', sa.String(64), nullable=True),
        sa.Column('deployed_at', sa.DateTime, nullable=False, default=sa.func.now()),
        sa.Column('deployed_by', sa.String(100), nullable=True),
        sa.Column('config_snapshot_id', sa.String(36), sa.ForeignKey('config_snapshots.id'), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, default='pending'),
        sa.Column('rolled_back_from', sa.String(36), nullable=True),
        sa.Column('rollback_reason', sa.Text, nullable=True),
        sa.Column('metadata', sa.JSON, nullable=False, default='{}'),
    )

    # ==================== Cost Tracking / Budgets ====================
    
    op.create_table(
        'budgets',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(36), nullable=False, index=True),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('limit_usd', sa.Float, nullable=False),
        sa.Column('period', sa.String(20), nullable=False, default='monthly'),  # daily, weekly, monthly
        sa.Column('alert_threshold', sa.Float, nullable=False, default=0.8),
        sa.Column('is_active', sa.Boolean, nullable=False, default=True),
        sa.Column('created_at', sa.DateTime, nullable=False, default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=False, default=sa.func.now(), onupdate=sa.func.now()),
    )


def downgrade() -> None:
    # Drop tables in reverse order (respecting foreign keys)
    op.drop_table('budgets')
    op.drop_table('deployments')
    op.drop_table('config_snapshots')
    op.drop_table('device_adapters')
    op.drop_table('cost_tracking')
    op.drop_table('ai_models')
    op.drop_table('deployment_releases')
    op.drop_table('deployment_configs')
    op.drop_table('storage_entries')
    op.drop_table('memory_items')
    op.drop_table('workflow_executions')
    op.drop_table('tasks')
    op.drop_table('goals')
    op.drop_table('plugin_conflicts')
    op.drop_table('plugin_dependencies')
    op.drop_table('sandbox_results')
    op.drop_table('sandbox_executions')
    op.drop_table('plugin_versions')
    op.drop_table('plugins')
    op.drop_table('alerts')
    op.drop_table('metrics')
    op.drop_table('spans')
    op.drop_table('audit_logs')
    op.drop_table('rbac_user_roles')
    op.drop_table('rbac_users')
    op.drop_table('rbac_role_permissions')
    op.drop_table('rbac_permissions')
    op.drop_table('rbac_roles')
    op.drop_table('jwt_tokens')
    op.drop_table('api_keys')