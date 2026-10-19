"""Single domain cutover: projects, user experience lifecycle, raw legacy archive."""
from alembic import op
import sqlalchemy as sa

revision = 'a6d307b912e4'
down_revision = 'f1b90a3d725e'
branch_labels = None
depends_on = None


def upgrade():
    op.rename_table('works', 'projects')
    op.add_column('research_experiences', sa.Column('status', sa.String(20), nullable=False, server_default='candidate'))
    op.add_column('research_experiences', sa.Column('history', sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
    op.add_column('research_experiences', sa.Column('validation', sa.JSON(), nullable=True))
    # Existing automatic summaries remain candidates, preserving their original
    # content, version and enabled preference; they need validation and adoption.
    bind = op.get_bind()
    experiences = sa.table('research_experiences', sa.column('id'), sa.column('version'),
                           sa.column('content_hash'), sa.column('payload'), sa.column('history', sa.JSON()))
    for row in bind.execute(sa.select(experiences.c.id, experiences.c.version,
                                     experiences.c.content_hash, experiences.c.payload)).mappings():
        payload = row['payload']
        if isinstance(payload, str):
            import json
            payload = json.loads(payload)
        bind.execute(experiences.update().where(experiences.c.id == row['id']).values(history=[{
            'version': row['version'], 'hash': row['content_hash'], 'payload': payload,
            'event': 'migrated_candidate', 'source': 'research_experiences'}]))
    op.create_table('legacy_domain_records',
                    sa.Column('id', sa.Integer(), primary_key=True),
                    sa.Column('source', sa.String(255), nullable=False),
                    sa.Column('payload', sa.JSON(), nullable=False))
    projects = sa.Table('projects', sa.MetaData(), autoload_with=bind)
    archive = sa.table('legacy_domain_records', sa.column('source'), sa.column('payload', sa.JSON()))
    for row in bind.execute(sa.select(projects.c.work_id, projects.c.progress)).mappings():
        bind.execute(archive.insert().values(source=f"works/{row['work_id']}/progress", payload={'progress':row['progress']}))
    op.drop_column('projects', 'progress')
    inspector = sa.inspect(bind)
    if 'work_flow_states' in inspector.get_table_names():
        table = sa.Table('work_flow_states', sa.MetaData(), autoload_with=bind)
        archive = sa.table('legacy_domain_records', sa.column('source'), sa.column('payload', sa.JSON()))
        for row in bind.execute(sa.select(table)).mappings():
            # Do not infer plans or scientific acceptance from a workflow label.
            import json
            raw = json.loads(json.dumps(dict(row), default=str))
            bind.execute(archive.insert().values(source=f"work_flow_states/{row['id']}", payload=raw))
        op.drop_table('work_flow_states')


def downgrade():
    raise RuntimeError('领域迁移不恢复旧写入路径；请从备份恢复整个数据库')
