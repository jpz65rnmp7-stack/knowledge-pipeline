#!/bin/bash
# Wrapper script for launchd — sources environment before running pipeline
# Called by com.jingyi.knowledge-pipeline.plist

# Load user environment
export HOME=/Users/jingyi
export PATH="/Users/jingyi/.local/bin:/usr/local/bin:/usr/bin:/bin"

# Load API keys from shell profile or .env file
if [ -f "$HOME/.zshrc" ]; then
    source "$HOME/.zshrc" 2>/dev/null || true
fi

# Fallback: load from .env file in project directory
PROJECT_DIR="$HOME/Documents/knowledge-pipeline"
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    source "$PROJECT_DIR/.env"
    set +a
fi

# Run the pipeline
cd "$PROJECT_DIR"
exec uv run python -m src.main "$@"
