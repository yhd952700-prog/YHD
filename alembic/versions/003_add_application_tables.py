"""Add application-level tables and enum types for plugin system and workflows"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '003'
down_revision: Union[str, None] = '002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enum types
    op.execute("""
        CREATE TYPE IF NOT EXISTS rbac_action AS TEXT;
        CREATE TYPE IF NOT EXISTS span_kind AS TEXT;
        CREATE TYPE IF NOT EXISTS metric_aggregation AS TEXT;
        CREATE TYPE IF NOT EXISTS alert_severity AS TEXT;
        CREATE TYPE IF NOT EXISTS plugin_status AS TEXT;
        CREATE TYPE IF NOT EXISTS workflow_status AS TEXT;
    """)
    
    # Plugin categories table
    op.create_table(
        'plugin_categories',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('name', sa.String(100), nullable=False, unique=True),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('parent_id', sa.String(36), sa.ForeignKey('plugin_categories.id'), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, default='active'),
        sa.Column('is_active', sa.Boolean, nullable=False, default=True),
        sa.Column('metadata', sa.JSON, nullable=False, default='{}'),
        sa.Column('created_at', sa.DateTime, nullable=False, default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=False, default=sa.func.now(), onupdate=sa.func.now()),
    )
    
    op.create_index('ix_plugin_categories_name', 'plugin_categories', ['name'], unique=True)
    op.create_index('ix_plugin_categories_parent_id', 'plugin_categories', ['parent_id'], unique=False)
    op.create_index('ix_plugin_categories_status', 'plugin_categories', ['status'], unique=False)
    
    # Plugin configurations table
    op.create_table(
        'plugin_configurations',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('plugin_id', sa.String(36), sa.ForeignKey('plugins.id', ondelete='CASCADE'), nullable=False),
        sa.Column('category_id', sa.String(36), sa.ForeignKey('plugin_categories.id'), nullable=True),
        sa.Column('config_key', sa.String(255), nullable=False),
        sa.Column('config_value', sa.Text, nullable=False),
        sa.Column('is_default', sa.Boolean, nullable=False, default=False),
        sa.Column('is_sensitive', sa.Boolean, nullable=False, default=False),
        sa.Column('metadata', sa.JSON, nullable=False, default='{}'),
        sa.Column('created_at', sa.DateTime, nullable=False, default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=False, default=sa.func.now(), onupdate=sa.func.now()),
    )
    
    op.create_index('ix_plugin_configurations_plugin_id', 'plugin_configurations', ['plugin_id'], unique=False)
    op.create_index('ix_plugin_configurations_config_key', 'plugin_configurations', ['config_key'], unique=False)
    op.create_index('ix_plugin_configurations_config_value', 'plugin_configurations', ['config_value'], unique=False)
    
    # Workflow definitions table
    op.create_table(
        'workflow_definitions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('name', sa.String(100), nullable=False, unique=True),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('version', sa.String(50), nullable=False, default='1.0.0'),
        sa.Column('definition', sa.JSON, nullable=False, default='{}'),
        sa.Column('status', sa.String(20), nullable=False, default='draft'),
        sa.Column('is_active', sa.Boolean, nullable=False, default=False),
        sa.Column('metadata', sa.JSON, nullable=False, default='{}'),
        sa.Column('created_at', sa.DateTime, nullable=False, default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=False, default=sa.func.now(), onupdate=sa.func.now()),
    )
    
    op.create_index('ix_workflow_definitions_name', 'workflow_definitions', ['name'], unique=True)
    op.create_index('ix_workflow_definitions_status', 'workflow_definitions', ['status'], unique=False)
    op.create_index('ix_workflow_definitions_is_active', 'workflow_definitions', ['is_active'], unique=False)
    
    # Workflow execution logs table
    op.create_table(
        'workflow_execution_logs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('workflow_definition_id', sa.String(36), sa.ForeignKey('workflow_definitions.id'), nullable=False),
        sa.Column('trigger_type', sa.String(50), nullable=False),  # manual, scheduled, event-driven
        sa.Column('input_data', sa.JSON, nullable=False, default='{}'),
        sa.Column('output_data', sa.JSON, nullable=True),
        sa.Column('status', sa.String(20), nullable=False, default='running'),
        sa.Column('started_at', sa.DateTime, nullable=False, default=sa.func.now()),
        sa.Column('completed_at', sa.DateTime, nullable=True),
        sa.Column('duration_ms', sa.Integer, nullable=True),
        sa.Column('error_message', sa.Text, nullable=True),
        sa.Column('metadata', sa.JSON, nullable=False, default='{}'),
    )
    
    op.create_index('ix_workflow_execution_logs_status', 'workflow_execution_logs', ['status'], unique=False)
    op.create_index('ix_workflow_execution_logs_started_at', 'workflow_execution_logs', ['started_at'], unique=False)
    op.create_index('ix_workflow_execution_logs_workflow_definition_id', 'workflow_execution_logs', ['workflow_definition_id'], unique=False)
    
    # Agent session tables
    op.create_table(
        'agent_sessions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('agent_id', sa.String(36), nullable=False),
        sa.Column('session_key', sa.String(255), nullable=False, unique=True),
        sa.Column('status', sa.String(20), nullable=False, default='active'),
        sa.Column('current_task', sa.String(255), nullable=True),
        sa.Column('context_data', sa.JSON, nullable=False, default='{}'),
        sa.Column('last_heartbeat', sa.DateTime, nullable=False, default=sa.func.now()),
        sa.Column('created_at', sa.DateTime, nullable=False, default=sa.func.now()),
        sa.Column('expires_at', sa.DateTime, nullable=True),
    )
    
    op.create_index('ix_agent_sessions_agent_id', 'agent_sessions', ['agent_id'], unique=False)
    op.create_index('ix_agent_sessions_status', 'agent_sessions', ['status'], unique=False)
    op.create_index('ix_agent_sessions_session_key', 'agent_sessions', ['session_key'], unique=True)
    
    # Agent task queue
    op.create_table(
        'agent_task_queue',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('agent_id', sa.String(36), nullable=False),
        sa.Column('task_id', sa.String(36), nullable=False),
        sa.Column('task_type', sa.String(50), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, default='pending'),
        sa.Column('priority', sa.Integer, nullable=False, default=0),
        sa.Column('created_at', sa.DateTime, nullable=False, default=sa.func.now()),
        sa.Column('started_at', sa.DateTime, nullable=True),
        sa.Column('completed_at', sa.DateTime, nullable=True),
        sa.Column('result', sa.Text, nullable=True),
        sa.Column('error', sa.Text, nullable=True),
    )
    
    op.create_index('ix_agent_task_queue_agent_id', 'agent_task_queue', ['agent_id'], unique=False)
    op.create_index('ix_agent_task_queue_task_id', 'agent_task_queue', ['task_id'], unique=False)
    op.create_index('ix_agent_task_queue_status', 'agent_task_queue', ['status'], unique=False)
    op.create_index('ix_agent_task_queue_priority', 'agent_task_queue', ['priority'], unique=False)


def downgrade() -> None:
    # Drop tables in reverse order
    op.drop_table('agent_task_queue')
    op.drop_index('ix_agent_sessions_session_key', table_name='agent_sessions')
    op.drop_index('ix_agent_sessions_status', table_name='agent_sessions')
    op.drop_index('ix_agent_sessions_agent_id', table_name='agent_sessions')
    op.drop_table('agent_sessions')
    op.drop_index('ix_workflow_execution_logs_workflow_definition_id', table_name='workflow_execution_logs')
    op.drop_index('ix_workflow_execution_logs_started_at', table_name='workflow_execution_logs')
    op.drop_index('ix_workflow_execution_logs_status', table_name='workflow_execution_logs')
    op.drop_table('workflow_execution_logs')
    op.drop_index('ix_workflow_definitions_is_active', table_name='workflow_definitions')
    op.drop_index('ix_workflow_definitions_status', table_name='workflow_definitions')
    op.drop_index('ix_workflow_definitions_name', table_name='workflow_definitions')
    op.drop_table('workflow_definitions')
    op.drop_index('ix_plugin_configurations_config_value', table_name='plugin_configurations')
    op.drop_index('ix_plugin_configurations_config_key', table_name='plugin_configurations')
    op.drop_index('ix_plugin_configurations_plugin_id', table_name='plugin_configurations')
    op.drop_table('plugin_configurations')
    op.drop_index('ix_plugin_categories_parent_id', table_name='plugin_categories')
    op.drop_index('ix_plugin_categories_status', table_name='plugin_categories')
    op.drop_index('ix_plugin_categories_name', table_name='plugin_categories')
    op.drop_table('plugin_categories')
    op.execute("DROP TYPE IF EXISTS rbac_action")
    op.execute("DROP TYPE IF EXISTS span_kind")
    op.execute("DROP TYPE IF EXISTS metric_aggregation")
    op.execute("DROP TYPE IF EXISTS alert_severity")
    op.execute("DROP TYPE IF EXISTS plugin_status")
    op.execute("DROP TYPE IF EXISTS workflow_status")