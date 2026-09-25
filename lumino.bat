@echo off
rem Lumino - lance l'app (exe, aucun serveur, rien en fond)
cd /d "%~dp0"
if exist Lumino.exe (
  start "Lumino" Lumino.exe
) else (
  python lumino_gui.py
)
