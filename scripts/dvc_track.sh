#!/usr/bin/env bash
# Track every locally built data version with DVC, then push the data to the DVC remote.
# A version is tracked once: data versions are immutable, so an existing pointer is kept.
# Each pointer (<version>.dvc) gets a `desc` with the manifest description and its SHA-256.
# Afterwards commit data/manifests/*.yaml and the new .dvc files with Git.
set -euo pipefail
cd "$(dirname "$0")/.."
DVC=${DVC:-"uvx --from \"dvc[gs]==3.67.1\" dvc"}

for manifest in data/manifests/*.yaml; do
  version=$(basename "$manifest" .yaml)
  case "$version" in
    raw-*) dir="data/raw/$version" ;;
    canonical-*) dir="data/processed/canonical/$version" ;;
    synthetic-*) dir="data/synthetic/$version" ;;
    *) continue ;;
  esac
  if [ -f "$dir.dvc" ]; then echo "$version: already tracked"; continue; fi
  if [ ! -d "$dir" ]; then echo "$version: $dir not present locally, skipped"; continue; fi
  $DVC add "$dir"
  description=$(sed -n 's/^description: //p' "$manifest" | tr -d "'\"")
  digest=$(shasum -a 256 "$manifest" | cut -d' ' -f1)
  awk -v version="$version" -v desc="$description (manifest $manifest sha256 $digest)" \
    '{ print } $0 == "  path: " version { print "  desc: \"" desc "\"" }' \
    "$dir.dvc" > "$dir.dvc.tmp" && mv "$dir.dvc.tmp" "$dir.dvc"
  echo "$version: tracked in $dir.dvc"
done
$DVC push
