#!/bin/bash
# Runs once, when the PostgreSQL data volume is empty.
# Creates the app database and a separate test database,
# and applies every migration to both, in file name order.
set -euo pipefail

TEST_DB="${POSTGRES_DB}_test"

createdb --username "$POSTGRES_USER" "$TEST_DB"

for db in "$POSTGRES_DB" "$TEST_DB"; do
    for migration in /migrations/*.sql; do
        echo "Applying $(basename "$migration") to $db"
        psql --username "$POSTGRES_USER" --dbname "$db" \
             --set ON_ERROR_STOP=1 --quiet --file "$migration"
    done
done
