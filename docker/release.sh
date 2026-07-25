#!/usr/bin/env sh
# The Fly release command (fly.toml `[deploy] release_command`) and the Compose
# smoke's one-shot `migrate` service: bring the schema to head before any app
# machine serves traffic. Aleph's release step is migrations and nothing else —
# there is no corpus to import and no content to seed.
#
# Retried because Neon Free suspends idle endpoints: the first connection after a
# quiet period can fail while the compute wakes, and a deploy must not die on a
# cold database. Adapted from habagou's entrypoint bootstrap, minus the import +
# seed steps aleph has no equivalent of.
set -eu

attempt=1
max_attempts="${ALEPH_MIGRATE_ATTEMPTS:-10}"
delay_seconds="${ALEPH_MIGRATE_RETRY_SECONDS:-2}"

while ! alembic upgrade head; do
  if [ "$attempt" -ge "$max_attempts" ]; then
    echo "database migration failed after $attempt attempts" >&2
    exit 1
  fi
  echo "database migration failed; retrying in ${delay_seconds}s ($attempt/$max_attempts)" >&2
  attempt=$((attempt + 1))
  sleep "$delay_seconds"
done
