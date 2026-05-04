"""add user profile table and replace assessor_name with first/last name

Revision ID: d4e5f6a7b8c9
Revises: 93a36b6017c8
Create Date: 2026-04-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = '93a36b6017c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    user_cols = [row[0] for row in conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='Users'"
    ))]

    if 'first_name' not in user_cols:
        op.add_column('Users', sa.Column('first_name', sa.Text(), nullable=True))
    if 'last_name' not in user_cols:
        op.add_column('Users', sa.Column('last_name', sa.Text(), nullable=True))
    if 'assessor_name' in user_cols:
        op.drop_column('Users', 'assessor_name')

    tables = [row[0] for row in conn.execute(sa.text(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
    ))]
    if 'User_Profiles' not in tables:
        op.create_table(
            'User_Profiles',
            sa.Column('id',          sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('user_id',     sa.Integer(), sa.ForeignKey('Users.id', ondelete='CASCADE'),
                      unique=True, nullable=False),
            sa.Column('rank',        sa.Text(), nullable=True),
            sa.Column('initials',    sa.Text(), nullable=True),
            sa.Column('surname',     sa.Text(), nullable=True),
            sa.Column('jpa_number',  sa.Text(), nullable=True),
            sa.Column('appointment', sa.Text(), nullable=True),
            sa.Column('no',          sa.Text(), nullable=True),
            sa.Column('sqn_vgs_no',  sa.Text(), nullable=True),
            sa.Column('wing_ccf',    sa.Text(), nullable=True),
            sa.Column('home_address', sa.Text(), nullable=True),
            sa.Column('car_reg',      sa.Text(), nullable=True),
        )


def downgrade() -> None:
    op.drop_table('User_Profiles')
    op.add_column('Users', sa.Column('assessor_name', sa.Text(), nullable=True))
    op.drop_column('Users', 'first_name')
    op.drop_column('Users', 'last_name')
