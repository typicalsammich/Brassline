$ErrorActionPreference = 'Stop'
$taskFolder = $PSScriptRoot
$taskPython = Join-Path $taskFolder '.venv\Scripts\pythonw.exe'
$taskWorker = Join-Path $taskFolder 'worker.py'
$taskConfig = Join-Path $taskFolder 'data\worker.json'
if (!(Test-Path -LiteralPath $taskPython) -or !(Test-Path -LiteralPath $taskConfig)) {
  throw 'Start the worker once and choose its configuration before enabling automatic startup.'
}
$taskStartup = [Environment]::GetFolderPath('Startup')
$taskShortcut = Join-Path $taskStartup 'Brassline worker.lnk'
$taskShell = New-Object -ComObject WScript.Shell
$taskLink = $taskShell.CreateShortcut($taskShortcut)
$taskLink.TargetPath = $taskPython
$taskLink.Arguments = '"' + $taskWorker + '"'
$taskLink.WorkingDirectory = $taskFolder
$taskLink.WindowStyle = 7
$taskLink.Save()
Write-Host 'Brassline worker will start quietly when you sign into Windows.'
Write-Host 'To disable it, remove the Brassline worker shortcut from the Windows Startup folder.'
