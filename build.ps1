# One-command Windows build: portable exe + installer. Usage: .\build.ps1
Set-Location $PSScriptRoot
$iscc = "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) { $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" }
if (-not (Test-Path $iscc)) { $c = Get-Command ISCC.exe -ErrorAction SilentlyContinue; if ($c) { $iscc = $c.Source } }
if (-not (Test-Path $iscc)) { throw "Inno Setup 6 not found (https://jrsoftware.org/isdl.php)" }

python -m pip install -r requirements.txt pyinstaller
if ($LASTEXITCODE) { throw "pip failed" }
$version = (python -c "import humantyper; print(humantyper.__version__)").Trim()

# Work/dist dirs outside the repo: OneDrive reparse points can make PyInstaller fail to clean folders in place.
$work = Join-Path $env:TEMP "HumanTyper-pyi-build"
$tmpDist = Join-Path $env:TEMP "HumanTyper-pyi-dist"
cmd /c "rmdir /s /q `"$tmpDist`" 2>nul"
python -m PyInstaller packaging\HumanTyper.spec --noconfirm --clean --workpath $work --distpath $tmpDist
if ($LASTEXITCODE) { throw "PyInstaller failed" }

New-Item -ItemType Directory -Force dist | Out-Null
cmd /c "rmdir /s /q dist\HumanTyper 2>nul"
Remove-Item dist\HumanTyper.exe -Force -ErrorAction SilentlyContinue
Copy-Item "$tmpDist\HumanTyper.exe" dist\HumanTyper.exe
robocopy "$tmpDist\HumanTyper" dist\HumanTyper /E /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "copy to dist failed" }

& $iscc "/DAppVersion=$version" packaging\installer.iss
if ($LASTEXITCODE) { throw "Inno Setup failed" }

Write-Host "`nBuilt:"
Get-ChildItem dist\HumanTyper.exe, "dist\HumanTyper-Setup-$version.exe" | ForEach-Object { "{0}  {1:N1} MB" -f $_.FullName, ($_.Length / 1MB) }
