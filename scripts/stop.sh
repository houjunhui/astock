#!/usr/bin/env bash
lsof -nP -iTCP:18789 -sTCP:LISTEN -t | xargs -r kill -9