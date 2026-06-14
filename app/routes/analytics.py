import traceback
import json
import numpy as np
import pandas as pd
from datetime import date, timedelta
from flask import Blueprint, request, jsonify, redirect
from sklearn.linear_model import LinearRegression
from scipy.stats import t as t_dist
from bokeh.plotting import figure
from bokeh.layouts import gridplot
from bokeh.models import ColumnDataSource, HoverTool
from bokeh.embed import json_item
from app.extensions import api_login_required, get_cached_dataframe, aws_session
from app.config import S3_BUCKET

bp = Blueprint('analytics', __name__)

_METRICS = [
    {'col': 'eric_data_volume_ul_dl', 'title': 'Data Volume (GB)',   'color': '#1f77b4', 'limit': None},
    {'col': 'eric_prb_util_rate',     'title': 'PRB Util (%)',       'color': '#ff7f0e', 'limit': 100},
    {'col': 'eric_dl_user_ip_thpt',   'title': 'Throughput (Mbps)',  'color': '#2ca02c', 'limit': None},
]


@bp.route('/plot')
def plot_route():
    site_id          = request.args.get('site_id')
    forecast_horizon = request.args.get('forecast_horizon', default=52, type=int)
    if not site_id:
        return jsonify({'error': 'Missing site_id'}), 400

    try:
        sql = f"""
            SELECT zoom_sector_id, week, year, eric_data_volume_ul_dl, eric_prb_util_rate, eric_dl_user_ip_thpt
            FROM sector_calculations WHERE zoom_sector_id LIKE '{site_id.strip()}%' ORDER BY year, week
        """
        df_actual = get_cached_dataframe(sql)
        if df_actual.empty:
            return jsonify({'error': 'No data found'}), 404

        def get_date(r):
            try:
                return date.fromisocalendar(int(r['year']), int(r['week']), 1)
            except Exception:
                return None

        df_actual['plot_date'] = pd.to_datetime(df_actual.apply(get_date, axis=1))
        df_actual  = df_actual.dropna(subset=['plot_date'])
        start_date = df_actual['plot_date'].min()
        all_plots  = []

        for sector in df_actual['zoom_sector_id'].unique():
            df_sec = df_actual[df_actual['zoom_sector_id'] == sector].sort_values('plot_date')
            df_sec['days']  = (df_sec['plot_date'] - start_date).dt.days
            x_raw           = df_sec['days'].values.reshape(-1, 1)
            last_day        = x_raw.max()
            future_days_col = np.arange(last_day + 7, last_day + (7 * forecast_horizon), 7).reshape(-1, 1)
            future_dates    = [start_date + timedelta(days=int(d)) for d in future_days_col.flatten()]
            row_plots       = []

            for j, metric in enumerate(_METRICS):
                y_raw = df_sec[metric['col']].values
                mask  = ~np.isnan(y_raw)
                title = f"{sector} - {metric['title']}" if j == 1 else (sector if j == 0 else metric['title'])
                p     = figure(title=title, x_axis_type="datetime", sizing_mode="stretch_width", height=280, tools="pan,wheel_zoom,reset,save", background_fill_color="#fafafa")

                if np.sum(mask) > 2:
                    x_clean   = x_raw[mask]; y_clean = y_raw[mask]; n = len(x_clean)
                    model     = LinearRegression(); model.fit(x_clean, y_clean)
                    y_pred    = model.predict(future_days_col)
                    x_mean    = np.mean(x_clean); y_hat_hist = model.predict(x_clean)
                    residuals = y_clean - y_hat_hist; rss = np.sum(residuals ** 2); dof = n - 2
                    s_err     = np.sqrt(rss / dof); sxx = np.sum((x_clean - x_mean) ** 2)
                    t_val     = t_dist.ppf(0.975, dof)
                    ci_width  = [t_val * (s_err * np.sqrt((1/n) + ((d - x_mean) ** 2 / sxx))) for d in future_days_col.flatten()]
                    y_pred    = np.maximum(y_pred, 0)
                    if metric['limit']:
                        y_pred = np.minimum(y_pred, metric['limit'])
                    upper  = y_pred + ci_width; lower = np.maximum(y_pred - ci_width, 0)
                    if metric['limit']:
                        upper = np.minimum(upper, metric['limit'])

                    band_x = np.append(future_dates, future_dates[::-1])
                    band_y = np.append(lower, upper[::-1])
                    p.patch(band_x, band_y, color=metric['color'], alpha=0.15, line_width=0)
                    p.line(future_dates, y_pred, color=metric['color'], line_dash="dashed", line_width=1.5, legend_label="Forecast")

                    src = ColumnDataSource(data=dict(date=df_sec['plot_date'], val=df_sec[metric['col']], week_num=df_sec['week']))
                    p.line('date', 'val', source=src, color=metric['color'], line_width=1.5, legend_label="Actual")
                    c = p.scatter('date', 'val', source=src, color=metric['color'], size=5, marker="circle")
                    p.add_tools(HoverTool(renderers=[c], tooltips=[("Week", "@week_num"), ("Val", "@val{0.2f}")], formatters={'@date': 'datetime'}))

                p.legend.location = "top_left"; p.legend.label_text_font_size = "7pt"
                row_plots.append(p)
            all_plots.append(row_plots)

        grid = gridplot(all_plots, toolbar_location="right", sizing_mode="stretch_width")
        return jsonify({'plot_image': json.dumps(json_item(grid, "myplot"))})

    except Exception as e:
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@bp.route('/plot_page')
def plot_page():
    site_id          = request.args.get('site_id')
    forecast_horizon = request.args.get('forecast_horizon', 52)
    if not site_id:
        return "Missing site_id", 400

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <script src="https://cdn.bokeh.org/bokeh/release/bokeh-3.9.0.min.js"></script>
    <script src="https://cdn.bokeh.org/bokeh/release/bokeh-widgets-3.9.0.min.js"></script>
    <script src="https://cdn.bokeh.org/bokeh/release/bokeh-tables-3.9.0.min.js"></script>
    <script src="https://cdn.bokeh.org/bokeh/release/bokeh-api-3.9.0.min.js"></script>
    <style>body{{margin:0;padding:10px;background:white;overflow-y:auto;overflow-x:hidden;}} #myplot{{width:100%;display:block;}}</style>
