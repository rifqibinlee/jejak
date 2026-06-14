import traceback
import pandas as pd
import awswrangler as wr
from datetime import datetime
from flask import Blueprint, request, jsonify
from app.extensions import api_login_required, get_cached_dataframe, apply_pandas_filters, aws_session
from app.config import ATHENA_DATABASE, S3_STAGING_DIR

bp = Blueprint('congestion', __name__)


@bp.route('/api/years')
def api_years():
    try:
        df = get_cached_dataframe("SELECT DISTINCT year FROM sector_calculations ORDER BY year DESC")
        return jsonify(df['year'].tolist())
    except Exception as e:
        print(f"Athena Error: {e}")
        return jsonify([datetime.now().year])


@bp.route('/api/weeks')
def api_weeks():
    try:
        sql = "SELECT DISTINCT CAST(year AS INTEGER) as yr, CAST(week AS INTEGER) as wk FROM sector_calculations ORDER BY yr DESC, wk DESC"
        df  = get_cached_dataframe(sql)
        return jsonify([{"year": int(row['yr']), "week": int(row['wk'])} for _, row in df.iterrows()])
    except Exception as e:
        print(f"Error fetching weeks: {e}")
        return jsonify([])


@bp.route('/api/filters/regions')
def api_filters_regions():
    try:
        df = get_cached_dataframe("SELECT DISTINCT UPPER(region) as reg FROM sector_calculations WHERE region IS NOT NULL ORDER BY UPPER(region)")
        return jsonify(df['reg'].tolist())
    except Exception:
        return jsonify([])


@bp.route('/api/dashboard/stats')
def api_dashboard_stats():
    try:
        year     = request.args.get('year',     str(datetime.now().year))
        week     = request.args.get('week',     'All')
        region   = request.args.get('region',   'All')
        operator = request.args.get('operator', 'All')
        cluster  = request.args.get('cluster',  'All')

        where_sc = f"CAST(year AS VARCHAR) = '{year}'"
        where_ca = f"CAST(year AS VARCHAR) = '{year}' AND congested = TRUE"

        for col, val in [('week', week), ('region', region), ('operator', operator), ('cluster', cluster)]:
            if val != 'All':
                if col == 'region':
                    where_sc += f" AND UPPER(region) = '{val.upper()}'"
                    where_ca += f" AND UPPER(region) = '{val.upper()}'"
                elif col == 'week':
                    where_sc += f" AND CAST(week AS VARCHAR) = '{val}'"
                    where_ca += f" AND CAST(week AS VARCHAR) = '{val}'"
                else:
                    where_sc += f" AND {col} = '{val}'"
                    where_ca += f" AND {col} = '{val}'"

        df_sc = get_cached_dataframe(f"SELECT COUNT(DISTINCT split_part(zoom_sector_id, '_', 1)) as total_sectors, AVG(eric_data_volume_ul_dl) as avg_vol FROM sector_calculations WHERE {where_sc}")
        df_ca = get_cached_dataframe(f"SELECT COUNT(DISTINCT zoom_sector_id) as congested_count FROM congestion_analysis WHERE {where_ca}")

        return jsonify({
            'total_sectors':   int(df_sc['total_sectors'].iloc[0])   if not df_sc.empty and pd.notna(df_sc['total_sectors'].iloc[0])   else 0,
            'congested_count': int(df_ca['congested_count'].iloc[0]) if not df_ca.empty and pd.notna(df_ca['congested_count'].iloc[0]) else 0,
            'avg_volume':      float(df_sc['avg_vol'].iloc[0])       if not df_sc.empty and pd.notna(df_sc['avg_vol'].iloc[0])         else 0.0,
        })
    except Exception as e:
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@bp.route('/api/sector_data')
def api_sector_data():
    try:
        year   = request.args.get('year', str(datetime.now().year))
        start  = int(request.args.get('start',  0))
        length = int(request.args.get('length', 25))

        required_columns    = ['zoom_sector_id', 'week', 'region', 'cluster', 'ibc_macro', 'f1f2f3', 'eric_prb_util_rate', 'eric_dl_user_ip_thpt', 'eric_data_volume_ul_dl', 'dataset_type', 'operator', 'area_target']
        my_partition_filter = lambda x: x["year"] == year

        df = wr.s3.read_parquet_table(
            database=ATHENA_DATABASE, table="sector_calculations",
            columns=required_columns, partition_filter=my_partition_filter,
            boto3_session=aws_session, use_threads=True,
        )
        df_filtered = apply_pandas_filters(df, request.args).sort_values(by=['zoom_sector_id', 'week'], ascending=[True, False])
        total_records = len(df_filtered)
        df_page       = df_filtered.iloc[start: start + length]

        return jsonify({
            'draw':             int(request.args.get('draw', 1)),
            'recordsTotal':     total_records,
            'recordsFiltered':  total_records,
            'data':             df_page.fillna('').to_dict('records'),
        })
    except Exception as e:
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@bp.route('/api/forecast_data')
def api_forecast_data():
    try:
        year   = request.args.get('year', str(datetime.now().year))
        start  = int(request.args.get('start',  0))
        length = int(request.args.get('length', 25))

        sql = f"""
            SELECT zoom_sector_id, month, year, predicted_eric_prb_util_rate, predicted_eric_dl_user_ip_thpt, congested
            FROM forecast_results WHERE CAST(year AS VARCHAR) = '{year}'
            ORDER BY zoom_sector_id, month
        """
        df = get_cached_dataframe(sql)
        df_filtered = apply_pandas_filters(df, request.args)
        total = len(df_filtered)
        return jsonify({'draw': int(request.args.get('draw', 1)), 'recordsTotal': total, 'recordsFiltered': total, 'data': df_filtered.iloc[start:start+length].fillna('').to_dict('records')})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/congestion_data')
