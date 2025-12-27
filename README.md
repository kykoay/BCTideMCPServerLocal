# BC Water Tides MCP Server

A Model Context Protocol (MCP) server that provides access to Canadian tide predictions from the Integrated Water Level System (IWLS) API. This server enables AI applications to retrieve tide forecasts for monitoring stations across Canada.

## Features

### Tools

- **`get_tide_forecast`** - Get 7-day tide height predictions for a specific station
- **`list_stations`** - List available tide monitoring stations with filtering options

## Installation

1. **Clone or download this repository**
   ```bash
   git clone <repository-url>
   cd bc-tide-mcp-server
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   # or using uv
   uv sync
   ```

## Usage

### Running the Server

**Development mode (stdio):**
```bash
python main.py
```

**Using uv:**
```bash
uv run main.py
```

### Integration with Claude Desktop

Add to your Claude Desktop configuration file (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "bc-tides": {
      "command": "python",
      "args": ["/absolute/path/to/bc-tide-mcp-server/main.py"]
    }
  }
}
```

Or using uv:

```json
{
  "mcpServers": {
    "bc-tides": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/bc-tide-mcp-server", "main.py"]
    }
  }
}
```

## API Reference

### Tools

#### `get_tide_forecast`

Get 7-day tide height forecast for a Canadian monitoring station.

**Parameters:**
- `station_name` (required): The name of the monitoring station (e.g., "Point Atkinson", "Vancouver", "Victoria"). Partial matching is supported.

**Returns:**
```json
{
  "success": true,
  "station": {
    "name": "Point Atkinson",
    "id": "5cebf1de3d0f4a073c4bbcef",
    "code": "07795",
    "latitude": 49.3299,
    "longitude": -123.2633,
    "operating": true
  },
  "time_range": {
    "from": "2024-01-01T00:00:00Z",
    "to": "2024-01-08T00:00:00Z"
  },
  "resolution": "SIXTY_MINUTES (hourly)",
  "total_predictions": 168,
  "forecasts": [
    {
      "timestamp": "2024-01-01T00:00:00Z",
      "water_level_meters": 3.42,
      "qc_flag": "2"
    }
  ]
}
```

#### `list_stations`

List available tide monitoring stations.

**Parameters:**
- `search_term` (optional): Filter stations by name
- `operating_only` (optional, default: true): Only return currently operating stations
- `limit` (optional, default: 20): Maximum number of stations to return (1-100)

**Returns:**
```json
{
  "success": true,
  "search_term": "vancouver",
  "operating_only": true,
  "total_matching": 3,
  "stations": [
    {
      "name": "Point Atkinson",
      "alternative_name": null,
      "id": "5cebf1de3d0f4a073c4bbcef",
      "code": "07795",
      "latitude": 49.3299,
      "longitude": -123.2633,
      "operating": true
    }
  ]
}
```

## Example Station Names

- Point Atkinson (near Vancouver)
- Victoria
- Tofino
- Prince Rupert
- Nanaimo
- Campbell River

## Data Source

This server uses the Integrated Water Level System (IWLS) API provided by Fisheries and Oceans Canada (DFO-MPO).

API Base URL: `https://api-iwls.dfo-mpo.gc.ca/api/v1`

## Example Usage in Claude

After setting up the server, you can use it in Claude Desktop:

```
"What are the tide predictions for Point Atkinson for the next week?"

"List all tide stations near Victoria"

"Get the tide forecast for Tofino"
```

## License

This project is licensed under the MIT License.

