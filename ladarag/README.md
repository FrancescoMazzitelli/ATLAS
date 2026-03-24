# LADARAG — LAnguage-Driven Retrieval Augmented Generation

LADARAG is a distributed, containerized platform that combines AI-driven service orchestration, real-time routing, geospatial data management, and dynamic traffic simulation. It is designed to allow a natural language control unit to discover, invoke, and coordinate heterogeneous microservices, including a road routing engine and a geospatial database, through a semantic catalog and a service registry.

---

## Architecture Overview

```
┌────────────────────────────────────────────────────────────────┐
│                        Control Unit                            │
│         (LLM orchestrator — phi4-reasoning:14b via Ollama)     │
└───────────────┬────────────────────────┬───────────────────────┘
                │                        │
        Service Discovery         Semantic Search
                │                        │
        ┌───────▼────────┐    ┌──────────▼──────────┐
        │    Registry    │    │   Catalog Gateway   │
        │  (Consul)      │    │  (MongoDB + Qdrant) │
        └───────┬────────┘    └─────────────────────┘
                │
    ┌───────────┼────────────────────┐
    │           │                    │
┌───▼────┐  ┌───▼──────────┐  ┌──────▼───────┐
│Valhalla│  │  Roadblock   │  │   Traffic    │
│Routing │  │   Service    │  │   Service    │
└───▲────┘  └──────┬───────┘  └──────┬───────┘          ┌───────────────┐
    │              │                 │                  │    Routing    │
    ├──────────────┴─────────────────┴─────────────────▶│    Updater    │
    │                                                   └─────┬────▲────┘
    └─────────────────────────────────────────────────────────┘    │
              ┌─────────────────┐                                  │
              │  PostGIS DB     │                                  │
              │ (roadblock +    │                                  │
              │  traffic DBs)   ├──────────────────────────────────┘
              └───────▲─────────┘
                      │
              ┌───────┴──────┐
              │ SHP Uploader │
              │   (Web GUI)  │
              └──────────────┘
```

---

## Services

### Registry — Consul
**Image:** `hashicorp/consul:latest`  
**Port:** `8500`

Service registry based on HashiCorp Consul. All microservices register themselves here on startup, providing health check endpoints and metadata. The Control Unit queries Consul to discover which services are currently alive before dispatching tasks.

---

### Catalog Gateway — `db-gateway`
**Port:** `5000`

A Python/Flask service that acts as a dual-store catalog for registered services. It combines:

- **MongoDB** — persistent document storage for service descriptors (name, description, capabilities, endpoints)
- **Qdrant** — vector database for semantic search over service capabilities

On startup it loads two models:
- `Qwen/Qwen3-Embedding-0.6B` — used to generate embeddings for service capabilities and user queries
- `cross-encoder/ms-marco-MiniLM-L-6-v2` — used to rerank search results

**Key endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Returns server readiness status |
| POST | `/service` | Register or update a service descriptor |
| POST | `/index/search` | Semantic search over registered capabilities |
| GET | `/services` | List all registered services |
| GET | `/services/<id>` | Get a specific service |
| DELETE | `/services/<id>` | Delete a service |

---

### Control Unit
**Port:** `5500`

A Python/Flask service that acts as the AI orchestrator of the platform. It exposes a single conversational endpoint that accepts a natural language query and:

1. Queries the Registry (Consul) for live services
2. Performs a semantic search on the Catalog Gateway to find relevant capabilities
3. Sends a structured prompt to a local **Ollama** instance running `phi4-reasoning:14b`
4. Parses the LLM response to extract an execution plan (list of tasks with service, endpoint, input, and HTTP method)
5. Executes all tasks concurrently via `asyncio` + `aiohttp`
6. Returns the execution plan and results

