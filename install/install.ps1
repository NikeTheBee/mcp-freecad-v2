# One command for the whole workshop (docs/FEUILLE_DE_ROUTE_ATELIER.md §8.2).
#   powershell -ExecutionPolicy Bypass -File install\install.ps1 -Home D:\Atelier -Packs tout -Register
# No admin rights. Nothing is run from the Internet directly: every download is checked against
# the SHA256 pinned in install/tools.lock.json and pixi.lock before use.
param(
    [Alias("Home")][string]$Root = "",
    [string]$Packs = "socle",
    [switch]$Register,
    [switch]$SkipValidate,
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$py = $null
foreach ($c in @(@("py", "-3.13"), @("py", "-3.12"), @("py", "-3.11"), @("python", "-I"))) {
    try {
        & $c[0] $c[1] -c "import sys; assert sys.version_info >= (3, 11)" 2>$null
        if ($LASTEXITCODE -eq 0) { $py = $c; break }
    } catch { }
}
if (-not $py) { throw "Python 3.11+ is required (https://www.python.org/downloads/windows/)" }
$a = @("$PSScriptRoot\atelier_install.py", "--packs", $Packs)
if ($Root) { $a += @("--home", $Root) }
if ($Register) { $a += "--register" }
if ($SkipValidate) { $a += "--skip-validate" }
if ($DryRun) { $a += "--dry-run" }
& $py[0] $py[1] @a
exit $LASTEXITCODE
