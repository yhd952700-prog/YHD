"""Add indexes, unique constraints, and foreign key optimizations"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '002'
down_revision: Union[str, None] = '001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NOTE: Migration 001 already creates inline indexes (via `index=True` /
    # `unique=True` on columns) whose auto-generated names collide with the
    # explicit index names below. On SQLite this caused
    # "index already exists" failures. We therefore create all indexes
    # idempotently with `CREATE INDEX IF NOT EXISTS`.
    indexes = [
        ("ix_api_keys_key_hash", "api_keys", ["key_hash"], True),
        ("ix_api_keys_name", "api_keys", ["name"], False),
        ("ix_api_keys_status", "api_keys", ["status"], False),
        ("ix_jwt_tokens_jti", "jwt_tokens", ["jti"], True),
        ("ix_jwt_tokens_subject", "jwt_tokens", ["subject"], False),
        ("ix_jwt_tokens_revoked_at", "jwt_tokens", ["revoked_at"], False),
        ("ix_rbac_roles_name", "rbac_roles", ["name"], True),
        ("ix_rbac_roles_is_system", "rbac_roles", ["is_system"], False),
        ("ix_rbac_permissions_resource_action", "rbac_permissions", ["resource", "action"], True),
        ("ix_rbac_permissions_name", "rbac_permissions", ["name"], True),
        ("ix_rbac_users_username", "rbac_users", ["username"], True),
        ("ix_rbac_users_email", "rbac_users", ["email"], True),
        ("ix_rbac_users_is_active", "rbac_users", ["is_active"], False),
        ("ix_rbac_user_roles_user_id", "rbac_user_roles", ["user_id"], False),
        ("ix_rbac_user_roles_role_id", "rbac_user_roles", ["role_id"], False),
        ("ix_rbac_role_permissions_role_id", "rbac_role_permissions", ["role_id"], False),
        ("ix_rbac_role_permissions_permission_id", "rbac_role_permissions", ["permission_id"], False),
        ("ix_audit_logs_event_type", "audit_logs", ["event_type"], False),
        ("ix_audit_logs_timestamp", "audit_logs", ["timestamp"], False),
        ("ix_audit_logs_user_id", "audit_logs", ["user_id"], False),
        ("ix_audit_logs_trace_id", "audit_logs", ["trace_id"], False),
        ("ix_audit_logs_request_id", "audit_logs", ["request_id"], False),
        ("ix_spans_trace_id", "spans", ["trace_id"], False),
        ("ix_spans_name", "spans", ["name"], False),
        ("ix_spans_kind", "spans", ["kind"], False),
        ("ix_spans_start_time", "spans", ["start_time"], False),
        ("ix_metrics_name", "metrics", ["name"], True),
        ("ix_metrics_unit", "metrics", ["unit"], False),
        ("ix_metrics_kind", "spans", ["kind"], False),
        ("ix_alerts_alert_type", "alerts", ["alert_type"], False),
        ("ix_alerts_severity", "alerts", ["severity"], False),
        ("ix_alerts_state", "alerts", ["state"], False),
        ("ix_alerts_fired_at", "alerts", ["fired_at"], False),
        ("ix_plugins_name", "plugins", ["name"], True),
        ("ix_plugins_status", "plugins", ["status"], False),
        ("ix_plugins_is_active", "plugins", ["is_active"], False),
        ("ix_plugins_current_version_id", "plugins", ["current_version_id"], False),
        ("ix_plugin_versions_plugin_id", "plugin_versions", ["plugin_id"], False),
        ("ix_plugin_versions_status", "plugin_versions", ["status"], False),
        ("ix_plugin_versions_released_at", "plugin_versions", ["released_at"], False),
        ("ix_sandbox_executions_plugin_id", "sandbox_executions", ["plugin_id"], False),
        ("ix_sandbox_executions_status", "sandbox_executions", ["status"], False),
        ("ix_sandbox_executions_start_time", "sandbox_executions", ["start_time"], False),
        ("ix_sandbox_results_execution_id", "sandbox_results", ["execution_id"], False),
        ("ix_sandbox_results_success", "sandbox_results", ["success"], False),
        ("ix_plugin_dependencies_plugin_id", "plugin_dependencies", ["plugin_id"], False),
        ("ix_plugin_dependencies_dependency_name", "plugin_dependencies", ["dependency_name"], False),
        ("ix_plugin_conflicts_plugin_id", "plugin_conflicts", ["plugin_id"], False),
        ("ix_deployment_configs_name", "deployment_configs", ["name"], True),
        ("ix_deployment_configs_strategy", "deployment_configs", ["strategy"], False),
        ("ix_deployment_releases_version", "deployment_releases", ["version"], False),
        ("ix_deployment_releases_status", "deployment_releases", ["status"], False),
        ("ix_deployment_releases_deployed_at", "deployment_releases", ["deployed_at"], False),
        ("ix_budgets_user_id", "budgets", ["user_id"], False),
        ("ix_budgets_limit_usd", "budgets", ["limit_usd"], False),
        ("ix_budgets_is_active", "budgets", ["is_active"], False),
        ("ix_ai_models_name", "ai_models", ["name"], True),
        ("ix_ai_models_provider", "ai_models", ["provider"], False),
        ("ix_ai_models_is_active", "ai_models", ["is_active"], False),
        ("ix_cost_tracking_user_id_timestamp", "cost_tracking", ["user_id", "timestamp"], False),
        ("ix_cost_tracking_model_timestamp", "cost_tracking", ["model", "timestamp"], False),
        ("ix_device_adapters_name", "device_adapters", ["name"], True),
        ("ix_device_adapters_type", "device_adapters", ["type"], False),
        ("ix_device_adapters_status", "device_adapters", ["status"], False),
        ("ix_config_snapshots_name", "config_snapshots", ["name"], False),
        ("ix_workflow_executions_name", "workflow_executions", ["workflow_name"], False),
        ("ix_workflow_executions_status", "workflow_executions", ["status"], False),
        ("ix_workflow_executions_thread_id", "workflow_executions", ["thread_id"], False),
        ("ix_workflow_executions_started_at", "workflow_executions", ["started_at"], False),
        ("ix_goals_status", "goals", ["status"], False),
        ("ix_goals_priority", "goals", ["priority"], False),
        ("ix_tasks_status", "tasks", ["status"], False),
        ("ix_tasks_priority", "tasks", ["priority"], False),
        ("ix_tasks_goal_id", "tasks", ["goal_id"], False),
        ("ix_storage_entries_tags", "storage_entries", ["tags"], False),
    ]
    for name, table, cols, unique in indexes:
        kind = "UNIQUE INDEX" if unique else "INDEX"
        col_list = ", ".join(cols)
        op.execute(f"CREATE {kind} IF NOT EXISTS {name} ON {table} ({col_list})")


def downgrade() -> None:
    # Drop indexes in reverse order
    op.drop_index('ix_storage_entries_tags', table_name='storage_entries')
    op.drop_index('ix_goals_priority', table_name='goals')
    op.drop_index('ix_goals_status', table_name='goals')
    op.drop_index('ix_tasks_priority', table_name='tasks')
    op.drop_index('ix_tasks_status', table_name='tasks')
    op.drop_index('ix_tasks_goal_id', table_name='tasks')
    op.drop_index('ix_workflow_executions_started_at', table_name='workflow_executions')
    op.drop_index('ix_workflow_executions_thread_id', table_name='workflow_executions')
    op.drop_index('ix_workflow_executions_status', table_name='workflow_executions')
    op.drop_index('ix_workflow_executions_name', table_name='workflow_executions')
    op.drop_index('ix_config_snapshots_name', table_name='config_snapshots')
    op.drop_index('ix_device_adapters_status', table_name='device_adapters')
    op.drop_index('ix_device_adapters_type', table_name='device_adapters')
    op.drop_index('ix_device_adapters_name', table_name='device_adapters')
    op.drop_index('ix_cost_tracking_model_timestamp', 'cost_tracking')
    op.drop_index('ix_cost_tracking_user_id_timestamp', 'cost_tracking')
    op.drop_index('ix_ai_models_is_active', 'ai_models')
    op.drop_index('ix_ai_models_provider', 'ai_models')
    op.drop_index('ix_ai_models_name', 'ai_models')
    op.drop_index('ix_budgets_is_active', 'budgets')
    op.drop_index('ix_budgets_limit_usd', 'budgets')
    op.drop_index('ix_budgets_user_id', 'budgets')
    op.drop_index('ix_deployment_releases_deployed_at', table_name='deployment_releases')
    op.drop_index('ix_deployment_releases_status', table_name='deployment_releases')
    op.drop_index('ix_deployment_releases_version', table_name='deployment_releases')
    op.drop_index('ix_deployment_configs_strategy', table_name='deployment_configs')
    op.drop_index('ix_deployment_configs_name', table_name='deployment_configs')
    op.drop_index('ix_plugin_conflicts_plugin_id', table_name='plugin_conflicts')
    op.drop_index('ix_plugin_dependencies_dependency_name', table_name='plugin_dependencies')
    op.drop_index('ix_plugin_dependencies_plugin_id', table_name='plugin_dependencies')
    op.drop_index('ix_sandbox_results_success', table_name='sandbox_results')
    op.drop_index('ix_sandbox_results_execution_id', table_name='sandbox_results')
    op.drop_index('ix_sandbox_executions_start_time', table_name='sandbox_executions')
    op.drop_index('ix_sandbox_executions_status', table_name='sandbox_executions')
    op.drop_index('ix_plugin_versions_released_at', table_name='plugin_versions')
    op.drop_index('ix_plugin_versions_status', table_name='plugin_versions')
    op.drop_index('ix_plugin_versions_plugin_id', table_name='plugin_versions')
    op.drop_index('ix_plugins_is_active', table_name='plugins')
    op.drop_index('ix_plugins_status', table_name='plugins')
    op.drop_index('ix_plugins_name', table_name='plugins')
    op.drop_index('ix_alerts_fired_at', table_name='alerts')
    op.drop_index('ix_alerts_state', table_name='alerts')
    op.drop_index('ix_alerts_severity', table_name='alerts')
    op.drop_index('ix_alerts_alert_type', table_name='alerts')
    op.drop_index('ix_metrics_kind', table_name='metrics')
    op.drop_index('ix_metrics_unit', table_name='metrics')
    op.drop_index('ix_metrics_name', table_name='metrics')
    op.drop_index('ix_spans_start_time', table_name='spans')
    op.drop_index('ix_spans_kind', table_name='spans')
    op.drop_index('ix_spans_name', table_name='spans')
    op.drop_index('ix_spans_trace_id', table_name='spans')