# Cria/atualiza um atalho .lnk apontando pro Apolo.exe -- chamado por
# build.bat, não pra rodar direto. Script separado (em vez de tudo inline no
# .bat) porque path com espaço + aspas dentro de "powershell -Command" vira
# um pesadelo de escaping; assim cada lado (bat, ps1) só lida com as próprias
# aspas.
param(
    [Parameter(Mandatory = $true)][string]$Target,
    [Parameter(Mandatory = $true)][string]$Shortcut
)

$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($Shortcut)
$sc.TargetPath = $Target
$sc.IconLocation = "$Target,0"
$sc.WorkingDirectory = Split-Path $Target
$sc.Save()

Write-Host "Atalho: $Shortcut -> $Target"
