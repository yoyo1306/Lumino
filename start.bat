@echo off
rem Lumino - double-clique : reutilise le serveur s'il tourne, sinon en lance un + ouvre le site
cd /d "%~dp0"
echo [Lumino] Verification du serveur local...

rem 1) Serveur deja en cours ? -> ouvre juste le site
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop http://127.0.0.1:6420/api/health; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" 2>nul
if %errorlevel%==0 (
  echo [Lumino] Serveur deja en cours, ouverture du site.
  start "" "http://127.0.0.1:6420"
  exit /b 0
)

echo [Lumino] Serveur arrete, nettoyage + demarrage...

rem 2) Nettoie les vieux serveurs bloques (evite l'accumulation sur le port)
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'pythonw.exe' -or $_.Name -eq 'python.exe') -and ($_.CommandLine -like '*app.py*') } | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force } catch {} }" 2>nul
timeout /t 1 /nobreak >nul

rem 3) Lance un seul serveur sans fenetre console
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "Lumino" pythonw app.py --no-browser
) else (
  start "Lumino" /min python app.py --no-browser
)

rem 4) Attend le serveur (max ~15 s) puis ouvre le navigateur
echo [Lumino] Attente du demarrage...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ok = $false; for ($i = 0; $i -lt 30; $i++) { try { $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 -ErrorAction Stop http://127.0.0.1:6420/api/health; if ($r.StatusCode -eq 200) { $ok = $true; break } } catch {}; Start-Sleep -Milliseconds 500 }; if ($ok) { exit 0 } else { exit 1 }" 2>nul
if %errorlevel%==0 (
  echo [Lumino] Ouverture du site.
  start "" "http://127.0.0.1:6420"
) else (
  echo [Lumino] ERREUR : le serveur ne repond pas. Lance "python app.py" pour voir l'erreur.
  pause
)
exit /b 0
