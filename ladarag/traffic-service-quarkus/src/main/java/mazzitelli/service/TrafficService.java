package mazzitelli.service;

import mazzitelli.controller.TrafficController;
import mazzitelli.model.Location;
import mazzitelli.model.payload.CatalogPayload;
import mazzitelli.model.payload.Check;
import mazzitelli.model.payload.Meta;
import mazzitelli.model.payload.RegistryPayload;
import mazzitelli.model.valhalla.ValhallaLeg;
import mazzitelli.model.valhalla.ValhallaResponse;

import jakarta.ws.rs.core.*;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.sql.*;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import org.eclipse.microprofile.config.inject.ConfigProperty;
import org.jboss.logging.Logger;

import com.fasterxml.jackson.databind.ObjectMapper;

public class TrafficService implements TrafficController {

    private static final Logger log = Logger.getLogger(TrafficService.class);

    @ConfigProperty(name = "SELF_HOST", defaultValue = "localhost")
    String selfHost;

    @ConfigProperty(name = "SELF_PORT", defaultValue = "8080")
    String selfPort;

    @ConfigProperty(name = "VALHALLA_HOST", defaultValue = "valhalla")
    String valhallaHost;

    @ConfigProperty(name = "VALHALLA_PORT", defaultValue = "8002")
    String valhallaPort;

    @ConfigProperty(name = "CONSUL_HOST", defaultValue = "registry")
    String consulHost;

    @ConfigProperty(name = "CONSUL_PORT", defaultValue = "8500")
    String consulPort;

    @ConfigProperty(name = "GATEWAY_HOST", defaultValue = "catalog-gateway")
    String gatewayHost;

    @ConfigProperty(name = "GATEWAY_PORT", defaultValue = "5000")
    String gatewayPort;

    @ConfigProperty(name = "UPDATER_HOST", defaultValue = "routing-updater")
    String updaterHost;

    @ConfigProperty(name = "UPDATER_PORT", defaultValue = "8080")
    String updaterPort;

    @ConfigProperty(name = "POSTGIS_JDBC", defaultValue = "jdbc:postgresql://localhost:5432/traffic")
    String postgisJdbc;

    @ConfigProperty(name = "POSTGIS_USER", defaultValue = "admin")
    String postgisUser;

    @ConfigProperty(name = "POSTGIS_PASSWORD", defaultValue = "admin")
    String postgisPassword;

    private String getValhallaUrl() {
        return "http://" + valhallaHost + ":" + valhallaPort + "/route";
    }

    private String getConsulUrl() {
        return "http://" + consulHost + ":" + consulPort + "/v1/agent/service/register";
    }

    private String getGatewayUrl() {
        return "http://" + gatewayHost + ":" + gatewayPort;
    }

    private String getUpdaterUrl() {
        return "http://" + updaterHost + ":" + updaterPort;
    }

    @Override
    public Response healthCheck() {
        return Response.ok("{\"status\":\"ALIVE\"}").build();
    }

    @Override
    public Response computeAlternativePath(List<Location> locations) {
        try {
            log.info("1. Building the initial request...");
            String request = buildValhallaRequest(locations);

            log.info("2. Sending the initial request to Valhalla...");
            String initialRouteJson = callValhalla(request);

            log.info("3. Extracting the set of locations along the route...");
            List<Location> routePoints = decodeRoutePoints(initialRouteJson);

            log.info("4. Retrieving congested segments from PostGIS near the route...");
            List<TrafficSegment> congestedSegments = queryCongestedSegmentsNearRoute(routePoints);

            String finalRoute;

            if (!congestedSegments.isEmpty()) {
                log.info("5. Patching routing engine with " + congestedSegments.size() + " congested segment(s)...");
                for (TrafficSegment segment : congestedSegments) {
                    String patchRequest = buildPatchRequest(segment.shape, segment.speed);
                    callUpdaterPatch(patchRequest);
                }

                log.info("6. Sending the final request to Valhalla after patching...");
                finalRoute = callValhalla(request);

                log.info("7. Resetting routing engine to original speeds...");
                callUpdaterReset();
            } else {
                log.info("5. No congested segments found near route, returning initial route...");
                finalRoute = initialRouteJson;
            }

            return Response.ok(finalRoute).build();

        } catch (Exception e) {
            e.printStackTrace();
            return Response.status(Response.Status.INTERNAL_SERVER_ERROR)
                    .entity("{\"error\":\"Error computing alternative path\"}")
                    .build();
        }
    }

