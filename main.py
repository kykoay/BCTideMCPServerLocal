"""
BC Water Tides MCP Server

This server provides access to the Integrated Water Level System (IWLS) API through 
the Model Context Protocol (MCP). It retrieves forecasted tide heights for Canadian
monitoring stations.

Features:
- Look up station by name
- Get 7-day tide height predictions
- Hourly resolution data
- PST timezone conversion support
- Tide height plotting
- Comprehensive error handling
"""

import os
import io
import base64
import logging
import ssl
from typing import Optional, List
from datetime import datetime, timedelta, timezone

import httpx
import pytz
import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend for server
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from pydantic import BaseModel, Field
from mcp.server.fastmcp import FastMCP


# Configure logging for STDIO transport (writes to stderr, not stdout)
# Set to WARNING to reduce noise during MCP communication
logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Suppress httpx info logging
logging.getLogger("httpx").setLevel(logging.WARNING)

# Create FastMCP server
mcp = FastMCP(
    name="bc-water-tides",
    instructions="A Model Context Protocol server for accessing Canadian tide predictions from the Integrated Water Level System (IWLS) API. Provides tools to get forecasted tide heights for monitoring stations."
)

# IWLS API base URL
IWLS_BASE_URL = "https://api-iwls.dfo-mpo.gc.ca/api/v1"


class TideDataPoint(BaseModel):
    """Represents a single tide measurement/prediction"""
    event_date: str
    value: float
    qc_flag_code: str
    time_series_id: str


class StationInfo(BaseModel):
    """Represents a monitoring station"""
    id: str
    code: str
    official_name: str
    latitude: float
    longitude: float
    operating: bool


async def make_iwls_request(endpoint: str, params: Optional[dict] = None) -> dict | list:
    """Make a request to the IWLS API"""
    url = f"{IWLS_BASE_URL}/{endpoint}"
    
    try:
        # Create SSL context that doesn't verify certificates (the IWLS API has certificate issues on some systems)
        async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
            logger.info(f"Making request to {url} with params: {params}")
            response = await client.get(url, params=params)
            
            if response.status_code == 200:
                data = response.json()
                logger.info(f"Successfully retrieved data from {endpoint}")
                return data
            else:
                error_msg = f"IWLS API error: {response.status_code}"
                try:
                    error_data = response.json()
                    error_msg += f" - {error_data}"
                except:
                    error_msg += f" - {response.text}"
                
                logger.error(error_msg)
                raise Exception(error_msg)
                
    except httpx.RequestError as e:
        error_msg = f"Network error connecting to IWLS API: {str(e)}"
        logger.error(error_msg)
        raise Exception(error_msg)


async def find_station_by_name(station_name: str) -> Optional[dict]:
    """Find a station by its name (partial match, case-insensitive)"""
    stations = await make_iwls_request("stations")
    
    # Search for station by name (case-insensitive partial match)
    station_name_lower = station_name.lower()
    
    for station in stations:
        official_name = station.get("officialName", "").lower()
        alternative_name = station.get("alternativeName", "").lower() if station.get("alternativeName") else ""
        
        if station_name_lower in official_name or station_name_lower in alternative_name:
            return station
    
    return None


