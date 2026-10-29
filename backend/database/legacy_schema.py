"""Recognize the one pre-Alembic schema created by the old local startup."""
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

LEGACY_REVISION = 'c4e8a1b2d9f0'


def _schema(connection):
    inspector = sa.inspect(connection)
    result = {}
    for table in set(inspector.get_table_names()) - {'alembic_version'}:
        columns = {
            column['name']: (
                'DATETIME' if isinstance(column['type'], sa.DateTime) else str(column['type']),
                column['nullable'],
            )
            for column in inspector.get_columns(table)
        }
        foreign_keys = {
            (tuple(key['constrained_columns']), key['referred_table'], tuple(key['referred_columns']))
            for key in inspector.get_foreign_keys(table)
        }
        unique = {tuple(key['column_names']) for key in inspector.get_unique_constraints(table)}
        unique.update(tuple(index['column_names']) for index in inspector.get_indexes(table) if index['unique'])
        result[table] = (columns, tuple(inspector.get_pk_constraint(table)['constrained_columns']), foreign_keys, unique)
    return result


def adopt_legacy_schema(migration, scripts):
    if migration.get_current_heads():
        return
    actual = _schema(migration.bind)
    if not actual:
        return

    # Reuse the existing migrations as the schema definition instead of keeping
    # a second copy of the old ORM. Only this known legacy revision is adopted.
    reference = sa.create_engine('sqlite://')
    try:
        with reference.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                for revision in reversed(list(scripts.walk_revisions(base='base', head=LEGACY_REVISION))):
                    revision.module.upgrade()
            expected = _schema(connection)
    finally:
        reference.dispose()
    if actual != expected:
        raise RuntimeError(
            '数据库已有表但没有 Alembic 版本，且结构不匹配已知旧版。'
            '未认领迁移版本；请备份数据库并核对部署版本和表结构后再升级。'
        )
    migration.stamp(scripts, LEGACY_REVISION)
