package mazzitelli.controller;

import java.util.List;

import jakarta.ws.rs.*;
import jakarta.ws.rs.core.*;
import mazzitelli.model.Location;

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
    public Response computeAlternativePath(List<Location> locations);

    @POST
    @Path("/register")
    public Response register();
}
