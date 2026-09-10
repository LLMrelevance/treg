"""Nullable archive body location for reversible R2 double writing."""
from alembic import op
import sqlalchemy as sa

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("archivesnapshot", sa.Column("body_storage", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("archivesnapshot", "body_storage")
