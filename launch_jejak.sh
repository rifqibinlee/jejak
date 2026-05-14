#!/bin/bash
set -e

echo "🛑 Stopping existing VIBE containers..."
docker compose down --remove-orphans

echo "🗑️  Running Docker System Prune (Safe Mode - Preserving Volumes)..."
# Removed --volumes so your database survives the rebuild!
docker system prune -a -f

echo "🏗️  Rebuilding Images..."
docker compose build --no-cache

echo "🌐 Starting Databases and Cache..."
docker compose up -d vibe_db geoserver
sleep 15

echo "🛠️ Initializing NetAlytics Database..."
docker compose run --rm vibe python app_database.py

echo "🚀 Launching All Services..."
docker compose up -d

echo "🚀 Launching All Services..."
docker compose up -d

echo "✅ ALL SYSTEMS ONLINE (Data Preserved!)"

