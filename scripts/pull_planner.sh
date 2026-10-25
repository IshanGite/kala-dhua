#!/bin/zsh
ollama pull qwen3:4b > /tmp/pull_planner.log 2>&1 && echo OK >> /tmp/pull_planner.log
