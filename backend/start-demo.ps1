param([int]$Port = 8000)

$ErrorActionPreference = 'Stop'
$fitwazePython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (!(Test-Path -LiteralPath $fitwazePython)) {
    throw 'Create backend/.venv and install requirements.txt first.'
}

# Use the existing local demo database and secrets in backend/.env.
# Reload ensures API code and the HTML served from disk stay in sync.
$env:DATABASE_URL = 'sqlite:///' + (Join-Path $PSScriptRoot 'demo.db').Replace('\', '/')
Push-Location $PSScriptRoot
try {
    & $fitwazePython -c 'from app.main import app; from app.db import Base, engine; Base.metadata.create_all(engine)'
    if ($LASTEXITCODE -ne 0) { throw 'Could not initialize the demo database.' }
    & $fitwazePython -m uvicorn app.main:app --host 127.0.0.1 --port $Port --reload
} finally {
    Pop-Location
}
