#!/bin/bash

# Check if mydailytravel folder already exists
if [ -d "mydailytravel" ]; then
    echo "mydailytravel folder already exists. Skipping..."
    exit 0
fi

# Clone the repository
git clone https://github.com/CMAP-REPOS/mydailytravel.git

# Unzip the MyDailyTravelData.zip file
unzip mydailytravel/source/MyDailyTravelData.zip -d mydailytravel/source/
