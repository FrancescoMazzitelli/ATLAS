package mazzitelli.controller;

import jakarta.inject.Inject;
import jakarta.ws.rs.*;
import jakarta.ws.rs.core.MediaType;
import mazzitelli.model.TracePatchRequest;
import mazzitelli.service.TrafficService;

@Path("/traffic")
@Produces(MediaType.APPLICATION_JSON)
@Consumes(MediaType.APPLICATION_JSON)
public class TrafficController {

    @Inject
    TrafficService trafficService;

    @POST
    @Path("/patch")
    public String patch(TracePatchRequest request) {
        try {
            return trafficService.traceAndPatch(request.shape, request.speed);
        } catch (Exception e) {
            return "Errore: " + e.getMessage();
        }
    }
}
