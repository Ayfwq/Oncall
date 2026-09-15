$root=(Resolve-Path "$PSScriptRoot\..").Path
Set-Location $root
& "$root\.venv\Scripts\oncall-api.exe"
