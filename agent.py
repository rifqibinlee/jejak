import os
import json
import re
import boto3
import awswrangler as wr
import pandas as pd
import psycopg2
import requests
import time
from langchain_community.chat_models import ChatLiteLLM
from langchain_core.tools import tool
from langchain_core.messages import SystemMessage, HumanMessage, messages_to_dict, messages_from_dict
from langgraph.prebuilt import create_react_agent
from sentence_transformers import CrossEncoder, SentenceTransformer

# --- AWS ATHENA CONFIGURATION ---
ATHENA_DATABASE = "advanced-analytics"
S3_STAGING_DIR = "s3://jejak-mappro-demo /3W-data/athena-query-results/"
aws_session = boto3.Session(region_name="ap-southeast-1")

print("[System] Loading Cross-Encoder Reranker Model...")

try:
    reranker = CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')
    print("[System] Loading Native Nomic Embedder...")
    embedder = SentenceTransformer('nomic-ai/nomic-embed-text-v1.5', trust_remote_code=True)
except Exception as e:
    print(f"[Warning] Could not load models: {e}")
    reranker = None

# ==========================================
# 1. DEFINE THE TOOLS FOR LLAMA 3.2
# ==========================================

@tool
def get_site_capacity(site_id: str, week: str = "All") -> str:
    """
    Use this tool to find out if a specific cell site is congested, its PRB utilization, throughput, user count, AND required CAPEX upgrades.
    Provide the site_id (e.g., 'KUL_01' or '1712H') and the week.
    """
    site_id = site_id.strip().upper()
    week_num = re.sub(r'[^0-9]', '', str(week))

    try:
        # DYNAMIC TRACING: Always find the latest year internally. The AI cannot pass a fake year anymore.
        yr_df = wr.athena.read_sql_query("SELECT MAX(year) as max_yr FROM congestion_analysis", database=ATHENA_DATABASE, s3_output=S3_STAGING_DIR, boto3_session=aws_session, ctas_approach=False)
        year = str(yr_df['max_yr'].iloc[0])

        print(f"[Agent Tool] Querying Athena for Site: {site_id}, Year: {year}, Week: {week_num}...")

        # JOIN with capex_upgrades to get region, cluster, and actual upgrade recommendations
        sql = f"""
            SELECT
                ca.region, ca.cluster, ca.zoom_sector_id, ca.week,
                ca.eric_prb_util_rate, ca.eric_dl_user_ip_thpt,
                ca.eric_max_rrc_user, ca.max_active_user, ca.area_target, ca.bau_nic,
                cu.suggested_upgrade_case, cu.estimated_total_capex_rm
            FROM congestion_analysis ca
            LEFT JOIN capex_upgrades cu
                ON TRIM(UPPER(ca.zoom_sector_id)) = TRIM(UPPER(cu.zoom_sector_id))
                AND CAST(ca.year AS VARCHAR) = CAST(cu.data_year AS VARCHAR)
                AND CAST(ca.week AS VARCHAR) = CAST(cu.data_week AS VARCHAR)
            WHERE UPPER(ca.zoom_sector_id) LIKE '{site_id}%'
            AND CAST(ca.year AS VARCHAR) = '{year}'
        """

        df = wr.athena.read_sql_query(
            sql=sql, database=ATHENA_DATABASE, s3_output=S3_STAGING_DIR,
            boto3_session=aws_session, ctas_approach=False
        )

        if df.empty:
            return f"I could not find any capacity data for site {site_id} in the AWS database."

        if week_num:
            df = df[df['week'].astype(str) == str(week_num)]

        if df.empty:
            return f"Site {site_id} exists, but there is no data recorded for Week {week_num}."

        df = df.head(6)

        # Extract Region and Cluster from the first row
        region = df['region'].iloc[0] if pd.notna(df['region'].iloc[0]) else "Unknown Region"
        cluster = df['cluster'].iloc[0] if pd.notna(df['cluster'].iloc[0]) else "Unknown Cluster"

        result_str = f"Detailed Capacity & CAPEX Analysis for Site {site_id} (Region: {region}, Cluster: {cluster}, Week {week_num}):\n\n"

        for _, row in df.iterrows():
            sec = row['zoom_sector_id']

            # 1. Extract Metrics
            prb = round(float(row['eric_prb_util_rate']), 2) if pd.notna(row['eric_prb_util_rate']) else 0.0
            thpt = round(float(row['eric_dl_user_ip_thpt']), 2) if pd.notna(row['eric_dl_user_ip_thpt']) else 0.0

            users_rrc = float(row['eric_max_rrc_user']) if pd.notna(row['eric_max_rrc_user']) else 0.0
            users_act = float(row['max_active_user']) if pd.notna(row['max_active_user']) else 0.0
            users = max(users_rrc, users_act)

            upg_case = str(row['suggested_upgrade_case']) if pd.notna(row['suggested_upgrade_case']) else "None"
            upg_cost = float(row['estimated_total_capex_rm']) if pd.notna(row['estimated_total_capex_rm']) else 0.0

            # 2. Determine Dynamic Thresholds
            area = str(row['area_target']).lower()
            mode = str(row['bau_nic']).lower()
            is_urban = 'urban' in area or 'kmc' in area

            prb_thresh = 80.0 if is_urban else 92.0
            thpt_thresh = 3.0
            if is_urban:
                thpt_thresh = 7.0 if 'nic' in mode else 5.0
            user_thresh = 120.0

            # 3. Evaluate the 3 KPIs
            exceeded_count = 0
            reasons = []

            if prb >= prb_thresh:
                exceeded_count += 1
                reasons.append(f"PRB ({prb}%) >= limit ({prb_thresh}%)")

            if thpt > 0 and thpt <= thpt_thresh:
                exceeded_count += 1
                reasons.append(f"Throughput ({thpt} Mbps) <= limit ({thpt_thresh} Mbps)")

            if users >= user_thresh:
                exceeded_count += 1
                reasons.append(f"Users ({int(users)}) >= limit (120)")

            # 4. Assign Priority
            if exceeded_count == 3:
                priority = "CRITICAL PRIORITY (Fully Congested)"
            elif exceeded_count == 2:
                priority = "MODERATE PRIORITY (At Risk)"
            elif exceeded_count == 1:
                priority = "LOW PRIORITY (Minor Degradation)"
            else:
                priority = "HEALTHY"

            # 5. Build String
            result_str += f"Sector {sec}: {priority}\n"
            if exceeded_count > 0:
                result_str += f"  - Exceeded {exceeded_count}/3 Thresholds. Reasons: {', '.join(reasons)}\n"
                if upg_case != "None" and upg_case != "nan" and upg_case != "":
                    result_str += f"  - RECOMMENDED CAPEX UPGRADE: {upg_case} (Est. Cost: RM {upg_cost:,.2f})\n"
                else:
                    result_str += "  - RECOMMENDED CAPEX UPGRADE: No specific hardware upgrade suggested yet.\n"
            else:
                result_str += f"  - Metrics normal (PRB: {prb}%, Thpt: {thpt} Mbps, Users: {int(users)}). No upgrade needed.\n"
            result_str += "\n"

        return result_str

    except Exception as e:
        return f"Error fetching site data from AWS Athena: {str(e)}"

