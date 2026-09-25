@echo off
rem Lumino - installe la conservation au demarrage (exe one-shot, rien en fond).
rem Cree un raccourci dans Demarrage : Lumino.exe --boot --delay 10
cd /d "%~dp0"
if not exist Lumino.exe (
  echo Lumino.exe introuvable. Pour le construire :
  echo   pip install pyinstaller
  echo   pyinstaller --noconfirm --clean --onefile --windowed --name Lumino --icon lumino.ico --hidden-import hid --hidden-import apply_boot --hidden-import blackwell --hidden-import nvapi --hidden-import aura lumino_gui.py
  echo   copy dist\Lumino.exe Lumino.exe
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -ComObject WScript.Shell).SpecialFolders('Startup'); $w=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $s 'Lumino.lnk')); $w.TargetPath='%~dp0Lumino.exe'; $w.Arguments='--boot --delay 10'; $w.WorkingDirectory='%~dp0'; $w.WindowStyle=7; $w.Save()"
echo Raccourci de demarrage installe : les LEDs seront restaurees au boot, puis rien ne restera en fond.
pause
