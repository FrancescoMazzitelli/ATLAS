package mazzitelli.controller;

import org.eclipse.microprofile.openapi.annotations.Operation;

import jakarta.ws.rs.*;
import jakarta.ws.rs.core.*;
import mazzitelli.model.Input;

@Path("/roadblock")
public interface RoadblockController {


    @GET
    @Path("/health")
    @Produces(MediaType.APPLICATION_JSON)
    public Response healthCheck();


    @POST
    @Path("/alternative")
    @Produces(MediaType.APPLICATION_JSON)
    @Consumes(MediaType.APPLICATION_JSON)
    @Operation(description = "Given a list of coordinates, calculates the optimal route while avoiding roads closed due to construction or emergencies.")
    public Response computeAlternativePath(Input request);

    @POST
    @Path("/register")
    public Response register();
}
