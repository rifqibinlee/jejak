import os
import requests
from langchain_core.tools import tool


@tool
def analyze_metabase_dashboard(dashboard_id: str) -> str:
    """
    Use this tool when the user asks you to explain, summarize, or read a Metabase dashboard.
    Provide the dashboard_id (e.g., '1', '2').
    """
    mb_url  = os.getenv('METABASE_INTERNAL_URL', 'http://metabase:3000')
    mb_user = os.getenv('METABASE_USER', '')
    mb_pass = os.getenv('METABASE_PASSWORD', '')

    if not mb_user or not mb_pass:
        return "Metabase credentials are not configured. Set METABASE_USER and METABASE_PASSWORD environment variables."

    try:
        print(f"[Agent Tool] Connecting to Metabase API for Dashboard {dashboard_id} at {mb_url}...")

        session_res = requests.post(
            f"{mb_url}/api/session",
            json={"username": mb_user, "password": mb_pass},
            timeout=10,
        )
        if session_res.status_code != 200:
            return "Failed to authenticate with Metabase API. Please check credentials."

        mb_token = session_res.json().get("id")
        headers  = {"X-Metabase-Session": mb_token}

        dash_res = requests.get(f"{mb_url}/api/dashboard/{dashboard_id}", headers=headers, timeout=10)
        if dash_res.status_code != 200:
            return f"Failed to find Dashboard {dashboard_id} in Metabase."

        dash_data = dash_res.json()
        story = f"Raw Data extracted from Metabase Dashboard: '{dash_data.get('name', 'Unknown')}'\n\n"

        for dashcard in dash_data.get('dashcards', []):
            card = dashcard.get('card', {})
            if not card or 'id' not in card:
                continue
            card_id   = card.get('id')
            card_name = card.get('name', 'Unknown Chart')
            query_res = requests.post(f"{mb_url}/api/card/{card_id}/query/json", headers=headers, timeout=15)
            if query_res.status_code == 200:
                story += f"--- Chart: {card_name} ---\nData Snippet: {query_res.json()[:5]}\n\n"

        story += "INSTRUCTIONS FOR AI: Read the raw JSON data extracted from these Metabase charts. Write a comprehensive, executive-level summary explaining what these charts mean for the network's performance."
        return story

    except Exception as e:
        return f"Error connecting to Metabase API: {str(e)}"