**Key endpoint:**

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/control/invoke` | Submit a natural language query for orchestration |

**Request body:**
```json
{ "input": "Calculate an alternative route avoiding roadblocks from A to B" }
```

**Environment variables:**

| Variable | Description |
|----------|-------------|
| `REGISTRY_URL` | Consul base URL |
| `CATALOG_URL` | Catalog Gateway base URL |
| `OLLAMA_API_URL` | Ollama API base URL |

---

### Valhalla — Routing Engine
**Image:** `ghcr.io/valhalla/valhalla-scripted:3.6.3`  
**Port:** `8002`

Open-source routing engine. On first startup it downloads and builds OSM routing tiles for the configured region (Southern Italy by default). Exposes a standard Valhalla HTTP API used by both the Roadblock Service and the Routing Updater.

> **Note:** The first startup takes several minutes while tiles are being built. The service is considered healthy only after the build completes.

**Environment variables:**

| Variable | Description |
|----------|-------------|
| `tile_urls` | OSM PBF download URL |
| `build_elevation` | Whether to build elevation data |
| `server_threads` | Number of worker threads |

---

### Routing Updater — `routing-updater-quarkus`
**Port:** `5700` (mapped from internal `8080`)

A Quarkus/Java service that dynamically modifies Valhalla's traffic data at the binary level, without requiring a full tile rebuild. It allows simulating congestion or road closures on specific road segments in real time.

**How it works:**

1. Receives a list of GPS coordinates and a target speed (kph)
2. Calls Valhalla's `trace_attributes` API to identify the affected road edge IDs
3. Reads the `traffic.tar` file from the Valhalla container
4. Patches the speed value for each edge directly in the binary file using Valhalla's internal traffic tile format
5. Copies the patched file back into the container and restarts Valhalla

A reset endpoint restores the original traffic data from a backup taken on first use.

**Key endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| POST | `/traffic/patch` | Apply a custom speed to a set of road coordinates |
| POST | `/traffic/reset` | Restore original traffic speeds |

**Request body for `/traffic/patch`:**
```json
{
  "shape": [
    { "lat": 41.1296, "lon": 14.7821 },
    { "lat": 41.1310, "lon": 14.7800 }
  ],
  "speed": 10
}
```

**Environment variables:**

| Variable | Description |
|----------|-------------|
| `TARGET_CONTAINER_NAME` | Name of the Valhalla Docker container |
| `TRAFFIC_TAR_PATH` | Path to `traffic.tar` inside the container |
| `VALHALLA_HOST` / `VALHALLA_PORT` | Valhalla address |

> **Note:** This service requires access to the Docker socket (`/var/run/docker.sock`) to execute commands inside the Valhalla container.

---

### Roadblock Service — `roadblock-service-quarkus`
**Port:** `5701` (mapped from internal `8080`)

A Quarkus/Java microservice that computes road routes while avoiding known roadblocks stored in PostGIS. Its routing logic works in two steps:

**How it works:**

1. Sends an initial routing request to Valhalla to get the default route and decode its polyline6-encoded shape into a list of coordinates
2. Queries the `roadblocks` table in PostGIS for any points within 50 meters of the route using `ST_DWithin`
3. Sends a second routing request to Valhalla with those points as `avoid_locations`

On startup it can register itself with both Consul and the Catalog Gateway by calling `POST /roadblock/register`, which reads its own OpenAPI spec and builds the service descriptor automatically.

**Key endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/roadblock/health` | Health check |
| POST | `/roadblock/alternative` | Compute route avoiding roadblocks |
| POST | `/roadblock/register` | Self-register with Consul and Catalog Gateway |

**Request body for `/roadblock/alternative`:**
```json
[
  { "lat": 41.1296, "lon": 14.7821 },
  { "lat": 41.1350, "lon": 14.7774 }
]
```

**Environment variables:**

| Variable | Description |
|----------|-------------|
| `POSTGIS_JDBC` | JDBC connection string for PostGIS |
| `POSTGIS_USER` | Database user |
| `POSTGIS_PASSWORD` | Database password |
| `VALHALLA_HOST` / `VALHALLA_PORT` | Valhalla address |
| `CONSUL_HOST` / `CONSUL_PORT` | Consul address |
| `GATEWAY_HOST` / `GATEWAY_PORT` | Catalog Gateway address |

