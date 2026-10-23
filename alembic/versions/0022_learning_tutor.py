"""Add account-wide Learning Tutor state and durable receipts.

Revision ID: 0022_learning_tutor
Revises: 0021_lesson_conversations
"""

from alembic import op

revision = "0022_learning_tutor"
down_revision = "0021_lesson_conversations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("uq_paths_user_id_id", "paths", ["user_id", "id"], unique=True)
    op.create_index("uq_beats_user_id_id", "beats", ["user_id", "id"], unique=True)
    op.execute("""
        CREATE TABLE learning_tutor_operations (
            id uuid PRIMARY KEY,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            epoch integer NOT NULL,
            request_id uuid NOT NULL,
            request_hash text NOT NULL,
            kind text NOT NULL,
            revision integer NOT NULL,
            status text NOT NULL,
            started_at timestamptz NOT NULL,
            expires_at timestamptz NOT NULL,
            finished_at timestamptz,
            error_code text,
            input jsonb,
            output jsonb,
            input_tokens integer NOT NULL DEFAULT 0,
            output_tokens integer NOT NULL DEFAULT 0,
            model_admitted boolean NOT NULL DEFAULT false,
            cleared_at timestamptz,
            CONSTRAINT uq_lt_operation_owner UNIQUE (user_id, id),
            CONSTRAINT uq_lt_operation_request UNIQUE (user_id, epoch, request_id),
            CONSTRAINT ck_lt_operation_kind CHECK (kind IN ('turn','refresh')),
            CONSTRAINT ck_lt_operation_status
                CHECK (status IN ('running','succeeded','failed')),
            CONSTRAINT ck_lt_operation_counts CHECK (
                epoch >= 0 AND revision >= 0 AND
                input_tokens >= 0 AND output_tokens >= 0)
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX uq_lt_running ON learning_tutor_operations(user_id) "
        "WHERE status = 'running'"
    )
    op.execute("""
        CREATE TABLE learning_tutor_states (
            user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            focus text,
            revision integer NOT NULL DEFAULT 0,
            focus_version integer NOT NULL DEFAULT 0,
            epoch integer NOT NULL DEFAULT 0,
            last_refresh_operation_id uuid
                REFERENCES learning_tutor_operations(id) ON DELETE SET NULL,
            CONSTRAINT ck_lt_state_versions
                CHECK (revision >= 0 AND focus_version >= 0 AND epoch >= 0),
            CONSTRAINT fk_lt_refresh_owner
                FOREIGN KEY (user_id, last_refresh_operation_id)
                REFERENCES learning_tutor_operations(user_id, id)
        )
    """)
    op.execute("""
        CREATE TABLE learning_recommendations (
            id uuid PRIMARY KEY,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            operation_id uuid
                REFERENCES learning_tutor_operations(id) ON DELETE SET NULL,
            epoch integer NOT NULL,
            focus_version integer NOT NULL,
            kind text NOT NULL,
            topic text,
            level text,
            guidance text,
            description text,
            rationale text,
            evidence jsonb,
            normalized_topic text,
            status text NOT NULL DEFAULT 'active',
            dismissed_at timestamptz,
            accepted_at timestamptz,
            accepted_path_id uuid REFERENCES paths(id) ON DELETE SET NULL,
            accepted_beat_id uuid REFERENCES beats(id) ON DELETE SET NULL,
            accepted_request_hash text,
            CONSTRAINT fk_lt_recommendation_operation_owner
                FOREIGN KEY (user_id, operation_id)
                REFERENCES learning_tutor_operations(user_id, id),
            CONSTRAINT fk_lt_recommendation_path_owner
                FOREIGN KEY (user_id, accepted_path_id)
                REFERENCES paths(user_id, id),
            CONSTRAINT fk_lt_recommendation_beat_owner
                FOREIGN KEY (user_id, accepted_beat_id)
                REFERENCES beats(user_id, id),
            CONSTRAINT ck_lt_recommendation_kind CHECK (kind IN ('path','beat')),
            CONSTRAINT ck_lt_recommendation_status
                CHECK (status IN ('active','dismissed','superseded','accepted')),
            CONSTRAINT ck_lt_recommendation_versions
                CHECK (epoch >= 0 AND focus_version >= 0),
            CONSTRAINT ck_lt_recommendation_target_kind CHECK (
                (accepted_path_id IS NULL OR kind = 'path') AND
                (accepted_beat_id IS NULL OR kind = 'beat')),
            CONSTRAINT ck_lt_recommendation_acceptance CHECK (
                (status = 'accepted' AND accepted_at IS NOT NULL
                    AND accepted_request_hash IS NOT NULL)
                OR (status <> 'accepted' AND accepted_at IS NULL
                    AND accepted_request_hash IS NULL
                    AND accepted_path_id IS NULL AND accepted_beat_id IS NULL)),
            CONSTRAINT ck_lt_recommendation_payload CHECK (status = 'accepted' OR
                (topic IS NOT NULL AND level IS NOT NULL AND description IS NOT NULL AND
                rationale IS NOT NULL AND evidence IS NOT NULL
                    AND normalized_topic IS NOT NULL)),
            CONSTRAINT ck_lt_recommendation_level CHECK (
                level IS NULL OR level IN ('new_to_it','some_experience','work_in_it'))
        )
    """)
    op.create_index(
        "ix_learning_recommendations_user", "learning_recommendations", ["user_id"]
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_lt_active_topic ON "
        "learning_recommendations(user_id, kind, normalized_topic) "
        "WHERE status = 'active'"
    )


def downgrade() -> None:
    op.drop_table("learning_recommendations")
    op.drop_table("learning_tutor_states")
    op.drop_table("learning_tutor_operations")
    op.drop_index("uq_beats_user_id_id", table_name="beats")
    op.drop_index("uq_paths_user_id_id", table_name="paths")
