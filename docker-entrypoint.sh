#!/bin/bash
set -e

echo "Starting VIBE Production Initializer..."

# 1. Wait for Postgres to be ready
echo "Waiting for PostgreSQL to wake up..."
while ! pg_isready -h vibe_db -p 5432 -U postgres -d vibe_db; do
  sleep 1
done
echo "PostgreSQL is ready!"

# 2. Build the exact tables needed for the new features (IAM, Chat, Annotations, Pricing, Reviews)
echo "Running Database Schema Setup (Stateful Features Only)..."
python app_database.py

# 3. Ensure the master Admin account exists
echo "Verifying Admin Credentials..."
python recreate_admin_user.py

echo "GeoServer workspace/layer bootstrap (non-fatal if GeoServer not ready)..."
python geoserver_bootstrap.py || true

echo "All migrations successful. Booting Gunicorn..."
exec "$@"
