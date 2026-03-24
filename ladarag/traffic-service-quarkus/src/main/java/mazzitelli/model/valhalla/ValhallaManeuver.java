package mazzitelli.model.valhalla;

import java.util.List;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

@JsonIgnoreProperties(ignoreUnknown = true)
public class ValhallaManeuver {
    public int type;
    public String instruction;

    @JsonProperty("verbal_transition_alert_instruction")
    public String verbalTransitionAlertInstruction;

    @JsonProperty("verbal_succinct_transition_instruction")
    public String verbalSuccinctTransitionInstruction;

    @JsonProperty("verbal_pre_transition_instruction")
    public String verbalPreTransitionInstruction;

    @JsonProperty("verbal_post_transition_instruction")
    public String verbalPostTransitionInstruction;

    public List<String> street_names;

    @JsonProperty("bearing_before")
    public Integer bearingBefore;

    @JsonProperty("bearing_after")
    public Integer bearingAfter;

    public double time;
    public double length;
    public double cost;

    @JsonProperty("begin_shape_index")
    public int beginShapeIndex;

    @JsonProperty("end_shape_index")
    public int endShapeIndex;

    @JsonProperty("verbal_multi_cue")
    public Boolean verbalMultiCue;

    @JsonProperty("travel_mode")
    public String travelMode;

    @JsonProperty("travel_type")
    public String travelType;

    @JsonProperty("roundabout_exit_count")
    public Integer roundaboutExitCount;
}