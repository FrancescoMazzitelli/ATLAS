#!/bin/bash

URL="https://www.transitchicago.com/downloads/sch_data/google_transit.zip"
DEST="../../cache/gtfs"

if [ ! -f "$DEST/google_transit.zip" ]; then
    mkdir -p "$DEST"
    wget -O "$DEST/google_transit.zip" "$URL"
else
    echo "file already exists, skipping download"
fi

EVENT_PATH="../../cache/events"

if [ ! -f "$EVENT_PATH/bus_stop_alerts.shp" ]; then
    python transit.py
    echo "created bus stop alerts"
else
    echo "bus stop alerts already created"
fi