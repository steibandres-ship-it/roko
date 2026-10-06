"""Bulk email pre-registration, independent of fan consent."""
from alembic import op
import sqlalchemy as sa
revision = 'a03preusers01'
down_revision = 'f92a206supa01'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('preuser_imports',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('filename',sa.String(255),nullable=False),
        sa.Column('file_hash',sa.String(64),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('rows',sa.Integer(),nullable=False),
        sa.Column('imported',sa.Integer(),nullable=False),
        sa.Column('duplicates',sa.Integer(),nullable=False),
        sa.Column('invalid',sa.Integer(),nullable=False),
        sa.Column('errors',sa.JSON(),nullable=False))
    op.create_index('ix_preuser_imports_file_hash','preuser_imports',['file_hash'])
    op.create_table('preusers',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('email',sa.String(254),nullable=False,unique=True),
        sa.Column('status',sa.String(24),nullable=False),
        sa.Column('import_id',sa.String(36),sa.ForeignKey('preuser_imports.id'),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_preusers_status','preusers',['status'])
    op.create_index('ix_preusers_import_id','preusers',['import_id'])
    if op.get_bind().dialect.name == 'postgresql':
        for table in ('preusers','preuser_imports'):
            op.execute(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'REVOKE ALL ON TABLE public."{table}" FROM PUBLIC')
            for role in ('anon','authenticated'):
                op.execute(f'''DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN REVOKE ALL ON TABLE public."{table}" FROM {role}; END IF; END $$''')

def downgrade():
    op.drop_table('preusers')
    op.drop_table('preuser_imports')
