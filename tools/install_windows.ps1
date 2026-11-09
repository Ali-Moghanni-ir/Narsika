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
    param([string[]]$Arguments, [switch]$Stream)
    if (-not $Stream) {
        & $script:Docker @Arguments
        if ($LASTEXITCODE -ne 0) { throw "Docker command failed (exit $LASTEXITCODE): $($Arguments[0]). Read the error above." }
        return
    }
    # Show the output live and remember the last one-line cause printed by Narsika's setup tools.
    $causes = New-Object System.Collections.Generic.List[string]
    $oldPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $script:Docker @Arguments 2>&1 | ForEach-Object {
            $line = "$_"
            Write-Host $line
            $index = $line.IndexOf('NARSIKA_SETUP_CAUSE: ')
            if ($index -ge 0) { $causes.Add($line.Substring($index + 21).Trim()) }
        }
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $oldPreference }
    if ($code -ne 0) {
        if ($causes.Count) { throw "$($causes[$causes.Count - 1]) (docker $($Arguments[0]) exit $code)" }
        throw "Docker command failed (exit $code): $($Arguments[0]). Read the error above."
    }
}

function ConvertTo-BuildProxy {
    # A proxy as seen from inside a build container. 127.0.0.1 there is the container itself,
    # so a local proxy is reached through Docker Desktop's built-in proxy, which forwards
    # to the proxy configured for Windows.
    param([string]$Value)
    if (-not $Value) { return $null }
    $text = $Value.Trim()
    if ($text.Contains('=')) {
        # WinINET per-protocol list, for example "http=127.0.0.1:8080;https=127.0.0.1:8080".
        $entries = @{}
        foreach ($part in ($text -split ';')) {
            $pair = $part -split '=', 2
            if ($pair.Count -eq 2) { $entries[$pair[0].Trim().ToLowerInvariant()] = $pair[1].Trim() }
        }
        $text = $null
        foreach ($key in @('https', 'http')) { if ($entries.ContainsKey($key)) { $text = $entries[$key]; break } }
        if (-not $text) { return $null }
    }
    if ($text -notmatch '^[A-Za-z][A-Za-z0-9+.-]*://') { $text = 'http://' + $text }
    if ($text -match '^socks') { return $null }
    try { $uri = [Uri]$text } catch { return $null }
    if ($uri.Host -in @('127.0.0.1', 'localhost', '[::1]', '::1')) { return 'http://http.docker.internal:3128' }
    return $text.TrimEnd('/')
}

function Get-BuildProxy {
    $override = [Environment]::GetEnvironmentVariable('NARSIKA_BUILD_PROXY')
    if ($override) { return $override.Trim() }
    foreach ($name in @('HTTPS_PROXY', 'HTTP_PROXY')) {
        $converted = ConvertTo-BuildProxy ([Environment]::GetEnvironmentVariable($name))
        if ($converted) { return $converted }
    }
    try {
        $settings = Get-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -ErrorAction Stop
        if ($settings.ProxyEnable -eq 1 -and $settings.ProxyServer) { return (ConvertTo-BuildProxy $settings.ProxyServer) }
    } catch { }
    return $null
}

function Get-ProxyLabel {
    # Never print proxy credentials.
    param([string]$Proxy)
    try { $uri = [Uri]$Proxy; return "$($uri.Scheme)://$($uri.Host):$($uri.Port)" } catch { return 'the configured proxy' }
}

function Get-ProxyArguments {
    # Proxy variables for one build or check container only; they are never written to the image or .env.
    param([string]$Proxy, [string]$Flag)
    if (-not $Proxy) { return @() }
    $noProxy = [Environment]::GetEnvironmentVariable('NO_PROXY')
    if (-not $noProxy) { $noProxy = 'localhost,127.0.0.1' }
    $arguments = @()
    foreach ($name in @('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy')) { $arguments += @($Flag, "$name=$Proxy") }
    foreach ($name in @('NO_PROXY', 'no_proxy')) { $arguments += @($Flag, "$name=$noProxy") }
    return $arguments
}

