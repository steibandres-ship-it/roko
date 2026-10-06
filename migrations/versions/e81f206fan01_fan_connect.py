"""Fan contacts, campaign drafts and send queue."""
from alembic import op
import sqlalchemy as sa
revision = 'e81f206fan01'
down_revision = 'd74e19ac6f21'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('fan_contacts',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('artist_id', sa.String(length=36), sa.ForeignKey('artists.id'), primary_key=False, nullable=False),
        sa.Column('email', sa.String(length=254), primary_key=False, nullable=False),
        sa.Column('name', sa.String(length=120), primary_key=False, nullable=False),
        sa.Column('source', sa.String(length=240), primary_key=False, nullable=False),
        sa.Column('consent_at', sa.String(length=40), primary_key=False, nullable=False),
        sa.Column('status', sa.String(length=20), primary_key=False, nullable=False),
        sa.Column('token', sa.String(length=64), primary_key=False, nullable=False),
        sa.UniqueConstraint('token'),
        sa.UniqueConstraint('artist_id', 'email'),
    )
    op.create_table('fan_campaigns',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('artist_id', sa.String(length=36), sa.ForeignKey('artists.id'), primary_key=False, nullable=False),
        sa.Column('subject', sa.String(length=200), primary_key=False, nullable=False),
        sa.Column('body', sa.Text(), primary_key=False, nullable=False),
        sa.Column('source', sa.String(length=240), primary_key=False, nullable=False),
        sa.Column('status', sa.String(length=20), primary_key=False, nullable=False),
    )
    op.create_table('fan_recipients',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('campaign_id', sa.String(length=36), sa.ForeignKey('fan_campaigns.id'), primary_key=False, nullable=False),
        sa.Column('contact_id', sa.String(length=36), sa.ForeignKey('fan_contacts.id'), primary_key=False, nullable=False),
        sa.Column('status', sa.String(length=20), primary_key=False, nullable=False),
        sa.UniqueConstraint('campaign_id', 'contact_id'),
    )

def downgrade():
    op.drop_table('fan_recipients')
    op.drop_table('fan_campaigns')
    op.drop_table('fan_contacts')
