#Requires -Version 7.0
param([switch]$Uninstall)

$ErrorActionPreference = "Stop"
$home_dir = Join-Path $HOME ".cine-recast"
$venv = Join-Path $home_dir "venv"
$bin = Join-Path $home_dir "bin"
$launcher = Join-Path $bin "cine-recast.ps1"
$skillSource = Join-Path $PSScriptRoot "skill"
# The agents whose global skill folder receives the skill: Claude Code and Codex.
$agents = @(
    [pscustomobject]@{ Name = "Claude Code"; Home = (Join-Path $HOME ".claude") },
    [pscustomobject]@{ Name = "Codex"; Home = (Join-Path $HOME ".codex") }
)
foreach ($agent in $agents) {
    $agent | Add-Member -NotePropertyName Target -NotePropertyValue (Join-Path $agent.Home "skills\cine-recast")
}
$skillTargets = @($agents | ForEach-Object { $_.Target })

function Test-OwnSkill {
    # True when the folder holds a SKILL.md that names the cine-recast skill.
    param([string]$Folder)
    $file = Join-Path $Folder "SKILL.md"
    if (-not (Test-Path $file -PathType Leaf)) { return $false }
    return [bool](Select-String -Path $file -Pattern '^name:\s*cine-recast\s*$' -Quiet)
}

if ($Uninstall) {
    if (Test-Path $venv) { Remove-Item -Recurse -Force $venv }
    if (Test-Path $launcher) { Remove-Item -Force $launcher }
    foreach ($target in $skillTargets) {
        if (-not (Test-Path $target)) { continue }
        if (Test-OwnSkill $target) { Remove-Item -Recurse -Force $target }
        else { Write-Output "left in place (not the cine-recast skill): $target" }
    }
    Write-Output "uninstalled cine-recast"
    return
}

# Refuse before anything is installed: a folder that is not this skill is never replaced.
if (-not (Test-Path (Join-Path $skillSource "SKILL.md") -PathType Leaf)) {
    throw "skill source not found: $skillSource"
}
foreach ($target in $skillTargets) {
    if ((Test-Path $target) -and -not (Test-OwnSkill $target)) {
        throw "$target exists and is not the cine-recast skill (no SKILL.md with 'name: cine-recast' in it); move it away and run again."
    }
}

# The tool runs ffmpeg and ffprobe; installing works without them, using the tool does not.
foreach ($tool in "ffmpeg", "ffprobe") {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        Write-Warning "$tool was not found on PATH; cine-recast needs ffmpeg and ffprobe, install ffmpeg and add it to PATH before using the tool."
    }
}

function Test-Python312 {
    param([string]$Exe, [string[]]$PrefixArgs)
    try {
        $out = & $Exe @PrefixArgs -c "import sys; print(int(sys.version_info >= (3, 12)))" 2>$null
        return ($LASTEXITCODE -eq 0 -and "$out".Trim() -eq "1")
    } catch {
        return $false
    }
}

$pyExe = $null
$pyArgs = @()
if ((Get-Command py -ErrorAction SilentlyContinue) -and (Test-Python312 "py" @("-3.12"))) {
    $pyExe = "py"
    $pyArgs = @("-3.12")
} elseif ((Get-Command python -ErrorAction SilentlyContinue) -and (Test-Python312 "python" @())) {
    $pyExe = "python"
}
if (-not $pyExe) { throw "Python 3.12 or newer was not found (tried 'py -3.12' and 'python')." }

if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
    New-Item -ItemType Directory -Force $home_dir | Out-Null
    & $pyExe @pyArgs -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
}
$venvPython = Join-Path $venv "Scripts\python.exe"

$repo = $PSScriptRoot
& $venvPython -m pip install --disable-pip-version-check -q -e "$repo[dev]"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

New-Item -ItemType Directory -Force $bin | Out-Null
# The launcher decodes the tool's UTF-8 output as UTF-8 while it runs, so output captured inside
# PowerShell (paths with Hangul, the JSON of jobs next) stays intact on a console with another code
# page; the previous console encoding is restored afterwards. Without a console it runs unchanged.
$launcherText = @'
$exe = Join-Path $HOME ".cine-recast\venv\Scripts\cine-recast.exe"
$previous = $env:PYTHONUTF8
$env:PYTHONUTF8 = "1"
$previousOutput = $null
try {
    $previousOutput = [Console]::OutputEncoding
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
} catch {
    $previousOutput = $null
}
try {
    & $exe @args
    $code = $LASTEXITCODE
} finally {
    if ($null -ne $previousOutput) {
        try { [Console]::OutputEncoding = $previousOutput } catch { }
    }
    if ($null -eq $previous) { Remove-Item Env:PYTHONUTF8 -ErrorAction SilentlyContinue }
    else { $env:PYTHONUTF8 = $previous }
}
exit $code
'@
Set-Content -Path $launcher -Value $launcherText -Encoding utf8

# Register the skill for each agent whose home folder exists: its target becomes a fresh copy of skill/.
$registered = @()
foreach ($agent in $agents) {
    if (-not (Test-Path $agent.Home -PathType Container)) {
        Write-Output "skipped $($agent.Name): $($agent.Home) does not exist"
        continue
    }
    if (Test-Path $agent.Target) { Remove-Item -Recurse -Force $agent.Target }
    New-Item -ItemType Directory -Force (Split-Path $agent.Target -Parent) | Out-Null
    Copy-Item -Recurse -Path $skillSource -Destination $agent.Target
    Write-Output "registered the skill for $($agent.Name): $($agent.Target)"
    $registered += $agent.Name
}

$version = & $venvPython -c "from importlib.metadata import version; print(version('cine-recast'))"
if ($registered.Count -gt 0) {
    Write-Output "installed cine-recast $version (skill registered for $($registered -join ', '))"
} else {
    Write-Output "installed cine-recast $version (skill not registered: neither ~/.claude nor ~/.codex exists)"
}
