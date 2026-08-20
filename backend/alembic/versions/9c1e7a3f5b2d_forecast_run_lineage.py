"""forecast_run lineage columns

Revision ID: 9c1e7a3f5b2d
Revises: 68ffb6b8092b
Create Date: 2026-08-20 00:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '9c1e7a3f5b2d'
down_revision: str | None = '68ffb6b8092b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('forecast_run', sa.Column('engine_version', sa.String(), nullable=False, server_default='unknown'))
    op.add_column('forecast_run', sa.Column('config_hash', sa.String(), nullable=False, server_default='unknown'))
    op.add_column('forecast_run', sa.Column('git_sha', sa.String(), nullable=False, server_default='unknown'))
    op.add_column(
        'forecast_run',
        sa.Column(
            'input_data_versions',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default='{}',
        ),
    )
    op.alter_column('forecast_run', 'engine_version', server_default=None)
    op.alter_column('forecast_run', 'config_hash', server_default=None)
    op.alter_column('forecast_run', 'git_sha', server_default=None)
    op.alter_column('forecast_run', 'input_data_versions', server_default=None)


def downgrade() -> None:
    op.drop_column('forecast_run', 'input_data_versions')
    op.drop_column('forecast_run', 'git_sha')
    op.drop_column('forecast_run', 'config_hash')
    op.drop_column('forecast_run', 'engine_version')
