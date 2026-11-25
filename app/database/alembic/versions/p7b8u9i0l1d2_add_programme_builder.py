"""add the programme builder

The monthly programme built in the SMS site rather than a Google Doc: the
session bank, rooms, each month's nights and placed sessions, the comment
threads NCOs and staff leave on it, and its change history
(routers/programme_builder.py).

Revision ID: p7b8u9i0l1d2
Revises: s1t2n3s4n5s6
Create Date: 2026-11-25 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'p7b8u9i0l1d2'
down_revision: Union[str, Sequence[str], None] = 's1t2n3s4n5s6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _index(table: str, column: str) -> None:
    op.create_index(f'ix_{table}_{column}', table, [column])


def upgrade() -> None:
    op.create_table(
        'Programme_Rooms',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.Text(), nullable=False, unique=True),
        sa.Column('capacity', sa.Integer(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('position', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('active', sa.Boolean(), nullable=False, server_default='1'),
    )
    op.create_table(
        'Programme_Bank_Items',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('category', sa.Text(), nullable=False),
        sa.Column('whole_night', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('room_id', sa.Integer(), sa.ForeignKey('Programme_Rooms.id', ondelete='SET NULL'), nullable=True),
        sa.Column('instructor', sa.Text(), nullable=True),
        sa.Column('subject_key', sa.Text(), nullable=True),
        sa.Column('part', sa.Integer(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by', sa.Text(), nullable=True),
    )
    _index('Programme_Bank_Items', 'room_id')
    op.create_table(
        'Programme_Drafts',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('month', sa.Text(), nullable=False, unique=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('updated_by', sa.Text(), nullable=True),
        sa.Column('published_at', sa.DateTime(), nullable=True),
        sa.Column('published_by', sa.Text(), nullable=True),
    )
    op.create_table(
        'Programme_Draft_Nights',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('programme_id', sa.Integer(), sa.ForeignKey('Programme_Drafts.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('uniform', sa.Text(), nullable=True),
        sa.Column('duty_nco', sa.Text(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.UniqueConstraint('programme_id', 'date', name='uq_programme_draft_night'),
    )
    _index('Programme_Draft_Nights', 'programme_id')
    op.create_table(
        'Programme_Slots',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('night_id', sa.Integer(), sa.ForeignKey('Programme_Draft_Nights.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('period', sa.Integer(), nullable=True),
        sa.Column('flights', sa.JSON(), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('category', sa.Text(), nullable=False),
        sa.Column('bank_item_id', sa.Integer(), sa.ForeignKey('Programme_Bank_Items.id', ondelete='SET NULL'),
                  nullable=True),
        sa.Column('room_id', sa.Integer(), sa.ForeignKey('Programme_Rooms.id', ondelete='SET NULL'), nullable=True),
        sa.Column('instructor', sa.Text(), nullable=True),
        sa.Column('people', sa.JSON(), nullable=False),
        sa.Column('subject_key', sa.Text(), nullable=True),
        sa.Column('part', sa.Integer(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
    )
    for column in ('night_id', 'bank_item_id', 'room_id'):
        _index('Programme_Slots', column)
    op.create_table(
        'Programme_Comments',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('programme_id', sa.Integer(), sa.ForeignKey('Programme_Drafts.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('parent_id', sa.Integer(), sa.ForeignKey('Programme_Comments.id', ondelete='CASCADE'),
                  nullable=True),
        sa.Column('night_id', sa.Integer(), sa.ForeignKey('Programme_Draft_Nights.id', ondelete='SET NULL'),
                  nullable=True),
        sa.Column('slot_id', sa.Integer(), sa.ForeignKey('Programme_Slots.id', ondelete='SET NULL'), nullable=True),
        sa.Column('quote', sa.Text(), nullable=True),
        sa.Column('author_id', sa.Integer(), sa.ForeignKey('Users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('mentions', sa.JSON(), nullable=False),
        sa.Column('assignee', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('edited_at', sa.DateTime(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.Column('resolved_by', sa.Text(), nullable=True),
    )
    for column in ('programme_id', 'parent_id', 'night_id', 'slot_id', 'author_id'):
        _index('Programme_Comments', column)
    op.create_table(
        'Programme_Changes',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('programme_id', sa.Integer(), sa.ForeignKey('Programme_Drafts.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('at', sa.DateTime(), nullable=False),
        sa.Column('by', sa.Text(), nullable=True),
        sa.Column('summary', sa.Text(), nullable=False),
    )
    _index('Programme_Changes', 'programme_id')


def downgrade() -> None:
    for table in ('Programme_Changes', 'Programme_Comments', 'Programme_Slots', 'Programme_Draft_Nights',
                  'Programme_Drafts', 'Programme_Bank_Items', 'Programme_Rooms'):
        op.drop_table(table)
