import re
import pandas as pd
import awswrangler as wr
from langchain_core.tools import tool
from app.config import ATHENA_DATABASE, S3_STAGING_DIR
from app.extensions import aws_session


@tool
def get_site_capacity(site_id: str, week: str = "All") -> str:
    """
    Use this tool to find out if a specific cell site is congested, its PRB utilization,
    throughput, user count, AND required CAPEX upgrades.
    Provide the site_id (e.g., 'KUL_01' or '1712H') and the week.
    """
    site_id = site_id.strip().upper()
    week_num = re.sub(r'[^0-9]', '', str(week))

    try:
        yr_df = wr.athena.read_sql_query(
            "SELECT MAX(year) as max_yr FROM congestion_analysis",
            database=ATHENA_DATABASE, s3_output=S3_STAGING_DIR,
            boto3_session=aws_session, ctas_approach=False,
        )
        year = str(yr_df['max_yr'].iloc[0])

        print(f"[Agent Tool] Querying Athena for Site: {site_id}, Year: {year}, Week: {week_num}...")

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
            boto3_session=aws_session, ctas_approach=False,
        )

        if df.empty:
            return f"I could not find any capacity data for site {site_id} in the AWS database."

        if week_num:
            df = df[df['week'].astype(str) == str(week_num)]

        if df.empty:
            return f"Site {site_id} exists, but there is no data recorded for Week {week_num}."

        df = df.head(6)
        region  = df['region'].iloc[0]  if pd.notna(df['region'].iloc[0])  else "Unknown Region"
        cluster = df['cluster'].iloc[0] if pd.notna(df['cluster'].iloc[0]) else "Unknown Cluster"
        result_str = f"Detailed Capacity & CAPEX Analysis for Site {site_id} (Region: {region}, Cluster: {cluster}, Week {week_num}):\n\n"

        for _, row in df.iterrows():
            sec  = row['zoom_sector_id']
            prb  = round(float(row['eric_prb_util_rate']), 2) if pd.notna(row['eric_prb_util_rate']) else 0.0
            thpt = round(float(row['eric_dl_user_ip_thpt']), 2) if pd.notna(row['eric_dl_user_ip_thpt']) else 0.0
            users_rrc = float(row['eric_max_rrc_user']) if pd.notna(row['eric_max_rrc_user']) else 0.0
            users_act = float(row['max_active_user'])   if pd.notna(row['max_active_user'])   else 0.0
            users     = max(users_rrc, users_act)
            upg_case  = str(row['suggested_upgrade_case'])  if pd.notna(row['suggested_upgrade_case'])  else "None"
            upg_cost  = float(row['estimated_total_capex_rm']) if pd.notna(row['estimated_total_capex_rm']) else 0.0

            area     = str(row['area_target']).lower()
            mode     = str(row['bau_nic']).lower()
            is_urban = 'urban' in area or 'kmc' in area

            prb_thresh  = 80.0  if is_urban else 92.0
            thpt_thresh = (7.0 if 'nic' in mode else 5.0) if is_urban else 3.0
            user_thresh = 120.0

            exceeded_count, reasons = 0, []
            if prb >= prb_thresh:
                exceeded_count += 1
                reasons.append(f"PRB ({prb}%) >= limit ({prb_thresh}%)")
            if thpt > 0 and thpt <= thpt_thresh:
                exceeded_count += 1
                reasons.append(f"Throughput ({thpt} Mbps) <= limit ({thpt_thresh} Mbps)")
            if users >= user_thresh:
                exceeded_count += 1
                reasons.append(f"Users ({int(users)}) >= limit (120)")

            if exceeded_count == 3:   priority = "CRITICAL PRIORITY (Fully Congested)"
            elif exceeded_count == 2: priority = "MODERATE PRIORITY (At Risk)"
            elif exceeded_count == 1: priority = "LOW PRIORITY (Minor Degradation)"
            else:                     priority = "HEALTHY"

            result_str += f"Sector {sec}: {priority}\n"
            if exceeded_count > 0:
                result_str += f"  - Exceeded {exceeded_count}/3 Thresholds. Reasons: {', '.join(reasons)}\n"
                if upg_case not in ("None", "nan", ""):
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
    """Use this tool when the user asks global questions like 'how many congested sites are there?',
    'how many congested sectors?', or network health. If the user asks for a specific year, pass it here."""
    try:
        year_digits = re.sub(r'[^0-9]', '', str(year))
        if not year_digits:
            yr_df = wr.athena.read_sql_query(
                "SELECT MAX(year) as max_yr FROM congestion_analysis",
                database=ATHENA_DATABASE, s3_output=S3_STAGING_DIR,
                boto3_session=aws_session, ctas_approach=False,
            )
            target_year = str(yr_df['max_yr'].iloc[0])
        else:
            target_year = year_digits

        week_digits = re.sub(r'[^0-9]', '', str(week).lower())
        print(f"[Agent Tool] Building Congestion Story for Year: {target_year}, Week: {week_digits or 'All'}, Region: {region}, Cluster: {cluster}...")

        sql = f"SELECT zoom_sector_id, region, cluster, week, congested, eric_prb_util_rate, eric_dl_user_ip_thpt FROM congestion_analysis WHERE CAST(year AS VARCHAR) = '{target_year}'"
        if region and region != "All":
            sql += f" AND UPPER(region) = '{region.upper()}'"
        if cluster and cluster != "All":
            sql += f" AND cluster = '{cluster}'"

        df = wr.athena.read_sql_query(sql=sql, database=ATHENA_DATABASE, s3_output=S3_STAGING_DIR, boto3_session=aws_session, ctas_approach=False)

        if df.empty:
            return f"No network data was found in AWS Athena for Year {target_year} matching those filters."

        if week_digits:
            df['clean_week'] = df['week'].astype(str).str.split('.').str[0]
            df = df[df['clean_week'] == week_digits]
            if df.empty:
                return f"Data exists for Year {target_year}, but no records were found specifically for Week {week_digits} with those filters."

        df = df.drop_duplicates(subset=['zoom_sector_id']).copy()
        df['base_site'] = df['zoom_sector_id'].astype(str).str.split('_').str[0].str.split('-').str[0]
        total_network_sites   = df['base_site'].nunique()
        total_network_sectors = df['zoom_sector_id'].nunique()

        df['is_cong'] = df['congested'].isin([True, 1, 1.0, "True", "true", "1", "1.0", "yes", "Yes"])
        cong_df = df[df['is_cong']].copy()
        total_cong_sectors = cong_df['zoom_sector_id'].nunique()
        total_cong_sites   = cong_df['base_site'].nunique() if total_cong_sectors > 0 else 0

        if total_cong_sectors == 0:
            return f"IMPORTANT: You MUST tell the user: For Year {target_year}, Week {week_digits or 'All'}, we analyzed {total_network_sites} sites and found exactly 0 congested sectors. The network is healthy!"

        site_pct   = (total_cong_sites   / total_network_sites)   * 100 if total_network_sites   > 0 else 0
        sector_pct = (total_cong_sectors / total_network_sectors) * 100 if total_network_sectors > 0 else 0

        story  = f"IMPORTANT: You MUST start your response by saying 'Here is the data for Year {target_year}, Week {week_digits or 'All'}:'\n\n"
        story += f"Out of **{total_network_sectors} total sectors** (across **{total_network_sites} physical sites**):\n\n"
        story += f"Currently, **{total_cong_sectors} sectors** ({sector_pct:.1f}%) are congested, affecting **{total_cong_sites} physical sites** ({site_pct:.1f}%).\n\n"
        story += "🚨 **Regional Breakdown:**\n"
        for reg, count in cong_df['region'].value_counts().items():
            if pd.notna(reg) and str(reg).strip():
                story += f"- **{reg.upper()}**: {count} congested sectors\n"

        story += "\n🔥 **Worst Affected Clusters:**\n"
        for clus, count in cong_df['cluster'].value_counts().head(3).items():
            if pd.notna(clus):
                story += f"- **Cluster {clus}**: {count} congested sectors\n"

        if total_cong_sectors > 0:
            story += "\n⚠️ **Specific Congested Sites & Sectors Identified:**\n"
            cong_df['eric_prb_util_rate'] = pd.to_numeric(cong_df['eric_prb_util_rate'], errors='coerce')
            cong_df = cong_df.sort_values(by='eric_prb_util_rate', ascending=False)
            for _, row in cong_df.head(10).iterrows():
                prb  = row['eric_prb_util_rate']
                thpt = row['eric_dl_user_ip_thpt']
                story += (
                    f"- **Site {row['base_site']}** (Sector: {row['zoom_sector_id']}) | "
                    f"Location: {row['region'] or 'Unknown'} - Cluster {row['cluster'] or 'Unknown'} | "
                    f"PRB: {f'{prb:.1f}%' if pd.notna(prb) else 'N/A'} | "
                    f"Thpt: {f'{thpt:.1f} Mbps' if pd.notna(thpt) else 'N/A'}\n"
                )
            if total_cong_sectors > 10:
                story += f"- *(...and {total_cong_sectors - 10} more sectors. Advise the user to filter down!)*\n"

        story += "\nINSTRUCTIONS FOR AI: Clearly list the specific congested sites/sectors provided above so the user knows exactly where the problems are."
        return story

    except Exception as e:
        return f"Error analyzing data: {e}"
