package mazzitelli.service;

import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;
import jakarta.ws.rs.client.Client;
import jakarta.ws.rs.client.ClientBuilder;
import jakarta.ws.rs.client.Entity;
import jakarta.ws.rs.core.MediaType;
import jakarta.ws.rs.core.Response;
import mazzitelli.model.TracePatchRequest;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.eclipse.microprofile.config.inject.ConfigProperty;

@ApplicationScoped
public class UpdateService {

    @Inject
    ContainerManager containerManager;

    @ConfigProperty(name = "VALHALLA_HOST", defaultValue = "valhalla")
    String valhallaHost;

    @ConfigProperty(name = "VALHALLA_PORT", defaultValue = "8002")
    String valhallaPort;

    private String getValhallaUrl() {
        return "http://" + valhallaHost + ":" + valhallaPort + "/trace_attributes";
    }

    
    public String traceAndPatch(List<TracePatchRequest.Coordinate> shape) throws Exception {
        Client client = ClientBuilder.newClient();

        String payload = createValhallaPayload(shape);

        Response response = client.target(getValhallaUrl())
                .request(MediaType.APPLICATION_JSON)
                .post(Entity.json(payload));

        if (response.getStatus() != 200) {
            throw new RuntimeException("Valhalla trace_attributes failed: "
                    + response.readEntity(String.class));
        }

        String json = response.readEntity(String.class);

        Map<Long, Integer> edgeSpeedMap = mapEdgesToSpeed(json, shape);

        if (edgeSpeedMap.isEmpty()) {
            return "No edges found.";
        }

        containerManager.executeFullPipeline(edgeSpeedMap);

        return "Success: " + edgeSpeedMap.size() + " edges updated.";
    }

    public String reset() throws Exception {
        containerManager.executeReset();
        return "Success: traffic restored to original speeds.";
    }

    private Map<Long, Integer> mapEdgesToSpeed(String json, List<TracePatchRequest.Coordinate> shape) throws Exception {
        ObjectMapper mapper = new ObjectMapper();
        JsonNode root = mapper.readTree(json);

        Map<Long, Integer> map = new HashMap<>();

        if (!root.has("edges")) return map;

        JsonNode edges = root.get("edges");

        int avgSpeed = shape.stream()
                .mapToInt(s -> s.speed)
                .sum() / shape.size();

        for (JsonNode edge : edges) {
            if (!edge.has("id")) continue;

            long edgeId = edge.get("id").asLong();
            map.put(edgeId, avgSpeed);
        }

        return map;
    }

    private String createValhallaPayload(List<TracePatchRequest.Coordinate> shape) {
        StringBuilder sb = new StringBuilder();
        sb.append("{\"shape\":[");

        for (int i = 0; i < shape.size(); i++) {
            var c = shape.get(i);
            sb.append("{\"lat\":").append(c.lat)
            .append(",\"lon\":").append(c.lon).append("}");
            if (i < shape.size() - 1) sb.append(",");
        }

        sb.append("],");
        sb.append("\"costing\":\"auto\",");
        sb.append("\"shape_match\":\"map_snap\",");
        sb.append("\"search_radius\":50,");
        sb.append("\"filters\":{\"attributes\":[\"edge.way_id\", \"edge.id\"],\"action\":\"include\"}");
        sb.append("}");

        return sb.toString();
    }
}