# Lumino — Guide de survie RGB (AORUS RTX 5080 INFINITY WOOD 16G)

## Lancer l'app (2 options)

**Option A — raccourci :** double-cliquer `Lumino` sur le Bureau.
Le serveur démarre et le site s'ouvre tout seul sur http://127.0.0.1:6420.

**Option B — terminal :**
```powershell
cd "C:\Users\kevin\Documents\Projects Cursor\Lumino"
python app.py
```
Puis ouvrir http://127.0.0.1:6420 dans le navigateur.

## Vérifier la détection (sans rien changer)

```powershell
cd "C:\Users\kevin\Documents\Projects Cursor\Lumino"
python scan.py
```
Attendu : `Blackwell Fusion2 détecté à 0x75, réponse=[1, 1, 1, 1]`.
Ou bouton **Scanner** dans l'UI (même chose).

## Changer la couleur en 1 ligne (sans l'UI)

```powershell
cd "C:\Users\kevin\Documents\Projects Cursor\Lumino"
python -c "from nvapi import NvAPI; import blackwell; nv=NvAPI(); nv.initialize(); h=nv.enum_gpus()[0]; print(blackwell.apply_static(nv,h, R,G,B, brightness10=N, do_save=True)))"
```
- `R,G,B` : 0–255 (ex rouge `255,0,0`, vert `0,255,0`, bleu `0,0,255`).
- `N` : luminosité 1–10 (ex `10` = max).
- `do_save=True` : grave sur la carte (persiste au reboot). `False` = volatile.

Exemples prêts à coller :
```powershell
# Rouge max + save
python -c "from nvapi import NvAPI; import blackwell; nv=NvAPI(); nv.initialize(); h=nv.enum_gpus()[0]; print(blackwell.apply_static(nv,h,255,0,0,10,True)))"
# Bleu discret sans save
python -c "from nvapi import NvAPI; import blackwell; nv=NvAPI(); nv.initialize(); h=nv.enum_gpus()[0]; print(blackwell.apply_static(nv,h,0,0,255,3,False)))"
# Éteindre + save
python -c "from nvapi import NvAPI; import blackwell; nv=NvAPI(); nv.initialize(); h=nv.enum_gpus()[0]; print(blackwell.apply_static(nv,h,0,0,0,1,True)))"
```

## Options de l'interface web

- **Carré couleur** : teinte (anneaux + bandeau latéral).
- **Luminosité 0–100 %** : convertie en 1–10 côté carte.
- **Grand rectangle** : aperçu (reprends la dernière couleur appliquée au chargement).
- **Appliquer** : envoie + sauvegarde sur GPU (persistant au reboot).
- **Éteindre** : noir + sauvegarde (reste éteint au reboot).
- **Scanner** : détection `0x75`, lecture seule.
- **État** : réponse JSON brute (debug).

## Si ça ne marche plus

1. `Erreur: Failed to fetch` → le serveur `python app.py` est coupé : relance-le.
2. Scan ne détecte plus → redémarre le PC (le contrôleur LED se coince parfois, bug Gigabyte connu), `pip`/`driver` inchangés.
3. Couleur perdue au reboot → refais Appliquer (le save a dû sauter après une maj driver) .
4. Page blanche ou vieux boutons → `Ctrl+F5` (cache navigateur).

## Fichiers utiles (tout est dans `Documents\Projects Cursor\Lumino`)

- `app.py` — serveur web + API (`/api/scan`, `/api/apply`, `/api/off`, `/api/status`).
- `scan.py` — diagnostic détection.
- `nvapi.py` — wrapper driver NVIDIA (I2C port GPU 1).
- `blackwell.py` — protocole Fusion2 Blackwell (`0x75`, paquets 64 o, `save=[0x13,0x01,…]`).
- `gigabyte.py` — repli cartes pré-RTX50.
- `static/index.html` — interface.
- `config.json` — dernière couleur appliquée (pré-remplit l'UI).
- `start.bat` / raccourci Bureau — lancement.
- `vendor/`, `trace_gcc.py`, `watch_apply.py`, `probe_illum.py`, `gv_official.py` — restes du reverse, supprimables.

## Carte mère + WC (ASUS Aura USB, sans Armoury Crate)

```powershell
cd "C:\Users\kevin\Documents\Projects Cursor\Lumino"
python -c "import aura; a=aura.Aura(); print(a.describe()); a.close()"
# CM (persistant) :  python -c "import aura; a=aura.Aura(); a.set_cm(R,G,B); a.close()"
# WC header 1 (direct, volatile) : python -c "import aura; a=aura.Aura(); a.set_wc(R,G,B); a.close()"
```
- Backend `aura.py` (dépendance `hidapi`), protocole OpenRGB `AsusAuraUSBController`.
- Canaux : `fixed` = LEDs CM (static persisté), `addr0` = header ARGB 1 = WC (direct seulement).
- Prérequis BIOS : `Advanced → Onboard Devices → LED Lighting → Enabled`.

## Rappel technique (si un jour il faut tout refaire)

- GPU : `VEN_10DE DEV_2C02 SUBSYS_41E2_1458`, bus I2C interne NVIDIA port 1 via `NvAPI_I2CWriteEx/ReadEx` (`0x283AC65A`/`0x4D7B0709`).
- Sonde : write 64 o `[0x10,0x01,0…]` à `0x75` → read 4 o `[0x01,0x01|0x02,0x01,*]`.
- Static zone 0–5 : `[0x12,0x01,0x01,speed,brightness,R,G,B,0x00,zone,0x00,…]`, pause ~120 ms entre zones.
- Référence protocole : OpenRGB `GigabyteRGBFusion2BlackwellGPUController` (GPL-2.0).
- Profils GCC (obsolète, GCC désinstallé) : `C:\Program Files\GIGABYTE\Control Center\GvProfile\41E20_GLeds*.dat` (JSON, `Color` en `0xRRGGBB`).