@tool
def analyze_network_congestion_story(week: str = "All", year: str = "Latest", region: str = "All", cluster: str = "All") -> str:
    """Use this tool when the user asks global questions like 'how many congested sites are there?', 'how many congested sectors?', or network health.
    If the user asks for a specific year, pass it here.
    """
    try:
        import re

        # Clean the Year
        year_str = str(year)
        year_digits = re.sub(r'[^0-9]', '', year_str)

        if not year_digits:
            yr_df = wr.athena.read_sql_query("SELECT MAX(year) as max_yr FROM congestion_analysis", database=ATHENA_DATABASE, s3_output=S3_STAGING_DIR, boto3_session=aws_session, ctas_approach=False)
            target_year = str(yr_df['max_yr'].iloc[0])
        else:
            target_year = year_digits

        # Clean the Week
        week_str = str(week).lower()
        week_digits = re.sub(r'[^0-9]', '', week_str)

        print(f"[Agent Tool] Building Congestion Story for Year: {target_year}, Week: {week_digits if week_digits else 'All'}, Region: {region}, Cluster: {cluster}...")

        # FIX: Inject the map filters directly into the SQL query and grab PRB/Throughput
        sql = f"SELECT zoom_sector_id, region, cluster, week, congested, eric_prb_util_rate, eric_dl_user_ip_thpt FROM congestion_analysis WHERE CAST(year AS VARCHAR) = '{target_year}'"

        if region and region != "All":
            sql += f" AND UPPER(region) = '{region.upper()}'"
        if cluster and cluster != "All":
            sql += f" AND cluster = '{cluster}'"

        df = wr.athena.read_sql_query(sql=sql, database=ATHENA_DATABASE, s3_output=S3_STAGING_DIR, boto3_session=aws_session, ctas_approach=False)

        if df.empty:
            return f"No network data was found in AWS Athena for Year {target_year} matching those filters."

        # Filter by Week
        if week_digits:
            df['clean_week'] = df['week'].astype(str).str.split('.').str[0]
            df = df[df['clean_week'] == week_digits]
            if df.empty:
                return f"Data exists for Year {target_year}, but no records were found specifically for Week {week_digits} with those filters."

        # Drop duplicates so we don't overcount sectors with multiple rows
        df = df.drop_duplicates(subset=['zoom_sector_id']).copy()

        # CALCULATE TOTAL NETWORK FOOTPRINT
        df['base_site'] = df['zoom_sector_id'].astype(str).str.split('_').str[0].str.split('-').str[0]
        total_network_sites = df['base_site'].nunique()
        total_network_sectors = df['zoom_sector_id'].nunique()

        # Filter for Congestion
        df['is_cong'] = df['congested'].isin([True, 1, 1.0, "True", "true", "1", "1.0", "yes", "Yes"])
        cong_df = df[df['is_cong']].copy()

        total_cong_sectors = cong_df['zoom_sector_id'].nunique()
        total_cong_sites = cong_df['base_site'].nunique() if total_cong_sectors > 0 else 0

        if total_cong_sectors == 0:
            return f"IMPORTANT: You MUST tell the user: For Year {target_year}, Week {week_digits if week_digits else 'All'}, we analyzed {total_network_sites} sites and found exactly 0 congested sectors. The network is healthy!"

        # CALCULATE RATIOS
        site_pct = (total_cong_sites / total_network_sites) * 100 if total_network_sites > 0 else 0
        sector_pct = (total_cong_sectors / total_network_sectors) * 100 if total_network_sectors > 0 else 0

        # BUILD THE EXECUTIVE STORY
        story = f"IMPORTANT: You MUST start your response by saying 'Here is the data for Year {target_year}, Week {week_digits if week_digits else 'All'}:'\n\n"
        story += f"Out of **{total_network_sectors} total sectors** (across **{total_network_sites} physical sites**):\n\n"
        story += f"Currently, **{total_cong_sectors} sectors** ({sector_pct:.1f}%) are congested, affecting **{total_cong_sites} physical sites** ({site_pct:.1f}%).\n\n"

        story += "🚨 **Regional Breakdown:**\n"

        for reg, count in cong_df['region'].value_counts().items():
            if pd.notna(reg) and str(reg).strip() != "":
                story += f"- **{reg.upper()}**: {count} congested sectors\n"

        story += "\n🔥 **Worst Affected Clusters:**\n"
        for clus, count in cong_df['cluster'].value_counts().head(3).items():
            if pd.notna(clus): story += f"- **Cluster {clus}**: {count} congested sectors\n"

        # ---> NEW CODE: LIST THE SPECIFIC SITES AND SECTORS <---
        if total_cong_sectors > 0:
            story += "\n⚠️ **Specific Congested Sites & Sectors Identified:**\n"

            # Sort by PRB to show the absolute worst offenders at the top
            cong_df['eric_prb_util_rate'] = pd.to_numeric(cong_df['eric_prb_util_rate'], errors='coerce')
            cong_df = cong_df.sort_values(by='eric_prb_util_rate', ascending=False)

            # Limit to top 10 to prevent overwhelming the LLM token limit on nationwide searches
            for _, row in cong_df.head(10).iterrows():
                sec_id = row['zoom_sector_id']
                site_id = row['base_site']
                reg = row['region'] if pd.notna(row['region']) else "Unknown"
                clus = row['cluster'] if pd.notna(row['cluster']) else "Unknown"
                prb = row['eric_prb_util_rate']
                thpt = row['eric_dl_user_ip_thpt']

                prb_str = f"{prb:.1f}%" if pd.notna(prb) else "N/A"
                thpt_str = f"{thpt:.1f} Mbps" if pd.notna(thpt) else "N/A"

                story += f"- **Site {site_id}** (Sector: {sec_id}) | Location: {reg} - Cluster {clus} | PRB: {prb_str} | Thpt: {thpt_str}\n"

            if total_cong_sectors > 10:
                story += f"- *(...and {total_cong_sectors - 10} more sectors. Advise the user to filter down for a more specific list!)*\n"

        story += "\nINSTRUCTIONS FOR AI: Clearly list the specific congested sites/sectors provided above so the user knows exactly where the problems are."

        return story
    except Exception as e:
        return f"Error analyzing data: {e}"

