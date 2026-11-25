#!/usr/bin/env bash
set -euo pipefail

# Upload version-prefixed COGs, GeoJSON overlays, manifest.json, and an unversioned
# latest.json pointer to Cloudflare R2.
#
# Every released version is uploaded under <VERSION>/<filename> with immutable caching
# and refuses to overwrite an existing prefix in the bucket. Only latest.json is
# unversioned and served with short caching (max-age=300).
#
# Usage:
#   BUCKET_NAME=bay-urban-flood-map-data ./deploy/upload_r2.sh --dry-run
#   BUCKET_NAME=bay-urban-flood-map-data ./deploy/upload_r2.sh
#   VERSION=v0.6.1 PUBLIC_URL=https://pub-xxx.r2.dev BUCKET_NAME=bay-urban-flood-map-data ./deploy/upload_r2.sh

DRY_RUN="${DRY_RUN:-0}"
for arg in "$@"; do
  case "${arg}" in
    --dry-run)
      DRY_RUN=1
      ;;
    -h|--help)
      sed -n '4,14p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "Error: unknown argument '${arg}'. Use --dry-run or --help." >&2
      exit 1
      ;;
  esac
done

BUCKET_NAME="${BUCKET_NAME:-bay-urban-flood-map-data}"
DATA_DIR="${DATA_DIR:-data/web_outputs}"
PUBLIC_URL="${PUBLIC_URL:-}"
VERSION="${VERSION:-}"
IMMUTABLE_CACHE="public, max-age=31536000, immutable"
POINTER_CACHE="public, max-age=300"

if [[ ! -d "${DATA_DIR}" ]]; then
  echo "Error: ${DATA_DIR} does not exist." >&2
  exit 1
fi

MANIFEST_FILE="${DATA_DIR}/manifest.json"
if [[ ! -f "${MANIFEST_FILE}" ]]; then
  echo "Error: ${MANIFEST_FILE} does not exist. Run pipeline/04_export_cog.py first." >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: python3 is required to parse ${MANIFEST_FILE}." >&2
  exit 1
fi

MANIFEST_VERSION="$(python3 - "${MANIFEST_FILE}" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as f:
    manifest = json.load(f)
raw = str(manifest.get("version", "")).strip().strip("/")
if not raw:
    sys.exit("Error: manifest.json is missing a top-level 'version' field.")
print(raw if raw.startswith("v") else f"v{raw}")
PY
)"

if [[ -z "${VERSION}" ]]; then
  VERSION="${MANIFEST_VERSION}"
else
  VERSION="${VERSION#/}"
  VERSION="${VERSION%/}"
  if [[ ! "${VERSION}" =~ ^v ]]; then
    VERSION="v${VERSION}"
  fi
fi

if [[ ! "${VERSION}" =~ ^v[0-9]+\.[0-9]+(\.[0-9]+)?([-.][A-Za-z0-9.]+)?$ ]]; then
  echo "Error: invalid VERSION '${VERSION}'. Expected SemVer like v0.6.1." >&2
  exit 1
fi

if [[ "${VERSION}" != "${MANIFEST_VERSION}" ]]; then
  echo "Error: VERSION (${VERSION}) does not match manifest.json version (${MANIFEST_VERSION})." >&2
  echo "Update export.manifest_version in configs/sf_citywide.yml and re-run pipeline/04_export_cog.py --overlays-only." >&2
  exit 1
fi

mapfile -t ASSET_FILES < <(python3 - "${MANIFEST_FILE}" "${DATA_DIR}" "${VERSION}" <<'PY'
import json
import os
import sys

manifest_path, data_dir, version = sys.argv[1:4]
with open(manifest_path, "r", encoding="utf-8") as f:
    manifest = json.load(f)

urls = []
for sc in manifest.get("scenarios", []):
    if isinstance(sc, dict) and sc.get("url"):
        urls.append(str(sc["url"]))

def collect_urls(obj):
    if isinstance(obj, dict):
        if "url" in obj and isinstance(obj["url"], str):
            urls.append(obj["url"])
        for val in obj.values():
            collect_urls(val)
    elif isinstance(obj, list):
        for item in obj:
            collect_urls(item)

collect_urls(manifest.get("overlays", {}))

if not urls:
    sys.exit("Error: manifest.json contains no scenario or overlay asset URLs.")

seen = set()
resolved = []
expected_prefix_a = f"/data/{version}/"
expected_prefix_b = f"{version}/"

for url in urls:
    if not (url.startswith(expected_prefix_a) or url.startswith(expected_prefix_b)):
        sys.exit(
            f"Error: manifest asset URL '{url}' does not include version prefix '{version}/'."
        )
    filename = os.path.basename(url)
    if filename in seen:
        continue
    seen.add(filename)
    candidate_flat = os.path.join(data_dir, filename)
    candidate_ver = os.path.join(data_dir, version, filename)
    if os.path.isfile(candidate_ver):
        resolved.append(f"{filename}\t{candidate_ver}")
    elif os.path.isfile(candidate_flat):
        resolved.append(f"{filename}\t{candidate_flat}")
    else:
        sys.exit(f"Error: asset referenced by manifest.json is missing: {candidate_flat}")

for entry in sorted(resolved):
    print(entry)
PY
)

