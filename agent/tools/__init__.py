from agent.tools.congestion  import get_site_capacity, analyze_network_congestion_story
from agent.tools.forecast    import get_capacity_forecast, analyze_quarterly_slr_forecast
from agent.tools.coverage    import analyze_coverage_holes, run_atom_coverage_analysis
from agent.tools.knowledge   import search_telecom_manuals
from agent.tools.navigation  import get_platform_navigation, get_capex_pricing_info
from agent.tools.metabase    import analyze_metabase_dashboard
from agent.tools.diagnostic  import diagnose_site_health

ALL_TOOLS = [
    get_site_capacity,
    analyze_network_congestion_story,
    get_capex_pricing_info,
    get_platform_navigation,
    get_capacity_forecast,
    analyze_coverage_holes,
    search_telecom_manuals,
    analyze_quarterly_slr_forecast,
    analyze_metabase_dashboard,
    diagnose_site_health,
    run_atom_coverage_analysis,
]
