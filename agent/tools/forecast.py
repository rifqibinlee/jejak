import pandas as pd
import awswrangler as wr
from langchain_core.tools import tool
from app.config import ATHENA_DATABASE, S3_STAGING_DIR
from app.extensions import aws_session


@tool
def get_capacity_forecast(site_id: str) -> str:
    """Use this tool to predict FUTURE network congestion and PRB utilization for a specific site."""
    site_id = site_id.strip().upper()
    try:
        yr_df = wr.athena.read_sql_query(
            "SELECT MAX(year) as max_yr FROM forecast_results",
            database=ATHENA_DATABASE, s3_output=S3_STAGING_DIR,
            boto3_session=aws_session, ctas_approach=False,
        )
        year = str(yr_df['max_yr'].iloc[0])
        print(f"[Agent Tool] Fetching Forecast for Site: {site_id}, Year: {year}...")

        sql = f"""
            SELECT zoom_sector_id, month, predicted_eric_prb_util_rate as pred_prb,
                   predicted_eric_dl_user_ip_thpt as pred_thpt, congested
            FROM forecast_results
            WHERE UPPER(zoom_sector_id) LIKE '{site_id}%'
            AND CAST(year AS VARCHAR) = '{year}'
            ORDER BY zoom_sector_id, month
        """
        df = wr.athena.read_sql_query(sql=sql, database=ATHENA_DATABASE, s3_output=S3_STAGING_DIR, boto3_session=aws_session, ctas_approach=False)

        if df.empty:
            return f"No AI forecast data is currently available for site {site_id}."

        story = f"🔮 **Capacity Forecast for Site {site_id} ({year}):**\n"
        for sector in df['zoom_sector_id'].unique():
            sec_df = df[df['zoom_sector_id'] == sector]
            story += f"\n**Sector {sector}**:\n"
            for _, row in sec_df.iterrows():
                cong_status = "⚠️ PREDICTED CONGESTION" if row['congested'] else "✅ Healthy"
                prb  = float(row['pred_prb'])  if pd.notna(row['pred_prb'])  else 0.0
                thpt = float(row['pred_thpt']) if pd.notna(row['pred_thpt']) else 0.0
                story += f"  - Month {int(row['month'])}: {cong_status} (Est. PRB: {prb:.1f}%, Est. Thpt: {thpt:.1f} Mbps)\n"

        story += (
            f"\nINSTRUCTIONS FOR AI:\n"
            f"You MUST copy and paste the following HTML iframe block exactly as it is "
            f"at the very bottom of your response to show the forecast graph for this site:\n\n"
            f"<br><iframe src='/plot_page?site_id={site_id}' width='100%' height='550px' "
            f"style='border: 1px solid #e5e7eb; border-radius: 8px; margin-top: 15px; background: white;'></iframe>\n"
        )
        return story

    except Exception as e:
        return f"Error fetching forecast data: {e}"


@tool
def analyze_quarterly_slr_forecast(year: str = "Latest") -> str:
    """
    Use this tool when the user asks for a predictive ML story, SLR forecast, or quarterly capacity predictions.
    Returns the number of sectors expected to congest each quarter and identifies the absolute worst-case sector.
    """
    try:
        yr_df = wr.athena.read_sql_query(
            "SELECT MAX(year) as max_yr FROM forecast_results",
            database=ATHENA_DATABASE, s3_output=S3_STAGING_DIR,
            boto3_session=aws_session, ctas_approach=False,
        )
        target_year = str(yr_df['max_yr'].iloc[0])
        print(f"[Agent Tool] Building Quarterly SLR Forecast Story for {target_year}...")

        q_sql = f"""
            SELECT CAST(CEIL(month / 3.0) AS INTEGER) AS quarter,
                   COUNT(DISTINCT zoom_sector_id) AS congested_sectors
            FROM forecast_results
            WHERE congested = TRUE AND CAST(year AS VARCHAR) = '{target_year}'
            GROUP BY CEIL(month / 3.0)
            ORDER BY quarter
        """
        q_df = wr.athena.read_sql_query(sql=q_sql, database=ATHENA_DATABASE, s3_output=S3_STAGING_DIR, boto3_session=aws_session, ctas_approach=False)

        w_sql = f"""
            SELECT zoom_sector_id, MAX(predicted_eric_prb_util_rate) as max_prb,
                   MIN(predicted_eric_dl_user_ip_thpt) as min_thpt
            FROM forecast_results
            WHERE CAST(year AS VARCHAR) = '{target_year}'
            GROUP BY zoom_sector_id ORDER BY max_prb DESC LIMIT 1
        """
        w_df = wr.athena.read_sql_query(sql=w_sql, database=ATHENA_DATABASE, s3_output=S3_STAGING_DIR, boto3_session=aws_session, ctas_approach=False)

        worst_sector = w_df['zoom_sector_id'].iloc[0] if not w_df.empty else "N/A"
        worst_prb    = w_df['max_prb'].iloc[0]        if not w_df.empty else 0.0
        worst_thpt   = w_df['min_thpt'].iloc[0]       if not w_df.empty else 0.0
        worst_site   = worst_sector.split('_')[0]      if worst_sector != "N/A" else "N/A"

        story_data = f"SLR Forecast Data for {target_year}:\n"
        for _, row in q_df.iterrows():
            story_data += f"- Q{row['quarter']}: {row['congested_sectors']} sectors predicted to hit critical congestion.\n"

        story_data += (
            f"\nAbsolute Worst Sector Predicted: {worst_sector}\n"
            f"- Peak Predicted PRB: {worst_prb:.1f}%\n"
            f"- Lowest Predicted Throughput: {worst_thpt:.1f} Mbps\n"
            f"\nINSTRUCTIONS FOR AI:\n"
            f"1. Write a compelling, executive-level narrative breaking down the network degradation quarter-by-quarter.\n"
            f"2. Explain WHAT will happen to the users (e.g., video buffering, dropped calls, web timeouts).\n"
            f"3. Explain the BUSINESS CONSEQUENCES if left unmanaged.\n"
            f"4. Highlight the worst sector ({worst_sector}) as your primary case study.\n"
            f"5. IMPORTANT: You MUST copy and paste the following HTML code block exactly as it is at the very bottom of your response:\n\n"
            f"<br><iframe src='/plot_page?site_id={worst_site}' width='100%' height='400px' "
            f"style='border: 1px solid #e5e7eb; border-radius: 8px; margin-top: 15px; background: white;'></iframe>\n"
        )
        return story_data

    except Exception as e:
        return f"Error analyzing SLR forecast: {e}"
