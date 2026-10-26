"""foreign-key indexes, delete rules and one-row-per uniques

PostgreSQL doesn't index a foreign key by itself, so every "this cadet's ..."
lookup and every cascade check scanned the child table. This indexes each
foreign key that no index leads with (renaming the three older hand-named ones
to the standard ``ix_<table>_<column>`` so the models and the database agree).

It also gives the cadet foreign keys that had no delete rule one: their rows
go with the cadet, except stores orders, which outlive them (the stock insights
count their sizes).

And it makes "one row per cadet and qualification" and "one row per cadet and
event" a rule the database keeps, after removing the duplicates already there:
for qualifications the row with the latest achieved date stays, for events the
oldest.

Revision ID: d1b2i3n4d5x6
Revises: c3o4m5p6l7e8
Create Date: 2026-10-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd1b2i3n4d5x6'
down_revision: Union[str, Sequence[str], None] = 'c3o4m5p6l7e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


INDEXED = [
    ('317_Events', 'location_id'), ('All_Events', 'parent_id'),
    ('Assessment_Sheets', 'cadet_id'), ('Assessment_Sheets', 'assessor_id'),
    ('Badge_Items', 'cell_id'), ('Badge_Order_Items', 'order_id'), ('Badge_Orders', 'cadet_id'),
    ('Cadet_Absences', 'cadet_id'), ('Cadet_Dietary', 'cadet_id'), ('Cadet_Events', 'event_id'),
    ('Cadet_Medical', 'cadet_id'), ('Committee_Request_Receipts', 'request_id'),
    ('Committee_Requests', 'requester_id'), ('Flying_Paperwork', 'cadet_id'),
    ('Logs_Form_Entries', 'form_id'), ('NCO_Appraisals', 'author_id'),
    ('NCO_Comment_Replies', 'comment_id'), ('NCO_Comment_Replies', 'author_id'),
    ('NCO_Comments', 'author_id'), ('NCO_Comments', 'cadet_id'), ('NCO_Holidays', 'user_id'),
    ('Scraper_Schedules', 'user_id'), ('Session_Plan_Attachments', 'plan_id'),
    ('Session_Plan_Comments', 'plan_id'), ('Session_Plan_Comments', 'author_id'),
    ('Session_Plans', 'author_id'), ('Stores_Item_Issuances', 'cadet_id'),
    ('Stores_Item_Issuances', 'user_id'), ('Stores_Items', 'box_id'), ('Stores_Items', 'section_id'),
    ('Stores_Order_Items', 'order_id'), ('Stores_Orders', 'cadet_id'), ('Stores_Orders', 'user_id'),
    ('Stores_Sections', 'box_id'),
]

# (table, column, referred table, referred column, new rule)
DELETE_RULES = [
    ('Cadet_Qualifications', 'cadet_id', 'Cadets', 'cin', 'CASCADE'),
    ('Cadet_Medical', 'cadet_id', 'Cadets', 'cin', 'CASCADE'),
    ('Cadet_Dietary', 'cadet_id', 'Cadets', 'cin', 'CASCADE'),
    ('Cadet_Events', 'cadet_id', 'Cadets', 'cin', 'CASCADE'),
    ('Cadet_Events', 'event_id', 'All_Events', 'id', 'CASCADE'),
    ('Assessment_Sheets', 'cadet_id', 'Cadets', 'cin', 'CASCADE'),
    ('Badge_Orders', 'cadet_id', 'Cadets', 'cin', 'CASCADE'),
    ('Stores_Orders', 'cadet_id', 'Cadets', 'cin', 'SET NULL'),
]

# (constraint, table, columns, which duplicate to keep: ORDER BY within the group)
UNIQUES = [
    ('uq_cadet_qualification', 'Cadet_Qualifications', ['cadet_id', 'qual_type'],
     # NULLs sort first in a DESC order on PostgreSQL, so the CASE puts dated rows first.
     'CASE WHEN date_achieved IS NULL THEN 1 ELSE 0 END, date_achieved DESC, id DESC'),
    ('uq_cadet_event', 'Cadet_Events', ['cadet_id', 'event_id'], 'id'),
]


def _ix(table: str, column: str) -> str:
    return f'ix_{table}_{column}'


def _single_column_indexes(insp, table: str) -> dict[str, str]:
    """column -> index name, for the table's one-column indexes."""
    return {i['column_names'][0]: i['name'] for i in insp.get_indexes(table) if len(i['column_names']) == 1}


def _fk_name(insp, table: str, column: str) -> str | None:
    for fk in insp.get_foreign_keys(table):
        if fk['constrained_columns'] == [column]:
            return fk['name']
    return None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    for table, column in INDEXED:
        existing = _single_column_indexes(insp, table).get(column)
        if existing == _ix(table, column):
            continue
        if existing:
            op.drop_index(existing, table_name=table)
        op.create_index(_ix(table, column), table, [column])

    # SQLite (the preview database) is built from the models by create_all and
    # can't alter a foreign key in place; production is PostgreSQL.
    if bind.dialect.name == 'postgresql':
        for table, column, ref_table, ref_column, rule in DELETE_RULES:
            name = _fk_name(insp, table, column)
            if name:
                op.drop_constraint(name, table, type_='foreignkey')
            op.create_foreign_key(f'{table}_{column}_fkey', table, ref_table, [column], [ref_column],
                                  ondelete=rule)

    for name, table, columns, keep_first in UNIQUES:
        cols = ', '.join(columns)
        op.execute(sa.text(
            f'DELETE FROM "{table}" WHERE id IN ('
            f'  SELECT id FROM ('
            f'    SELECT id, ROW_NUMBER() OVER (PARTITION BY {cols} ORDER BY {keep_first}) AS n'
            f'    FROM "{table}"'
            f'  ) ranked WHERE n > 1'
            f')'
        ))
        with op.batch_alter_table(table) as batch:
            batch.create_unique_constraint(name, columns)


def downgrade() -> None:
    bind = op.get_bind()
    for name, table, _, _ in UNIQUES:
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(name, type_='unique')

    if bind.dialect.name == 'postgresql':
        for table, column, ref_table, ref_column, _ in DELETE_RULES:
            op.drop_constraint(f'{table}_{column}_fkey', table, type_='foreignkey')
            op.create_foreign_key(f'{table}_{column}_fkey', table, ref_table, [column], [ref_column])

    # The three indexes that existed before keep their standard names; the rest go.
    kept = {('NCO_Holidays', 'user_id'), ('NCO_Comments', 'cadet_id'), ('NCO_Comment_Replies', 'comment_id')}
    for table, column in INDEXED:
        if (table, column) not in kept:
            op.drop_index(_ix(table, column), table_name=table)
