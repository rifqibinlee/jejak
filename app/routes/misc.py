"""
Miscellaneous routes: CAPEX pricing, Metabase embed, Superset guest token,
and the detailed site upgrade endpoint.
"""
import json
import time
import traceback
import pandas as pd
import requests
import jwt
from flask import Blueprint, request, jsonify, session
from app.extensions import api_login_required, get_cached_dataframe, aws_session
from app.config import (
    PRICING_FILE, DEFAULT_PRICING, S3_BUCKET,
    METABASE_SITE_URL, METABASE_SECRET_KEY,
)

bp = Blueprint('misc', __name__)


# ── Pricing helpers ────────────────────────────────────────────────────────────
def _get_pricing_raw():
    try:
        resp = aws_session.client('s3').get_object(Bucket=S3_BUCKET, Key='3W-data/capex_pricing/capex_pricing.json')
        return json.loads(resp['Body'].read().decode('utf-8'))
    except Exception as e:
        print(f"Could not read pricing from S3, falling back to local/default. Error: {e}")
        try:
            with open(PRICING_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            return DEFAULT_PRICING


def _get_pricing_flat():
    raw = _get_pricing_raw()
    normalized = {}
    for category, items in raw.items():
        normalized[category] = {}
        for name, vals in items.items():
            if isinstance(vals, dict):
                normalized[category][name] = {"price": float(vals.get("price", 0)), "min": float(vals.get("min", 0)), "max": float(vals.get("max", 0))}
            else:
                p = float(vals)
                normalized[category][name] = {"price": p, "min": p, "max": p}
    return normalized


def _get_pricing_for_calc():
    flat = _get_pricing_flat()
    return {cat: {name: vals["price"] for name, vals in items.items()} for cat, items in flat.items()}


def _get_pricing_ranges():
    full = _get_pricing_raw()
    ranges = {}
    for category, items in full.items():
        ranges[category] = {}
        for name, vals in items.items():
            if isinstance(vals, dict) and 'min' in vals:
                p_min = float(vals['min']); p_max = float(vals['max'])
            else:
                p = float(vals) if not isinstance(vals, dict) else float(vals.get('price', 0))
                p_min = p_max = p
            ranges[category][name] = {"min": p_min, "max": p_max, "display": f"RM {p_min:,.2f} \u2013 RM {p_max:,.2f}"}
    return ranges


def _recalculate_live_capex(row, pricing):
    case_str = str(row.get('suggested_upgrade_case', ''))
    if not case_str or case_str.lower() in ['nan', 'none', '']:
        return 0.0, 0.0, 0.0

    added_layers = 0
    for c in ['f1', 'f2']:
        for b in ['l9', 'l18', 'l21', 'l26']:
            curr = str(row.get(f'current_{c}_{b}', '0')).strip().lower()
            sugg = str(row.get(f'suggested_{c}_{b}', '0')).strip().lower()
            if curr in ['0', '', 'none', 'nan', '<na>'] and sugg not in ['0', '', 'none', 'nan', '<na>']:
                added_layers += 1

    eq_prices = pricing.get("EQ", {})
    es_prices = pricing.get("ES", {})
    layer_mult = {1:1.0,2:1.7,3:2.7,4:3.5,5:4.5,6:5.5,7:6.5,8:7.2}.get(added_layers, 1.0) if added_layers > 0 else 0
    add_layer_eq_cost = eq_prices.get("Add Layer", 0) * layer_mult
    case_lower = case_str.lower()

    eq_costs = []
    if "case 11" in case_lower:         eq_costs.append(eq_prices.get("NNS", 0))
    elif "case 10" in case_lower:       eq_costs.append(eq_prices.get("Split Omni to Sector", 0))
    elif "case 9" in case_lower:        eq_costs.append(eq_prices.get("MM", 0))
    elif "case 8" in case_lower:        eq_costs.append(eq_prices.get("Bi-Sect Radio", 0) + eq_prices.get("Bi-Sect Antenna + Accessory", 0))
    elif "case 7" in case_lower:        eq_costs.append(eq_prices.get("BW Upg", 0))
    elif "case 6" in case_lower:        eq_costs.append(add_layer_eq_cost + eq_prices.get("Add Sector Outdoor", 0))
    elif "case 5" in case_lower:        eq_costs.append(add_layer_eq_cost + eq_prices.get("Add Sector IBC", 0))
    elif "case 4" in case_lower:        eq_costs.append(eq_prices.get("Accelerate NIC", 0))
    elif "case 3" in case_lower:        eq_costs.append(eq_prices.get("Swap all Sector Radio Ericsson to ZTE", 0))
    elif "case 2" in case_lower:        eq_costs.append(add_layer_eq_cost)
    elif "case 1" in case_lower:        eq_costs.append(add_layer_eq_cost)

    total_eq = sum(eq_costs) if eq_costs else 0.0
    es_options = [(k, v) for k, v in es_prices.items()]
    best_es = max(es_options, key=lambda x: x[1], default=('', 0.0))
    total_es = best_es[1]
    return total_eq + total_es, total_eq, total_es


# ── Endpoints ──────────────────────────────────────────────────────────────────
@bp.route('/api/pricing', methods=['GET', 'POST'])
@api_login_required
def pricing_endpoint():
    role = session.get('role', 'Staff')
    if request.method == 'POST':
        if role not in ['Admin', 'Planner']:
            return jsonify({'error': 'Unauthorized'}), 403
        new_pricing = request.json
        with open(PRICING_FILE, 'w') as f:
            json.dump(new_pricing, f, indent=4)
        try:
            aws_session.client('s3').put_object(Bucket=S3_BUCKET, Key='capex_pricing/capex_pricing.json', Body=json.dumps(new_pricing, indent=4), ContentType='application/json')
            return jsonify({"success": True, "message": "Pricing updated and pushed to AWS S3!"})
        except Exception as e:
            return jsonify({"success": False, "message": f"Saved locally, but failed to sync: {str(e)}"}), 500

    if role in ['Admin', 'Planner']:
        return jsonify(_get_pricing_flat())
    return jsonify(_get_pricing_ranges())


@bp.route('/api/map/site_upgrade_details')
def api_site_upgrade_details():
    site_id = request.args.get('site_id')
    week    = request.args.get('week')
    year    = request.args.get('year', '2026')
    if not site_id:
        return jsonify({'error': 'No Site ID'}), 400
    if not week or week.lower() == 'all':
        week = 40
    try:
        sql = f"""
            SELECT ca.zoom_sector_id, ca.eric_prb_util_rate, ca.area_target as sc_area_target,
                cu.suggested_upgrade_case, cu.estimated_total_capex_rm,
                cu.eq_capex_rm, cu.es_capex_rm, cu.projected_prb_pct,
                cu.current_f1_l9, cu.suggested_f1_l9, cu.current_f1_l18, cu.suggested_f1_l18,
                cu.current_f1_l21, cu.suggested_f1_l21, cu.current_f1_l26, cu.suggested_f1_l26,
                cu.current_f2_l9, cu.suggested_f2_l9, cu.current_f2_l18, cu.suggested_f2_l18,
                cu.current_f2_l21, cu.suggested_f2_l21, cu.current_f2_l26, cu.suggested_f2_l26
            FROM congestion_analysis ca
            LEFT JOIN capex_upgrades cu
                ON TRIM(UPPER(ca.zoom_sector_id)) = TRIM(UPPER(cu.zoom_sector_id))
                AND CAST(ca.year AS VARCHAR) = CAST(cu.data_year AS VARCHAR)
                AND CAST(ca.week AS INTEGER) = CAST(cu.data_week AS INTEGER)
            WHERE split_part(ca.zoom_sector_id, '_', 1) = '{site_id}'
            AND CAST(ca.year AS VARCHAR) = '{year}'
            AND CAST(ca.week AS INTEGER) = {week}
        """
        df = get_cached_dataframe(sql)
        if df.empty:
            return jsonify({"error": "No sector data found for this week."})

        area_tgt     = df['sc_area_target'].iloc[0] if pd.notna(df['sc_area_target'].iloc[0]) else 'Unknown'
        live_pricing = _get_pricing_for_calc()
        live_pricing_full = _get_pricing_flat()
        sectors_dict = {}

        for _, row in df.iterrows():
            sec_id   = row['zoom_sector_id']
            prb      = float(row['eric_prb_util_rate']) if pd.notna(row['eric_prb_util_rate']) else 0.0
            is_urban = 'urban' in str(row.get('sc_area_target', '')).lower() or 'kmc' in str(row.get('sc_area_target', '')).lower()
            prb_threshold     = 80.0 if is_urban else 92.0
            suggested_case_str = str(row['suggested_upgrade_case']).strip()
            has_upgrade        = pd.notna(row['suggested_upgrade_case']) and suggested_case_str.lower() not in ['nan', 'none', '']

            matrix = {"F1": {b: {"curr": "-", "sugg": "-"} for b in ['L9','L18','L21','L26']},
                      "F2": {b: {"curr": "-", "sugg": "-"} for b in ['L9','L18','L21','L26']},
                      "F3": {b: {"curr": "-", "sugg": "-"} for b in ['L9','L18','L21','L26']}}
            capex_rm = eq_cost = es_cost = 0.0

            if has_upgrade:
                case_label = suggested_case_str
                live_total, live_eq, live_es = _recalculate_live_capex(row, live_pricing)
                capex_rm = live_total; eq_cost = live_eq; es_cost = live_es
                proj_prb = float(row['projected_prb_pct']) if pd.notna(row['projected_prb_pct']) else prb
                for c in ['F1', 'F2', 'F3']:
                    for b in ['L9', 'L18', 'L21', 'L26']:
                        c_val = str(row.get(f"current_{c.lower()}_{b.lower()}", "0")).strip()
                        s_val = str(row.get(f"suggested_{c.lower()}_{b.lower()}", "0")).strip()
                        if c_val.lower() not in ["0","0.0","none","nan","","<na>"]: matrix[c][b]["curr"] = c_val
                        if s_val.lower() not in ["0","0.0","none","nan","","<na>"]: matrix[c][b]["sugg"] = s_val
            else:
                proj_prb   = prb
                case_label = "MISSING FROM REFERENCE DATA" if prb >= prb_threshold else "No Upgrade Needed"

            capex_data = None
            if has_upgrade and capex_rm > 0:
                try:
                    min_pricing = {cat: {name: vals["min"] for name, vals in items.items()} for cat, items in live_pricing_full.items()}
                    max_pricing = {cat: {name: vals["max"] for name, vals in items.items()} for cat, items in live_pricing_full.items()}
                    _, min_eq, min_es = _recalculate_live_capex(row, min_pricing)
                    _, max_eq, max_es = _recalculate_live_capex(row, max_pricing)
                    eq_range = {"min": min_eq, "max": max_eq}
                    es_range = {"min": min_es, "max": max_es}
                except Exception:
                    eq_range = es_range = None
                capex_data = {
                    "total_capex":   capex_rm,
                    "eq_breakdown":  [[case_label[:45] + "...", eq_cost, eq_range]],
                    "es_chosen":     {"name": "Engineering Services (Highest)", "cost": es_cost, "range": es_range},
                }

            sectors_dict[sec_id] = {
                "is_congested":  has_upgrade or (prb >= prb_threshold),
                "capacity_pct":  round(proj_prb, 2),
                "case_label":    case_label,
                "matrix":        matrix,
                "capex":         capex_data,
            }

        return jsonify({"site_id": site_id, "area_target": area_tgt, "sectors": sectors_dict})

    except Exception as e:
        print(f"DEBUG: {traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500


@bp.route('/api/dashboard/embed')
@api_login_required
def get_metabase_embed():
    dashboard_id = request.args.get('dashboard_id', type=int)
    if not dashboard_id:
        return jsonify({"error": "Dashboard ID required"}), 400
    try:
        payload = {"resource": {"dashboard": dashboard_id}, "params": {}, "exp": round(time.time()) + 600}
        token   = jwt.encode(payload, METABASE_SECRET_KEY, algorithm="HS256")
        ip      = request.host.split(':')[0]
        return jsonify({"iframeUrl": f"http://{ip}:3000/embed/dashboard/{token}#bordered=false&titled=false&theme=night"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route('/api/superset/guest-token')
@api_login_required
def get_superset_guest_token():
    dashboard_id = request.args.get('dashboard_id')
    if not dashboard_id:
        return jsonify({"error": "Dashboard ID required"}), 400
    try:
        login_res = requests.post('http://superset:8088/api/v1/security/login', json={"username": "admin", "password": "admin", "provider": "db"}, timeout=5)
        login_res.raise_for_status()
        access_token    = login_res.json().get('access_token')
        guest_token_res = requests.post(
            'http://superset:8088/api/v1/security/guest_token/',
            headers={"Authorization": f"Bearer {access_token}"},
            json={"user": {"username": session.get('username'), "first_name": "NetAlytics", "last_name": "Admin"}, "resources": [{"type": "dashboard", "id": dashboard_id}], "rls": []},
            timeout=5,
        )
        guest_token_res.raise_for_status()
        return jsonify({"token": guest_token_res.json().get('token')})
    except Exception as e:
        return jsonify({"error": "Failed to communicate with analytics engine"}), 500
