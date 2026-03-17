package mazzitelli.service;

import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;
import jakarta.ws.rs.client.Client;
import jakarta.ws.rs.client.ClientBuilder;
import jakarta.ws.rs.client.Entity;
import jakarta.ws.rs.core.MediaType;
import jakarta.ws.rs.core.Response;
import mazzitelli.model.TracePatchRequest;

import java.util.ArrayList;
import java.util.List;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.eclipse.microprofile.config.inject.ConfigProperty;

@ApplicationScoped
public class TrafficService {

    @Inject
    ContainerManager containerManager;

    @ConfigProperty(name = "VALHALLA_IP", defaultValue = "valhalla")
    String valhallaIp;

    @ConfigProperty(name = "VALHALLA_PORT", defaultValue = "8002")
    String valhallaPort;

    private String getValhallaUrl() {
        return "http://" + valhallaIp + ":" + valhallaPort + "/trace_attributes";
    }

    public String traceAndPatch(List<TracePatchRequest.Coordinate> shape, int speed) throws Exception {
        Client client = ClientBuilder.newClient();
        String payload = createValhallaPayload(shape);
        
        Response response = client.target(getValhallaUrl())
                .request(MediaType.APPLICATION_JSON)
                .post(Entity.json(payload));

        if (response.getStatus() != 200) {
            throw new RuntimeException("Valhalla Trace failed: " + response.readEntity(String.class));
        }

        String jsonResponse = response.readEntity(String.class);
        List<Long> edgeIds = parseEdgeIds(jsonResponse);

        if (edgeIds.isEmpty()) {
            return "No edge found at these coordinates.";
        }

        containerManager.executeFullPipeline(edgeIds, speed);

        return "Success: " + edgeIds.size() + " edges updated.";
    }

    private List<Long> parseEdgeIds(String json) throws Exception {
        ObjectMapper mapper = new ObjectMapper();
        JsonNode root = mapper.readTree(json);
        List<Long> ids = new ArrayList<>();
        if (root.has("edges")) {
            for (JsonNode edge : root.get("edges")) {
                if (edge.has("id")) ids.add(edge.get("id").asLong());
            }
        }
        return ids;
    }

    private String createValhallaPayload(List<TracePatchRequest.Coordinate> shape) {
    return "{"
        + "\"shape\":" + serializeShape(shape) + ","
        + "\"costing\":\"auto\","
        + "\"filters\":{\"attributes\":[\"edge.id\",\"edge.way_id\"],\"action\":\"include\"},"
        + "\"search_radius\":30,"
        + "\"shape_match\":\"map_snap\""
        + "}";
    }

    private String serializeShape(List<TracePatchRequest.Coordinate> shape) {
        return new ObjectMapper().valueToTree(shape).toString();
    }
}
