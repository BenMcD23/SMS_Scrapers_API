"""add the programme builder

The monthly programme built in the SMS site rather than a Google Doc: the
session bank, rooms, each month's nights and placed sessions, the comment
threads NCOs and staff leave on it, and its change history
(routers/programme_builder.py).

It also fills in the squadron's rooms and a bank of the sessions the old
programme docs (2024-26) ran most, so the builder is useful on day one. They
are ordinary rows: staff edit or delete them like any they add.

Revision ID: p7b8u9i0l1d2
Revises: p1a2r3a4d5e6
Create Date: 2026-11-25 20:00:00.000000

"""
from typing import Sequence, Union

from datetime import datetime

from alembic import op
import sqlalchemy as sa


revision: str = 'p7b8u9i0l1d2'
down_revision: Union[str, Sequence[str], None] = 'p1a2r3a4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _index(table: str, column: str) -> None:
    op.create_index(f'ix_{table}_{column}', table, [column])


# The squadron's rooms. Their order sets their colours on the printed roomings.
ROOMS = [
    ('Main Parade Hall', None),
    ('Range', 'Shooting, and a normal room for lessons'),
    ('Hangar', 'Indoor drill, archery'),
    ('Flight Sim Room', 'Small: fine for a small lesson'),
    ('Outside', None),
]

# (title, kind, whole night, room): the old docs' most-run sessions. Lessons
# go in the Range, clear of drill in the Main Parade Hall; staff can move them.
BANK = [
    ('Drill', 'drill', False, 'Main Parade Hall'),
    ('Inspection', 'drill', False, 'Main Parade Hall'),
    ('Progressive Drill', 'drill', False, 'Main Parade Hall'),
    ('5x5 Practice', 'drill', False, 'Main Parade Hall'),
    ('Inter Flight Drill', 'drill', False, 'Main Parade Hall'),
    ('Passing Out Parade Practice', 'drill', False, 'Main Parade Hall'),
    ('Banner Drill', 'drill', False, 'Main Parade Hall'),
    ('Classifications', 'classifications', False, 'Range'),
    ('Classification Exams', 'classifications', False, 'Range'),
    ('History of the ATC', 'lessons', False, 'Range'),
    ('History of the RAF', 'lessons', False, 'Range'),
    ('History of Flight', 'lessons', False, 'Range'),
    ('Rank System', 'lessons', False, 'Range'),
    ('Squadron Security', 'lessons', False, 'Range'),
    ('Principles of Flight', 'lessons', False, 'Range'),
    ('Fieldcraft', 'fieldcraft', False, None),
    ('Map Reading', 'fieldcraft', False, 'Range'),
    ('Field Cooking', 'fieldcraft', False, 'Outside'),
    ('Essential First Aid', 'first_aid', False, None),
    ('Casualty Simulation', 'first_aid', False, None),
    ('Flight Sim', 'stem', False, 'Flight Sim Room'),
    ('Space', 'stem', False, None),
    ('Basic Radio Comms', 'stem', False, None),
    ('Bronze Cyber', 'stem', False, None),
    ('Sports', 'sport', False, 'Outside'),
    ('Inter Flight Sports', 'sport', False, 'Outside'),
    ('Archery', 'sport', False, 'Hangar'),
    ('Air Rifle Training', 'shooting', False, 'Range'),
    ('DofE Physical', 'dofe', False, None),
    ('DofE Prep', 'dofe', False, None),
    ('Band', 'music', False, None),
    ('Leadership', 'leadership', False, None),
    ('Cadets Own Session', 'leadership', False, None),
    ('NCO Session', 'leadership', False, None),
    ('MOI Lesson', 'leadership', False, None),
    ('Flight Time', 'admin', False, None),
    ('Uniform Preparation', 'admin', False, None),
    ('Uniform Measurement', 'admin', False, None),
    ('Logbook Catchup', 'admin', False, None),
    ('Recruitment Night', 'admin', True, None),
    ('Social Night', 'social', True, None),
    ('Flight Social', 'social', False, None),
]


def upgrade() -> None:
    rooms = op.create_table(
        'Programme_Rooms',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.Text(), nullable=False, unique=True),
        sa.Column('capacity', sa.Integer(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('position', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('active', sa.Boolean(), nullable=False, server_default='1'),
    )
    op.create_table(
        'Programme_Kinds',
        sa.Column('key', sa.Text(), primary_key=True),
        sa.Column('label', sa.Text(), nullable=False),
        sa.Column('colour', sa.Text(), nullable=True),
        sa.Column('position', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('hidden', sa.Boolean(), nullable=False, server_default='0'),
    )
    bank = op.create_table(
        'Programme_Bank_Items',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('category', sa.Text(), nullable=False),
        sa.Column('whole_night', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('room_id', sa.Integer(), sa.ForeignKey('Programme_Rooms.id', ondelete='SET NULL'), nullable=True),
        sa.Column('instructor', sa.Text(), nullable=True),
        sa.Column('people', sa.JSON(), nullable=False),
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
        sa.Column('key_dates', sa.JSON(), nullable=False),
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

    op.bulk_insert(rooms, [{'name': n, 'notes': note, 'position': i, 'active': True}
                           for i, (n, note) in enumerate(ROOMS)])
    room_ids = dict(op.get_bind().execute(sa.select(rooms.c.name, rooms.c.id)).all())
    now = datetime.now()
    op.bulk_insert(bank, [{'title': t, 'category': k, 'whole_night': w, 'room_id': room_ids.get(r), 'people': [],
                           'active': True, 'created_at': now, 'created_by': 'Default'} for t, k, w, r in BANK])


def downgrade() -> None:
    for table in ('Programme_Changes', 'Programme_Comments', 'Programme_Slots', 'Programme_Draft_Nights',
                  'Programme_Drafts', 'Programme_Bank_Items', 'Programme_Kinds', 'Programme_Rooms'):
        op.drop_table(table)
