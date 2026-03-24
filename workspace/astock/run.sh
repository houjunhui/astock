#!/bin/bash
cd /home/gem/workspace/agent/workspace
set -a
source .env
set +a
python3 -u astock/main.py "$@"