@tool
def get_capex_pricing_info() -> str:
    """Use if user asks about upgrade costs, CAPEX, pricing, or antenna costs."""
    return "The base pricing is configured by the Admin. Tell the user to click the 'Enterprise Use Cases' dropdown or click the 'Pricing Configuration' icon."

@tool
def get_platform_navigation(feature: str) -> str:
    """Use if user asks how to do something in the UI (e.g., 'draw annotations', 'CCTV planning', '3D map')."""
    feature = feature.lower()
    if "cctv" in feature or "camera" in feature:
        return "Click 'Enterprise Use Cases' in the top navbar, then select 'CCTV Planning'."
    elif "bitcoin" in feature or "mining" in feature:
        return "Click 'Enterprise Use Cases' in the top navbar, then select 'Illegal Bitcoin Mining'."
    elif "3d" in feature or "cesium" in feature:
        return "Drag the little 'Pegman' icon (bottom left of the map) onto the map to open the 3D Digital Twin."
    elif "layer" in feature or "traffic" in feature or "heat" in feature:
        return "Click the 'Layers' button (the traffic light icon) at the bottom left of the screen to toggle 5G, 4G, Heatmaps, and Live Traffic."
    elif "draw" in feature or "annotation" in feature:
        return "Click the Polygon icon on the right-side floating toolbar to open the Annotations panel."
    else:
        return "Use the top navigation bar or the floating icons on the right side of the screen."

@tool
def get_capacity_forecast(site_id: str) -> str:
    """Use this tool to predict FUTURE network congestion and PRB utilization for a specific site."""
    site_id = site_id.strip().upper()

    try:
        # DYNAMIC TRACING: Always find the latest year internally.
        yr_df = wr.athena.read_sql_query("SELECT MAX(year) as max_yr FROM forecast_results", database=ATHENA_DATABASE, s3_output=S3_STAGING_DIR, boto3_session=aws_session, ctas_approach=False)
        year = str(yr_df['max_yr'].iloc[0])

        print(f"[Agent Tool] Fetching Forecast for Site: {site_id}, Year: {year}...")

        # Query forecast_results (casting year to VARCHAR to be safe against Glue Crawlers)
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
                prb = float(row['pred_prb']) if pd.notna(row['pred_prb']) else 0.0
                thpt = float(row['pred_thpt']) if pd.notna(row['pred_thpt']) else 0.0
                story += f"  - Month {int(row['month'])}: {cong_status} (Est. PRB: {prb:.1f}%, Est. Thpt: {thpt:.1f} Mbps)\n"

        story += f"\nINSTRUCTIONS FOR AI:\n"
        story += f"You MUST copy and paste the following HTML iframe block exactly as it is at the very bottom of your response to show the forecast graph for this site:\n\n"
        story += f"<br><iframe src='/plot_page?site_id={site_id}' width='100%' height='550px' style='border: 1px solid #e5e7eb; border-radius: 8px; margin-top: 15px; background: white;'></iframe>\n"

        return story
    except Exception as e:
        return f"Error fetching forecast data: {e}"

@tool
def analyze_coverage_holes(site_id: str = "ALL") -> str:
    """Use this tool to find coverage holes, blind spots, or areas with bad signal."""
    site_id = site_id.strip().upper()
    try:
        print(f"[Agent Tool] Analyzing Coverage Holes for: {site_id}...")

        sql = """
            SELECT cluster_id, serving_cell, data_source, COUNT(*) as point_count, AVG(signal_strength) as avg_signal
            FROM coverage_holes_clustered
            WHERE cluster_id != -1
        """
        if site_id != "ALL":
            sql += f" AND UPPER(serving_cell) LIKE '{site_id}%'"

        sql += " GROUP BY cluster_id, serving_cell, data_source ORDER BY point_count DESC LIMIT 5"

        df = wr.athena.read_sql_query(sql=sql, database=ATHENA_DATABASE, s3_output=S3_STAGING_DIR, boto3_session=aws_session, ctas_approach=False)

        if df.empty:
            return f"Great news! No major coverage holes or blind spots found in the database for {site_id}."

        story = f"📡 **Top Coverage Holes & Blind Spots for {site_id}:**\n"
        for _, row in df.iterrows():
            story += f"- **Cluster {row['cluster_id']}** (Detected by {row['data_source']}): {row['point_count']} poor signal points. "
            story += f"Average Signal: {row['avg_signal']:.1f} dBm. Serving Cell: {row['serving_cell']}\n"
        return story
    except Exception as e:
        return f"Error analyzing coverage holes: {e}"

