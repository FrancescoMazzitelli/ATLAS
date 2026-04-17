package mazzitelli.model;

import java.util.List;

import com.fasterxml.jackson.annotation.JsonProperty;

public class Input {
    @JsonProperty("locations")
    public List<Location> locations;
    @JsonProperty("timestamp")
    public String timestamp;

    public Input() {
    }

    public Input(List<Location> locations, String timestamp) {
        this.locations = locations;
        this.timestamp = timestamp;
    }

    public List<Location> getLocations() {
        return locations;
    }

    public void setLocations(List<Location> locations) {
        this.locations = locations;
    }

    public String getTimestamp() {
        return timestamp;
    }

    public void setTimestamp(String timestamp) {
        this.timestamp = timestamp;
    }
}

