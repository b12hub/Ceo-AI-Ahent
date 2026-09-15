"""Initial schema

Revision ID: 73c25ec11e2f
Revises: 
Create Date: 2026-09-14 13:02:32.767836

"""
from typing import Sequence, Union

import pgvector
import sqlmodel
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '73c25ec11e2f'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Enable pgvector extension inside PostgreSQL
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    # 2. Existing table creation code follows below
    op.create_table(
        'companydocument',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('content_chunk', sa.String(), nullable=False),
        sa.Column('embedding', pgvector.sqlalchemy.Vector(768), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

def downgrade() -> None:
    op.drop_table('companydocument')
    op.execute("DROP EXTENSION IF EXISTS vector;")
