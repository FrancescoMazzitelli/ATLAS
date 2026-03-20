package mazzitelli.model.valhalla;

import java.util.List;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

@JsonIgnoreProperties(ignoreUnknown = true)
public class ValhallaLeg {
    public List<ValhallaManeuver> maneuvers;
    public ValhallaSummary summary;
    public String shape;
}
