@echo off
rem Lumino - desinstalle la conservation au demarrage.
cd /d "%~dp0"
echo [Lumino] Suppression du raccourci de demarrage...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -ComObject WScript.Shell).SpecialFolders('Startup'); $lnk=Join-Path $s 'Lumino.lnk'; if (Test-Path $lnk) { Remove-Item $lnk -Force; Write-Host '[Lumino] Raccourci supprime.' } else { Write-Host '[Lumino] Aucun raccourci Lumino.' }"
schtasks /delete /tn "LuminoRGB" /f 2>nul
echo [Lumino] Termine. Tes LEDs ne seront plus re-appliquees au boot.
echo Si le fond Corsair tourne encore : Lumino.exe --corsair-stop
pause
