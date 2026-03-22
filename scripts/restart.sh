#!/usr/bin/env bash
lsof -nP -iTCP:18789 -sTCP:LISTEN -t | xargs -r kill -9; nohup openclaw gateway run --port 18789 > /tmp/openclaw-gateway.log 2>&1 &