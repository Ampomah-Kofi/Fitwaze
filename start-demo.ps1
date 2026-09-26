# Start the FitWaze demo from the project folder.
# Same as running backend\start-demo.ps1; this copy saves the "cd backend".
param([int]$Port = 8000)
& (Join-Path $PSScriptRoot 'backend\start-demo.ps1') -Port $Port
