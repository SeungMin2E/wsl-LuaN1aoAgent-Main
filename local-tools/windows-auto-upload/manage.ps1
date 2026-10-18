param([ValidateSet('start','stop','status','enable','disable')][string]$Action = 'status')
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskPython = 'C:\Users\KSM\AppData\Local\Python\pythoncore-3.14-64\python.exe'
$taskPythonw = Join-Path (Split-Path $taskPython) 'pythonw.exe'
$startupLink = Join-Path ([Environment]::GetFolderPath('Startup')) 'Deeppoint GitHub Auto Upload.lnk'
$stopPath = Join-Path $taskRoot 'STOP'
if ($Action -eq 'enable') {
    $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($startupLink)
    $shortcut.TargetPath = $taskPythonw
    $shortcut.Arguments = '"' + (Join-Path $taskRoot 'auto_upload.py') + '" watch'
    $shortcut.WorkingDirectory = $taskRoot
    $shortcut.WindowStyle = 7
    $shortcut.Description = 'Upload changes to selected GitHub repositories after tests'
    $shortcut.Save()
}
if ($Action -in @('start','enable')) {
    if (Test-Path -LiteralPath $stopPath) { Remove-Item -LiteralPath $stopPath }
    Start-Process -FilePath $taskPythonw -ArgumentList ('"' + (Join-Path $taskRoot 'auto_upload.py') + '" watch') -WorkingDirectory $taskRoot -WindowStyle Hidden
    Write-Output 'Auto upload started. Check status for details.'
}
if ($Action -in @('stop','disable')) {
    Set-Content -LiteralPath $stopPath -Value 'Stop requested' -Encoding UTF8
    if ($Action -eq 'disable' -and (Test-Path -LiteralPath $startupLink)) { Remove-Item -LiteralPath $startupLink }
    Write-Output 'Stop requested. An upload already in progress may finish. disable also removes login startup.'
}
if ($Action -eq 'status') {
    $env:PYTHONIOENCODING = 'utf-8'
    & $taskPython (Join-Path $taskRoot 'auto_upload.py') status
    $statusPath = Join-Path $taskRoot 'state\status.json'
    $running = $false
    if (Test-Path -LiteralPath $statusPath) {
        $watchStatus = Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($watchStatus.pid) {
            $watchProcess = Get-Process -Id $watchStatus.pid -ErrorAction SilentlyContinue
            $recentHeartbeat = ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - $watchStatus.heartbeat) -lt 600
            $running = [bool]($watchProcess -and $watchProcess.ProcessName -eq 'pythonw' -and $recentHeartbeat)
        }
    }
    Write-Output ('Watcher running: ' + $running)
    Write-Output ('Login startup enabled: ' + (Test-Path -LiteralPath $startupLink))
}
