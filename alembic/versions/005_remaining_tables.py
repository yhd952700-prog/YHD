"""Add remaining tables and optimization indexes for the full system"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '005'
down_revision: Union[str, None] = '004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Indexes for workflow_step_executions (already created in 004, but add remaining ones)
    op.create_index('ix_workflow_step_executions_started_at', 'workflow_step_executions', ['started_at'], unique=False)
    
    # Status transition logs table
    op.create_table(
        'status_transition_logs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('entity_type', sa.String(50), nullable=False),  # goal, task, workflow, plugin
        sa.Column('entity_id', sa.String(36), nullable=False),
        sa.Column('from_status', sa.String(20), nullable=False),
        sa.Column('to_status', sa.String(20), nullable=False),
        sa.Column('transitioned_at', sa.DateTime, nullable=False, default=sa.func.now()),
        sa.Column('transitioned_by', sa.String(100), nullable=True),
        sa.Column('reason', sa.Text, nullable=True),
        sa.Column('metadata', sa.JSON, nullable=False, default='{}'),
    )
    
    op.create_index('ix_status_transition_logs_entity', 'status_transition_logs', ['entity_type', 'entity_id'], unique=False)
    op.create_index('ix_status_transition_logs_from_to', 'status_transition_logs', ['from_status', 'to_status'], unique=False)
    op.create_index('ix_status_transition_logs_timestamp', 'status_transition_logs', ['transitioned_at'], unique=False)
    
    # Plugin execution history
    op.create_table(
        'plugin_execution_history',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('plugin_id', sa.String(36), sa.ForeignKey('plugins.id'), nullable=False),
        sa.Column('execution_id', sa.String(36), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, default='running'),
        sa.Column('started_at', sa.DateTime, nullable=False, default=sa.func.now()),
        sa.Column('completed_at', sa.DateTime, nullable=True),
        sa.Column('duration_ms', sa.Integer, nullable=True),
        sa.Column('output', sa.Text, nullable=True),
        sa.Column('error', sa.Text, nullable=True),
        sa.Column('metadata', sa.JSON, nullable=False, default='{}'),
    )
    
    op.create_index('ix_plugin_execution_history_plugin_id', 'plugin_execution_history', ['plugin_id'], unique=False)
    op.create_index('ix_plugin_execution_history_status', 'plugin_execution_history', ['status'], unique=False)
    op.create_index('ix_plugin_execution_history_started_at', 'plugin_execution_history', ['started_at'], unique=False)
    
    # User preferences
    op.create_table(
        'user_preferences',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(36), nullable=False, unique=True),
        sa.Column('preference_key', sa.String(100), nullable=False),
        sa.Column('preference_value', sa.Text, nullable=False),
        sa.Column('category', sa.String(50), nullable=False, default='general'),
        sa.Column('is_public', sa.Boolean, nullable=False, default=False),
        sa.Column('metadata', sa.JSON, nullable=False, default='{}'),
        sa.Column('created_at', sa.DateTime, nullable=False, default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=False, default=sa.func.now(), onupdate=sa.func.now()),
    )
    
    op.create_index('ix_user_preferences_user_id', 'user_preferences', ['user_id'], unique=False)
    op.create_index('ix_user_preferences_preference_key', 'user_preferences', ['preference_key'], unique=True)
    op.create_index('ix_user_preferences_category', 'user_preferences', ['category'], unique=False)
    
    # Notification settings
    op.create_table(
        'notification_settings',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(36), nullable=False, unique=True),
        sa.Column('notification_type', sa.String(50), nullable=False),
        sa.Column('is_enabled', sa.Boolean, nullable=False, default=True),
        sa.Column('channels', sa.JSON, nullable=False, default='[]'),
        sa.Column('metadata', sa.JSON, nullable=False, default='{}'),
        sa.Column('created_at', sa.DateTime, nullable=False, default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=False, default=sa.func.now(), onupdate=sa.func.now()),
    )
    
    op.create_index('ix_notification_settings_user_id', 'notification_settings', ['user_id'], unique=False)
    op.create_index('ix_notification_settings_notification_type', 'notification_settings', ['notification_type'], unique=False)
    
    # System configuration
    op.create_table(
        'system_configurations',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('config_key', sa.String(100), nullable=False, unique=True),
        sa.Column('config_value', sa.Text, nullable=False),
        sa.Column('config_type', sa.String(20), nullable=False, default='string'),  # string, int, float, bool, json
        sa.Column('is_sensitive', sa.Boolean, nullable=False, default=False),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('metadata', sa.JSON, nullable=False, default='{}'),
        sa.Column('created_at', sa.DateTime, nullable=False, default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=False, default=sa.func.now(), onupdate=sa.func.now()),
    )
    
    op.create_index('ix_system_configurations_config_key', 'system_configurations', ['config_key'], unique=True)
    op.create_index('ix_system_configurations_is_sensitive', 'system_configurations', ['is_sensitive'], unique=False)
    
    # Audit trail for all table changes
    op.create_table(
        'audit_trail',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('entity_type', sa.String(50), nullable=False),
        sa.Column('entity_id', sa.String(36), nullable=True),
        sa.Column('action', sa.String(20), nullable=False),  # create, update, delete
        sa.Column('old_value', sa.JSON, nullable=True),
        sa.Column('new_value', sa.JSON, nullable=True),
        sa.Column('changed_at', sa.DateTime, nullable=False, default=sa.func.now()),
        sa.Column('changed_by', sa.String(100), nullable=True),
        sa.Column('ip_address', sa.String(45), nullable=True),
        sa.Column('metadata', sa.JSON, nullable=False, default='{}'),
    )
    
    op.create_index('ix_audit_trail_entity', 'audit_trail', ['entity_type', 'entity_id'], unique=False)
    op.create_index('ix_audit_trail_action', 'audit_trail', ['action'], unique=False)
    op.create_index('ix_audit_trail_changed_at', 'audit_trail', ['changed_at'], unique=False)


def downgrade() -> None:
    # Drop indexes BEFORE the tables that own them (SQLite removes a table's
    # indexes automatically, so dropping indexes afterwards fails with
    # "no such index").
    op.drop_index('ix_audit_trail_changed_at', table_name='audit_trail')
    op.drop_index('ix_audit_trail_action', table_name='audit_trail')
    op.drop_index('ix_audit_trail_entity', table_name='audit_trail')
    op.drop_index('ix_system_configurations_is_sensitive', table_name='system_configurations')
    op.drop_index('ix_system_configurations_config_key', table_name='system_configurations')
    op.drop_index('ix_notification_settings_notification_type', table_name='notification_settings')
    op.drop_index('ix_notification_settings_user_id', table_name='notification_settings')
    op.drop_index('ix_user_preferences_preference_key', table_name='user_preferences')
    op.drop_index('ix_user_preferences_category', table_name='user_preferences')
    op.drop_index('ix_user_preferences_user_id', table_name='user_preferences')
    op.drop_index('ix_plugin_execution_history_started_at', table_name='plugin_execution_history')
    op.drop_index('ix_plugin_execution_history_status', table_name='plugin_execution_history')
    op.drop_index('ix_plugin_execution_history_plugin_id', table_name='plugin_execution_history')
    op.drop_index('ix_status_transition_logs_timestamp', table_name='status_transition_logs')
    op.drop_index('ix_status_transition_logs_from_to', table_name='status_transition_logs')
    op.drop_index('ix_status_transition_logs_entity', table_name='status_transition_logs')
    op.drop_table('audit_trail')
    op.drop_table('system_configurations')
    op.drop_table('notification_settings')
    op.drop_table('user_preferences')
    op.drop_table('plugin_execution_history')
    op.drop_table('status_transition_logs')