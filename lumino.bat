@echo off
rem Lumino - lance l'app (exe, aucun serveur, rien en fond)
cd /d "%~dp0"
if exist Lumino.exe (
  start "Lumino" Lumino.exe
  exit /b 0
)
set PY312=C:\Users\kevin\AppData\Local\Programs\Python\Python312\python.exe
if exist "%PY312%" (
  "%PY312%" lumino_gui.py
) else (
  python lumino_gui.py
)
