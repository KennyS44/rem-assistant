# Общие проверки для теста установки/удаления.
$App       = "$env:LOCALAPPDATA\Programs\Rem"
$OllamaExe = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
$StartMenu = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Рэм.lnk"
$script:Failed = 0

function RunValue {
    (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -ErrorAction SilentlyContinue).Rem
}

function Expect([bool]$Cond, [string]$What) {
    if ($Cond) { Write-Host "OK    $What" } else { Write-Host "FAIL  $What"; $script:Failed++ }
}

function WaitGone([string]$Path, [int]$Seconds) {
    for ($i = 0; $i -lt $Seconds; $i++) { if (-not (Test-Path $Path)) { return }; Start-Sleep 1 }
    Write-Host "… $Path не исчез за $Seconds с"
}

function Finish {
    if ($script:Failed) { throw "Не прошло проверок: $script:Failed" }
}
