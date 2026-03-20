package mazzitelli.model.valhalla;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

@JsonIgnoreProperties(ignoreUnknown = true)
public class ValhallaSummary {
    @JsonProperty("has_time_restrictions")
    public boolean hasTimeRestrictions;
    @JsonProperty("has_toll")
    public boolean hasToll;
    @JsonProperty("has_highway")
    public boolean hasHighway;
    @JsonProperty("has_ferry")
    public boolean hasFerry;

    @JsonProperty("min_lat")
    public double minLat;
    @JsonProperty("min_lon")
    public double minLon;
    @JsonProperty("max_lat")
    public double maxLat;
    @JsonProperty("max_lon")
    public double maxLon;

    public double time;
    public double length;
    public double cost;
}
