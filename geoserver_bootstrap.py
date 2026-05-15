#!/usr/bin/env python3
"""
Idempotent GeoServer REST bootstrap: workspace, PostGIS datastore, publish demo layer.
Run from vibe container after app_database.py (PostGIS + table must exist).
Controlled by GEOSERVER_BOOTSTRAP (default true).
"""
import json
import os
import sys
import time

import requests

BASE = os.getenv("GEOSERVER_URL", "http://vibe_geoserver:8080/geoserver").rstrip("/")
REST = f"{BASE}/rest"
USER = os.getenv("GEOSERVER_USER", "admin")
PASSWORD = os.getenv("GEOSERVER_PASSWORD", "geoserver")
WORKSPACE = os.getenv("GEOSERVER_WORKSPACE", "vibe")
DATASTORE = os.getenv("GEOSERVER_PG_DATASTORE", "vibe_pg")
TABLE = os.getenv("GEOSERVER_BOOTSTRAP_LAYER_TABLE", "geoserver_demo_footprints")

DB_HOST = os.getenv("DB_HOST", "vibe_db")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "vibe_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "1234")

AUTH = (USER, PASSWORD)
HEADERS_JSON = {"Content-Type": "application/json"}
BOOT = os.getenv("GEOSERVER_BOOTSTRAP", "true").strip().lower() in ("1", "true", "yes", "on")


def wait_geoserver(max_wait=180):
    for _ in range(max_wait):
        try:
            r = requests.get(f"{BASE}/web/", auth=AUTH, timeout=5)
            if r.status_code < 500:
                return True
        except requests.RequestException:
            pass
        time.sleep(2)
    return False


def ensure_workspace():
    url = f"{REST}/workspaces/{WORKSPACE}.json"
    r = requests.get(url, auth=AUTH, timeout=30)
    if r.status_code == 200:
        print(f"  [OK] Workspace '{WORKSPACE}' exists.")
        return True
    r = requests.post(
        f"{REST}/workspaces",
        auth=AUTH,
        headers=HEADERS_JSON,
        data=json.dumps({"workspace": {"name": WORKSPACE}}),
        timeout=60,
    )
    if r.status_code in (200, 201):
        print(f"  [OK] Workspace '{WORKSPACE}' created.")
        return True
    print(f"  [WARN] Workspace create failed: {r.status_code} {r.text[:500]}")
    return False


def ensure_datastore():
    url = f"{REST}/workspaces/{WORKSPACE}/datastores/{DATASTORE}.json"
    r = requests.get(url, auth=AUTH, timeout=30)
    if r.status_code == 200:
        print(f"  [OK] Datastore '{DATASTORE}' exists.")
        return True
    payload = {
        "dataStore": {
            "name": DATASTORE,
            "type": "PostGIS",
            "enabled": True,
            "connectionParameters": {
                "entry": [
                    {"@key": "host", "$": DB_HOST},
                    {"@key": "port", "$": DB_PORT},
                    {"@key": "database", "$": DB_NAME},
                    {"@key": "schema", "$": "public"},
                    {"@key": "user", "$": DB_USER},
                    {"@key": "passwd", "$": DB_PASSWORD},
                    {"@key": "dbtype", "$": "postgis"},
                    {"@key": "Expose primary keys", "$": "true"},
                ]
            },
        }
    }
    r = requests.post(
        f"{REST}/workspaces/{WORKSPACE}/datastores",
        auth=AUTH,
        headers=HEADERS_JSON,
        data=json.dumps(payload),
        timeout=120,
    )
    if r.status_code in (200, 201):
        print(f"  [OK] Datastore '{DATASTORE}' created.")
        return True
    print(f"  [WARN] Datastore create failed: {r.status_code} {r.text[:800]}")
    return False


def ensure_feature_type():
    ft_url = f"{REST}/workspaces/{WORKSPACE}/datastores/{DATASTORE}/featuretypes/{TABLE}.json"
    r = requests.get(ft_url, auth=AUTH, timeout=30)
    if r.status_code == 200:
        print(f"  [OK] Feature type '{TABLE}' exists.")
        return True
    payload = {
        "featureType": {
            "name": TABLE,
            "nativeName": TABLE,
            "title": TABLE.replace("_", " ").title(),
            "enabled": True,
        }
    }
    r = requests.post(
        f"{REST}/workspaces/{WORKSPACE}/datastores/{DATASTORE}/featuretypes",
        auth=AUTH,
        headers=HEADERS_JSON,
        data=json.dumps(payload),
        timeout=120,
    )
    if r.status_code in (200, 201):
        print(f"  [OK] Feature type '{TABLE}' published.")
        return True
    print(f"  [WARN] FeatureType create failed: {r.status_code} {r.text[:800]}")
    return False


def main():
    if not BOOT:
        print("[GeoServer bootstrap] Skipped (GEOSERVER_BOOTSTRAP disabled).")
        return 0
    print("[GeoServer bootstrap] Waiting for GeoServer...")
    if not wait_geoserver():
        print("[GeoServer bootstrap] GeoServer did not become ready in time — skipping.")
        return 0
    print("[GeoServer bootstrap] Applying REST configuration...")
    ensure_workspace()
    if not ensure_datastore():
        return 0
    ensure_feature_type()
    print("[GeoServer bootstrap] Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
