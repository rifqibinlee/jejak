from langchain_core.tools import tool


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