def api_congestion_data():
    try:
        year = request.args.get('year', str(datetime.now().year))
        sql  = f"""
            SELECT zoom_sector_id, region, cluster, week, congested,
                   eric_prb_util_rate, eric_dl_user_ip_thpt, eric_max_rrc_user,
                   max_active_user, area_target, latitude, longitude
            FROM congestion_analysis WHERE CAST(year AS VARCHAR) = '{year}'
        """
        df = get_cached_dataframe(sql)
        df_filtered = apply_pandas_filters(df, request.args)
        return jsonify(df_filtered.fillna('').to_dict('records'))
    except Exception as e:
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@bp.route('/api/forecast_by_site')
def api_forecast_by_site():
    try:
        year    = request.args.get('year', str(datetime.now().year))
        site_id = request.args.get('site_id', '').strip().upper()
        sql     = f"""
            SELECT zoom_sector_id, month, predicted_eric_prb_util_rate as pred_prb,
                   predicted_eric_dl_user_ip_thpt as pred_thpt, congested
            FROM forecast_results
            WHERE UPPER(zoom_sector_id) LIKE '{site_id}%' AND CAST(year AS VARCHAR) = '{year}'
            ORDER BY zoom_sector_id, month
        """
        df = get_cached_dataframe(sql)
        return jsonify(df.fillna('').to_dict('records'))
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/sites')
def api_sites():
    try:
        year = request.args.get('year', str(datetime.now().year))
        sql  = f"""
            SELECT DISTINCT split_part(zoom_sector_id, '_', 1) as site_id,
                   region, cluster, latitude, longitude, area_target
            FROM congestion_analysis
            WHERE CAST(year AS VARCHAR) = '{year}' AND latitude IS NOT NULL AND longitude IS NOT NULL
        """
        df = get_cached_dataframe(sql)
        df_filtered = apply_pandas_filters(df, request.args)
        return jsonify(df_filtered.fillna('').to_dict('records'))
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/site_ids')
def api_site_ids():
    try:
        year = request.args.get('year', str(datetime.now().year))
        sql  = f"SELECT DISTINCT split_part(zoom_sector_id, '_', 1) as site_id FROM congestion_analysis WHERE CAST(year AS VARCHAR) = '{year}' ORDER BY site_id"
        df   = get_cached_dataframe(sql)
        return jsonify(df['site_id'].tolist())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/map/holes')
