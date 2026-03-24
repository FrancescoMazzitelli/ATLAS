package mazzitelli.model.valhalla;

import java.util.List;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

@JsonIgnoreProperties(ignoreUnknown = true)
public class ValhallaTrip {
    public List<ValhallaLocation> locations;
    public List<ValhallaLeg> legs;
    public ValhallaSummary summary;

    @JsonProperty("status_message")
    public String statusMessage;

    public int status;
    public String units;
    public String language;
}