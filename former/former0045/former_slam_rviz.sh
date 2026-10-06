#!/bin/bash
set -euo pipefail

# This script is a wrapper that calls the unified docker run script.
# Original parameters and options can still be passed.

DIR="$(cd "$(dirname "$0")" && pwd)"
"$DIR/run_former_env.sh" slam-rviz "$@"
