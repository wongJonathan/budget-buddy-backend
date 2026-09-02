"""add auth tables: sessions, auth_events; user auth columns

Revision ID: 9dd491ad9ae9
Revises: 371ddc09c490
Create Date: 2026-08-26 23:22:13.700594

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '9dd491ad9ae9'
down_revision: Union[str, Sequence[str], None] = '371ddc09c490'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('auth_events',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('event_type', sa.String(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('email', sa.String(), nullable=True),
    sa.Column('ip', postgresql.INET(), nullable=True),
    sa.Column('user_agent', sa.String(), nullable=True),
    sa.Column('session_id', sa.Uuid(), nullable=True),
    sa.Column('detail', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_auth_events_email_time', 'auth_events', ['email', sa.literal_column('occurred_at DESC')], unique=False)
    op.create_index('ix_auth_events_type_time', 'auth_events', ['event_type', sa.literal_column('occurred_at DESC')], unique=False)
    op.create_index('ix_auth_events_user_time', 'auth_events', ['user_id', sa.literal_column('occurred_at DESC')], unique=False)
    op.create_table('sessions',
    sa.Column('token_hash', sa.String(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('user_agent', sa.String(), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_index(op.f('ix_sessions_user_id'), 'sessions', ['user_id'], unique=False)

    # Side effect of adding `datetime.datetime -> DateTime(timezone=True)` to
    # Base.type_annotation_map: this pre-existing column was TIMESTAMP WITHOUT
    # TIME ZONE. Postgres reinterprets the stored values using the session
    # TimeZone, which is Etc/UTC here, so no values shift.
    op.alter_column('budgets', 'created_at',
               existing_type=postgresql.TIMESTAMP(),
               type_=sa.DateTime(timezone=True),
               existing_nullable=False,
               existing_server_default=sa.text('now()'))

    op.add_column('users', sa.Column('email', sa.String(), nullable=False))
    op.add_column('users', sa.Column('hashed_password', sa.String(), nullable=False))
    op.add_column('users', sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False))
    op.add_column('users', sa.Column('password_changed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False))
    op.create_unique_constraint('uq_users_email', 'users', ['email'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('uq_users_email', 'users', type_='unique')
    op.drop_column('users', 'password_changed_at')
    op.drop_column('users', 'created_at')
    op.drop_column('users', 'hashed_password')
    op.drop_column('users', 'email')
    op.alter_column('budgets', 'created_at',
               existing_type=sa.DateTime(timezone=True),
               type_=postgresql.TIMESTAMP(),
               existing_nullable=False,
               existing_server_default=sa.text('now()'))
    op.drop_index(op.f('ix_sessions_user_id'), table_name='sessions')
    op.drop_table('sessions')
    op.drop_index('ix_auth_events_user_time', table_name='auth_events')
    op.drop_index('ix_auth_events_type_time', table_name='auth_events')
    op.drop_index('ix_auth_events_email_time', table_name='auth_events')
    op.drop_table('auth_events')