</head>
<body>
    <div id="myplot"></div>
    <script>
        fetch('/plot?site_id={site_id}&forecast_horizon={forecast_horizon}')
            .then(r => r.json())
            .then(data => {{
                if (data.error) {{
                    document.getElementById('myplot').innerHTML = "<p style='color:red;padding:20px'>" + data.error + "</p>";
                }} else {{
                    Bokeh.embed.embed_item(JSON.parse(data.plot_image), "myplot");
                }}
            }}).catch(err => console.error(err));
    </script>
</body>
</html>"""


@bp.route('/download/cd_file')
def download_cd_file():
    try:
        url = aws_session.client('s3').generate_presigned_url('get_object', Params={'Bucket': S3_BUCKET, 'Key': '3W-data/processed_network_data/cd-combined-results/CD_Combined_Results.csv'}, ExpiresIn=3600)
        return redirect(url)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/download/sector')
def download_sector():
    try:
        url = aws_session.client('s3').generate_presigned_url('get_object', Params={'Bucket': S3_BUCKET, 'Key': '3W-data/processed_network_data/cd-combined-results/Sector_Metrics.csv'}, ExpiresIn=3600)
        return redirect(url)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/download/congested')
def download_congested():
    try:
        url = aws_session.client('s3').generate_presigned_url('get_object', Params={'Bucket': S3_BUCKET, 'Key': '3W-data/processed_network_data/cd-combined-results/Congested_Sectors.csv'}, ExpiresIn=3600)
        return redirect(url)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
