#!/bin/bash

export SCRAPER_PATH="../../cache/events/scraper"

cd /Users/isaacsalvador/Git/ATLAS/synthgen/incidents/

source ../../venv/bin/activate

/Users/isaacsalvador/Git/ATLAS/venv/bin/python incidents.py

deactivate
