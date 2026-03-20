package mazzitelli.model.payload;

import java.util.List;

import com.fasterxml.jackson.annotation.JsonProperty;

import io.quarkus.runtime.annotations.RegisterForReflection;

@RegisterForReflection
public class CatalogPayload {
    @JsonProperty("id")
    public String id;
    @JsonProperty("name")
    public String name;
    @JsonProperty("description")
    public String description;
    @JsonProperty("capabilities")
    public List<Capability> capabilities;
    @JsonProperty("endpoints")
    public List<Endpoint> endpoints;
    
    public CatalogPayload() {}

    public CatalogPayload(String id, String name, String description, List<Capability> capabilities,
            List<Endpoint> endpoints) {
        this.id = id;
        this.name = name;
        this.description = description;
        this.capabilities = capabilities;
        this.endpoints = endpoints;
    }

    public String getId() {
        return id;
    }

    public void setId(String id) {
        this.id = id;
    }

    public String getName() {
        return name;
    }

    public void setName(String name) {
        this.name = name;
    }

    public String getDescription() {
        return description;
    }

    public void setDescription(String description) {
        this.description = description;
    }

    public List<Capability> getCapabilities() {
        return capabilities;
    }

    public void setCapabilities(List<Capability> capabilities) {
        this.capabilities = capabilities;
    }

    public List<Endpoint> getEndpoints() {
        return endpoints;
    }

    public void setEndpoints(List<Endpoint> endpoints) {
        this.endpoints = endpoints;
    }
}
