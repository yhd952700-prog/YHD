"""Add workflow execution and agent management tables"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '004'
down_revision: Union[str, None] = '003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Workflow step execution logs
    op.create_table(
        'workflow_step_executions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('workflow_execution_id', sa.String(36), sa.ForeignKey('workflow_execution_logs.id'), nullable=False),
        sa.Column('step_name', sa.String(100), nullable=False),
        sa.Column('step_order', sa.Integer, nullable=False, default=0),
        sa.Column('status', sa.String(20), nullable=False, default='running'),
        sa.Column('started_at', sa.DateTime, nullable=False, default=sa.func.now()),
        sa.Column('completed_at', sa.DateTime, nullable=True),
        sa.Column('duration_ms', sa.Integer, nullable=True),
        sa.Column('output_data', sa.JSON, nullable=True),
        sa.Column('error_message', sa.Text, nullable=True),
    )
    
    op.create_index('ix_workflow_step_executions_workflow_execution_id', 'workflow_step_executions', ['workflow_execution_id'], unique=False)
    op.create_index('ix_workflow_step_executions_step_name', 'workflow_step_executions', ['step_name'], unique=False)
    op.create_index('ix_workflow_step_executions_status', 'workflow_step_executions', ['status'], unique=False)
    
    # Agent capabilities table
    op.create_table(
        'agent_capabilities',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('agent_id', sa.String(36), sa.ForeignKey('agent_sessions.agent_id'), nullable=False),
        sa.Column('capability_name', sa.String(100), nullable=False),
        sa.Column('capability_level', sa.String(20), nullable=False, default='basic'),
        sa.Column('is_active', sa.Boolean, nullable=False, default=True),
        sa.Column('metadata', sa.JSON, nullable=False, default='{}'),
        sa.Column('granted_at', sa.DateTime, nullable=False, default=sa.func.now()),
        sa.Column('revoked_at', sa.DateTime, nullable=True),
    )
    
    op.create_index('ix_agent_capabilities_agent_id', 'agent_capabilities', ['agent_id'], unique=False)
    op.create_index('ix_agent_capabilities_capability_name', 'agent_capabilities', ['capability_name'], unique=False)
    op.create_index('ix_agent_capabilities_is_active', 'agent_capabilities', ['is_active'], unique=False)
    
    # Agent audit log
    op.create_table(
        'agent_audit_log',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('agent_id', sa.String(36), nullable=False),
        sa.Column('action', sa.String(100), nullable=False),
        sa.Column('details', sa.JSON, nullable=False, default='{}'),
        sa.Column('timestamp', sa.DateTime, nullable=False, default=sa.func.now()),
        sa.Column('ip_address', sa.String(45), nullable=True),
    )
    
    op.create_index('ix_agent_audit_log_agent_id', 'agent_audit_log', ['agent_id'], unique=False)
    op.create_index('ix_agent_audit_log_timestamp', 'agent_audit_log', ['timestamp'], unique=False)
    
    # Resource consumption tracking
    op.create_table(
        'resource_consumption',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('agent_id', sa.String(36), nullable=True),
        sa.Column('resource_type', sa.String(50), nullable=False),  # cpu, memory, tokens, time
        sa.Column('amount', sa.Float, nullable=False),
        sa.Column('unit', sa.String(20), nullable=False, default='units'),
        sa.Column('timestamp', sa.DateTime, nullable=False, default=sa.func.now()),
        sa.Column('metadata', sa.JSON, nullable=False, default='{}'),
    )
    
    op.create_index('ix_resource_consumption_agent_id', 'resource_consumption', ['agent_id'], unique=False)
    op.create_index('ix_resource_consumption_resource_type', 'resource_consumption', ['resource_type'], unique=False)
    op.create_index('ix_resource_consumption_timestamp', 'resource_consumption', ['timestamp'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_resource_consumption_timestamp', table_name='resource_consumption')
    op.drop_index('ix_resource_consumption_resource_type', table_name='resource_consumption')
    op.drop_index('ix_resource_consumption_agent_id', table_name='resource_consumption')
    op.drop_index('ix_agent_audit_log_timestamp', table_name='agent_audit_log')
    op.drop_index('ix_agent_audit_log_agent_id', table_name='agent_audit_log')
    op.drop_index('ix_agent_capabilities_is_active', table_name='agent_capabilities')
    op.drop_index('ix_agent_capabilities_capability_name', table_name='agent_capabilities')
    op.drop_index('ix_agent_capabilities_agent_id', table_name='agent_capabilities')
    op.drop_index('ix_workflow_step_executions_status', table_name='workflow_step_executions')
    op.drop_index('ix_workflow_step_executions_step_name', table_name='workflow_step_executions')
    op.drop_index('ix_workflow_step_executions_workflow_execution_id', table_name='workflow_step_executions')
    op.drop_table('resource_consumption')
    op.drop_table('agent_audit_log')
    op.drop_table('agent_capabilities')
    op.drop_table('workflow_step_executions')