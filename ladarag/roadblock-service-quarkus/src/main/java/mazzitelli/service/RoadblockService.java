package mazzitelli.service;

import mazzitelli.controller.RoadblockController;
import mazzitelli.model.Location;
import mazzitelli.model.payload.Capability;
import mazzitelli.model.payload.CatalogPayload;
import mazzitelli.model.payload.Check;
import mazzitelli.model.payload.Meta;
import mazzitelli.model.payload.Endpoint;
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

public class RoadblockService implements RoadblockController {

    private static final Logger log = Logger.getLogger(RoadblockService.class);

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

    @ConfigProperty(name = "GATEWAY_HOST", defaultValue= "catalog-gateway")
    String gatewayHost;

    @ConfigProperty(name = "GATEWAY_PORT", defaultValue = "5000")
    String gatewayPort;

    @ConfigProperty(name = "POSTGIS_JDBC", defaultValue = "jdbc:postgresql://localhost:5432/roadblock")
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
        return "http://" + gatewayHost + ":" + gatewayPort + "";
    }

    @Override
    public Response healthCheck() {
        return Response.ok("{\"status\":\"UP\"}").build();
    }

    @Override
    public Response computeAlternativePath(List<Location> locations) {
        try {
            log.info("1. Building the initial request...");
            String initialRequest = buildValhallaRequest(locations, new ArrayList<>());

            log.info("2. Sending the initial request to Valhalla...");
            String initialRouteJson = callValhalla(initialRequest);

            log.info("3. Extracting the set of locations...");
            List<Location> routePoints = decodeRoutePoints(initialRouteJson);

            log.info("4. Retrieving from postgis locations near the ones extracted...");
            List<Location> avoidLocations = queryRoadblocksNearRoute(routePoints);

            log.info("4. Building the final request...");
            String finalRequest = buildValhallaRequest(locations, avoidLocations);

            log.info("5. Sending the final request to Valhalla");
            String finalRoute = callValhalla(finalRequest);

            return Response.ok(finalRoute).build();

        } catch (Exception e) {
            e.printStackTrace();
            return Response.status(Response.Status.INTERNAL_SERVER_ERROR)
                    .entity("{\"error\":\"Error computing alternative path\"}")
                    .build();
        }
    }

    private String buildValhallaRequest(List<Location> locations, List<Location> avoidLocations) {
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
        sb.append("\"directions_options\":{");
        sb.append("\"units\":\"km\",\"language\":\"it-IT\"");
        sb.append("},");
        sb.append("\"shape_format\":\"polyline6\",");
        sb.append("\"shape_attributes\":[\"edge.id\",\"edge.speed\",\"edge.length\"],");
        sb.append("\"avoid_locations\":[");
        for (int i = 0; i < avoidLocations.size(); i++) {
            Location loc = avoidLocations.get(i);
            sb.append("{\"lat\":").append(loc.lat)
              .append(",\"lon\":").append(loc.lon).append("}");
            if (i < avoidLocations.size() - 1) sb.append(",");
        }
        sb.append("]");
        sb.append("}");
        return sb.toString();
    }

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

    private List<Location> queryRoadblocksNearRoute(List<Location> route) throws SQLException {
        List<Location> avoidLocations = new ArrayList<>();
        if (route.isEmpty()) return avoidLocations;

        try (Connection conn = DriverManager.getConnection(postgisJdbc, postgisUser, postgisPassword)) {
            StringBuilder pointsArray = new StringBuilder();
            for (int i = 0; i < route.size(); i++) {
                Location loc = route.get(i);
                pointsArray.append("ST_MakePoint(").append(loc.lon).append(",").append(loc.lat).append(")");
                if (i < route.size() - 1) pointsArray.append(", ");
            }

            String sql = "SELECT ST_Y(geom) AS lat, ST_X(geom) AS lon " +
                         "FROM roadblocks " +
                         "WHERE ST_DWithin(" +
                         "  geom::geography, " +
                         "  ST_SetSRID(ST_MakeLine(ARRAY[" + pointsArray + "]), 4326)::geography, " +
                         "  50" +
                         ")";

            try (PreparedStatement stmt = conn.prepareStatement(sql);
                 ResultSet rs = stmt.executeQuery()) {
                while (rs.next()) {
                    Location avoid = new Location();
                    avoid.lat = rs.getDouble("lat");
                    avoid.lon = rs.getDouble("lon");
                    avoidLocations.add(avoid);
                }
            }
        }
        return avoidLocations;
    }

    public Response register() {
        try {
            ObjectMapper mapper = new ObjectMapper();
            HttpClient client = HttpClient.newHttpClient();

            String baseUrl = "http://" + selfHost + ":" + selfPort;
            String openApiUrl = baseUrl + "/q/openapi?format=json";

            // Fetch the OpenAPI JSON
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
                    ? info.get("title").toString()
                    : "unknown-service";
            String description = info != null && info.get("description") != null
                    ? info.get("description").toString()
                    : "No description";

            List<Capability> capabilities = new ArrayList<>();
            List<Endpoint> endpoints = new ArrayList<>();

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
                                    ? operation.get("summary").toString()
                                    : key;

                        capabilities.add(new Capability(key, desc));
                        endpoints.add(new Endpoint(key, baseUrl + path));
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
            check.setHttp(baseUrl + "/roadblock/health");
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