if [[ ${#ASSET_FILES[@]} -eq 0 ]]; then
  echo "Error: no assets found in ${MANIFEST_FILE}." >&2
  exit 1
fi

content_type_for() {
  local filename="$1"
  case "${filename}" in
    *.tif|*.tiff) echo "image/tiff" ;;
    *.geojson) echo "application/geo+json; charset=utf-8" ;;
    *.pmtiles) echo "application/octet-stream" ;;
    *.json) echo "application/json; charset=utf-8" ;;
    *) echo "application/octet-stream" ;;
  esac
}

check_key_absent() {
  local key="$1"
  local output=""
  local status=0
  output="$(npx wrangler r2 object get "${BUCKET_NAME}/${key}" --file /dev/null --remote 2>&1)" || status=$?
  if [[ ${status} -eq 0 ]]; then
    echo "Error: version prefix '${VERSION}/' already exists in r2://${BUCKET_NAME} (found ${key})." >&2
    echo "Refusing to overwrite a released version. Bump VERSION to publish a new release." >&2
    exit 1
  fi
  if ! grep -qiE "(does not exist|NoSuchKey|not found|404)" <<< "${output}"; then
    echo "Error: failed to check whether r2://${BUCKET_NAME}/${key} exists:" >&2
    echo "${output}" >&2
    exit 1
  fi
}

upload_file() {
  local file_path="$1"
  local key="$2"
  local content_type="$3"
  local cache_control="$4"

  if [[ "${DRY_RUN}" == "1" ]]; then
    echo "[dry-run] ${key} (${content_type}; ${cache_control}) <- ${file_path}"
    return 0
  fi

  echo "Uploading ${key} (${content_type}) -> r2://${BUCKET_NAME}/${key}"
  npx wrangler r2 object put "${BUCKET_NAME}/${key}" \
    --file "${file_path}" \
    --content-type "${content_type}" \
    --cache-control "${cache_control}" \
    --remote
}

FIRST_ASSET_NAME="${ASSET_FILES[0]%%$'\t'*}"
if [[ "${DRY_RUN}" == "1" ]]; then
  echo "[dry-run] Bucket: r2://${BUCKET_NAME} | Version: ${VERSION}"
else
  echo "Checking that prefix ${VERSION}/ does not already exist in r2://${BUCKET_NAME}..."
  check_key_absent "${VERSION}/manifest.json"
  check_key_absent "${VERSION}/${FIRST_ASSET_NAME}"
fi

SAMPLE_COG_KEY=""
for row in "${ASSET_FILES[@]}"; do
  filename="${row%%$'\t'*}"
  file_path="${row#*$'\t'}"
  key="${VERSION}/${filename}"
  ctype="$(content_type_for "${filename}")"
  upload_file "${file_path}" "${key}" "${ctype}" "${IMMUTABLE_CACHE}"
  if [[ -z "${SAMPLE_COG_KEY}" && "${filename}" == *depth_3857.tif ]]; then
    SAMPLE_COG_KEY="${key}"
  fi
done

upload_file "${MANIFEST_FILE}" "${VERSION}/manifest.json" "application/json; charset=utf-8" "${IMMUTABLE_CACHE}"

LATEST_TMP="$(mktemp)"
trap 'rm -f "${LATEST_TMP}"' EXIT
printf '{\n  "version": "%s",\n  "manifest": "%s/manifest.json"\n}\n' "${VERSION}" "${VERSION}" > "${LATEST_TMP}"

upload_file "${LATEST_TMP}" "latest.json" "application/json; charset=utf-8" "${POINTER_CACHE}"

if [[ "${DRY_RUN}" != "1" && -n "${PUBLIC_URL}" ]]; then
  POINTER_URL="${PUBLIC_URL%/}/latest.json"
  echo "Verifying pointer ${POINTER_URL}..."
  curl -fsSI "${POINTER_URL}" | grep -iE "^(HTTP/|content-type|cache-control)"

  if [[ -n "${SAMPLE_COG_KEY}" ]]; then
    SAMPLE_COG="${PUBLIC_URL%/}/${SAMPLE_COG_KEY}"
    echo "Verifying HTTP Range (206 Partial Content) and CORS on ${SAMPLE_COG}..."
    curl -fsSI \
      -H "Range: bytes=0-16383" \
      -H "Origin: https://bay-urban-flood-map.pages.dev" \
      "${SAMPLE_COG}" | grep -iE "^(HTTP/|accept-ranges|content-range|content-type|cache-control|access-control-allow-origin)"
  fi
fi
