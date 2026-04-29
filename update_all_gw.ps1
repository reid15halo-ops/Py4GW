# Read accounts from accounts.json and start each GW client with login credentials
# This forces the GW auto-update to complete so the Py4GW launcher works afterwards

$json = Get-Content -Path "accounts.json" -Raw | ConvertFrom-Json

# Try Dervish Way first, then Standard
$teamName = if ($json.PSObject.Properties.Name -contains "Dervish Way") { "Dervish Way" } else { "Standard" }
$accounts = $json.$teamName

Write-Host "Updating $($accounts.Count) GW clients (team: $teamName)..." -ForegroundColor Cyan
Write-Host ""

foreach ($acc in $accounts) {
    $exe = $acc.gw_path.Replace("/", "\")
    $email = $acc.email
    $pw = $acc.password
    $char = $acc.character_name

    if (-not (Test-Path $exe)) {
        Write-Host "  SKIP: $exe not found" -ForegroundColor Red
        continue
    }

    Write-Host "  Starting: $char ($exe)" -ForegroundColor Yellow
    $args = "-email `"$email`" -password `"$pw`" -character `"$char`""
    $proc = Start-Process -FilePath $exe -ArgumentList $args -PassThru

    Write-Host "    PID: $($proc.Id) - waiting 30 seconds for login + update..."
    Start-Sleep -Seconds 30

    Write-Host "    Killing Gw.exe..."
    Get-Process -Name "Gw" -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Seconds 5
    Write-Host "    Done." -ForegroundColor Green
    Write-Host ""
}

Write-Host "All GW clients updated. Run the Py4GW Launcher now." -ForegroundColor Green
