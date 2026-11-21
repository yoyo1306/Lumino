# Changelog — Lumino

## v2.0.0 — Modernisation Windows 11 (WinUI 3 / .NET 10)

### Nouveautés & Architecture
- Réécriture complète en **C# / .NET 10** (`net10.0-windows10.0.19041.0`, `x64`, `self-contained`) : **zéro prérequis** sur un PC Windows 11 vierge (ni Python, ni runtime .NET, ni Windows App Runtime à installer séparément).
- Nouvelle interface native **WinUI 3** (style WinUI Gallery / Paramètres Windows 11) : fond **Mica**, barre de titre personnalisée avec zones `Passthrough`, icône native, accent système Windows 11, mode **Clair / Sombre** mémorisé, cartes Fluent arrondies et valeurs numériques tabulaires alignées.
- Nouveau **tiroir Options** intégré en barre de titre : lancement au démarrage de Windows, démarrage/arrêt du processus de fond Corsair, bascule température GPU sur le LCD, thème Clair/Sombre, ouverture du dossier de données et visionneuse du journal `lumino.log`.
- Instance unique via Mutex + Named Pipe : un second lancement ramène instantanément la fenêtre existante au premier plan.
- Mode **portable** ou **installé dans `Program Files`** (`AppPaths`) : écrit sur place si le dossier est inscriptible, ou bascule automatiquement vers `%LOCALAPPDATA%\Lumino\` avec reprise de la configuration existante.
- Communication UI ↔ Worker de fond via **Named Pipe** (`AuthenticatedUserSid` ReadWrite) avec repli fichier pour des mises à jour instantanées et un arrêt propre.
- Véritable **installateur Windows 11 (Inno Setup 6)** : `Lumino-Setup-2.0.0.exe` (compression `lzma2/ultra64`, `WizardStyle=modern dynamic`, français), raccourci placé directement à la racine du Menu Démarrer sans sous-dossier ni raccourci Désinstaller, raccourci Bureau et option PATH (décochée par défaut), arrêt automatique des instances actives avant mise à jour, préservation de `config.json`.
- Archivage des anciens scripts Python v1.2.2 dans `legacy/`.

### Fonctionnalités conservées (v1.2.2)
- **GPU Gigabyte RTX 5080 (Blackwell)** via `nvapi64.dll` I2C Ex (`0x75`, 6 zones, gravage statique, mode direct, vague matérielle, sonde thermique GPU).
- **Carte mère ASUS Aura USB** (`0B05:19AF`, LEDs fixes + headers ARGB, statique persistant et direct temps réel).
- **Corsair iCUE LINK System Hub** (`1B1C:0C3F`, pompe Titan + anneau LCD + 2× ventilateurs RX à couleurs/luminosités indépendantes, sans iCUE).
- **Philips Evnia 27M2N8500 Ambiglow** : modes OSD DisplayPort DDC (8 modes, 14 couleurs, 3 luminosités, 3 vitesses, application immédiate au clic) + indicateur et pilotage USB WinUSB ENE (`0CF2:A201`, 30 LEDs).
- **Écran LCD Corsair 480×480** (`0C4E` et modèles Elite/Titan) : image fixe, GIF animé avec aperçu circulaire temps réel, zoom, luminosité matérielle, mode température GPU et retour au fond matériel.
- **7 effets synchronisés** (`Statique`, `Arc-en-ciel`, `Vague arc-en-ciel`, `Vague couleur`, `Pulsation`, `Ondulation`, `Température`) avec vitesse `1` à `10` et barre d’aperçu animée.
- Modes CLI sans interface graphique : `--boot [--delay N] [--tries N] [--check]`, `--corsair-keepalive`, `--corsair-stop`.

## v1.2.2 — 2026-10-17

- La barre de titre affiche l'icône Lumino à la place de la plume de Tk.
- Le numéro de version est en bas à droite. « Prêt » reste visible quand la fenêtre est réduite.
- La lecture Ambiglow ignore la sonde de luminosité d'hdr2sdr quand les deux se croisent sur le bus de l'écran.

## v1.2.1 — 2026-10-16

- Retire le pourcentage de la ligne Ambiglow dans la sidebar (n'a pas de sens, l'Ambiglow utilise Brillant / Plus brillant / Très brillant). Pastille couleur conservée.

## v1.2.0 — 2026-10-09

Contrôle Ambiglow du Philips Evnia 27M2N8500 depuis Lumino, par le DisplayPort.

- Modes du menu : Désactivé, Suivre la vidéo, Suivre l'audio, Changement de couleur, Onde de couleur, Respiration des couleurs, Nuit étoilée, Mode statique.
- Couleurs du menu, grisées quand le mode ne les accepte pas. Un clic ne change pas le mode.
- Luminosité : Brillant, Plus brillant, Très brillant.
- Vitesse : Bas, Normal, Haut, seulement pour les quatre effets de couleur.
- Pastilles de Tout alignées sur cette palette, sans l'arc-en-ciel. Les teintes PC sont ajustées pour coller à l'écran.
- Bulles de couleur lissées.

## v1.1.0 — 2026-10-06

Hub iCUE LINK (waterblock et ventilos séparés), effets partagés sur le GPU, la carte mère et le Corsair, image et luminosité de l'écran LCD. Le site web n'est plus dans l'application.

## v1.0.1 — 2026-09-27

Corrige la restauration du pourcentage au lancement : aperçu atténué selon la luminosité, teinte d'origine récupérée, curseurs synchronisés sur Tout appliquer.

## v1.0.0 — 2026-09-25

Première version : app bureau RGB (RTX 5080 + ASUS Aura), sans serveur. `installer_demarrage.bat` réapplique la couleur au démarrage de Windows.
