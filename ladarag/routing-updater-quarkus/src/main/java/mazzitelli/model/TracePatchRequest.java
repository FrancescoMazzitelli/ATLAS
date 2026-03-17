package mazzitelli.model;

import java.util.List;

public class TracePatchRequest {
    public List<Coordinate> shape;
    public int speed;

    public static class Coordinate {
        public double lat;
        public double lon;
    }
}