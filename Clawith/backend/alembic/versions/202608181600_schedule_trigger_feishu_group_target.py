"""Add Feishu group delivery targets to schedules and triggers.

Revision ID: f065_feishu_group_target
Revises: f064_tool_call_tenants
Create Date: 2026-08-18 16:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f065_feishu_group_target"
down_revision: str | Sequence[str] | None = "f064_tool_call_tenants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Fresh deployments get this column from initial_schema's create_all, so
    # the add must be idempotent.
    op.execute("ALTER TABLE agent_schedules ADD COLUMN IF NOT EXISTS delivery_target_id UUID")
    op.execute("ALTER TABLE agent_triggers ADD COLUMN IF NOT EXISTS delivery_target_id UUID")


def downgrade() -> None:
    op.drop_column("agent_triggers", "delivery_target_id")
    op.drop_column("agent_schedules", "delivery_target_id")
