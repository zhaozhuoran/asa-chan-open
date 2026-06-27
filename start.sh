#!/bin/bash

# Check if .env file exists
if [ -f .env ]; then
    echo "Loading environment variables from .env"
    # Read .env file line by line and export variables
    # This handles basic KEY=VALUE pairs and ignores comments
    export $(grep -v '^#' .env | xargs)
else
    echo ".env file not found, continuing with existing environment variables"
fi

# Start the bot
echo "Starting Asachan Slack Bot..."
python3 main.py