@tool
def analyze_quarterly_slr_forecast(year: str = "Latest") -> str:
    """
    Use this tool when the user asks for a predictive ML story, SLR forecast, or quarterly capacity predictions.
    It returns the number of sectors expected to congest each quarter and identifies the absolute worst-case sector.
    """
    try:
        # 1. Get the latest forecast year
        yr_df = wr.athena.read_sql_query("SELECT MAX(year) as max_yr FROM forecast_results", database=ATHENA_DATABASE, s3_output=S3_STAGING_DIR, boto3_session=aws_session, ctas_approach=False)
        target_year = str(yr_df['max_yr'].iloc[0])

        print(f"[Agent Tool] Building Quarterly SLR Forecast Story for {target_year}...")

        # 2. Group congestion predictions into Quarters (Q1, Q2, Q3, Q4)
        q_sql = f"""
            SELECT
                CAST(CEIL(month / 3.0) AS INTEGER) AS quarter,
                COUNT(DISTINCT zoom_sector_id) AS congested_sectors
            FROM forecast_results
            WHERE congested = TRUE AND CAST(year AS VARCHAR) = '{target_year}'
            GROUP BY CEIL(month / 3.0)
            ORDER BY quarter
        """
        q_df = wr.athena.read_sql_query(sql=q_sql, database=ATHENA_DATABASE, s3_output=S3_STAGING_DIR, boto3_session=aws_session, ctas_approach=False)

        # 3. Find the Absolute Worst Sector in the network
        w_sql = f"""
            SELECT
                zoom_sector_id,
                MAX(predicted_eric_prb_util_rate) as max_prb,
                MIN(predicted_eric_dl_user_ip_thpt) as min_thpt
            FROM forecast_results
            WHERE CAST(year AS VARCHAR) = '{target_year}'
            GROUP BY zoom_sector_id
            ORDER BY max_prb DESC
            LIMIT 1
        """
        w_df = wr.athena.read_sql_query(sql=w_sql, database=ATHENA_DATABASE, s3_output=S3_STAGING_DIR, boto3_session=aws_session, ctas_approach=False)

        worst_sector = w_df['zoom_sector_id'].iloc[0] if not w_df.empty else "N/A"
        worst_prb = w_df['max_prb'].iloc[0] if not w_df.empty else 0.0
        worst_thpt = w_df['min_thpt'].iloc[0] if not w_df.empty else 0.0

        # Extract base site ID (e.g., KUL_01 from KUL_01_1) to pass to the plotting engine
        worst_site = worst_sector.split('_')[0] if worst_sector != "N/A" else "N/A"

        # 4. Package the data for Claude to tell the story
        story_data = f"SLR Forecast Data for {target_year}:\n"
        for _, row in q_df.iterrows():
            story_data += f"- Q{row['quarter']}: {row['congested_sectors']} sectors predicted to hit critical congestion.\n"

        story_data += f"\nAbsolute Worst Sector Predicted: {worst_sector}\n"
        story_data += f"- Peak Predicted PRB: {worst_prb:.1f}%\n"
        story_data += f"- Lowest Predicted Throughput: {worst_thpt:.1f} Mbps\n"

        # 5. Inject strict instructions for the LLM's narrative and UI Graph Button
        story_data += f"\nINSTRUCTIONS FOR AI:\n"
        story_data += f"1. Write a compelling, executive-level narrative breaking down the network degradation quarter-by-quarter based on these numbers.\n"
        story_data += f"2. Explain WHAT will happen to the users (e.g., video buffering, dropped calls, web timeouts).\n"
        story_data += f"3. Explain the BUSINESS CONSEQUENCES if left unmanaged (e.g., customer churn, brand damage, SLA breaches).\n"
        story_data += f"4. Highlight the worst sector ({worst_sector}) as your primary case study.\n"
        story_data += f"5. IMPORTANT: You MUST copy and paste the following HTML code block exactly as it is at the very bottom of your response to display the graph button:\n\n"
        story_data += f"<br><iframe src='/plot_page?site_id={worst_site}' width='100%' height='400px' style='border: 1px solid #e5e7eb; border-radius: 8px; margin-top: 15px; background: white;'></iframe>\n"

        return story_data

    except Exception as e:
        return f"Error analyzing SLR forecast: {e}"

