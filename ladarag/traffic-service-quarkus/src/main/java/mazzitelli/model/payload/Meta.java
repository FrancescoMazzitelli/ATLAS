package mazzitelli.model.payload;

import com.fasterxml.jackson.annotation.JsonProperty;

import io.quarkus.runtime.annotations.RegisterForReflection;

@RegisterForReflection
public class Meta {
    @JsonProperty("service_doc_id")
    public String serviceDocId;

    public Meta() {}

    public Meta(String serviceDocId) {
        this.serviceDocId = serviceDocId;
    }

    public String getServiceDocId() {
        return serviceDocId;
    }

    public void setServiceDocId(String serviceDocId) {
        this.serviceDocId = serviceDocId;
    }
}