"""
GeoServer integration helpers — Jejak remains the entrypoint (session auth + WMS proxy).
"""
import os
import requests
from flask import Response, jsonify

_GEOSERVER_ENABLED = os.getenv("GEOSERVER_ENABLED", "true").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
_GEOSERVER_BASE = os.getenv("GEOSERVER_URL", "http://geoserver:8080/geoserver").rstrip("/")
_GEOSERVER_USER = os.getenv("GEOSERVER_USER", "admin")
_GEOSERVER_PASSWORD = os.getenv("GEOSERVER_PASSWORD", "geoserver")
_GEOSERVER_WORKSPACE = os.getenv("GEOSERVER_WORKSPACE", "vibe")
# Comma-separated: workspace:layer|Title|opacity or workspace:layer
_GEOSERVER_LAYERS_RAW = os.getenv(
    "GEOSERVER_WMS_LAYERS",
    "vibe:geoserver_demo_footprints|Published regions (demo)|0.45",
)


def geoserver_enabled():
    return _GEOSERVER_ENABLED


def parse_layer_catalog():
    """Returns list of {layers, title, opacity} for WMS LAYERS param."""
    out = []
    for part in _GEOSERVER_LAYERS_RAW.split(","):
        part = part.strip()
        if not part:
            continue
        bits = [b.strip() for b in part.split("|")]
        layers_param = bits[0]
        title = bits[1] if len(bits) > 1 else layers_param.replace(":", " · ")
        opacity = 0.65
        if len(bits) > 2:
            try:
                opacity = max(0.05, min(1.0, float(bits[2])))
            except ValueError:
                pass
        if ":" not in layers_param:
            layers_param = f"{_GEOSERVER_WORKSPACE}:{layers_param}"
        out.append({"layers": layers_param, "title": title, "opacity": opacity})
    return out


def catalog_payload():
    """JSON for GET /api/geoserver/config (caller attaches enabled flag logic)."""
    layers = parse_layer_catalog()
    return {
        "enabled": geoserver_enabled() and bool(layers),
        "workspace": _GEOSERVER_WORKSPACE,
        "wmsPath": "/api/geoserver/wms",
        "layers": layers,
    }


def proxy_wms_get(query_string: str):
    """Forward WMS GetMap/GetCapabilities to GeoServer with server-side Basic auth."""
    if not query_string:
        return jsonify({"error": "Missing query string"}), 400
    url = f"{_GEOSERVER_BASE}/wms?{query_string}"
    try:
        r = requests.get(
            url,
            auth=(_GEOSERVER_USER, _GEOSERVER_PASSWORD),
            timeout=120,
        )
    except requests.RequestException as e:
        return jsonify({"error": "GeoServer unreachable", "detail": str(e)}), 502

    ct = r.headers.get("Content-Type") or "application/octet-stream"
    excluded = {"transfer-encoding", "connection", "content-encoding"}
    hdrs = {
        k: v
        for k, v in r.headers.items()
        if k.lower() not in excluded
    }
    mime = ct.split(";")[0].strip()
    return Response(r.content, status=r.status_code, headers=hdrs, mimetype=mime)
