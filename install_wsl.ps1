# MolDynStudio WSL2 Setup
# Run as Administrator in PowerShell.

$ErrorActionPreference = "Stop"

Write-Host "=== MolDynStudio WSL2 Setup ===" -ForegroundColor Cyan

# --- Admin check -----------------------------------------------------------
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "Please run this script as Administrator."
    exit 1
}

# --- Virtualization check (firmware / BIOS) --------------------------------
Write-Host "Checking CPU virtualization (BIOS/UEFI)..." -ForegroundColor Yellow
try {
    $info = Get-ComputerInfo -Property `
        'HyperVRequirementVirtualizationFirmwareEnabled',`
        'HyperVRequirementSecondLevelAddressTranslation',`
        'HyperVRequirementVMMonitorModeExtensions'
} catch {
    Write-Warning "Could not query Hyper-V firmware requirements: $($_.Exception.Message)"
    $info = $null
}

if ($info -and $info.HyperVRequirementVirtualizationFirmwareEnabled -eq $false) {
    Write-Host ""
    Write-Host "[BLOCKER] CPU virtualization is DISABLED in BIOS/UEFI." -ForegroundColor Red
    Write-Host ""
    Write-Host "WSL2 requires hardware virtualization. Enable it before continuing:" -ForegroundColor Yellow
    Write-Host "  1. Reboot and enter BIOS/UEFI (Del / F2 / F10 / F12 / Esc at boot)."
    Write-Host "  2. Find one of these settings and set it to ENABLED:"
    Write-Host "       Intel CPU: 'Intel Virtualization Technology' / 'Intel VT-x' / 'VT-d'"
    Write-Host "       AMD   CPU: 'SVM Mode' / 'AMD-V'"
    Write-Host "     Usually under: Advanced -> CPU Configuration, or Security."
    Write-Host "  3. Save & Exit. After Windows boots, re-run this installer."
    Write-Host ""
    pause
    exit 2
}

# --- Feature helpers -------------------------------------------------------
function Test-WslReady {
    try {
        $status = wsl --status 2>&1
        return ($LASTEXITCODE -eq 0 -and ($status -match "Default Version:\s*2|Vers.o Padr.o:\s*2"))
    } catch {
        return $false
    }
}

function Test-AnyDistro {
    try {
        $distros = wsl --list --quiet 2>&1
        if ($LASTEXITCODE -ne 0) { return $false }
        $clean = ($distros -replace "`0", "").Split("`n") | Where-Object { $_.Trim() -ne "" }
        $real = $clean | Where-Object { $_ -notmatch "no distributions|nao tem distribui|n.o tem distribui" }
        return ($real.Count -gt 0)
    } catch {
        return $false
    }
}

function Get-TargetDistro {
    try {
        $verbose = (wsl --list --verbose 2>&1) -replace "`0", ""
        foreach ($line in ($verbose -split "`n")) {
            $trimmed = $line.Trim()
            if ($trimmed.StartsWith("*")) {
                $name = (($trimmed.TrimStart("*").Trim()) -split "\s+")[0]
                if ($name) { return $name }
            }
        }
        $distros = (wsl --list --quiet 2>&1) -replace "`0", ""
        foreach ($line in ($distros -split "`n")) {
            $name = $line.Trim()
            if ($name) { return $name }
        }
        return $null
    } catch {
        return $null
    }
}

function Test-CondaInWsl {
    param([string]$Distro)
    try {
        wsl -d $Distro -- bash -lc 'command -v conda >/dev/null 2>&1 || test -x "\$HOME/miniforge3/bin/conda"' 2>&1 | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Install-MiniforgeInWsl {
    param([string]$Distro)
    $script = @'
set -euo pipefail
if command -v conda >/dev/null 2>&1; then
  echo "Conda already available: $(command -v conda)"
  exit 0
fi
if [ -x "$HOME/miniforge3/bin/conda" ]; then
  echo "Miniforge already installed at $HOME/miniforge3"
  exit 0
fi

echo "Installing prerequisites..."
if ! command -v curl >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y curl ca-certificates bzip2
fi

echo "Downloading Miniforge..."
curl -L https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh -o /tmp/miniforge.sh

echo "Installing Miniforge to $HOME/miniforge3..."
bash /tmp/miniforge.sh -b -p "$HOME/miniforge3"
rm -f /tmp/miniforge.sh

PROFILE_LINE='. "$HOME/miniforge3/etc/profile.d/conda.sh"'
touch "$HOME/.profile"
grep -qxF "$PROFILE_LINE" "$HOME/.profile" || echo "$PROFILE_LINE" >> "$HOME/.profile"
. "$HOME/miniforge3/etc/profile.d/conda.sh"
conda config --set channel_priority strict
conda --version
'@
    $script | wsl -d $Distro -- bash -s
    return $LASTEXITCODE
}

# --- Enable Windows features ----------------------------------------------
if (-not (Test-WslReady)) {
    Write-Host "Enabling required Windows features..." -ForegroundColor Yellow
    Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -NoRestart | Out-Null
    Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -NoRestart | Out-Null

    Write-Host "Installing WSL2 base..." -ForegroundColor Yellow
    wsl --install --no-distribution
    wsl --set-default-version 2

    Write-Host ""
    Write-Host "Reboot required before WSL2 can start a distro." -ForegroundColor Green
    Write-Host "After reboot, re-launch MolDynStudio.exe and click 'Install WSL2' again." -ForegroundColor Green
    pause
    exit 0
}

# --- Install or reuse Ubuntu distro ----------------------------------------
$targetDistro = Get-TargetDistro
if (-not $targetDistro) {
    Write-Host "Installing Ubuntu distribution..." -ForegroundColor Yellow
    wsl --install -d Ubuntu
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "[FAIL] wsl --install returned exit code $LASTEXITCODE." -ForegroundColor Red
        Write-Host "Run 'wsl --status' and 'wsl --list --online' manually for details." -ForegroundColor Yellow
        pause
        exit 3
    }
    Write-Host ""
    Write-Host "Ubuntu installed. The first time you open it, set a username + password." -ForegroundColor Green
    Write-Host "After that, click 'Re-check' in MolDynStudio." -ForegroundColor Green
    pause
    exit 0
}

Write-Host "Using WSL distro: $targetDistro" -ForegroundColor Cyan

if (-not (Test-CondaInWsl -Distro $targetDistro)) {
    Write-Host "Installing Miniforge inside $targetDistro..." -ForegroundColor Yellow
    Install-MiniforgeInWsl -Distro $targetDistro
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "[FAIL] Miniforge installation returned exit code $LASTEXITCODE." -ForegroundColor Red
        Write-Host "Open Ubuntu once, finish user setup if prompted, then run this installer again." -ForegroundColor Yellow
        pause
        exit 4
    }
}

Write-Host "WSL2, $targetDistro, and Conda are ready." -ForegroundColor Green
Write-Host "MolDynStudio can now create the moldynstudio environment and run WSL-backed GROMACS commands." -ForegroundColor Cyan
