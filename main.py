"""
BC Water Tides MCP Server

This server provides access to the Integrated Water Level System (IWLS) API through 
the Model Context Protocol (MCP). It retrieves forecasted tide heights for Canadian
monitoring stations.

Features:
- Look up station by name
- Get 7-day tide height predictions
- Hourly resolution data
- Comprehensive error handling
"""

import os
import logging
import ssl
from typing import Optional, List
from datetime import datetime, timedelta, timezone

import httpx
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

