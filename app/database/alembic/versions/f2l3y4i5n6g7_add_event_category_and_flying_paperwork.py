"""add event categories and flying paperwork tracking

Revision ID: f2l3y4i5n6g7
Revises: f1y2i3n4g5d6
Create Date: 2026-10-11 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2l3y4i5n6g7'
down_revision: Union[str, Sequence[str], None] = 'f1y2i3n4g5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = inspector.get_table_names()

    all_event_cols = {c['name'] for c in inspector.get_columns('All_Events')}
    if 'bader_event_id' not in all_event_cols:
        op.add_column('All_Events', sa.Column('bader_event_id', sa.Text(), nullable=True))
    if 'category' not in all_event_cols:
        op.add_column('All_Events', sa.Column('category', sa.Text(), nullable=True))

    event_317_cols = {c['name'] for c in inspector.get_columns('317_Events')}
    if 'category' not in event_317_cols:
        op.add_column('317_Events', sa.Column('category', sa.Text(), nullable=True))

    if 'Flying_Events' not in tables:
        op.create_table(
            'Flying_Events',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('bader_event_id', sa.Text(), nullable=False),
            sa.Column('title', sa.Text(), nullable=False),
            sa.Column('category', sa.Text(), nullable=False),
            sa.Column('date_from', sa.DateTime(), nullable=True),
            sa.Column('date_to', sa.DateTime(), nullable=True),
            sa.Column('on_bader', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('last_seen_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('bader_event_id'),
        )

    if 'Flying_Paperwork' not in tables:
        op.create_table(
            'Flying_Paperwork',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('flying_event_id', sa.Integer(), nullable=False),
            sa.Column('cadet_id', sa.BigInteger(), nullable=False),
            sa.Column('on_event', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column('tg21', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('tg23', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('av_med', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.Column('updated_by', sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(['flying_event_id'], ['Flying_Events.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['cadet_id'], ['Cadets.cin'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('flying_event_id', 'cadet_id'),
        )
        op.create_index('ix_Flying_Paperwork_flying_event_id', 'Flying_Paperwork', ['flying_event_id'])


def downgrade() -> None:
    op.drop_index('ix_Flying_Paperwork_flying_event_id', table_name='Flying_Paperwork')
    op.drop_table('Flying_Paperwork')
    op.drop_table('Flying_Events')
    op.drop_column('317_Events', 'category')
    op.drop_column('All_Events', 'category')
    op.drop_column('All_Events', 'bader_event_id')
