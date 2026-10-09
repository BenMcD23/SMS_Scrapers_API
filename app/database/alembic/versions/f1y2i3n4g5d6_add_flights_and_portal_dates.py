"""add cadet flights, join date and classification dates (Volunteer Portal)

Revision ID: f1y2i3n4g5d6
Revises: v1p2s3y4n5c6
Create Date: 2026-10-09 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1y2i3n4g5d6'
down_revision: Union[str, Sequence[str], None] = 'v1p2s3y4n5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    cadet_cols = {c['name'] for c in inspector.get_columns('Cadets')}
    if 'joined_on' not in cadet_cols:
        op.add_column('Cadets', sa.Column('joined_on', sa.Date(), nullable=True))
    if 'classification_dates' not in cadet_cols:
        op.add_column('Cadets', sa.Column('classification_dates', sa.JSON(), nullable=True))

    if 'Cadet_Flights' not in inspector.get_table_names():
        op.create_table(
            'Cadet_Flights',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('cadet_id', sa.BigInteger(), nullable=False),
            sa.Column('date', sa.Date(), nullable=False),
            sa.Column('activity', sa.Text(), nullable=False),
            sa.Column('aircraft', sa.Text(), nullable=True),
            sa.Column('category', sa.Text(), nullable=True),
            sa.Column('duty', sa.Text(), nullable=True),
            sa.Column('sortie', sa.Text(), nullable=True),
            sa.Column('unit', sa.Text(), nullable=True),
            sa.Column('minutes', sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(['cadet_id'], ['Cadets.cin'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_Cadet_Flights_cadet_id', 'Cadet_Flights', ['cadet_id'])


def downgrade() -> None:
    op.drop_index('ix_Cadet_Flights_cadet_id', table_name='Cadet_Flights')
    op.drop_table('Cadet_Flights')
    op.drop_column('Cadets', 'classification_dates')
    op.drop_column('Cadets', 'joined_on')
