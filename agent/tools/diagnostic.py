import pandas as pd
import awswrangler as wr
from langchain_core.tools import tool
from app.config import ATHENA_DATABASE, S3_STAGING_DIR
from app.extensions import aws_session


@tool
def diagnose_site_health(site_id: str) -> str:
    """
    Use this tool WHENEVER the user asks to troubleshoot, diagnose, or assess the health/status
    of a specific site. It automatically executes a 4-step L2/L3 engineering triage:
    Power Check, Geospatial Neighbors & Coverage, Capacity/CAPEX, and ML Forecast.
    """
    site_id   = site_id.strip().upper()
    base_site = site_id.split('_')[0]

    try:
        yr_df = wr.athena.read_sql_query(
            "SELECT MAX(year) as max_yr FROM congestion_analysis",
            database=ATHENA_DATABASE, s3_output=S3_STAGING_DIR,
            boto3_session=aws_session, ctas_approach=False,
        )
        year = str(yr_df['max_yr'].iloc[0])

        wk_df = wr.athena.read_sql_query(
            f"SELECT MAX(week) as max_wk FROM congestion_analysis WHERE CAST(year AS VARCHAR) = '{year}'",
            database=ATHENA_DATABASE, s3_output=S3_STAGING_DIR,
            boto3_session=aws_session, ctas_approach=False,
        )
        latest_week = str(wk_df['max_wk'].iloc[0])
        report = f"### 🛠️ L2/L3 Diagnostic Report for Site: {base_site}\n\n"

        # ── Step 1: Power & Availability ──────────────────────────────────────
        report += "**Step 1: Power & Availability Check**\n"
        report += (
            "- *Status:* ⚠️ LIVE TELEMETRY PENDING INTEGRATION.\n"
            "- *Engineering Note:* Live power alarms and downtime logs are not yet streaming into "
            "the AWS Data Lake. Assuming the site has power, proceeding to RF analysis...\n\n"
        )

        # ── Step 2: RF Quality & Geospatial Neighbors ─────────────────────────
        report += "**Step 2: RF Quality, Terrain & Geospatial Neighbors**\n"
        coord_sql = f"SELECT latitude, longitude, cluster FROM site_coordinates WHERE UPPER(site_id) = '{base_site}' LIMIT 1"
        coord_df  = wr.athena.read_sql_query(sql=coord_sql, database=ATHENA_DATABASE, s3_output=S3_STAGING_DIR, boto3_session=aws_session, ctas_approach=False)

        if coord_df.empty:
            report += f"- *Location:* Coordinates for {base_site} not found in database.\n\n"
        else:
            lat     = coord_df['latitude'].iloc[0]
            lon     = coord_df['longitude'].iloc[0]
            cluster = coord_df['cluster'].iloc[0]
            report += f"- *Location:* {lat}, {lon} (Cluster: {cluster})\n"

            neighbor_sql = f"""
                SELECT c.site_id,
                       ROUND(ST_Distance(ST_Point(c.longitude, c.latitude), ST_Point({lon}, {lat})) * 111.32, 2) as dist_km
                FROM site_coordinates c
                WHERE UPPER(c.site_id) != '{base_site}'
                  AND c.latitude IS NOT NULL AND c.longitude IS NOT NULL
                  AND UPPER(c.site_id) IN (
                      SELECT DISTINCT UPPER(site_id) FROM congestion_analysis
                      WHERE CAST(year AS VARCHAR) = '{year}'
                  )
                ORDER BY ST_Distance(ST_Point(c.longitude, c.latitude), ST_Point({lon}, {lat})) ASC
                LIMIT 3
            """
            neighbor_df = wr.athena.read_sql_query(sql=neighbor_sql, database=ATHENA_DATABASE, s3_output=S3_STAGING_DIR, boto3_session=aws_session, ctas_approach=False)

            if not neighbor_df.empty:
                neighbors = [f"{row['site_id']} ({row['dist_km']} km)" for _, row in neighbor_df.iterrows()]
                report += f"- *Nearest Tier-1 Neighbors:* {', '.join(neighbors)} (Verified Active)\n"
            else:
                report += "- *Nearest Tier-1 Neighbors:* No active traffic-bearing neighbors found within a reasonable radius.\n"

            cov_sql = f"""
                SELECT COUNT(*) as point_count, AVG(signal_strength) as avg_signal
                FROM coverage_holes_clustered WHERE UPPER(serving_cell) LIKE '{base_site}%'
            """
            cov_df = wr.athena.read_sql_query(sql=cov_sql, database=ATHENA_DATABASE, s3_output=S3_STAGING_DIR, boto3_session=aws_session, ctas_approach=False)
            pts = cov_df['point_count'].iloc[0]
            if pts > 0:
                avg_sig = cov_df['avg_signal'].iloc[0]
                report += f"- *RSRP / Signal Quality:* Detected {pts} poor signal points. Average Signal Strength: {avg_sig:.1f} dBm.\n"
                report += "- *Terrain Warning:* If neighbors are healthy but this site has blind spots, cross-reference local terrain affecting propagation.\n\n"
            else:
                report += (
                    "- *RSRP / Signal Quality:* ⚠️ LIVE RF TELEMETRY UNAVAILABLE FOR THIS REGION.\n"
                    "- *Engineering Note:* Coverage hole data is only available for specific test areas. Cannot currently confirm the presence of blind spots.\n\n"
                )

        # ── Step 3: Congestion & CAPEX BoQ ────────────────────────────────────
        report += "**Step 3: Capacity, Congestion & CAPEX BoQ**\n"
        cap_sql = f"""
            SELECT ca.zoom_sector_id, ca.eric_prb_util_rate, ca.eric_dl_user_ip_thpt, ca.congested,
                   cu.suggested_upgrade_case, cu.estimated_total_capex_rm
            FROM congestion_analysis ca
            LEFT JOIN capex_upgrades cu
                ON TRIM(UPPER(ca.zoom_sector_id)) = TRIM(UPPER(cu.zoom_sector_id))
                AND CAST(ca.year AS VARCHAR) = CAST(cu.data_year AS VARCHAR)
                AND CAST(ca.week AS VARCHAR) = CAST(cu.data_week AS VARCHAR)
            WHERE UPPER(ca.zoom_sector_id) LIKE '{base_site}%'
            AND CAST(ca.year AS VARCHAR) = '{year}'
            AND CAST(ca.week AS VARCHAR) = '{latest_week}'
            ORDER BY ca.eric_prb_util_rate DESC
        """
        cap_df = wr.athena.read_sql_query(sql=cap_sql, database=ATHENA_DATABASE, s3_output=S3_STAGING_DIR, boto3_session=aws_session, ctas_approach=False)

        if cap_df.empty:
            report += f"- No capacity or congestion data found for {base_site} in Week {latest_week}, {year}.\n"
        else:
            num_sectors = cap_df['zoom_sector_id'].nunique()
            report += f"- *Site Configuration:* Confirmed {num_sectors} active sectors.\n"
            for _, row in cap_df.iterrows():
                prb      = row['eric_prb_util_rate']
                thpt     = row['eric_dl_user_ip_thpt']
                is_cong  = row['congested']
                upg_case = str(row['suggested_upgrade_case'])
                cost     = float(row['estimated_total_capex_rm']) if pd.notna(row['estimated_total_capex_rm']) else 0.0
                status   = "🔴 CONGESTED" if is_cong else "🟢 HEALTHY"
                report  += f"  - **Sector {row['zoom_sector_id']}**: {status} (PRB: {prb:.1f}%, Thpt: {thpt:.1f} Mbps)\n"
                if is_cong and upg_case.lower() not in ['nan', 'none', '']:
                    report += f"    - *BoQ Decision:* {upg_case}\n"
                    report += f"    - *CAPEX Required:* RM {cost:,.2f}\n"

        # ── Step 4: Predictive ML Forecast ────────────────────────────────────
        report += "\n**Step 4: Predictive Capacity Forecast**\n"
        fc_sql = f"""
            SELECT zoom_sector_id, MAX(month) as max_month,
                   MAX(predicted_eric_prb_util_rate) as max_prb,
                   MIN(predicted_eric_dl_user_ip_thpt) as min_thpt
            FROM forecast_results
            WHERE UPPER(zoom_sector_id) LIKE '{base_site}%'
            AND CAST(year AS VARCHAR) = '{year}'
            GROUP BY zoom_sector_id ORDER BY max_prb DESC
        """
        fc_df = wr.athena.read_sql_query(sql=fc_sql, database=ATHENA_DATABASE, s3_output=S3_STAGING_DIR, boto3_session=aws_session, ctas_approach=False)

        if fc_df.empty:
            report += f"- No ML forecast data available for {base_site}.\n"
        else:
            worst_sec  = fc_df.iloc[0]['zoom_sector_id']
            worst_prb  = fc_df.iloc[0]['max_prb']
            worst_thpt = fc_df.iloc[0]['min_thpt']
            report += (
                f"- *Predictive Analysis:* Without CAPEX injection, Sector {worst_sec} will peak at "
                f"{worst_prb:.1f}% PRB utilization, crashing throughput down to {worst_thpt:.1f} Mbps.\n"
                "- *Business Impact:* Detail the user experience story here (e.g., customer churn, "
                "complete inability to stream video, failed VoLTE calls) if these numbers are reached.\n"
            )

        report += (
            "\n**INSTRUCTIONS FOR AI:**\n"
            "1. Summarize Steps 1, 2, and 3 clearly. Acknowledge data unavailability gracefully.\n"
            "2. Transition into Step 4 with a compelling 'Forecast Story' about what will happen "
            "if the upgrades are not implemented.\n"
            f"3. You MUST copy and paste the following HTML iframe block exactly as it is at the very "
            f"bottom of your final response so the user can see the forecast graph:\n\n"
            f"<br><iframe src='/plot_page?site_id={base_site}' width='100%' height='550px' "
            f"style='border: 1px solid #e5e7eb; border-radius: 8px; margin-top: 15px; background: white;'></iframe>\n"
        )
        return report

    except Exception as e:
        return f"Error executing L2/L3 diagnostic: {str(e)}"
