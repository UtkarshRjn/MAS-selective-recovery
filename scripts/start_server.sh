#!/usr/bin/env bash
# Starts llama.cpp's OpenAI-compatible server with the fixed model used in all experiments.
# The first run downloads the GGUF (~4.7 GB) into the llama.cpp cache.
# --parallel 1 keeps a single slot, so requests are never batched together; batching
# changes floating-point results and breaks reproducibility at temperature 0.
set -euo pipefail

MODEL="${MODEL:-bartowski/Qwen2.5-7B-Instruct-GGUF:Q4_K_M}"
PORT="${PORT:-8080}"
CTX="${CTX:-16384}"

exec llama-server -hf "$MODEL" --port "$PORT" -c "$CTX" --parallel 1 --jinja "$@"
