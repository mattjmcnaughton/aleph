"""MCP OAuth: authorization codes and connection columns on ``api_keys``.

Aleph is the OAuth authorization server for its MCP server (``docs/mcp.md``).
``oauth_codes`` holds one row per approved authorization request (only the
code's SHA-256), redeemed at most once. ``api_keys`` gains the columns a
**connection** needs: the client's metadata URL, the access token's expiry and
the rotating refresh token's digests and expiry. All are nullable and null for
hand-made keys, so existing rows are untouched. Purely additive; the downgrade
is a clean reversal.

Revision ID: 0029_mcp_oauth
Revises: 0028_api_keys
Create Date: 2026-11-27
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0029_mcp_oauth"
down_revision: str | None = "0028_api_keys"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.add_column("api_keys", sa.Column("client_id", sa.Text(), nullable=True))
    op.add_column(
        "api_keys",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("api_keys", sa.Column("refresh_digest", sa.Text(), nullable=True))
    op.add_column(
        "api_keys", sa.Column("previous_refresh_digest", sa.Text(), nullable=True)
    )
    op.add_column(
        "api_keys",
        sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_unique_constraint(
        "uq_api_keys_refresh_digest", "api_keys", ["refresh_digest"]
    )
    op.create_unique_constraint(
        "uq_api_keys_previous_refresh_digest",
        "api_keys",
        ["previous_refresh_digest"],
    )

    op.create_table(
        "oauth_codes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code_digest", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("client_name", sa.Text(), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("code_challenge", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("api_key_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["api_key_id"], ["api_keys.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code_digest"),
    )
    op.create_index(
        "ix_oauth_codes_user_id_created_at", "oauth_codes", ["user_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_oauth_codes_user_id_created_at", table_name="oauth_codes")
    op.drop_table("oauth_codes")
    op.drop_constraint(
        "uq_api_keys_previous_refresh_digest", "api_keys", type_="unique"
    )
    op.drop_constraint("uq_api_keys_refresh_digest", "api_keys", type_="unique")
    for column in (
        "refresh_expires_at",
        "previous_refresh_digest",
        "refresh_digest",
        "expires_at",
        "client_id",
    ):
        op.drop_column("api_keys", column)