---

### Traffic Service — `traffic-service-quarkus`
**Port:** `5702` (mapped from internal `8080`)

A Quarkus/Java microservice that computes traffic-aware alternative routes by dynamically integrating real-time congestion data stored in PostGIS and applied via the Routing Updater.

Unlike the Roadblock Service (which avoids points), this service modifies routing cost dynamically by slowing down specific road segments.

**How it works:**

1. Sends an initial route request to Valhalla
2. Decodes the polyline6 route into a list of coordinates
3. Queries the traffic_segments table in PostGIS:
    - Finds segments within 50 meters of the route (ST_DWithin)
4. If congestion is found:
    - Calls the Routing Updater /traffic/patch endpoint for each segment
    - Recomputes the route using modified speeds
    - Resets Valhalla traffic data afterward
5. Returns the updated route

**Key endpoints:**

| Method | Path                   | Description                                   |
| ------ | ---------------------- | --------------------------------------------- |
| GET    | `/traffic/health`      | Health check                                  |
| POST   | `/traffic/alternative` | Compute traffic-aware alternative route       |
| POST   | `/traffic/register`    | Self-register with Consul and Catalog Gateway |

**Request body for `/traffic/alternative`:**
```json
[
  { "lat": 41.1296, "lon": 14.7821 },
  { "lat": 41.1375, "lon": 14.7774 }
]
```

**Environment variables:**

| Variable                          | Description             |
| --------------------------------- | ----------------------- |
| `POSTGIS_JDBC`                    | JDBC connection string  |
| `POSTGIS_USER`                    | Database user           |
| `POSTGIS_PASSWORD`                | Database password       |
| `VALHALLA_HOST` / `VALHALLA_PORT` | Valhalla address        |
| `UPDATER_HOST` / `UPDATER_PORT`   | Routing Updater address |
| `CONSUL_HOST` / `CONSUL_PORT`     | Consul address          |
| `GATEWAY_HOST` / `GATEWAY_PORT`   | Catalog Gateway address |


---

### PostGIS Database — `locations-db`
**Image:** `postgis/postgis:18-3.6`  
**Port:** `5800` (mapped from internal `5432`)

PostgreSQL database with the PostGIS extension. On first startup, `init-dbs.sh` creates two databases and enables the PostGIS extension in each:

| Database | Purpose |
|----------|---------|
| `roadblock` | Stores roadblock point geometries queried by the Roadblock Service |
| `traffic` | Stores traffic-related geospatial data |

---

### pgAdmin — `locations-gui`
**Image:** `dpage/pgadmin4:9.13`  
**Port:** `5801`

Web-based GUI for managing the PostGIS database. Pre-configured with a `servers.json` and `pgpass` file for automatic connection to `locations-db`.

Access at: `http://localhost:5801`  
Credentials: `admin@admin.com` / `admin`

---

### SHP Uploader — `shp-uploader`
**Port:** `5802`

A lightweight Python/Flask web application for importing shapefiles directly into PostGIS via a browser GUI. It uses `pyshp` to parse shapefiles in pure Python — no PostGIS client tools required.

> **Important:** Valhalla uses the EPSG:3857
**Features:**
- Drag-and-drop ZIP upload containing `.shp`, `.shx`, `.dbf`, `.prj`
- Automatic geometry type and SRID detection from the `.prj` file
- Data preview table (up to 100 rows) before importing
- Database and schema selection from a dropdown populated at runtime
- Import modes: Create, Append, Replace
- Optional GIST spatial index creation

Access at: `http://localhost:5802`

**Environment variable:**

```yaml
DB_CONNECTIONS: |
  {
    "traffic":  {"host":"locations-db","port":5432,"dbname":"traffic","user":"admin","password":"admin"},
    "roadblock":{"host":"locations-db","port":5432,"dbname":"roadblock","user":"admin","password":"admin"}
  }
```

