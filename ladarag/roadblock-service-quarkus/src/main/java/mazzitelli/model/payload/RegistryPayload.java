package mazzitelli.model.payload;

import com.fasterxml.jackson.annotation.JsonProperty;

import io.quarkus.runtime.annotations.RegisterForReflection;

@RegisterForReflection
public class RegistryPayload {
    @JsonProperty("Name")
    public String name;
    @JsonProperty("Id")
    public String id;
    @JsonProperty("Meta")
    public Meta meta;
    @JsonProperty("Check")
    public Check check;

    public RegistryPayload() {}

    public RegistryPayload(String name, String id, Meta meta, Check check) {
        this.name = name;
        this.id = id;
        this.meta = meta;
        this.check = check;
    }

    public String getName() {
        return name;
    }

    public void setName(String name) {
        this.name = name;
    }

    public String getId() {
        return id;
    }

    public void setId(String id) {
        this.id = id;
    }

    public Meta getMeta() {
        return meta;
    }

    public void setMeta(Meta meta) {
        this.meta = meta;
    }

    public Check getCheck() {
        return check;
    }

    public void setCheck(Check check) {
        this.check = check;
    }

    
}