@tool
def search_telecom_manuals(query: str, vendor: str = "All") -> str:
    """
    Use this tool WHENEVER the user asks for theoretical definitions, general telecom concepts, how things work, or specific terms from textbooks/manuals (e.g., 'what is congestion', 'define X', 'how does Y work').
    Do NOT rely solely on your internal glossary for general definitions—search the manuals first!
    Optionally pass the vendor (e.g., 'Ericsson', 'ZTE') if mentioned in the prompt.
    """
    print(f"[Agent Tool] Searching manuals for: {query} | Vendor: {vendor}")
    try:
        # 1. Turn the user's question into a vector natively
        query_vector = embedder.encode(query).tolist()
        vector_str = f"[{','.join(map(str, query_vector))}]"

        # 2. Search PostgreSQL (HYBRID SEARCH: Vector + Text Match)
        conn = psycopg2.connect(
            host=os.getenv('DB_HOST', 'vibe_db'),
            database=os.getenv('DB_NAME', 'vibe_db'),
            user=os.getenv('DB_USER', 'postgres'),
            password=os.getenv('DB_PASSWORD', '1234'),
            port=os.getenv('DB_PORT', '5432')
        )
        cursor = conn.cursor()

        # Build SQL with Vendor filtering and Hybrid Scoring
        params = [vector_str, f"%{query}%"]
        vendor_filter = ""
        if vendor and vendor != "All":
            vendor_filter = "WHERE vendor ILIKE %s"
            params.append(f"%{vendor}%")

        # Fetch top 10 candidates to give the reranker enough options
        sql = f"""
            SELECT document_name, chunk_text
            FROM telecom_knowledge_base
            {vendor_filter}
            ORDER BY
                (embedding <=> %s::vector) * 0.7 +
                (CASE WHEN chunk_text ILIKE %s THEN 0 ELSE 0.3 END) ASC
            LIMIT 10;
        """
        cursor.execute(sql, tuple(params))
        results = cursor.fetchall()

        cursor.close()
        conn.close()

        if not results:
            return "I searched the engineering manuals but couldn't find any relevant information."

        # 3. RERANKING STAGE
        if reranker and len(results) > 1:
            print("[Agent Tool] Reranking the top 10 search results...")

            # Create pairs of [User Query, Document Chunk] for the AI to score
            pairs = [[query, text] for doc, text in results]
            scores = reranker.predict(pairs)

            # Sort the results by the highest score
            scored_results = list(zip(scores, results))
            scored_results.sort(key=lambda x: x[0], reverse=True)

            # Keep only the absolute best 3
            final_results = [res for score, res in scored_results[:3]]
        else:
            # Fallback if reranker fails to load
            final_results = results[:3]

        # 4. Format the result for Claude
        formatted_results = "Here is the exact, reranked information from the engineering manuals:\n\n"
        for doc, text in final_results:
            formatted_results += f"--- Source: {doc} ---\n{text}\n\n"

        return formatted_results

    except Exception as e:
        return f"Error searching the knowledge base: {str(e)}"

@tool
def analyze_metabase_dashboard(dashboard_id: str) -> str:
    """
    Use this tool when the user asks you to explain, summarize, or read a Metabase dashboard.
    Provide the dashboard_id (e.g., '1', '2').
    """
    # FIX 1: Use internal Docker routing to bypass HTTPS loopback blocks!
    mb_url = "http://metabase:3000"
    mb_user = "hualee@celcomdigi.com"
    mb_pass = "8899230Ab"

    try:
        print(f"[Agent Tool] Connecting to Metabase API for Dashboard {dashboard_id} at {mb_url}...")

        session_res = requests.post(f"{mb_url}/api/session", json={"username": mb_user, "password": mb_pass}, timeout=10)
        if session_res.status_code != 200:
            return "Failed to authenticate with Metabase API. Please check credentials."

        mb_token = session_res.json().get("id")
        headers = {"X-Metabase-Session": mb_token}

        dash_res = requests.get(f"{mb_url}/api/dashboard/{dashboard_id}", headers=headers, timeout=10)
        if dash_res.status_code != 200:
            return f"Failed to find Dashboard {dashboard_id} in Metabase."

        dash_data = dash_res.json()
        story = f"Raw Data extracted from Metabase Dashboard: '{dash_data.get('name', 'Unknown')}'\n\n"

        for dashcard in dash_data.get('dashcards', []):
            card = dashcard.get('card', {})
            if not card or 'id' not in card:
                continue

            card_name = card.get('name', 'Unknown Chart')
            card_id = card.get('id')

            query_res = requests.post(f"{mb_url}/api/card/{card_id}/query/json", headers=headers, timeout=15)
            if query_res.status_code == 200:
                chart_data = query_res.json()
                preview = chart_data[:5]
                story += f"--- Chart: {card_name} ---\nData Snippet: {preview}\n\n"

        story += "INSTRUCTIONS FOR AI: Read the raw JSON data extracted from these Metabase charts. Write a comprehensive, executive-level summary explaining what these charts mean for the network's performance."
        return story

    except Exception as e:
        return f"Error connecting to Metabase API: {str(e)}"

