package mazzitelli.model.valhalla;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

@JsonIgnoreProperties(ignoreUnknown = true)
public class ValhallaResponse {
    public ValhallaTrip trip;
}