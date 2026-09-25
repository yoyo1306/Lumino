# Lumino — contrôle RGB local (RTX 5080 + ASUS Aura)

App bureau Windows **sans GCC, sans OpenRGB, sans tâche de fond**.
Parle directement au contrôleur LED via I2C interne du GPU (NvAPI Ex, port 1)
et au contrôleur ASUS Aura en USB (HID).

Testé sur : `GV-N5080AORUSIF WD-16GD` (`VEN_10DE DEV_2C02 SUBSYS_41E2_1458`)
+ ROG Strix B850-A (`0B05:19AF`).

## Utilisation (exe)

1. Lance `Lumino.exe` (ou `lumino.bat`), choisis couleur + luminosité,
   **Appliquer**, ferme la fenêtre. Rien ne reste en mémoire.
2. `installer_demarrage.bat` : restaure tes LEDs **une fois** à chaque
   démarrage Windows (`Lumino.exe --boot`), puis se ferme.

`config.json` garde le dernier choix (+ taille de fenêtre `win_geo`).
`Lumino.exe --boot --delay 10` = mode démarrage sans fenêtre.

## Construire l'exe

```powershell
pip install pyinstaller hidapi pillow
pyinstaller --noconfirm --clean --onefile --windowed --name Lumino --icon lumino.ico --hidden-import hid --hidden-import apply_boot --hidden-import blackwell --hidden-import nvapi --hidden-import aura lumino_gui.py
copy dist\Lumino.exe Lumino.exe
```

Sans exe : `python lumino_gui.py` (dépendance : `hidapi`).

## Fichiers

- `lumino_gui.py` — app bureau tkinter (GPU + CM + WC, `--boot` inclus)
- `apply_boot.py` — ré-applique `config.json` au boot (retry driver/USB)
- `blackwell.py` — protocole Fusion2 Blackwell (`0x75`, paquets 64 o)
- `aura.py` — backend ASUS Aura USB (CM persistée, WC direct)
- `nvapi.py` — wrapper ctypes NvAPI Ex (I2C port GPU 1)
- `scan.py` — diagnostic détection (lecture seule)
- `lumino.ico` / `lumino.png` — icône (`make_icon.py` pour la régénérer)
- `config.example.json` — config de départ
- `app.py`, `static/`, `gigabyte.py`, `gv_official.py`, … — ancien serveur
  web et outils de reverse (conservés pour référence)

## Protocole (référence OpenRGB, GPL-2.0)

- Adresse `0x75`, blocs 64 octets sans registre.
- Sonde : write `[0x10,0x01,0...]` → read 4 o `[0x01,0x01|0x02,0x01,*]`.
- Static par zone hw 0..5 : `[0x12,0x01,0x01,speed,brightness,R,G,B,0x00,zone,0x00,...]`
  brightness `0x01..0x0A`. Pause ~120 ms entre zones.
- Save : `[0x13,0x01,0...]`.
