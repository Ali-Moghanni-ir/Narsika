#requires -Version 5.1
[CmdletBinding()]
param([switch]$LoadOnly, [switch]$WSL)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Find-Docker {
    $command = Get-Command docker.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    foreach ($root in @("$env:LOCALAPPDATA\Programs\DockerDesktop", "$env:ProgramFiles\Docker\Docker")) {
        $candidate = Join-Path $root 'resources\bin\docker.exe'
        if (Test-Path $candidate) { return $candidate }
    }
    return $null
}

function Invoke-Docker {
    param([string[]]$Arguments)
    & $script:Docker @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Docker command failed (exit $LASTEXITCODE): $($Arguments[0]). Read the error above." }
}

function Get-EngineType {
    # Windows PowerShell treats native stderr as ErrorRecord; inspect the exit code instead.
    $oldPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $script:Docker info --format '{{.OSType}}' 2>$null
        if ($LASTEXITCODE -eq 0) { return ($output -join '').Trim() }
        return ''
    } finally { $ErrorActionPreference = $oldPreference }
}

function Install-WSL {
    $wsl = Join-Path $env:SystemRoot 'System32\wsl.exe'
    if (-not (Test-Path $wsl)) { throw 'WSL is unavailable. Install current Windows updates, restart, then rerun this installer.' }
    $oldPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $versionText = (& $wsl --version 2>$null | Out-String).Replace([string][char]0, '')
        $versionOk = $LASTEXITCODE -eq 0
        & $wsl --status *> $null
        $statusOk = $LASTEXITCODE -eq 0
    } finally { $ErrorActionPreference = $oldPreference }
    $match = [regex]::Match($versionText, '\d+\.\d+\.\d+')
    if ($statusOk -and $versionOk -and $match.Success -and [version]$match.Value -ge [version]'2.1.5') { return }
    Write-Host 'Installing/updating WSL. Approve the Windows administrator prompt.'
    $arguments = @('--install', '--no-distribution', '--web-download')
    if ($statusOk -and $versionOk) { $arguments = @('--update', '--web-download') }
    $process = Start-Process -FilePath $wsl -ArgumentList $arguments -Verb RunAs -Wait -PassThru
    if ($process.ExitCode -in @(3010, 1641)) { throw 'Windows requires a restart. Restart your PC and run run_windows.bat again.' }
    if ($process.ExitCode -ne 0) { throw "WSL setup failed (exit $($process.ExitCode)). Check Windows updates and virtualization, then rerun." }
    if (-not $statusOk -or -not $versionOk) { throw 'WSL installation completed. Restart Windows, then run run_windows.bat again to continue.' }
}

function Get-SupportedUbuntuDistro {
    param([string]$DistributionList)
    $supported = @()
    foreach ($name in ($DistributionList -split "`r?`n")) {
        $candidate = $name.Trim()
        $match = [regex]::Match($candidate, '^Ubuntu-(\d+\.\d+)$')
        if ($match.Success -and [version]$match.Groups[1].Value -ge [version]'24.04') {
            $supported += [pscustomobject]@{ Name=$candidate; Version=[version]$match.Groups[1].Value }
        }
    }
    $selected = $supported | Sort-Object Version -Descending | Select-Object -First 1
    if ($null -eq $selected) { return $null }
    return $selected.Name
}

function Install-DockerDesktop {
    Write-Host 'Downloading the official Docker Desktop installer; Python is not required.'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $directory = Join-Path ([IO.Path]::GetTempPath()) ('narsika-setup-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $directory | Out-Null
    $installer = Join-Path $directory 'DockerDesktopInstaller.exe'
    try {
        Invoke-WebRequest -UseBasicParsing -Uri 'https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe' -OutFile $installer
        $signature = Get-AuthenticodeSignature -FilePath $installer
        if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O="?Docker Inc') {
            throw 'Docker installer signature verification failed. The downloaded program was not executed.'
        }
        # Per-user install preserves the calling user context; WSL elevation is separate.
        $process = Start-Process -FilePath $installer -ArgumentList @('install', '--user', '--backend=wsl-2') -Wait -PassThru
        if ($process.ExitCode -in @(3010, 1641)) { throw 'Restart Windows, then run run_windows.bat again to continue.' }
        if ($process.ExitCode -ne 0) { throw "Docker Desktop installation failed or was cancelled (exit $($process.ExitCode))." }
    } finally { Remove-Item -LiteralPath $directory -Recurse -Force -ErrorAction SilentlyContinue }
}

