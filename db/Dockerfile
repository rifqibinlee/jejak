# Postgres 16 + pgvector (base) + PostGIS for GeoServer-published layers
FROM pgvector/pgvector:pg16

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-16-postgis-3 \
    && rm -rf /var/lib/apt/lists/*

USER postgres
