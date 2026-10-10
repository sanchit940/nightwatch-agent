#!/usr/bin/env bash
# Delete the kind cluster "nightwatch".
set -euo pipefail
kind delete cluster --name nightwatch
