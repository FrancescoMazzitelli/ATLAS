package mazzitelli.controller;

import java.util.List;

import org.eclipse.microprofile.openapi.annotations.Operation;

import jakarta.ws.rs.*;
import jakarta.ws.rs.core.*;
import mazzitelli.model.Location;

@Path("/traffic")
public interface TrafficController {


    @GET
    @Path("/health")
    @Produces(MediaType.APPLICATION_JSON)
    public Response healthCheck();


    @POST
    @Path("/alternative")
    @Produces(MediaType.APPLICATION_JSON)
    @Consumes(MediaType.APPLICATION_JSON)
    @Operation(description = "Given a list of coordinates, calculates the optimal route while avoiding congested routes due to intende traffic")
    public Response computeAlternativePath(List<Location> locations);

    @POST
    @Path("/register")
    public Response register();
}