function Get-BaseImage {
    param([string]$Project)
    $match = Select-String -Path (Join-Path $Project 'Dockerfile') -Pattern '^FROM\s+(\S+)' | Select-Object -First 1
    if (-not $match) { throw 'The Dockerfile has no FROM line. Extract the release ZIP again.' }
    return $match.Matches[0].Groups[1].Value
}

function Test-BuildNetwork {
    # A short check from a throwaway container of the pinned base image, before the long build.
    param([string]$Project, [string]$Proxy)
    $image = Get-BaseImage $Project
    $environment = @(Get-ProxyArguments -Proxy $Proxy -Flag '-e')
    $index = [Environment]::GetEnvironmentVariable('NARSIKA_PIP_INDEX_URL')
    if ($index) { $environment += @('-e', "NARSIKA_PIP_INDEX_URL=$index") }
    Write-Host 'Checking network access for the build (package index and files, Ansible collections, Debian packages).'
    try {
        Invoke-Docker -Stream -Arguments (@('run', '--rm') + $environment + @('--mount', "type=bind,source=$Project\tools,target=/narsika-tools,readonly", '--entrypoint', 'python', $image, '/narsika-tools/network_check.py'))
    } catch {
        if ($_.Exception.Message.StartsWith('Docker command failed')) {
            throw "Docker could not start the base image $image for the network check. If Docker Hub is unreachable, set the proxy in Docker Desktop (Settings > Resources > Proxies) and rerun."
        }
        throw
    }
}

function Show-StaleFiles {
    # Files a release does not list were usually left by an older version extracted into the same folder.
    param([string]$Project)
    $manifest = Join-Path $Project 'MANIFEST.sha256'
    if (-not (Test-Path $manifest)) { return }
    $listed = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    foreach ($line in Get-Content -Path $manifest) {
        $parts = $line -split '  ', 2
        if ($parts.Count -eq 2) { [void]$listed.Add($parts[1]) }
    }
    $root = (Resolve-Path $Project).Path.TrimEnd('\', '/')
    $stale = @()
    foreach ($file in Get-ChildItem -Path $Project -File -Recurse -ErrorAction SilentlyContinue) {
        # The manifest uses forward slashes.
        $relative = $file.FullName.Substring($root.Length + 1).Replace('\', '/')
        if ($relative -in @('.env', 'MANIFEST.sha256') -or $relative.StartsWith('instance/') -or $relative.Contains('__pycache__')) { continue }
        if (-not $listed.Contains($relative)) { $stale += $relative }
    }
    if ($stale.Count) {
        Write-Host ('These files are not part of this release and may be left over from an older version: ' + (($stale | Select-Object -First 10) -join ', ')) -ForegroundColor Yellow
        Write-Host 'For a clean install, extract each version into a new, empty folder.' -ForegroundColor Yellow
    }
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
        Show-StaleFiles $project
        $proxy = Get-BuildProxy
        if ($proxy) { Write-Host ('Using ' + (Get-ProxyLabel $proxy) + ' for the network check and the image build only.') }
        Test-BuildNetwork -Project $project -Proxy $proxy
        $buildArguments = @('build', '--progress=plain')
        foreach ($setting in @('NARSIKA_PIP_INDEX_URL', 'NARSIKA_PIP_TIMEOUT', 'NARSIKA_PIP_RETRIES')) {
            $value = [Environment]::GetEnvironmentVariable($setting)
            if ($value) { $buildArguments += @('--build-arg', "$setting=$value") }
        }
        # Docker's predefined proxy build arguments are not stored in the image configuration or history.
        $buildArguments += @(Get-ProxyArguments -Proxy $proxy -Flag '--build-arg')
        Write-Host 'Building with cached dependencies. First setup still downloads the base image, packages and Ansible collections.'
        Write-Host 'For later starts use docker compose up -d --no-build; setup is not required on every launch.'
        Invoke-Docker -Stream -Arguments ($buildArguments + @('-t', 'narsika:local', '.'))
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