---

## Port Reference

| Port  | Service           |
| ----- | ----------------- |
| 5000  | Catalog Gateway   |
| 5500  | Control Unit      |
| 5700  | Routing Updater   |
| 5701  | Roadblock Service |
| 5702  | Traffic Service   |
| 5800  | PostGIS           |
| 5801  | pgAdmin           |
| 5802  | SHP Uploader      |
| 6333  | Qdrant HTTP       |
| 6334  | Qdrant gRPC       |
| 8002  | Valhalla          |
| 8500  | Consul            |
| 27017 | MongoDB           |
| 27018 | Mongo Express     |


---

## Getting Started

### Prerequisites

- Docker and Docker Compose
- A running Ollama instance with `phi4-reasoning:14b` pulled, reachable at the address configured in `OLLAMA_API_URL`

### Startup

```bash
docker compose -f Compose.yaml up -d
```

If the building process results in failures because of host resources, consider building services one-by-one, e.g.

```bash
docker compose -f Compose.yaml build roadblock-service
```

> **Important:** Valhalla will take several minutes on first startup to download and build routing tiles. The Roadblock Service and Routing Updater will wait for it to be healthy before starting.

### Registering services

Once the stack is up, each service that needs to be discoverable by the Control Unit must register itself:

```bash
# Register the Roadblock Service
curl -X POST http://localhost:5701/roadblock/register

# Register the Traffic Service
curl -X POST http://localhost:5702/traffic/register
```

### Loading geospatial data

1. Open `http://localhost:5802`
2. Upload a ZIP file containing your shapefile
3. Select the target database (`roadblock` or `traffic`), schema, and table name
4. Click **Import into PostGIS**

### Invoking the Control Unit

Access the exposed the swagger interface at: http://localhost:5500/swagger and fill the json.

Otherwise you can use this curl:
```bash
curl -X POST http://localhost:5500/api/control/invoke \
  -H "Content-Type: application/json" \
  -d '{"input": "Calculate an alternative route from coordinates A to B avoiding roadblocks"}'
```

---
## Data Flow — Traffic Patching

```
Client
  │
  ▼
POST /traffic/patch  [shape + speed]
  │
  ├─► Valhalla /trace_attributes  → edge IDs
  │
  ├─► Read traffic.tar from Valhalla container
  │
  ├─► Patch edge speed bytes in binary traffic tile
  │
  ├─► Copy patched traffic.tar back to container
  │
  └─► Restart Valhalla container
```

## Data Flow — Roadblock Avoidance

```
Client
  │
  ▼
POST /roadblock/alternative  [lat/lon list]
  │
  ├─► Valhalla /route  (initial route, no avoidances)
  │       └─► decode polyline6 → list of coordinates
  │
  ├─► PostGIS ST_DWithin  (find roadblocks within 50m of route)
  │       └─► list of avoid_locations
  │
  └─► Valhalla /route  (final route with avoid_locations)
          └─► return to client
```

## Data Flow - Traffic Avoidance

```
Client
  │
  ▼
POST /traffic/alternative  [lat/lon list]
  │
  ├─► Valhalla /route  (initial route)
  │       └─► decode polyline6 → route points
  │
  ├─► PostGIS ST_DWithin
  │       └─► find nearby traffic_segments
  │
  ├─► Routing Updater /traffic/patch
  │       └─► apply reduced speeds
  │
  ├─► Valhalla /route  (recomputed with traffic)
  │
  ├─► Routing Updater /traffic/reset
  │
  └─► return final route
```

## Data Flow — Control Unit Orchestration

```
Client
  │
  ▼
POST /api/control/invoke  [natural language query]
  │
  ├─► Consul  →  list of live services
  │
  ├─► Catalog Gateway /index/search  →  semantically relevant capabilities
  │
  ├─► Ollama (phi4-reasoning:14b)  →  execution plan (JSON)
  │
  └─► HTTP calls to each service in the plan
          └─► aggregated results returned to client
```