    // -------------------------------------------------------------------------
    // Routing Updater calls
    // -------------------------------------------------------------------------

    private void callUpdaterPatch(String jsonBody) throws Exception {
        HttpClient client = HttpClient.newHttpClient();
        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(getUpdaterUrl() + "/traffic/patch"))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(jsonBody))
                .build();
        HttpResponse<String> response = client.send(request, HttpResponse.BodyHandlers.ofString());
        log.info("Updater patch response: " + response.body());
    }

    private void callUpdaterReset() throws Exception {
        HttpClient client = HttpClient.newHttpClient();
        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(getUpdaterUrl() + "/traffic/reset"))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.noBody())
                .build();
        HttpResponse<String> response = client.send(request, HttpResponse.BodyHandlers.ofString());
        log.info("Updater reset response: " + response.body());
    }

    // -------------------------------------------------------------------------
    // Request builders
    // -------------------------------------------------------------------------

    private String buildPatchRequest(List<Location> shape, int speed) throws Exception {
        ObjectMapper mapper = new ObjectMapper();
        StringBuilder sb = new StringBuilder();
        sb.append("{");
        sb.append("\"shape\":[");
        for (int i = 0; i < shape.size(); i++) {
            Location loc = shape.get(i);
            sb.append("{\"lat\":").append(loc.lat)
              .append(",\"lon\":").append(loc.lon).append("}");
            if (i < shape.size() - 1) sb.append(",");
        }
        sb.append("],");
        sb.append("\"speed\":").append(speed);
        sb.append("}");
        return sb.toString();
    }

    private String buildValhallaRequest(List<Location> locations) {
        StringBuilder sb = new StringBuilder();
        sb.append("{");

        sb.append("\"locations\":[");
        for (int i = 0; i < locations.size(); i++) {
            Location loc = locations.get(i);
            sb.append("{\"lat\":").append(loc.lat)
            .append(",\"lon\":").append(loc.lon).append("}");
            if (i < locations.size() - 1) sb.append(",");
        }
        sb.append("],");
        sb.append("\"costing\":\"auto\",");
        sb.append("\"date_time\":{\"type\":0},");
        sb.append("\"costing_options\":{");
        sb.append("\"auto\":{");
        sb.append("\"disable_hierarchy_pruning\":true");
        sb.append("}},");
        sb.append("\"directions_options\":{");
        sb.append("\"units\":\"kilometers\"");
        sb.append("}");
        sb.append("}");

        return sb.toString();
    }

    // -------------------------------------------------------------------------
    // Valhalla call
    // -------------------------------------------------------------------------

    private String callValhalla(String jsonBody) throws Exception {
        HttpClient client = HttpClient.newHttpClient();
        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(getValhallaUrl()))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(jsonBody))
                .build();
        HttpResponse<String> response = client.send(request, HttpResponse.BodyHandlers.ofString());
        return response.body();
    }

    // -------------------------------------------------------------------------
    // Route decoding
    // -------------------------------------------------------------------------

    private List<Location> decodeRoutePoints(String valhallaJson) {
        List<Location> routePoints = new ArrayList<>();
        try {
            ObjectMapper mapper = new ObjectMapper();
            ValhallaResponse response = mapper.readValue(valhallaJson, ValhallaResponse.class);
            if (response.trip != null && response.trip.legs != null) {
                for (ValhallaLeg leg : response.trip.legs) {
                    if (leg.shape != null && !leg.shape.isEmpty()) {
                        routePoints.addAll(decodePolyline6(leg.shape));
                    }
                }
            }
        } catch (Exception e) {
            e.printStackTrace();
        }
        return routePoints;
    }

    private List<Location> decodePolyline6(String encoded) {
        List<Location> points = new ArrayList<>();
        int index = 0, lat = 0, lon = 0;
        while (index < encoded.length()) {
            int[] resultLat = decodeNext(encoded, index);
            lat += resultLat[0];
            index = resultLat[1];
            int[] resultLon = decodeNext(encoded, index);
            lon += resultLon[0];
            index = resultLon[1];
            Location loc = new Location();
            loc.lat = lat / 1e6;
            loc.lon = lon / 1e6;
            points.add(loc);
        }
        return points;
    }

    private int[] decodeNext(String encoded, int start) {
        int result = 0, shift = 0, b;
        int index = start;
        do {
            b = encoded.charAt(index++) - 63;
            result |= (b & 0x1f) << shift;
            shift += 5;
        } while (b >= 0x20);
        int delta = ((result & 1) != 0) ? ~(result >> 1) : (result >> 1);
        return new int[]{delta, index};
    }

    // -------------------------------------------------------------------------
    // PostGIS query
    //
    // Expects a table named "traffic_segments" in the "traffic" database with:
    //   geom    GEOMETRY   — the segment geometry (LineString or Point)
    //   speed   INTEGER    — the congested speed in kph to apply
    //   shape   JSONB      — array of {lat, lon} coordinates for the updater
    // -------------------------------------------------------------------------

    private static class TrafficSegment {
        List<Location> shape;
        int speed;
    }

    private List<TrafficSegment> queryCongestedSegmentsNearRoute(List<Location> route) throws SQLException {
        List<TrafficSegment> segments = new ArrayList<>();
        if (route.isEmpty()) return segments;

        try (Connection conn = DriverManager.getConnection(postgisJdbc, postgisUser, postgisPassword)) {
            StringBuilder pointsArray = new StringBuilder();
            for (int i = 0; i < route.size(); i++) {
                Location loc = route.get(i);
                pointsArray.append("ST_MakePoint(").append(loc.lon).append(",").append(loc.lat).append(")");
                if (i < route.size() - 1) pointsArray.append(", ");
            }

            // Retrieves all congested segments within 50 metres of the route,
            // along with their target speed and the coordinate shape for the updater.
            String sql = "SELECT speed, shape " +
                         "FROM traffic_segments " +
                         "WHERE ST_DWithin(" +
                         "  geom::geography, " +
                         "  ST_SetSRID(ST_MakeLine(ARRAY[" + pointsArray + "]), 4326)::geography, " +
                         "  50" +
                         ")";

            try (PreparedStatement stmt = conn.prepareStatement(sql);
                 ResultSet rs = stmt.executeQuery()) {
                ObjectMapper mapper = new ObjectMapper();
                while (rs.next()) {
                    TrafficSegment segment = new TrafficSegment();
                    segment.speed = rs.getInt("speed");

                    // shape is stored as a JSONB array: [{lat, lon}, ...]
                    String shapeJson = rs.getString("shape");
                    List<Location> shape = new ArrayList<>();
                    for (var node : mapper.readTree(shapeJson)) {
                        Location loc = new Location();
                        loc.lat = node.get("lat").asDouble();
                        loc.lon = node.get("lon").asDouble();
                        shape.add(loc);
                    }
                    segment.shape = shape;
                    segments.add(segment);
                }
            } catch (Exception e) {
                throw new SQLException("Error parsing traffic segments from PostGIS", e);
            }
        }
        return segments;
    }

    // -------------------------------------------------------------------------
    // Self-registration
    // -------------------------------------------------------------------------

    public Response register() {
        try {
            ObjectMapper mapper = new ObjectMapper();
            HttpClient client = HttpClient.newHttpClient();

            String baseUrl = "http://" + selfHost + ":" + selfPort;
            String openApiUrl = baseUrl + "/q/openapi?format=json";

            HttpResponse<String> openApiResponse = client.send(
                    HttpRequest.newBuilder()
                            .uri(URI.create(openApiUrl))
                            .GET()
                            .build(),
                    HttpResponse.BodyHandlers.ofString()
            );

            if (openApiResponse.statusCode() != 200) {
                return Response.status(500)
                        .entity("Cannot fetch OpenAPI: " + openApiResponse.statusCode())
                        .build();
            }

            Map<String, Object> openApiMap = mapper.readValue(openApiResponse.body(), Map.class);
            Map<String, Object> info = (Map<String, Object>) openApiMap.get("info");
            String serviceName = info != null && info.get("title") != null
                    ? info.get("title").toString() : "unknown-service";
            String description = info != null && info.get("description") != null
                    ? info.get("description").toString() : "No description";

            Map<String, String> capabilities = new HashMap<>();
            Map<String, String> endpoints = new HashMap<>();

            Map<String, Object> paths = (Map<String, Object>) openApiMap.get("paths");
            if (paths != null) {
                for (Map.Entry<String, Object> entry : paths.entrySet()) {
                    String path = entry.getKey();
                    Map<String, Object> pathItem = (Map<String, Object>) entry.getValue();
                    if (pathItem == null) continue;
                    for (String method : new String[]{"get", "post", "put", "delete", "patch", "options", "head", "trace"}) {
                        Map<String, Object> operation = (Map<String, Object>) pathItem.get(method);
                        if (operation == null) continue;
                        String key = method.toUpperCase() + " " + path;
                        String desc = operation.get("description") != null
                                ? operation.get("description").toString()
                                : operation.get("summary") != null
                                    ? operation.get("summary").toString() : key;
                        capabilities.put(key, desc);
                        endpoints.put(key, baseUrl + path);
                    }
                }
            }

            CatalogPayload catalogPayload = new CatalogPayload();
            catalogPayload.setId(selfHost);
            catalogPayload.setName(serviceName);
            catalogPayload.setDescription(description);
            catalogPayload.setCapabilities(capabilities);
            catalogPayload.setEndpoints(endpoints);

            RegistryPayload consulPayload = new RegistryPayload();
            consulPayload.setId(selfHost);
            consulPayload.setName(serviceName);

            Meta meta = new Meta();
            meta.setServiceDocId(selfHost);
            consulPayload.setMeta(meta);

            Check check = new Check();
            check.setTlsSkipVerify(true);
            check.setMethod("GET");
            check.setHttp(baseUrl + "/traffic/health");
            check.setInterval("10s");
            check.setTimeout("5s");
            check.setDeregister("30s");
            consulPayload.setCheck(check);

            HttpResponse<String> consulResponse = client.send(
                    HttpRequest.newBuilder()
                            .uri(URI.create(getConsulUrl()))
                            .header("Content-Type", "application/json")
                            .PUT(HttpRequest.BodyPublishers.ofString(mapper.writeValueAsString(consulPayload)))
                            .build(),
                    HttpResponse.BodyHandlers.ofString()
            );

            HttpResponse<String> gatewayResponse = client.send(
                    HttpRequest.newBuilder()
                            .uri(URI.create(getGatewayUrl() + "/service"))
                            .header("Content-Type", "application/json")
                            .POST(HttpRequest.BodyPublishers.ofString(mapper.writeValueAsString(catalogPayload)))
                            .build(),
                    HttpResponse.BodyHandlers.ofString()
            );

            Map<String, Integer> result = new HashMap<>();
            result.put("registry", consulResponse.statusCode());
            result.put("gateway", gatewayResponse.statusCode());

            return Response.ok(mapper.writeValueAsString(result)).build();

        } catch (Exception e) {
            log.error("Registration failed", e);
            return Response.status(Response.Status.INTERNAL_SERVER_ERROR)
                    .entity("Registration failed: " + e.getMessage())
                    .build();
        }
    }
}