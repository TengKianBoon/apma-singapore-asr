Param(
    [string]$ImageTag = "apma-v5:dev"
)

Write-Host "Building Docker image ($ImageTag)..."
docker build -t $ImageTag .

Write-Host "Running Docker container (dry-run)..."
$jobsPath = Join-Path (Get-Location) "jobs"
docker run --rm -e DRY_RUN=true -v "${jobsPath}:/app/jobs" $ImageTag

Write-Host "Done."
