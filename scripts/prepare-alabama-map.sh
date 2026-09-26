#!/usr/bin/env bash
# One-time setup: download the free OpenStreetMap extract for Alabama and
# build walking and cycling routing data from it for OSRM.
#
#   ./scripts/prepare-alabama-map.sh          # then: docker compose --profile alabama up -d
#
# Needs Docker and ~2 GB of free disk. Takes roughly 5-15 minutes. Re-run it
# to pick up newer map data (Geofabrik refreshes the extract daily).
set -euo pipefail

REGION_URL="${REGION_URL:-https://download.geofabrik.de/north-america/us/alabama-latest.osm.pbf}"
OSRM_IMAGE="${OSRM_IMAGE:-ghcr.io/project-osrm/osrm-backend:v5.27.1}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA="$ROOT/mapdata"

mkdir -p "$DATA/foot" "$DATA/bike"
echo "Downloading Alabama map data from Geofabrik..."
curl -fL --retry 3 -o "$DATA/region.osm.pbf" "$REGION_URL"

for profile in foot bike; do
  lua=$([ "$profile" = "bike" ] && echo bicycle || echo foot)
  cp "$DATA/region.osm.pbf" "$DATA/$profile/region.osm.pbf"
  echo "Building $profile routing data..."
  docker run --rm -v "$DATA/$profile:/data" "$OSRM_IMAGE" osrm-extract -p "/opt/$lua.lua" /data/region.osm.pbf
  docker run --rm -v "$DATA/$profile:/data" "$OSRM_IMAGE" osrm-partition /data/region.osrm
  docker run --rm -v "$DATA/$profile:/data" "$OSRM_IMAGE" osrm-customize /data/region.osrm
  rm "$DATA/$profile/region.osm.pbf"
done

echo
echo "Done. Start the routing servers with:"
echo "  docker compose --profile alabama up -d"
echo "and set in backend/.env:"
echo "  ROUTE_PROVIDER=osrm"
echo "  OSRM_FOOT_URL=http://localhost:5001/route/v1/foot"
echo "  OSRM_BIKE_URL=http://localhost:5002/route/v1/bike"
echo "(inside docker compose the backend uses http://osrm-foot:5000/... automatically)"