def api_map_holes():
    try:
        sql = "SELECT cluster_id, serving_cell, data_source, centroid_lat, centroid_lng, point_count, avg_signal FROM coverage_holes_clustered WHERE cluster_id != -1 ORDER BY point_count DESC LIMIT 200"
        df  = get_cached_dataframe(sql)
        return jsonify(df.fillna('').to_dict('records'))
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/map/top_congested')
def api_map_top_congested():
    try:
        year = request.args.get('year', str(datetime.now().year))
        week = request.args.get('week')
        sql  = f"""
            SELECT zoom_sector_id, region, cluster, eric_prb_util_rate, eric_dl_user_ip_thpt,
                   congested, latitude, longitude
            FROM congestion_analysis
            WHERE CAST(year AS VARCHAR) = '{year}' AND congested = TRUE
            AND latitude IS NOT NULL AND longitude IS NOT NULL
        """
        if week:
            sql += f" AND CAST(week AS VARCHAR) = '{week}'"
        sql += " ORDER BY eric_prb_util_rate DESC LIMIT 50"
        df = get_cached_dataframe(sql)
        return jsonify(df.fillna('').to_dict('records'))
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/map/worst_clusters')
def api_map_worst_clusters():
    try:
        year = request.args.get('year', str(datetime.now().year))
        sql  = f"""
            SELECT cluster, region,
                   COUNT(DISTINCT zoom_sector_id) as congested_sectors,
                   AVG(eric_prb_util_rate) as avg_prb
            FROM congestion_analysis
            WHERE CAST(year AS VARCHAR) = '{year}' AND congested = TRUE
            GROUP BY cluster, region ORDER BY congested_sectors DESC LIMIT 10
        """
        df = get_cached_dataframe(sql)
        return jsonify(df.fillna('').to_dict('records'))
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/map/upgrade-cases')
@api_login_required
def api_map_upgrade_cases():
    week = request.args.get('week', type=int)
    year = request.args.get('year', str(datetime.now().year))
    if not week:
        return jsonify([]), 400
    try:
        sql = f"""
            SELECT DISTINCT split_part(cu.zoom_sector_id, '_', 1) as site_id,
                cu.zoom_sector_id, cu.suggested_upgrade_case as upgrade_case,
                cu.estimated_total_capex_rm as total_capex, cu.projected_prb_pct as prb,
                ca.eric_dl_user_ip_thpt as dl_thpt,
                GREATEST(COALESCE(ca.eric_max_rrc_user,0), COALESCE(ca.max_active_user,0)) as user_count,
                CAST(cu.data_week AS INTEGER) as week
            FROM capex_upgrades cu
            LEFT JOIN congestion_analysis ca
                ON cu.zoom_sector_id = ca.zoom_sector_id AND cu.data_week = ca.week
                AND CAST(ca.year AS VARCHAR) = '{year}'
            WHERE cu.suggested_upgrade_case IS NOT NULL
              AND cu.suggested_upgrade_case NOT IN ('', 'None', 'No Upgrade Needed')
              AND CAST(cu.data_week AS INTEGER) = {week}
            ORDER BY cu.estimated_total_capex_rm DESC
        """
        df = get_cached_dataframe(sql)
        if df.empty:
            return jsonify([])
        result = []
        for site_id, group in df.groupby('site_id'):
            details = [{'sector_id': row['zoom_sector_id'], 'upgrade_case': row['upgrade_case'],
                        'capex': float(row['total_capex']) if pd.notna(row['total_capex']) else 0,
                        'prb': float(row['prb']) if pd.notna(row['prb']) else 0,
                        'thpt': float(row['dl_thpt']) if pd.notna(row['dl_thpt']) else 0,
                        'users': int(row['user_count']) if pd.notna(row['user_count']) else 0}
                       for _, row in group.iterrows()]
            result.append({'site_id': site_id, 'upgrade_details': details, 'total_capex': sum(d['capex'] for d in details)})
        return jsonify(result)
    except Exception as e:
        print(traceback.format_exc())
        return jsonify([]), 500
