#!/bin/bash
# Loads the OpenCode Go key into the current shell env without echoing it.
set -a
KEY=$(python3 -c 'import json,os;print(json.load(open(os.path.expanduser("~/.pi/agent/auth.json")))["opencode-go"]["key"].strip())')
export OPENCODE_API_KEY="$KEY"
export OPENAI_API_KEY="$KEY"
set +a