function Start-LinuxEngine {
    $engine = Get-EngineType
    if ($engine -eq 'linux') { return }
    $desktopRoot = Split-Path (Split-Path (Split-Path $script:Docker -Parent) -Parent) -Parent
    $desktop = Join-Path $desktopRoot 'Docker Desktop.exe'
    if (-not (Test-Path $desktop)) { throw 'Docker engine is unavailable. Start your local Linux Docker engine and rerun.' }
    Start-Process -FilePath $desktop | Out-Null
    if ($engine -eq 'windows') {
        $switcher = Join-Path $desktopRoot 'DockerCli.exe'
        if (Test-Path $switcher) {
            $process = Start-Process -FilePath $switcher -ArgumentList '-SwitchLinuxEngine' -Wait -PassThru
            if ($process.ExitCode -ne 0) { throw 'Select Linux containers in Docker Desktop, then rerun.' }
        }
    }
    Write-Host 'Waiting for the Linux engine. Complete any Docker Desktop setup/license screen.'
    $deadline = [DateTime]::UtcNow.AddMinutes(3)
    do {
        if ((Get-EngineType) -eq 'linux') { return }
        Start-Sleep -Seconds 3
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'Docker did not become ready. Complete its setup, enable virtualization in BIOS if requested, or restart Windows; then rerun.'
}

function Start-Narsika {
    if (-not [Environment]::Is64BitProcess -or $env:PROCESSOR_ARCHITECTURE -ne 'AMD64') {
        throw 'This Windows installer supports x64 Windows. Use a supported x64 PC or a Linux Docker server.'
    }
    $os = Get-CimInstance Win32_OperatingSystem
    if ($os.ProductType -ne 1 -or [int]$os.BuildNumber -lt 19045) { throw 'Use an updated Windows 10/11 x64 desktop with WSL2 support. Windows Server requires a Linux host.' }
    $project = Split-Path $PSScriptRoot -Parent
    if ($WSL) {
        Install-WSL
        $wslCommand = Join-Path $env:SystemRoot 'System32\wsl.exe'
        $distros = (& $wslCommand --list --quiet | Out-String).Replace([string][char]0, '')
        $ubuntuDistro = Get-SupportedUbuntuDistro -DistributionList $distros
        if (-not $ubuntuDistro) {
            $ubuntuDistro = 'Ubuntu-26.04'
            & $wslCommand --install --distribution $ubuntuDistro
            if ($LASTEXITCODE -ne 0) { throw 'Complete Ubuntu installation, restart if requested, and rerun with -WSL.' }
            throw 'Create your Ubuntu username/password in its first-start window, then rerun run_windows.bat -WSL.'
        }
        $linuxPath = (& $wslCommand -d $ubuntuDistro -- wslpath -a $project | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or -not $linuxPath.StartsWith('/')) { throw 'Unable to locate this extracted project inside Ubuntu.' }
        & $wslCommand -d $ubuntuDistro --cd $linuxPath -- bash run_linux.sh
        if ($LASTEXITCODE -ne 0) { throw 'Ubuntu setup stopped. Read its error above. WSL must run systemd.' }
        Write-Host 'WSL installation completed. For this PC use http://localhost:8000 (or your selected port).'
        Write-Host 'LAN access to WSL requires separate Windows forwarding/mirrored networking and Windows firewall configuration.'
        return
    }
    if ($project.Contains(',')) { throw 'Extract the ZIP to a folder without commas, for example C:\Narsika.' }
    Push-Location $project
    try {
        $script:Docker = Find-Docker
        if (-not $script:Docker -or (Get-EngineType) -ne 'linux') { Install-WSL }
        if (-not $script:Docker) { Install-DockerDesktop; $script:Docker = Find-Docker }
        if (-not $script:Docker) { throw 'Docker CLI was not found after installation. Complete the installer and rerun.' }
        Start-LinuxEngine
        Invoke-Docker -Arguments @('compose', 'version')
        Invoke-Docker -Arguments @('build', '-t', 'narsika:local', '.')
        $terminal = @()
        if (-not [Console]::IsInputRedirected) { $terminal = @('-it') }
        $configuration = @('run', '--rm') + $terminal + @('--network', 'none', '--user', '0:0', '--mount', "type=bind,source=$project,target=/setup", '--entrypoint', 'python', 'narsika:local', '/setup/configure.py')
        Invoke-Docker -Arguments $configuration
        Invoke-Docker -Arguments @('run', '--rm', '--network', 'none', '--mount', "type=bind,source=$project,target=/setup,readonly", '--entrypoint', 'python', 'narsika:local', '/setup/configure.py', '--check')
        Invoke-Docker -Arguments @('compose', 'stop', 'narsika')
        Invoke-Docker -Arguments @('compose', 'run', '--rm', '--no-deps', 'bootstrap')
        try { Invoke-Docker -Arguments @('compose', 'up', '-d', '--no-build', '--wait', '--wait-timeout', '120') }
        catch { & $script:Docker compose logs --tail=80 narsika; throw }
        $binding = (Invoke-Docker -Arguments @('compose', 'port', 'narsika', '8000') | Out-String).Trim()
        Write-Host ('Narsika is healthy. Open http://' + $binding.Replace('0.0.0.0', '127.0.0.1')) -ForegroundColor Green
    } finally { Pop-Location }
}

if (-not $LoadOnly) {
    try { Start-Narsika; exit 0 }
    catch { Write-Host ("SETUP STOPPED: " + $_.Exception.Message) -ForegroundColor Red; Write-Host 'Existing Narsika configuration and data are preserved. Correct the issue and rerun.'; exit 1 }
}
