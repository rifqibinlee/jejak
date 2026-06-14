import pandas as pd
import awswrangler as wr
import requests as req
from langchain_core.tools import tool
from app.config import ATHENA_DATABASE, S3_STAGING_DIR
from app.extensions import aws_session


@tool
def analyze_coverage_holes(site_id: str = "ALL") -> str:
    """Use this tool to find coverage holes, blind spots, or areas with bad signal."""
    site_id = site_id.strip().upper()
    try:
        print(f"[Agent Tool] Analyzing Coverage Holes for: {site_id}...")
        sql = """
            SELECT cluster_id, serving_cell, data_source, COUNT(*) as point_count, AVG(signal_strength) as avg_signal
            FROM coverage_holes_clustered WHERE cluster_id != -1
        """
        if site_id != "ALL":
            sql += f" AND UPPER(serving_cell) LIKE '{site_id}%'"
        sql += " GROUP BY cluster_id, serving_cell, data_source ORDER BY point_count DESC LIMIT 5"

        df = wr.athena.read_sql_query(sql=sql, database=ATHENA_DATABASE, s3_output=S3_STAGING_DIR, boto3_session=aws_session, ctas_approach=False)

        if df.empty:
            return f"Great news! No major coverage holes or blind spots found in the database for {site_id}."

        story = f"📡 **Top Coverage Holes & Blind Spots for {site_id}:**\n"
        for _, row in df.iterrows():
            story += (
                f"- **Cluster {row['cluster_id']}** (Detected by {row['data_source']}): "
                f"{row['point_count']} poor signal points. "
                f"Average Signal: {row['avg_signal']:.1f} dBm. Serving Cell: {row['serving_cell']}\n"
            )
        return story

    except Exception as e:
        return f"Error analyzing coverage holes: {e}"


@tool
def run_atom_coverage_analysis(region: str = "All", week: str = "All") -> str:
    """
    Use this tool when the user asks to run ATOM, detect coverage gaps, find clusters of bad signal,
    or asks about coverage hole analysis. ATOM auto-tunes DBSCAN parameters using KNN and groups
    poor-signal MR points into geographic clusters.
    Optionally pass a region (e.g. 'KL') or week number.
    """
    try:
        print(f"[Agent Tool] Triggering ATOM pipeline — region={region}, week={week}...")
        payload = {"region": region if region != "All" else "All"}
        if week and week != "All":
            payload["week"] = str(week)

        res = req.post(
            "http://localhost:5000/api/atom/run",
            json=payload,
            timeout=120,
            cookies={"session": "agent-internal"},
        )
        if res.status_code != 200:
            return f"ATOM pipeline returned HTTP {res.status_code}. Check server logs."

        data = res.json()
        if not data.get("success"):
            return f"ATOM pipeline error: {data.get('error', 'Unknown error')}"

        params   = data["params"]
        clusters = data["cluster_summaries"]
        n_c      = data["n_clusters"]
        n_noise  = data["n_noise"]
        n_pts    = data["total_points"]

        story = (
            f"ATOM Analysis Complete:\n"
            f"- Total MR bad-signal points analysed: {n_pts:,} (RSRP ≤ -115 dBm)\n"
            f"- AutoDBSCAN parameters: eps = {params['eps']}, minPts = {params['min_pts']}\n"
            f"- Clusters detected: {n_c}\n"
            f"- Noise/isolated points: {n_noise:,}\n\n"
        )

        if clusters:
            story += "Top Clusters (by size):\n"
            for c in sorted(clusters, key=lambda x: x["point_count"], reverse=True)[:5]:
                story += (
                    f"  - Cluster {c['cluster_id']}: {c['point_count']} points, "
                    f"avg RSRP {c['avg_rsrp']} dBm, "
                    f"centered at ({c['center_lat']}, {c['center_lng']})\n"
                )
        else:
            story += "No significant coverage gap clusters were found.\n"

        story += (
            "\nINSTRUCTIONS FOR AI: Tell the user the ATOM analysis is complete and the clusters are now "
            "visible on the map as coloured convex hull polygons. Each colour represents a distinct coverage "
            "gap zone. They can click any polygon on the map for details."
        )
        return story

    except Exception as e:
        return f"Error running ATOM analysis: {str(e)}"