def get_time_range() -> tuple[str, str]:
    """Calculate timeFrom (midnight UTC today) and timeTo (7 days later)"""
    now = datetime.now(timezone.utc)
    time_from = now.replace(hour=0, minute=0, second=0, microsecond=0)
    time_to = time_from + timedelta(days=7)
    
    return (
        time_from.strftime("%Y-%m-%dT%H:%M:%SZ"),
        time_to.strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def convert_utc_to_pst(utc_timestamp: str) -> str:
    """Convert a UTC timestamp string to PST/PDT timezone"""
    # Parse the UTC timestamp
    dt_utc = datetime.fromisoformat(utc_timestamp.replace("Z", "+00:00"))
    
    # Convert to Pacific Time (handles PST/PDT automatically)
    pacific_tz = pytz.timezone('America/Vancouver')
    dt_pacific = dt_utc.astimezone(pacific_tz)
    
    return dt_pacific.strftime("%Y-%m-%dT%H:%M:%S %Z")


@mcp.tool()
async def get_tide_forecast(
    station_name: str = Field(
        description="The name of the monitoring station (e.g., 'Point Atkinson', 'Vancouver', 'Victoria'). Partial matching is supported."
    )
) -> dict:
    """
    Get 7-day tide height forecast for a Canadian monitoring station.
    
    This tool retrieves predicted water levels for the next 7 days from the 
    Integrated Water Level System (IWLS) API. Data is returned at hourly intervals.
    
    The tool first searches for the station by name (partial match supported),
    then retrieves the tide predictions starting from midnight UTC today.
    
    Returns:
    - Station information (name, ID, coordinates)
    - List of hourly tide predictions with timestamps and water levels (in meters)
    
    Examples of station names:
    - "Point Atkinson" (near Vancouver)
    - "Victoria"
    - "Tofino"
    - "Prince Rupert"
    """
    
    try:
        # Step 1: Find the station by name
        logger.info(f"Searching for station: {station_name}")
        station = await find_station_by_name(station_name)
        
        if not station:
            return {
                "success": False,
                "error": f"Station '{station_name}' not found. Please check the station name and try again.",
                "station_name_searched": station_name
            }
        
        station_id = station["id"]
        official_name = station.get("officialName", "Unknown")
        logger.info(f"Found station: {official_name} (ID: {station_id})")
        
        # Check if the station has the wlp (water level predictions) time series
        time_series = station.get("timeSeries", [])
        has_wlp = any(ts.get("code") == "wlp" for ts in time_series)
        
        if not has_wlp:
            return {
                "success": False,
                "error": f"Station '{official_name}' does not have tide prediction data available.",
                "station_name": official_name,
                "station_id": station_id,
                "available_time_series": [ts.get("code") for ts in time_series]
            }
        
        # Step 2: Calculate time range
        time_from, time_to = get_time_range()
        logger.info(f"Fetching tide data from {time_from} to {time_to}")
        
        # Step 3: Get tide prediction data
        params = {
            "time-series-code": "wlp",
            "from": time_from,
            "to": time_to,
            "resolution": "SIXTY_MINUTES"
        }
        
        tide_data = await make_iwls_request(f"stations/{station_id}/data", params)
        
        # Step 4: Format the response
        forecasts = []
        for point in tide_data:
            forecasts.append({
                "timestamp": point.get("eventDate"),
                "water_level_meters": point.get("value"),
                "qc_flag": point.get("qcFlagCode")
            })
        
        return {
            "success": True,
            "station": {
                "name": official_name,
                "id": station_id,
                "code": station.get("code"),
                "latitude": station.get("latitude"),
                "longitude": station.get("longitude"),
                "operating": station.get("operating", False)
            },
            "time_range": {
                "from": time_from,
                "to": time_to
            },
            "resolution": "SIXTY_MINUTES (hourly)",
            "total_predictions": len(forecasts),
            "forecasts": forecasts
        }
        
    except Exception as e:
        logger.error(f"Error getting tide forecast: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "station_name_searched": station_name
        }


@mcp.tool()
async def list_stations(
    search_term: Optional[str] = Field(
        default=None,
        description="Optional search term to filter stations by name. Leave empty to list all stations."
    ),
    operating_only: bool = Field(
        default=True,
        description="If true, only return currently operating stations."
    ),
    limit: int = Field(
        default=20,
        description="Maximum number of stations to return (1-100)."
    )
) -> dict:
    """
    List available tide monitoring stations.
    
    This tool retrieves a list of monitoring stations from the IWLS API.
    You can filter by name and operating status.
    
    Useful for discovering station names before using the get_tide_forecast tool.
    """
    
    if limit < 1 or limit > 100:
        limit = 20
    
    try:
        stations = await make_iwls_request("stations")
        
        # Filter stations
        filtered = []
        for station in stations:
            # Check operating status
            if operating_only and not station.get("operating", False):
                continue
            
            # Check if station has tide predictions
            time_series = station.get("timeSeries", [])
            has_wlp = any(ts.get("code") == "wlp" for ts in time_series)
            
            if not has_wlp:
                continue
            
            # Check search term
            if search_term:
                search_lower = search_term.lower()
                official_name = station.get("officialName", "").lower()
                alt_name = station.get("alternativeName", "").lower() if station.get("alternativeName") else ""
                
                if search_lower not in official_name and search_lower not in alt_name:
                    continue
            
            filtered.append({
                "name": station.get("officialName"),
                "alternative_name": station.get("alternativeName"),
                "id": station.get("id"),
                "code": station.get("code"),
                "latitude": station.get("latitude"),
                "longitude": station.get("longitude"),
                "operating": station.get("operating")
            })
            
            if len(filtered) >= limit:
                break
        
        return {
            "success": True,
            "search_term": search_term,
            "operating_only": operating_only,
            "total_matching": len(filtered),
            "stations": filtered
        }
        
    except Exception as e:
        logger.error(f"Error listing stations: {str(e)}")
        return {
            "success": False,
            "error": str(e)
        }


@mcp.tool()
async def get_tide_forecast_pst(
    station_name: str = Field(
        description="The name of the monitoring station (e.g., 'Point Atkinson', 'Vancouver', 'Victoria'). Partial matching is supported."
    ),
    date: Optional[str] = Field(
        default=None,
        description="Optional specific date to filter results (format: YYYY-MM-DD). If provided, only returns data for that date in PST. Leave empty to get all 7 days."
    )
) -> dict:
    """
    Get tide height forecast with timestamps converted to Pacific Standard Time (PST/PDT).
    
    This tool retrieves predicted water levels and converts all timestamps from UTC 
    to Pacific Time (America/Vancouver timezone), automatically handling PST/PDT transitions.
    
    Use this tool when you need tide data displayed in local BC time rather than UTC.
    
    Returns:
    - Station information (name, ID, coordinates)
    - List of hourly tide predictions with PST timestamps and water levels (in meters)
    - High and low tide information for the requested period
    
    Examples of station names:
    - "Point Atkinson" (near Vancouver)
    - "Campbell River"
    - "Victoria"
    - "Tofino"
    """
    
    try:
        # Step 1: Find the station by name
        logger.info(f"Searching for station: {station_name}")
        station = await find_station_by_name(station_name)
        
        if not station:
            return {
                "success": False,
                "error": f"Station '{station_name}' not found. Please check the station name and try again.",
                "station_name_searched": station_name
            }
        
        station_id = station["id"]
        official_name = station.get("officialName", "Unknown")
        logger.info(f"Found station: {official_name} (ID: {station_id})")
        
        # Check if the station has the wlp (water level predictions) time series
        time_series = station.get("timeSeries", [])
        has_wlp = any(ts.get("code") == "wlp" for ts in time_series)
        
        if not has_wlp:
            return {
                "success": False,
                "error": f"Station '{official_name}' does not have tide prediction data available.",
                "station_name": official_name,
                "station_id": station_id,
                "available_time_series": [ts.get("code") for ts in time_series]
            }
        
        # Step 2: Calculate time range
        time_from, time_to = get_time_range()
        logger.info(f"Fetching tide data from {time_from} to {time_to}")
        
        # Step 3: Get tide prediction data
        params = {
            "time-series-code": "wlp",
            "from": time_from,
            "to": time_to,
            "resolution": "SIXTY_MINUTES"
        }
        
        tide_data = await make_iwls_request(f"stations/{station_id}/data", params)
        
        # Step 4: Convert timestamps to PST and format the response
        pacific_tz = pytz.timezone('America/Vancouver')
        forecasts = []
        
        # Parse the optional date filter
        filter_date = None
        if date:
            try:
                filter_date = datetime.strptime(date, "%Y-%m-%d").date()
            except ValueError:
                return {
                    "success": False,
                    "error": f"Invalid date format: '{date}'. Please use YYYY-MM-DD format."
                }
        
        for point in tide_data:
            utc_timestamp = point.get("eventDate")
            water_level = point.get("value")
            
            # Convert to PST
            dt_utc = datetime.fromisoformat(utc_timestamp.replace("Z", "+00:00"))
            dt_pst = dt_utc.astimezone(pacific_tz)
            
            # Apply date filter if specified
            if filter_date and dt_pst.date() != filter_date:
                continue
            
            forecasts.append({
                "timestamp_utc": utc_timestamp,
                "timestamp_pst": dt_pst.strftime("%Y-%m-%dT%H:%M:%S %Z"),
                "date_pst": dt_pst.strftime("%Y-%m-%d"),
                "time_pst": dt_pst.strftime("%I:%M %p"),
                "water_level_meters": water_level,
                "qc_flag": point.get("qcFlagCode")
            })
        
        if not forecasts:
            return {
                "success": False,
                "error": f"No tide data found for date {date}" if date else "No tide data available",
                "station_name": official_name
            }
        
        # Calculate high and low tides
        levels = [f["water_level_meters"] for f in forecasts]
        min_level = min(levels)
        max_level = max(levels)
        min_idx = levels.index(min_level)
        max_idx = levels.index(max_level)
        
        low_tide = forecasts[min_idx]
        high_tide = forecasts[max_idx]
        
        return {
            "success": True,
            "timezone": "Pacific Time (PST/PDT)",
            "station": {
                "name": official_name,
                "id": station_id,
                "code": station.get("code"),
                "latitude": station.get("latitude"),
                "longitude": station.get("longitude"),
                "operating": station.get("operating", False)
            },
            "time_range": {
                "from_utc": time_from,
                "to_utc": time_to,
                "date_filter": date
            },
            "summary": {
                "low_tide": {
                    "water_level_meters": low_tide["water_level_meters"],
                    "time_pst": low_tide["time_pst"],
                    "date_pst": low_tide["date_pst"]
                },
                "high_tide": {
                    "water_level_meters": high_tide["water_level_meters"],
                    "time_pst": high_tide["time_pst"],
                    "date_pst": high_tide["date_pst"]
                },
                "tidal_range_meters": round(max_level - min_level, 2)
            },
            "resolution": "SIXTY_MINUTES (hourly)",
            "total_predictions": len(forecasts),
            "forecasts": forecasts
        }
        
    except Exception as e:
        logger.error(f"Error getting tide forecast: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "station_name_searched": station_name
        }


@mcp.tool()
async def plot_tide_forecast(
    station_name: str = Field(
        description="The name of the monitoring station (e.g., 'Point Atkinson', 'Campbell River', 'Victoria'). Partial matching is supported."
    ),
    date: str = Field(
        description="The specific date to plot (format: YYYY-MM-DD). The plot will show tide heights for this entire day in PST."
    ),
    save_path: Optional[str] = Field(
        default=None,
        description="Optional file path to save the plot image. If not provided, returns base64-encoded image data."
    ),
    title: Optional[str] = Field(
        default=None,
        description="Optional custom title for the plot. If not provided, uses default title with station name and date."
    )
) -> dict:
    """
    Generate a tide height plot showing water levels as a function of time in PST.
    
    This tool creates a visual chart of tide heights throughout a specified day,
    with timestamps displayed in Pacific Standard Time (PST/PDT).
    
    The plot includes:
    - Tide height curve with hourly data points
    - Highlighted high and low tide markers with annotations
    - Optimal diving zone indicator (low tide window)
    - Dark, ocean-themed styling
    
    Returns:
    - Plot image (either saved to file or as base64 data)
    - High and low tide summary information
    
    Use this tool when the user wants a visual representation of tide patterns
    for dive planning, fishing, boating, or other marine activities.
    """
    
    try:
        # Step 1: Find the station by name
        logger.info(f"Searching for station: {station_name}")
        station = await find_station_by_name(station_name)
        
        if not station:
            return {
                "success": False,
                "error": f"Station '{station_name}' not found. Please check the station name and try again.",
                "station_name_searched": station_name
            }
        
        station_id = station["id"]
        official_name = station.get("officialName", "Unknown")
        
        # Check if the station has the wlp time series
        time_series = station.get("timeSeries", [])
        has_wlp = any(ts.get("code") == "wlp" for ts in time_series)
        
        if not has_wlp:
            return {
                "success": False,
                "error": f"Station '{official_name}' does not have tide prediction data available."
            }
        
        # Parse and validate the date
        try:
            plot_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            return {
                "success": False,
                "error": f"Invalid date format: '{date}'. Please use YYYY-MM-DD format."
            }
        
        # Step 2: Get tide data (fetch a bit more to ensure we cover the full day in PST)
        time_from, time_to = get_time_range()
        
        params = {
            "time-series-code": "wlp",
            "from": time_from,
            "to": time_to,
            "resolution": "SIXTY_MINUTES"
        }
        
        tide_data = await make_iwls_request(f"stations/{station_id}/data", params)
        
        # Step 3: Filter data for the specified date in PST
        pacific_tz = pytz.timezone('America/Vancouver')
        times_pst = []
        levels = []
        
        for point in tide_data:
            utc_timestamp = point.get("eventDate")
            water_level = point.get("value")
            
            dt_utc = datetime.fromisoformat(utc_timestamp.replace("Z", "+00:00"))
            dt_pst = dt_utc.astimezone(pacific_tz)
            
            if dt_pst.date() == plot_date:
                times_pst.append(dt_pst)
                levels.append(water_level)
        
        if not times_pst:
            return {
                "success": False,
                "error": f"No tide data available for {date}. The date may be outside the 7-day forecast range.",
                "station_name": official_name
            }
        
        # Calculate high and low tides
        min_level = min(levels)
        max_level = max(levels)
        min_idx = levels.index(min_level)
        max_idx = levels.index(max_level)
        low_tide_time = times_pst[min_idx]
        high_tide_time = times_pst[max_idx]
        
        # Step 4: Create the plot
        plt.style.use('dark_background')
        fig, ax = plt.subplots(figsize=(14, 7))
        
        # Dark ocean-themed background
        fig.patch.set_facecolor('#0a1628')
        ax.set_facecolor('#0a1628')
        
        # Plot the tide curve with gradient fill
        ax.fill_between(times_pst, levels, alpha=0.4, color='#00d4ff')
        ax.plot(times_pst, levels, color='#00d4ff', linewidth=3, 
                marker='o', markersize=5, markerfacecolor='white', 
                markeredgecolor='#00d4ff', markeredgewidth=2)
        
        # Mark high and low tides
        ax.scatter([low_tide_time], [min_level], color='#ff6b6b', s=200, 
                   zorder=5, edgecolor='white', linewidth=2)
        ax.scatter([high_tide_time], [max_level], color='#4ecdc4', s=200, 
                   zorder=5, edgecolor='white', linewidth=2)
        
        # Annotate high and low tides
        ax.annotate(f'LOW TIDE\n{min_level:.2f}m\n{low_tide_time.strftime("%I:%M %p")}', 
                    xy=(low_tide_time, min_level), 
                    xytext=(low_tide_time + timedelta(hours=2), min_level + 0.8),
                    fontsize=11, fontweight='bold', color='#ff6b6b',
                    ha='center',
                    arrowprops=dict(arrowstyle='->', color='#ff6b6b', lw=2))
        
        ax.annotate(f'HIGH TIDE\n{max_level:.2f}m\n{high_tide_time.strftime("%I:%M %p")}', 
                    xy=(high_tide_time, max_level), 
                    xytext=(high_tide_time - timedelta(hours=2), max_level - 0.8),
                    fontsize=11, fontweight='bold', color='#4ecdc4',
                    ha='center',
                    arrowprops=dict(arrowstyle='->', color='#4ecdc4', lw=2))
        
        # Styling
        ax.set_xlabel('Time (PST)', fontsize=14, color='white', fontweight='bold')
        ax.set_ylabel('Water Level (meters)', fontsize=14, color='white', fontweight='bold')
        
        # Title
        formatted_date = plot_date.strftime("%B %d, %Y")
        plot_title = title if title else f'{official_name} Tide Forecast - {formatted_date}'
        ax.set_title(plot_title, fontsize=18, color='white', fontweight='bold', pad=20)
        
        # Format x-axis for time
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%I:%M %p', tz=pacific_tz))
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=2))
        plt.xticks(rotation=45, ha='right')
        
        # Grid
        ax.grid(True, alpha=0.2, color='white', linestyle='--')
        ax.set_ylim(0, max(5, max_level + 0.5))
        
        # Add optimal diving zone indicator
        ax.axhspan(0, 1.5, alpha=0.15, color='#ffd93d', label='Optimal diving (low tide window)')
        ax.legend(loc='upper left', fontsize=10, facecolor='#0a1628', edgecolor='white')
        
        # Spine styling
        for spine in ax.spines.values():
            spine.set_color('#00d4ff')
            spine.set_linewidth(1)
        
        ax.tick_params(colors='white', labelsize=10)
        
        plt.tight_layout()
        
        # Step 5: Save or return the plot
        result = {
            "success": True,
            "station": {
                "name": official_name,
                "id": station_id,
                "latitude": station.get("latitude"),
                "longitude": station.get("longitude")
            },
            "date": date,
            "timezone": "Pacific Time (PST/PDT)",
            "summary": {
                "low_tide": {
                    "water_level_meters": min_level,
                    "time_pst": low_tide_time.strftime("%I:%M %p %Z")
                },
                "high_tide": {
                    "water_level_meters": max_level,
                    "time_pst": high_tide_time.strftime("%I:%M %p %Z")
                },
                "tidal_range_meters": round(max_level - min_level, 2)
            },
            "data_points": len(times_pst)
        }
        
        if save_path:
            # Save to file
            plt.savefig(save_path, dpi=150, facecolor='#0a1628', 
                        edgecolor='none', bbox_inches='tight')
            result["plot_saved_to"] = save_path
            result["message"] = f"Tide plot saved to {save_path}"
        else:
            # Return as base64
            buffer = io.BytesIO()
            plt.savefig(buffer, format='png', dpi=150, facecolor='#0a1628', 
                        edgecolor='none', bbox_inches='tight')
            buffer.seek(0)
            image_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
            result["plot_base64"] = image_base64
            result["message"] = "Tide plot generated successfully. Use the base64 data to display or save the image."
        
        plt.close(fig)
        
        return result
        
    except Exception as e:
        logger.error(f"Error generating tide plot: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "station_name_searched": station_name
        }


def main():
    """Run the BC Water Tides MCP server"""
    # Suppress SSL verification warnings using built-in warnings module
    import warnings
    warnings.filterwarnings("ignore", message="Unverified HTTPS request")
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    
    # Run the server using stdio transport (no logging before this to keep stdout clean)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