@tool
def diagnose_site_health(site_id: str) -> str:
    """
    Use this tool WHENEVER the user asks to troubleshoot, diagnose, or assess the health/status of a specific site.
    It automatically executes a 4-step L2/L3 engineering triage: Power Check, Geospatial Neighbors & Coverage, Capacity/CAPEX, and ML Forecast.
    """
    site_id = site_id.strip().upper()
    # Extract base site ID in case the user passed a sector (e.g., KUL_01_1 -> KUL_01)
    base_site = site_id.split('_')[0]

    try:
        # Dynamic Tracing: Find the latest year and week
        yr_df = wr.athena.read_sql_query("SELECT MAX(year) as max_yr FROM congestion_analysis", database=ATHENA_DATABASE, s3_output=S3_STAGING_DIR, boto3_session=aws_session, ctas_approach=False)
        year = str(yr_df['max_yr'].iloc[0])

        wk_df = wr.athena.read_sql_query(f"SELECT MAX(week) as max_wk FROM congestion_analysis WHERE CAST(year AS VARCHAR) = '{year}'", database=ATHENA_DATABASE, s3_output=S3_STAGING_DIR, boto3_session=aws_session, ctas_approach=False)
        latest_week = str(wk_df['max_wk'].iloc[0])

        report = f"### 🛠️ L2/L3 Diagnostic Report for Site: {base_site}\n\n"

        # ==========================================
        # STEP 1: POWER & AVAILABILITY (Graceful Fallback)
        # ==========================================
        report += "**Step 1: Power & Availability Check**\n"
        report += "- *Status:* ⚠️ LIVE TELEMETRY PENDING INTEGRATION.\n"
        report += "- *Engineering Note:* Live power alarms (Mains Breakdown) and downtime logs are not yet streaming into the AWS Data Lake. In a real-world scenario, checking this is the critical first step to rule out a hard physical outage before analyzing RF metrics. Assuming the site has power, proceeding to RF analysis...\n\n"

        # ==========================================
        # STEP 2: GEOSPATIAL NEIGHBORS & RSRP
        # ==========================================
        report += "**Step 2: RF Quality, Terrain & Geospatial Neighbors**\n"

        # 2a. Get Target Coordinates
        coord_sql = f"SELECT latitude, longitude, cluster FROM site_coordinates WHERE UPPER(site_id) = '{base_site}' LIMIT 1"
        coord_df = wr.athena.read_sql_query(sql=coord_sql, database=ATHENA_DATABASE, s3_output=S3_STAGING_DIR, boto3_session=aws_session, ctas_approach=False)

        if coord_df.empty:
            report += f"- *Location:* Coordinates for {base_site} not found in database. Cannot perform spatial neighbor lookup.\n\n"
        else:
            lat = coord_df['latitude'].iloc[0]
            lon = coord_df['longitude'].iloc[0]
            cluster = coord_df['cluster'].iloc[0]
            report += f"- *Location:* {lat}, {lon} (Cluster: {cluster})\n"

            # 2b. Spatial Intersection: Find top 3 nearest active neighbors
            # We use congestion_analysis (which is derived from raw_network_data)
            # to guarantee we only pull active sites with a clean site_id match.
            neighbor_sql = f"""
                SELECT c.site_id,
                       ROUND(ST_Distance(ST_Point(c.longitude, c.latitude), ST_Point({lon}, {lat})) * 111.32, 2) as dist_km
                FROM site_coordinates c
                WHERE UPPER(c.site_id) != '{base_site}'
                  AND c.latitude IS NOT NULL AND c.longitude IS NOT NULL
                  AND UPPER(c.site_id) IN (
                      SELECT DISTINCT UPPER(site_id)
                      FROM congestion_analysis
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
                report += f"- *Nearest Tier-1 Neighbors:* No active traffic-bearing neighbors found within a reasonable radius.\n"

            # 2c. Check Coverage Holes / RSRP
            cov_sql = f"""
                SELECT COUNT(*) as point_count, AVG(signal_strength) as avg_signal
                FROM coverage_holes_clustered
                WHERE UPPER(serving_cell) LIKE '{base_site}%'
            """
            cov_df = wr.athena.read_sql_query(sql=cov_sql, database=ATHENA_DATABASE, s3_output=S3_STAGING_DIR, boto3_session=aws_session, ctas_approach=False)

            pts = cov_df['point_count'].iloc[0]
            if pts > 0:
                avg_sig = cov_df['avg_signal'].iloc[0]
                report += f"- *RSRP / Signal Quality:* Detected {pts} poor signal points in the vicinity. Average Signal Strength: {avg_sig:.1f} dBm.\n"
                report += "- *Terrain Warning:* If neighbors are healthy but this site has blind spots, cross-reference local terrain (buildings, hills) affecting propagation.\n\n"
            else:
                report += "- *RSRP / Signal Quality:* ⚠️ LIVE RF TELEMETRY UNAVAILABLE FOR THIS REGION.\n"
                report += "- *Engineering Note:* Currently, precise coverage hole and signal quality mapping is only available for the Kepong test area. Geospatial RF data for this specific site's region is not yet ingested into the database and will be implemented in a future update. Cannot currently confirm the presence of blind spots.\n\n"

        # ==========================================
        # STEP 3: CONGESTION & CAPEX BOQ
        # ==========================================
        report += "**Step 3: Capacity, Congestion & CAPEX BoQ**\n"

        cap_sql = f"""
            SELECT
                ca.zoom_sector_id, ca.eric_prb_util_rate, ca.eric_dl_user_ip_thpt, ca.congested,
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

            for _, row in cap_df.iterrows(): # Will only loop through the exact number of sectors for the latest week
                sec = row['zoom_sector_id']
                prb = row['eric_prb_util_rate']
                thpt = row['eric_dl_user_ip_thpt']
                is_cong = row['congested']
                upg_case = str(row['suggested_upgrade_case'])
                cost = float(row['estimated_total_capex_rm']) if pd.notna(row['estimated_total_capex_rm']) else 0.0

                status = "🔴 CONGESTED" if is_cong else "🟢 HEALTHY"
                report += f"  - **Sector {sec}**: {status} (PRB: {prb:.1f}%, Thpt: {thpt:.1f} Mbps)\n"

                if is_cong and upg_case.lower() not in ['nan', 'none', '']:
                    report += f"    - *BoQ Decision:* {upg_case}\n"
                    report += f"    - *CAPEX Required:* RM {cost:,.2f}\n"

        # ==========================================
        # STEP 4: PREDICTIVE ML FORECAST & GRAPH
        # ==========================================
        report += "\n**Step 4: Predictive Capacity Forecast**\n"

        fc_sql = f"""
            SELECT zoom_sector_id, MAX(month) as max_month,
                   MAX(predicted_eric_prb_util_rate) as max_prb,
                   MIN(predicted_eric_dl_user_ip_thpt) as min_thpt
            FROM forecast_results
            WHERE UPPER(zoom_sector_id) LIKE '{base_site}%'
            AND CAST(year AS VARCHAR) = '{year}'
            GROUP BY zoom_sector_id
            ORDER BY max_prb DESC
        """
        fc_df = wr.athena.read_sql_query(sql=fc_sql, database=ATHENA_DATABASE, s3_output=S3_STAGING_DIR, boto3_session=aws_session, ctas_approach=False)

        if fc_df.empty:
            report += f"- No ML forecast data available for {base_site} to project future degradation.\n"
        else:
            worst_sec = fc_df.iloc[0]['zoom_sector_id']
            worst_prb = fc_df.iloc[0]['max_prb']
            worst_thpt = fc_df.iloc[0]['min_thpt']

            report += f"- *Predictive Analysis:* The ML model projects continued degradation. Without CAPEX injection, Sector {worst_sec} will peak at {worst_prb:.1f}% PRB utilization, crashing throughput down to {worst_thpt:.1f} Mbps.\n"
            report += "- *Business Impact:* Detail the user experience story here (e.g., massive customer churn, complete inability to stream video, failed voice-over-LTEcalls) if these numbers are reached.\n"

        # THE CRITICAL GRAPH IFRAME INJECTION
        report += "\n**INSTRUCTIONS FOR AI:**\n"
        report += "1. Summarize Steps 1, 2, and 3 clearly. Acknowledge data unavailability gracefully where instructed.\n"
        report += "2. Transition into Step 4 by telling a compelling 'Forecast Story' about what will happen to the network and users if the upgrades are not implemented,using the Predictive Analysis data above.\n"
        report += "3. You MUST copy and paste the following HTML iframe block exactly as it is at the very bottom of your final response so the user can see the forecast graph:\n\n"
        report += f"<br><iframe src='/plot_page?site_id={base_site}' width='100%' height='550px' style='border: 1px solid #e5e7eb; border-radius: 8px; margin-top: 15px; background: white;'></iframe>\n"

        return report

    except Exception as e:
        return f"Error executing L2/L3 diagnostic: {str(e)}"

