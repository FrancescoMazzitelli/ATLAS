package mazzitelli.service;

import mazzitelli.controller.RoadblockController;
import mazzitelli.model.Input;
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
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

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

    private final ObjectMapper mapper = new ObjectMapper();

    @Override
    public Response healthCheck() {
        return Response.ok("{\"status\":\"ALIVE\"}").build();
    }

    @Override
    public Response computeAlternativePath(Input request) {
        try {
            List<Location> locations = request.getLocations();
            OffsetDateTime timestamp = OffsetDateTime.parse(request.getTimestamp());

            log.info("1. Building the initial request...");
            String initialRequest = buildValhallaRequest(locations, new ArrayList<>());

            log.info("2. Sending the initial request to Valhalla...");
            String initialRouteJson = callValhalla(initialRequest);

            log.info("3. Extracting the set of locations...");
            List<Location> routePoints = decodeRoutePoints(initialRouteJson);

            log.info("4. Retrieving from postgis locations near the ones extracted...");
            List<String> avoidLocations = queryRoadblocksNearRoute(routePoints, timestamp);

            log.info("5. Building the final request...");
            String finalRequest = buildValhallaRequest(locations, avoidLocations);

            log.info("6. Sending the final request to Valhalla");
            String finalRoute = callValhalla(finalRequest);

            return Response.ok(finalRoute).build();

        } catch (Exception e) {
            e.printStackTrace();
            return Response.status(Response.Status.INTERNAL_SERVER_ERROR)
                    .entity("{\"error\":\"Error computing alternative path\"}")
                    .build();
        }
    }


    private String buildValhallaRequest(List<Location> locations,
                                         List<String> excludePolygons) {

        StringBuilder sb = new StringBuilder();
        sb.append("{");

        sb.append("\"locations\":[");
        for (int i = 0; i < locations.size(); i++) {
            Location l = locations.get(i);
            sb.append("{\"lat\":").append(l.lat)
              .append(",\"lon\":").append(l.lon).append("}");
            if (i < locations.size() - 1) sb.append(",");
        }
        sb.append("],");

        sb.append("\"costing\":\"auto\",");

        // EXCLUDE POLYGONS (fix completo)
        if (excludePolygons != null && !excludePolygons.isEmpty()) {
            sb.append("\"exclude_polygons\":[");

            for (int i = 0; i < excludePolygons.size(); i++) {
                sb.append(excludePolygons.get(i));
                if (i < excludePolygons.size() - 1) sb.append(",");
            }

            sb.append("],");
        }

        sb.append("\"directions_options\":{\"units\":\"km\",\"language\":\"en-US\"},");
        sb.append("\"shape_format\":\"polyline6\"");
        sb.append("}");

        return sb.toString();
    }

    private String callValhalla(String body) throws Exception {
        HttpClient client = HttpClient.newHttpClient();

        HttpRequest req = HttpRequest.newBuilder()
                .uri(URI.create(getValhallaUrl()))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(body))
                .build();

        return client.send(req, HttpResponse.BodyHandlers.ofString()).body();
    }

    private List<Location> decodeRoutePoints(String json) throws Exception {
        List<Location> out = new ArrayList<>();
        ValhallaResponse r = mapper.readValue(json, ValhallaResponse.class);

        if (r.trip != null && r.trip.legs != null) {
            for (ValhallaLeg leg : r.trip.legs) {
                if (leg.shape != null) {
                    out.addAll(decodePolyline6(leg.shape));
                }
            }
        }
        return out;
    }

    private List<Location> decodePolyline6(String encoded) {
        List<Location> pts = new ArrayList<>();
        int i = 0, lat = 0, lon = 0;

        while (i < encoded.length()) {
            int[] a = decodeNext(encoded, i);
            lat += a[0];
            i = a[1];

            int[] b = decodeNext(encoded, i);
            lon += b[0];
            i = b[1];

            Location l = new Location();
            l.lat = lat / 1e6;
            l.lon = lon / 1e6;
            pts.add(l);
        }
        return pts;
    }

    private int[] decodeNext(String enc, int start) {
        int result = 0, shift = 0, b;
        int i = start;

        do {
            b = enc.charAt(i++) - 63;
            result |= (b & 0x1f) << shift;
            shift += 5;
        } while (b >= 0x20);

        int delta = ((result & 1) != 0) ? ~(result >> 1) : (result >> 1);
        return new int[]{delta, i};
    }

    private List<String> queryRoadblocksNearRoute(List<Location> route, OffsetDateTime ts) throws SQLException {

        List<String> polygons = new ArrayList<>();
        if (route.isEmpty()) return polygons;

        OffsetDateTime dayStart = ts.toLocalDate().atStartOfDay().atOffset(ts.getOffset());
        OffsetDateTime dayEnd = dayStart.plusDays(1).minusNanos(1);

        String line = buildLineString(route);

        String sql =
                "WITH filtered AS ( " +
                "  SELECT geom FROM roadblock " +
                "  WHERE \"startTime\" <= ? " +
                "    AND (\"endTime\" IS NULL OR \"endTime\" >= ?) " +
                "    AND ST_DWithin( " +
                "      geom::geography, " +
                "      ST_GeogFromText(?), " +
                "      50 " +
                "    ) " +
                "), merged AS ( " +
                "  SELECT ST_ConvexHull(ST_Union(geom)) AS geom FROM filtered " +
                ") " +
                "SELECT ST_AsGeoJSON(geom) AS geojson FROM merged";

        try (Connection c = DriverManager.getConnection(postgisJdbc, postgisUser, postgisPassword);
             PreparedStatement st = c.prepareStatement(sql)) {

            st.setObject(1, dayEnd);
            st.setObject(2, dayStart);
            st.setString(3, line);

            ResultSet rs = st.executeQuery();

            while (rs.next()) {
                String geojson = rs.getString("geojson");
                if (geojson != null) {
                    try {
                        polygons.add(convertGeoJsonToValhalla(geojson));
                    } catch (Exception e) {
                        e.printStackTrace();
                    }
                }
            }
        }

        if (polygons.size() > 3) {
            polygons = polygons.subList(0, 3);
        }

        return polygons;
    }

    private String convertGeoJsonToValhalla(String geojson) throws Exception {

        Map g = mapper.readValue(geojson, Map.class);
        Map geom = (Map) g.get("geometry");

        List coords = (List) geom.get("coordinates");

        return mapper.writeValueAsString(coords);
    }

    private String buildLineString(List<Location> route) {
        StringBuilder sb = new StringBuilder("LINESTRING(");

        for (int i = 0; i < route.size(); i++) {
            Location l = route.get(i);
            sb.append(l.lon).append(" ").append(l.lat);
            if (i < route.size() - 1) sb.append(", ");
        }

        sb.append(")");
        return sb.toString();
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
                                    ? operation.get("summary").toString()
                                    : key;

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