"""
GeoServer integration helpers — Jejak remains the entrypoint (session auth + WMS proxy).
"""
import os
import time
import xml.etree.ElementTree as ET

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
# env | capabilities | merge — merge = env list first, then GeoServer-listed layers not already present
_GEOSERVER_LAYER_CATALOG_MODE = os.getenv("GEOSERVER_LAYER_CATALOG_MODE", "env").strip().lower()
try:
    _GEOSERVER_CAPABILITIES_CACHE_SECONDS = max(
        0.0, float(os.getenv("GEOSERVER_CAPABILITIES_CACHE_SECONDS", "90"))
    )
except ValueError:
    _GEOSERVER_CAPABILITIES_CACHE_SECONDS = 90.0
try:
    _GEOSERVER_DEFAULT_LAYER_OPACITY = max(
        0.05, min(1.0, float(os.getenv("GEOSERVER_DEFAULT_LAYER_OPACITY", "0.65")))
    )
except ValueError:
    _GEOSERVER_DEFAULT_LAYER_OPACITY = 0.65

_capabilities_cache: dict = {"expires": 0.0, "layers": []}


def geoserver_enabled():
    return _GEOSERVER_ENABLED


def _local_tag(tag: str | None) -> str:
    if not tag:
        return ""
    if tag.startswith("{"):
        return tag.split("}", 1)[-1]
    return tag


def _extract_named_layers_from_capability(layer_elem: ET.Element, workspace_prefix: str):
    """Collect WMS Layer entries that declare a Name in this workspace (document order)."""
    found = []
    name_text = None
    title_text = None
    nested = []
    for child in layer_elem:
        lt = _local_tag(child.tag)
        if lt == "Name" and (child.text or "").strip():
            name_text = child.text.strip()
        elif lt == "Title" and (child.text or "").strip():
            title_text = child.text.strip()
        elif lt == "Layer":
            nested.append(child)
    for child in nested:
        found.extend(_extract_named_layers_from_capability(child, workspace_prefix))
    if name_text and name_text.startswith(workspace_prefix):
        found.append(
            {
                "layers": name_text,
                "title": title_text or name_text.replace(":", " · "),
                "opacity": _GEOSERVER_DEFAULT_LAYER_OPACITY,
            }
        )
    return found


def discover_layers_from_capabilities():
    """
    Layers GeoServer advertises in WMS GetCapabilities for this workspace.
    Styling/titles follow what you configured in GeoServer; Jejak only lists them for toggling.
    """
    global _capabilities_cache
    now = time.monotonic()
    if (
        _GEOSERVER_CAPABILITIES_CACHE_SECONDS > 0
        and now < _capabilities_cache["expires"]
        and _capabilities_cache["layers"]
    ):
        return list(_capabilities_cache["layers"])

    prefix = f"{_GEOSERVER_WORKSPACE}:"
    url = (
        f"{_GEOSERVER_BASE}/wms?service=WMS&request=GetCapabilities&version=1.3.0"
    )
    discovered = []
    try:
        r = requests.get(
            url,
            auth=(_GEOSERVER_USER, _GEOSERVER_PASSWORD),
            timeout=45,
        )
        if r.status_code != 200:
            ttl = min(_GEOSERVER_CAPABILITIES_CACHE_SECONDS, 5.0)
            _capabilities_cache = {"expires": now + ttl, "layers": []}
            return []
        root = ET.fromstring(r.content)
        capability = None
        for elem in root.iter():
            if _local_tag(elem.tag) == "Capability":
                capability = elem
                break
        if capability is not None:
            for child in capability:
                if _local_tag(child.tag) == "Layer":
                    discovered.extend(
                        _extract_named_layers_from_capability(child, prefix)
                    )
    except (requests.RequestException, ET.ParseError):
        ttl = min(_GEOSERVER_CAPABILITIES_CACHE_SECONDS, 5.0)
        _capabilities_cache = {"expires": now + ttl, "layers": []}
        return []

    # Dedupe by layers id (Capabilities can repeat paths)
    seen = set()
    unique = []
    for row in discovered:
        lid = row["layers"]
        if lid in seen:
            continue
        seen.add(lid)
        unique.append(row)

    ttl = _GEOSERVER_CAPABILITIES_CACHE_SECONDS
    _capabilities_cache = {"expires": now + ttl if ttl > 0 else 0.0, "layers": unique}
    return list(unique)


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
        opacity = _GEOSERVER_DEFAULT_LAYER_OPACITY
        if len(bits) > 2:
            try:
                opacity = max(0.05, min(1.0, float(bits[2])))
            except ValueError:
                pass
        if ":" not in layers_param:
            layers_param = f"{_GEOSERVER_WORKSPACE}:{layers_param}"
        out.append({"layers": layers_param, "title": title, "opacity": opacity})
    return out


def _merged_catalog():
    mode = _GEOSERVER_LAYER_CATALOG_MODE
    env_layers = parse_layer_catalog()

    if mode == "env":
        return env_layers
    if mode == "capabilities":
        return discover_layers_from_capabilities()

    # merge
    discovered = discover_layers_from_capabilities()
    by_id = {}
    order = []
    for row in env_layers:
        lid = row["layers"]
        if lid not in by_id:
            order.append(lid)
        by_id[lid] = dict(row)
    for row in discovered:
        lid = row["layers"]
        if lid not in by_id:
            order.append(lid)
            by_id[lid] = dict(row)
    return [by_id[lid] for lid in order]


def catalog_payload():
    """JSON for GET /api/geoserver/config (caller attaches enabled flag logic)."""
    layers = _merged_catalog()
    # catalogMode helps operators confirm why layers appear (env vs GeoServer discovery)
    catalog_mode = _GEOSERVER_LAYER_CATALOG_MODE
    if catalog_mode not in ("env", "capabilities", "merge"):
        catalog_mode = "env"
    return {
        "enabled": geoserver_enabled() and bool(layers),
        "workspace": _GEOSERVER_WORKSPACE,
        "wmsPath": "/api/geoserver/wms",
        "layers": layers,
        "catalogMode": catalog_mode,
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