SYSTEM_PROMPT = """You are the Principal Architect for NetAlytics, an enterprise-grade AI assistant specialized in telecommunications capacity management, RF performance analytics, and CAPEX optimization.

CRITICAL TELECOM GLOSSARY & KNOWLEDGE BASE:
- PRB (Physical Resource Block): The fundamental unit of radio frequency allocation. High PRB (e.g., >80% Urban, >92% Outside) means the airwaves are fully congested, leading to queuing and delays.
- Thpt (Throughput): Measured in Mbps. Low throughput means users are experiencing severe buffering and slow data speeds.
- User Count (Max RRC / Active Users): The volume of devices connected to a sector. High users (e.g., >120) physically exhaust the base station's processing capabilities.
- Area Target (Urban/KMC vs Outside): Urban areas have stricter thresholds (80% PRB / 5 Mbps) because of higher density expectations. Outside/Rural areas have relaxed thresholds (92% PRB / 3 Mbps).
- BAU (Business As Usual) vs NIC (Network Improvement Cluster): NIC indicates areas selected for proactive investment, meaning throughput expectations are higher (7 Mbps target).
- Coverage Hole / Blind Spot: Areas where signal drops below acceptable dBm levels, causing dropped calls.
- Priority Scale:
    * CRITICAL: 3 out of 3 KPIs breached. Immediate CAPEX upgrade required (e.g., adding layers or Massive MIMO).
    * MODERATE: 2 out of 3 KPIs breached. High risk of severe degradation.
    * LOW: 1 out of 3 KPIs breached. Minor degradation, monitor closely.
- CAPEX: Capital Expenditure (cost of upgrading telecom hardware).
- CCTV Planning Pipeline: An enterprise tool that takes Building polygons, Parking polygons, Pole points, Camera Specs, and Offsets to automatically generate optimal camera placements (FOV wedges) using Hex Grid spacing.
- Illegal Bitcoin Mining Analyser: A triangulation tool. It uses 2-Point or 3-Point intersection between highly congested cell sites to find suspected mining locations. It automatically maps nearby commercial/industrial buildings and electrical substations (which miners need for heavy power usage).
- 3D Digital Twin (Cesium): A 3D view of the network showing tower heights, 3D building extrusions, and exact sector beam lengths.
- Map Overlays: The map supports 5G (800m), 4G (3km), 3G (30km), 2G (30km), TomTom Live Traffic, and Coverage Holes (MR = Squares, Ookla = Triangles).
- Metabase Dashboard: A deep-dive analytics engine available via the 'Enterprise Use Cases' dropdown.

WARNING: DO NOT CALCULATE THESE METRICS OR ASSIGN PRIORITIES YOURSELF. THE PYTHON TOOLS ALREADY DO THE MATH. USE THIS GLOSSARY STRICTLY TO EXPLAIN THE "WHY" BEHIND THE TOOL'S OUTPUT.

STRICT RULES (READ CAREFULLY):
1. SPECIFIC SITE TROUBLESHOOTING: Use `diagnose_site_health`. This is a master tool that runs a 3-step triage (Power, Neighbors/RF, Congestion/CAPEX). You MUST explicitlywalk the user through all 3 steps in your response, explaining the findings of each stage.
2. GLOBAL CONGESTION: Use `analyze_network_congestion_story`. You MUST explicitly state the Year and Week of the data you are summarizing in your very first sentence. Summarize the metrics exactly as the tool provides them. DO NOT MAKE UP NUMBERS FOR PRB OR THROUGHPUT.
3. FUTURE PREDICTIONS: If asked about future performance, forecasts, or predictions, use `get_capacity_forecast`.
4. BLIND SPOTS: If asked about coverage holes, bad signal, or blind spots, use `analyze_coverage_holes`.
5. UI NAVIGATION: Use `get_platform_navigation` for UI questions.
6. PROVIDE REASONING: Base your exact numbers strictly on the tool's output. Then, use the Glossary to explain *why* the network is behaving that way (e.g., explain how ahigh user count causes the low throughput the tool reported).
7. NEVER mention your internal Python tools to the user.
8. If the user asks about camera placements or security, guide them to the CCTV Planning Tool.
9. If the user asks about unexplained high data usage or triangulation, guide them to the Illegal Bitcoin Mining tool.
10. DEFINITIONS & THEORY: If the user asks for a general definition, theory, or telecom concept (e.g., 'what is congestion', 'how is X defined'), you MUST use the `search_telecom_manuals` tool and structure your response in two distinct parts:
    - Part 1: Provide the theoretical/textbook explanation based STRICTLY on the results from the search tool.
    - Part 2: Create a heading called "CelcomDigi Criteria" and explain exactly how this concept is measured internally using the thresholds from your CRITICAL TELECOM GLOSSARY (e.g., the specific PRB and Throughput limits for Urban vs Rural).
11. ML SLR FORECAST STORY: If the user asks about the SLR Forecast, quarterly predictions, or what happens if we do not manage congestion, use `analyze_quarterly_slr_forecast`. Deliver a dramatic, executive-level narrative. Emphasize the quarter-by-quarter degradation, the exact user experience impact, and the cost of inaction. ALWAYS include the HTML iframe exactly as the tool provides it at the end of your response so the graph renders in the chat.
12. CONVERSATIONAL DRIVER: You are an interactive assistant, not a static dictionary. ALWAYS end your response with a single, relevant follow-up question to keep the conversation moving. Guide the user to your other tools. For example: if you define congestion, ask "Would you like me to check the congestion status of a specific site?" If you summarize network health, ask "Would you like to see the predictive forecast for next quarter?"
13. METABASE STORYTELLER: If the user asks you to "explain the dashboard", "read Metabase", or "summarize dashboard X", you MUST use the `analyze_metabase_dashboard` tool. Pass the ID of the dashboard (e.g., '1'). Once the tool returns the raw chart data, you must write a beautiful, easy-to-read narrative explaining the trends and numbers found in the charts.
14. REPETITIVE QUESTIONS: You must NEVER point out that a user is repeating themselves, and NEVER state that you have already answered a question. Treat every single prompt with fresh enthusiasm, as if it is the very first time they are asking it. If a user asks about a topic you just explained, simply provide a rich, detailed, and comprehensive answer using a new analytical angle or fresh phrasing, completely ignoring the fact that it is a repeat. Never express frustration or use phrases like "As I mentioned before."
15. NO TOPOLOGY ASSUMPTIONS: When a tool provides a list of nearest neighbor sites and their distances, you MUST NOT infer or invent their RF relationship (e.g., do not call them "handover partners", "secondary layers", or "redundancy"). Simply state that they are the nearest active neighbors and provide their exact distances.
"""

