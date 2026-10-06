@echo off
rem Lumino - installe la restauration au demarrage.
rem Raccourci Demarrage : Lumino.exe --boot --delay 10
rem GPU + carte mere une fois, puis le fond Corsair reste actif.
cd /d "%~dp0"
if not exist Lumino.exe (
  echo Lumino.exe introuvable. Pour le construire :
  echo   pip install pyinstaller
  echo   pyinstaller --noconfirm --clean --onefile --windowed --name Lumino --icon lumino.ico --hidden-import hid --hidden-import apply_boot --hidden-import blackwell --hidden-import nvapi --hidden-import aura --hidden-import corsair_link --hidden-import corsair_keep --hidden-import effects lumino_gui.py
  echo   copy dist\Lumino.exe Lumino.exe
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -ComObject WScript.Shell).SpecialFolders('Startup'); $w=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $s 'Lumino.lnk')); $w.TargetPath='%~dp0Lumino.exe'; $w.Arguments='--boot --delay 10'; $w.WorkingDirectory='%~dp0'; $w.WindowStyle=7; $w.Save()"
echo Raccourci de demarrage installe.
echo Au boot : GPU + carte mere une fois, puis le fond Corsair reste actif.
echo iCUE doit rester ferme.
pause
