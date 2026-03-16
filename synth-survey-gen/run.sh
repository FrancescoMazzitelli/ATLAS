#!/bin/bash

# Check if at least 2 arguments are provided
if [ "$#" -lt 2 ]; then
    echo "Usage: $0 <runtype> <path/to/config> [path/to/runfolder]"
    exit 1
fi

runtype="$1"
config_path="$2"
runfolder="$3"  # optional

echo "Runtype: $runtype"
echo "Config path: $config_path"

if [ -n "$runfolder" ]; then
    echo "Runfolder: $runfolder"
else
    echo "No runfolder provided, using default."
fi

if [ "$runtype" = "local" ]; then
    # Check if Python virtual environment is active
    if [ -z "$VIRTUAL_ENV" ]; then
        echo "Virtual environment not active. Attempting to activate ../venv..."
        if [ -f "../venv/bin/activate" ]; then
            source ../venv/bin/activate
        else
            echo "Error: Virtual environment not found at ../venv"
            exit 1
        fi
    else
        echo "Virtual environment already active: $VIRTUAL_ENV"
    fi

    # Run Python script with appropriate arguments
    if [ -n "$runfolder" ]; then
        python main.py "$config_path" "$runfolder"
    else
        python main.py "$config_path"
    fi

elif [ "$runtype" = "acer" ]; then
    module load Mamba
    mamba activate synth_env

else
    echo "Invalid runtype: $runtype"
    echo "Available options:"
    echo "  local - run on local machine"
    echo "  acer  - run on HPC compute node (not implemented)"
    exit 1
fi