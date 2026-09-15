#!/usr/bin/env bash
# Recreate the binary dependencies at this prefix; never installs system packages.
set -euo pipefail
source "$(dirname "$0")/env.sh"
task_root="$MOONRAY_LOCAL_ROOT"
mkdir -p "$task_root"/{tools,downloads,logs,cache,tmp,sources}
export MAMBA_ROOT_PREFIX="$task_root/cache/mamba"
export XDG_CACHE_HOME="$task_root/cache"
export TMPDIR="$task_root/tmp"
if [[ ! -x "$task_root/tools/bin/micromamba" ]]; then
  curl -fL --retry 3 https://micro.mamba.pm/api/micromamba/linux-64/latest \
    -o "$task_root/downloads/micromamba.tar.bz2"
  tar -xjf "$task_root/downloads/micromamba.tar.bz2" -C "$task_root/tools" bin/micromamba
fi
if [[ ! -f "$task_root/deps/conda-meta/history" ]]; then
  "$task_root/tools/bin/micromamba" create --no-rc -y -p "$task_root/deps" \
    -f "$MOONRAY_LOCAL_SCRIPTS/dependencies-explicit.txt"
fi
if [[ ! -f "$task_root/downloads/optix-7.6.0.tar.gz" ]]; then
  curl -fL --retry 3 https://codeload.github.com/NVIDIA/optix-dev/tar.gz/refs/tags/v7.6.0 \
    -o "$task_root/downloads/optix-7.6.0.tar.gz"
fi
tar -xzf "$task_root/downloads/optix-7.6.0.tar.gz" -C "$task_root/sources"
cp -a "$task_root/sources/optix-dev-7.6.0/include/." "$task_root/deps/include/"
