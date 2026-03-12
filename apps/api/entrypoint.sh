#!/bin/bash
set -e

# Read database password for pg_isready check
if [ -f /run/secrets/db_password ]; then
    DB_PASSWORD=$(cat /run/secrets/db_password | tr -d '[:space:]')
    echo "Using database password from Docker secret"
else
    DB_PASSWORD="moose"
    echo "WARNING: No db_password secret found, using default password"
fi

# Wait for database to be ready
echo "Waiting for database..."
until PGPASSWORD="${DB_PASSWORD}" pg_isready -h db -U moose -d moose_empire; do
  sleep 1
done

# Run migrations (uses Pydantic settings which reads Docker secrets)
echo "Running database migrations..."
alembic upgrade head

# Start application (uses Pydantic settings which reads Docker secrets)
echo "Starting application..."
exec "$@"
