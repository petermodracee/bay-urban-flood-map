#!/usr/bin/env bash
set -euo pipefail

# Upload v0.2 citywide COGs, GeoJSON overlays, and manifest.json to Cloudflare R2.
# Usage:
#   BUCKET_NAME=bay-urban-flood-map-data ./deploy/upload_r2.sh
#   PUBLIC_URL=https://pub-xxx.r2.dev BUCKET_NAME=bay-urban-flood-map-data ./deploy/upload_r2.sh

BUCKET_NAME="${BUCKET_NAME:-bay-urban-flood-map-data}"
DATA_DIR="${DATA_DIR:-data/web_outputs}"
PUBLIC_URL="${PUBLIC_URL:-}"

if [[ ! -d "${DATA_DIR}" ]]; then
  echo "Error: ${DATA_DIR} does not exist." >&2
  exit 1
fi

upload_file() {
  local file_path="$1"
  local content_type="$2"
  local cache_control="$3"
  local key
  key="$(basename "${file_path}")"
  echo "Uploading ${key} (${content_type}) -> r2://${BUCKET_NAME}/${key}"
  npx wrangler r2 object put "${BUCKET_NAME}/${key}" \
    --file "${file_path}" \
    --content-type "${content_type}" \
    --cache-control "${cache_control}" \
    --remote
}

shopt -s nullglob
for tif in "${DATA_DIR}"/sf_citywide_*_3857.tif; do
  upload_file "${tif}" "image/tiff" "public, max-age=31536000, immutable"
done

for gj in "${DATA_DIR}/sf_sfpuc_100yr_benchmark.geojson" "${DATA_DIR}/sf_311_20221231_floods.geojson"; do
  if [[ -f "${gj}" ]]; then
    upload_file "${gj}" "application/geo+json; charset=utf-8" "public, max-age=86400"
  fi
done

if [[ -f "${DATA_DIR}/manifest.json" ]]; then
  upload_file "${DATA_DIR}/manifest.json" "application/json; charset=utf-8" "public, max-age=300"
fi

if [[ -n "${PUBLIC_URL}" ]]; then
  SAMPLE_COG="${PUBLIC_URL%/}/sf_citywide_100yr_slr0ft_depth_3857.tif"
  echo "Verifying HTTP Range (206 Partial Content) on ${SAMPLE_COG}..."
  curl -fsSI -H "Range: bytes=0-16383" "${SAMPLE_COG}" | grep -iE "^(HTTP/|accept-ranges|content-range|content-type)"
fi
