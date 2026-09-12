# Execute with Windows PowerShell 5.1; no Pester, Python or Docker required.
$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path $PSScriptRoot 'install_windows.ps1'
$tokens = $null; $errors = $null
[System.Management.Automation.Language.Parser]::ParseFile($scriptPath, [ref]$tokens, [ref]$errors) | Out-Null
if ($errors.Count) { throw ($errors | Out-String) }
. $scriptPath -LoadOnly
if ((Get-SupportedUbuntuDistro "Ubuntu-22.04") -ne $null) { throw 'Unsupported Ubuntu release was accepted' }
if ((Get-SupportedUbuntuDistro "Ubuntu-24.04") -ne 'Ubuntu-24.04') { throw 'Ubuntu 24.04 was rejected' }
if ((Get-SupportedUbuntuDistro "Ubuntu-24.04`r`nUbuntu-26.04") -ne 'Ubuntu-26.04') { throw 'Newest supported Ubuntu distribution was not selected' }
$script:Calls = New-Object System.Collections.Generic.List[string]
function Get-CimInstance { [pscustomobject]@{ ProductType=1; BuildNumber=22631 } }
function Find-Docker { 'C:\test\docker.exe' }
function Get-EngineType { 'linux' }
function Start-LinuxEngine {}
function Install-WSL { throw 'Unexpected WSL install on a healthy engine' }
function Install-DockerDesktop { throw 'Unexpected Docker install on a healthy engine' }
function Invoke-Docker {
    param([string[]]$Arguments)
    $command = $Arguments -join ' '
    $script:Calls.Add($command)
    if ($script:Fail -and $command.StartsWith($script:Fail)) { throw 'Controlled command failure' }
    if ($command.StartsWith('compose port')) { '127.0.0.1:8123' }
}
$script:Fail = ''
Start-Narsika
if (-not ($script:Calls | Where-Object { $_ -like 'run * /setup/configure.py' })) { throw 'Configuration generation missing' }
if (-not ($script:Calls | Where-Object { $_ -like '*configure.py --check' })) { throw 'Configuration check missing' }
if (-not ($script:Calls | Where-Object { $_ -like 'compose up *--wait*' })) { throw 'Health wait missing' }
$script:Calls.Clear(); $script:Fail='build'; $failed=$false
try { Start-Narsika } catch { $failed=$true }
if (-not $failed) { throw 'Failed build was reported successful' }
if ($script:Calls | Where-Object { $_ -like 'compose up *' -or $_ -like 'run *' }) { throw 'Installer continued after failed build' }
# Missing prerequisites follow the installation path before any build.
$script:Calls.Clear(); $script:Fail=''; $script:FindCount=0
function Find-Docker { $script:FindCount++; if ($script:FindCount -gt 1) { 'C:\test\docker.exe' } }
function Install-WSL { $script:Calls.Add('install-wsl') }
function Install-DockerDesktop { $script:Calls.Add('install-desktop') }
Start-Narsika
if ($script:Calls[0] -ne 'install-wsl' -or $script:Calls[1] -ne 'install-desktop') { throw 'Missing prerequisite installation order is wrong' }
# A required reboot stops the application build rather than claiming success.
$script:Calls.Clear(); $script:FindCount=0
function Install-WSL { throw 'Restart Windows and rerun' }
$failed=$false
try { Start-Narsika } catch { $failed=$true }
if (-not $failed -or $script:Calls.Count) { throw 'Reboot requirement did not stop setup' }
# Test the real native command wrapper, including nonzero process exit propagation.
Remove-Item Function:\Invoke-Docker
. $scriptPath -LoadOnly
$script:Docker = $env:ComSpec
$failed=$false
try { Invoke-Docker -Arguments @('/c','exit','7') } catch { $failed=$true }
if (-not $failed) { throw 'Native command exit code was ignored' }
# A downloaded executable must never run with an invalid publisher signature.
function Invoke-WebRequest { param([switch]$UseBasicParsing, $Uri, $OutFile); Set-Content -Path $OutFile -Value 'test-invalid-executable' }
function Get-AuthenticodeSignature { [pscustomobject]@{ Status='NotSigned'; SignerCertificate=$null } }
$script:Executed=$false
function Start-Process { $script:Executed=$true; throw 'An unverified executable must not execute' }
$failed=$false
try { Install-DockerDesktop } catch { $failed=$true }
if (-not $failed -or $script:Executed) { throw 'Invalid installer signature was accepted' }
Write-Host 'PASS Windows PowerShell 5.1 syntax, prerequisite reuse, container-only Python, configuration check, health wait and failed-command propagation'
# The intentional native failure above must not become the CI runner's final exit code.
exit 0
