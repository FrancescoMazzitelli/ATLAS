package mazzitelli.model.valhalla;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

import io.quarkus.runtime.annotations.RegisterForReflection;

@RegisterForReflection
@JsonIgnoreProperties(ignoreUnknown = true)
public class ValhallaLocation {
    public String type;
    public double lat;
    public double lon;
    
    @JsonProperty("date_time")
    public String dateTime;

    @JsonProperty("original_index")
    public int originalIndex;
}