llm = ChatLiteLLM(
    model="litellm_proxy/global.anthropic.claude-sonnet-4-20250514-v1:0",
    api_key=os.environ.get("LITELLM_API_KEY", "sk-hatoBbI4dupFLwIPj3pzCQ"), # Moved to env var for safety
    api_base="https://gateway.ai.celcomdigi.tech/",
    temperature=0.6,
    model_kwargs={"caching": False}
)

# ADDED THE TWO NEW TOOLS TO THIS ARRAY!
tools = [get_site_capacity,
        analyze_network_congestion_story,
        get_capex_pricing_info,
        get_platform_navigation,
        get_capacity_forecast,
        analyze_coverage_holes,
        search_telecom_manuals,
        analyze_quarterly_slr_forecast,
        analyze_metabase_dashboard,
        diagnose_site_health
        ]

# 1. Create the base agent without checkpointers or modifiers to completely bypass library version issues
agent_executor = create_react_agent(llm, tools)

# 2. Setup PostgreSQL Memory Table (Runs once on boot)
def init_memory_db():
    try:
        conn = psycopg2.connect(
            host=os.getenv('DB_HOST', 'vibe_db'),
            database=os.getenv('DB_NAME', 'vibe_db'),
            user=os.getenv('DB_USER', 'postgres'),
            password=os.getenv('DB_PASSWORD', '1234'),
            port=os.getenv('DB_PORT', '5432')
        )
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS chat_memory (
                thread_id VARCHAR(255) PRIMARY KEY,
                history JSONB
            );
        ''')
        conn.commit()
        cursor.close()
        conn.close()
        print("[System] Chat memory database initialized successfully.")
    except Exception as e:
        print(f"[Memory DB Error] {e}")

init_memory_db()

def run_netalytics_agent(user_message: str, week: str, region: str, operator: str, cluster: str, thread_id: str) -> str:
    try:
        # FIX 2: Cleaned prompt for natural conversation flow. Removed cache buster.
        contextual_prompt = f"{user_message}\n\n[Hidden Context - Active UI Filters: Week: {week}, Region: {region}, Operator: {operator}, Cluster: {cluster}]"

        conn = psycopg2.connect(
            host=os.getenv('DB_HOST', 'vibe_db'),
            database=os.getenv('DB_NAME', 'vibe_db'),
            user=os.getenv('DB_USER', 'postgres'),
            password=os.getenv('DB_PASSWORD', '1234'),
            port=os.getenv('DB_PORT', '5432')
        )
        cursor = conn.cursor()

        cursor.execute("SELECT history FROM chat_memory WHERE thread_id = %s", (thread_id,))
        result = cursor.fetchone()

        if result:
            # FIX 3: Bulletproof JSON parsing! This stops the "engine offline" crashes.
            raw_history = result[0]
            if isinstance(raw_history, str):
                raw_history = json.loads(raw_history)
            chat_history = messages_from_dict(raw_history)
        else:
            chat_history = [SystemMessage(content=SYSTEM_PROMPT)]

        chat_history.append(HumanMessage(content=contextual_prompt))

        inputs = {"messages": chat_history}
        response = agent_executor.invoke(inputs)

        updated_history = response["messages"]
        history_json = json.dumps(messages_to_dict(updated_history))

        cursor.execute('''
            INSERT INTO chat_memory (thread_id, history)
            VALUES (%s, %s)
            ON CONFLICT (thread_id) DO UPDATE SET history = EXCLUDED.history;
        ''', (thread_id, history_json))

        conn.commit()
        cursor.close()
        conn.close()

        return updated_history[-1].content

    except Exception as e:
        print(f"[Agent Error] {e}")
        return "My analytical engine is currently offline. Please try again."
