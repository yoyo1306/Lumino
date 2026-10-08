# Lumino — contrôle RGB local (RTX 5080 + ASUS Aura + Ambiglow)

App bureau Windows **sans GCC, sans OpenRGB, sans site web**.
Parle directement au contrôleur LED via I2C interne du GPU (NvAPI Ex, port 1)
et au contrôleur ASUS Aura en USB (HID).
L'Ambiglow du Philips Evnia 27M2N8500 passe par le DisplayPort.

Testé sur : `GV-N5080AORUSIF WD-16GD` (`VEN_10DE DEV_2C02 SUBSYS_41E2_1458`)
+ ROG Strix B850-A (`0B05:19AF`).

Trio Corsair (Titan 240 LCD + 2x RX via Hub `1B1C:0C3F`) : tenu par
`corsair_keep.py` en fond (statique : renvoi toutes les 4 s ; animé :
environ 5 img/s, limite du protocole), iCUE fermé
(autostart désactivé). Layout [pompe 20][anneau LCD 24][RX 8][RX 8].
LCD via image hardware figee ou HydroScreen (open source).
Vitesses : profil hardware du Hub (regler une fois en iCUE Device
Memory Mode avant de fermer iCUE).

## Utilisation (exe)

1. Lance `Lumino.exe` (ou `lumino.bat`), choisis couleur + luminosité,
   **Appliquer**. En statique, le GPU et la carte mère restent gravés.
   Un effet (arc-en-ciel, vague, pulsation, etc.) est joué en même temps
   sur les trois par `corsair_keep.py`, déjà là parce que le hub oublie
   sa couleur. iCUE doit rester fermé.
2. `installer_demarrage.bat` : au démarrage Windows, `Lumino.exe --boot`
   réapplique le statique GPU + carte mère, puis relance le fond
   (il reprend l'effet s'il y en a un).

`config.json` garde le dernier choix (+ taille de fenêtre `win_geo`).
`Lumino.exe --boot --delay 10` = mode démarrage sans fenêtre.

## Ambiglow (Philips Evnia 27M2N8500)

Page **Ambiglow** : modes, couleurs, luminosité et vitesse du menu OSD,
envoyés par le câble vidéo. Le câble USB n'est pas nécessaire pour ces réglages.

Les couleurs sont grisées pour Désactivé, Suivre la vidéo et Suivre l'audio.
La vitesse n'apparaît que pour Changement de couleur, Onde de couleur,
Respiration des couleurs et Nuit étoilée.

Les pastilles de **Tout** suivent cette palette, sans l'arc-en-ciel.
Appliquer envoie la même teinte au PC et l'entrée correspondante à l'écran,
sans changer le mode Ambiglow. Le mode de l'écran doit accepter une couleur.

## Construire l'exe

```powershell
pip install pyinstaller hidapi pillow
pyinstaller --noconfirm --clean --onefile --windowed --name Lumino --icon lumino.ico --hidden-import hid --hidden-import ambiglow --hidden-import apply_boot --hidden-import blackwell --hidden-import nvapi --hidden-import aura --hidden-import corsair_link --hidden-import corsair_keep --hidden-import effects lumino_gui.py
copy dist\Lumino.exe Lumino.exe
```

Sans exe : `python lumino_gui.py` (dépendance : `hidapi`).

## Fichiers

- `ambiglow.py` — Philips Evnia 27M2N8500, menu OSD par DisplayPort
- `lumino_gui.py` — app bureau tkinter (GPU + CM + Corsair LINK + Ambiglow, `--boot` inclus,
  `--corsair-keepalive` / `--corsair-stop` pour le fond Corsair). Pas de serveur.
- `apply_boot.py` — ré-applique `config.json` au boot (retry driver/USB) + démarre le keepalive Corsair
- `blackwell.py` — protocole Fusion2 Blackwell (`0x75`, paquets 64 o)
- `aura.py` — backend ASUS Aura USB (CM persistée)
- `corsair_link.py` — backend Hub iCUE LINK (`1B1C:0C3F`, layout [pompe 20][anneau 24][RX 8][RX 8], réf OpenRGB GPL-2.0)
- `corsair_keep.py` — keepalive/animateur de fond (statique : renvoi
  toutes les 4 s ; animés : environ 5 img/s ; suit `corsair_last` en live,
  verrou `corsair_keepalive.lock`)
- `effects.py` — presets style iCUE : Statique, Arc-en-ciel, Vague
  arc-en-ciel, Vague couleur, Pulsation, Ondulation, Température
  (sonde GPU). La luminosité 0–100 % s'applique à tous. Non reproduits
  (2e couleur requise) : Dégradé, Changement couleur.

## Trio Corsair sans iCUE (Titan 240 LCD + 2x RX, Hub fw v4.1.656)

1. Dans iCUE (une fois pour toutes) : figer l'image LCD en hardware
   (Device Memory Mode) + régler courbe ventilos/pompe + couleur de base
   en Device Memory Mode (fallback). Écran live : bouton **Écran LCD**
   (température GPU). HydroScreen
   (https://github.com/UDPSendToFailed/HydroScreen) ne doit pas tourner en même temps.
2. Fermer iCUE + désactiver son autostart (fait : clé HKLM Run supprimée,
   backup dans `%TEMP%\opencode\run_hklm_backup.reg`).
3. Lumino > Appliquer Corsair : sauve + live + démarre le keepalive.
   Le Hub retombe en hard sans refresh, d'où le fond permanent.
- `nvapi.py` — wrapper ctypes NvAPI Ex (I2C port GPU 1)
- `scan.py` — diagnostic détection (lecture seule)
- `lumino.ico` / `lumino.png` — icône (`make_icon.py` pour la régénérer)
- `config.example.json` — config de départ
- `gigabyte.py`, `gv_official.py`, `trace_gcc.py`, … — outils de reverse
  (hors app, conservés pour référence). Le site web a été retiré.

## Protocole (référence OpenRGB, GPL-2.0)

- Adresse `0x75`, blocs 64 octets sans registre.
- Sonde : write `[0x10,0x01,0...]` → read 4 o `[0x01,0x01|0x02,0x01,*]`.
- Static par zone hw 0..5 : `[0x12,0x01,0x01,speed,brightness,R,G,B,0x00,zone,0x00,...]`
  brightness `0x01..0x0A`. Pause ~120 ms entre zones.
- Save : `[0x13,0x01,0...]`.
