# One-time setup (Windows): download the free OpenStreetMap extract for
# Alabama and build walking and cycling routing data from it for OSRM.
#
#   .\scripts\prepare-alabama-map.ps1        # then: docker compose --profile alabama up -d
#
# Needs Docker Desktop and ~2 GB of free disk. Takes roughly 5-15 minutes.
param(
    [string]$RegionUrl = 'https://download.geofabrik.de/north-america/us/alabama-latest.osm.pbf',
    [string]$OsrmImage = 'ghcr.io/project-osrm/osrm-backend:v5.27.1'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$data = Join-Path $root 'mapdata'
New-Item -ItemType Directory -Force -Path (Join-Path $data 'foot'), (Join-Path $data 'bike') | Out-Null

Write-Host 'Downloading Alabama map data from Geofabrik...'
Invoke-WebRequest -Uri $RegionUrl -OutFile (Join-Path $data 'region.osm.pbf')

foreach ($profile in 'foot', 'bike') {
    $lua = if ($profile -eq 'bike') { 'bicycle' } else { 'foot' }
    $dir = Join-Path $data $profile
    Copy-Item (Join-Path $data 'region.osm.pbf') (Join-Path $dir 'region.osm.pbf') -Force
    Write-Host "Building $profile routing data..."
    docker run --rm -v "${dir}:/data" $OsrmImage osrm-extract -p "/opt/$lua.lua" /data/region.osm.pbf
    if ($LASTEXITCODE -ne 0) { throw "osrm-extract failed for $profile" }
    docker run --rm -v "${dir}:/data" $OsrmImage osrm-partition /data/region.osrm
    if ($LASTEXITCODE -ne 0) { throw "osrm-partition failed for $profile" }
    docker run --rm -v "${dir}:/data" $OsrmImage osrm-customize /data/region.osrm
    if ($LASTEXITCODE -ne 0) { throw "osrm-customize failed for $profile" }
    Remove-Item (Join-Path $dir 'region.osm.pbf')
}

Write-Host ''
Write-Host 'Done. Start the routing servers with:  docker compose --profile alabama up -d'
Write-Host 'and set in backend/.env:'
Write-Host '  ROUTE_PROVIDER=osrm'
Write-Host '  OSRM_FOOT_URL=http://localhost:5001/route/v1/foot'
Write-Host '  OSRM_BIKE_URL=http://localhost:5002/route/v1/bike'
