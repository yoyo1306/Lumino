# Lumino — guide

App bureau. Pas de site, pas de serveur. Double-clic sur `Lumino.exe`
(ou `lumino.bat` s'il n'y a pas encore d'exe).

## Régler les LEDs

À gauche, les quatre cibles (Tout, GPU, carte mère, Corsair) avec leur couleur et leur luminosité. À droite, le réglage de celle qui est sélectionnée.

- **Tout** — même couleur et même effet partout.
- **GPU** — en statique, couleur gravée dans la RTX 5080. Un effet est le même que sur la carte mère et le Corsair.
- **Carte mère** — en statique, couleur gravée. Un effet suit les deux autres.
- **Corsair** — Titan 240 (pompe + anneau) et 2× RX. Le mode et la vitesse valent pour les trois.

**Sombre / Clair** en haut à gauche change le thème et le mémorise.

**0 %** éteint l'appareil. La teinte et le mode restent en mémoire : remonter le curseur et réappliquer rallume.

**Éteindre** (à droite) concerne l'appareil sélectionné. Sur Tout, c'est le même arrêt que **Tout éteindre** à gauche : confirmation, les trois passent à 0 %, et c'est cet état qui revient au prochain démarrage.

Le résultat s'affiche en bas du panneau. Une boîte de dialogue n'apparaît qu'en cas d'échec, ou pour confirmer un arrêt. Pendant un appliquer, les boutons sont bloqués.

**Arrêter le fond** laisse le hub revenir aux couleurs iCUE (Device Memory). Les LEDs Corsair ne tiendront plus la couleur Lumino tant que tu n'auras pas réappliqué.

## Corsair, à savoir

iCUE doit rester **fermé**. Le hub oublie la couleur logicielle dès qu'on arrête de lui parler : Lumino laisse donc un petit processus en fond (`corsair_keep`). Ce fond joue aussi l'effet sur le GPU et la carte mère. Le GPU reçoit une image entière environ chaque seconde (son contrôleur est lent) ; la carte mère suit en direct. Statique et température sur le Corsair : renvoi toutes les 4 s. Les autres modes Corsair tournent vers 5 images/s (le protocole USB impose ~30 ms entre chaque commande, 20 images/s n'est pas atteignable).

Ordre des LEDs : pompe 20, anneau LCD 24, RX 8, RX 8. **Écran LCD** (barre de gauche) affiche la température GPU sur l'écran du Titan. HydroScreen et iCUE doivent rester fermés pendant ce temps : un seul programme peut parler à l'écran. L'arrêter rend tout de suite le fond enregistré dans l'écran.

Les ventilos et la pompe gardent la courbe réglée une fois dans iCUE, mode Device Memory, avant de fermer iCUE.

La luminosité agit sur tous les modes, y compris arc-en-ciel et température.

## Démarrage Windows

`installer_demarrage.bat` pose un raccourci : `Lumino.exe --boot --delay 10`.
Au login, Lumino réapplique le GPU et la carte mère, relance le fond Corsair, puis la fenêtre ne s'ouvre pas.

`desinstaller_demarrage.bat` retire ce raccourci.

## Si ça ne suit plus

1. Ferme iCUE (y compris la zone de notification) avant d'appliquer le Corsair.
2. Scan KO sur le GPU → redémarre le PC. Le contrôleur LED Gigabyte se coince parfois.
3. Couleur GPU perdue après une mise à jour driver → réapplique (le boot renvoie la couleur sans réécrire l'EEPROM à chaque fois).
4. Le fond Corsair ne démarre pas → relance `Lumino.exe`. Le journal est `lumino.log`, à côté de l'exe.

## Fichiers utiles

- `lumino_gui.py` — l'interface.
- `apply_boot.py` — restauration au démarrage.
- `blackwell.py` / `nvapi.py` — GPU.
- `aura.py` — carte mère.
- `corsair_link.py` / `corsair_keep.py` / `effects.py` — hub Corsair et animations.
- `config.json` — dernier choix (teinte brute + luminosité 0–100, pas une couleur déjà assombrie).
- `scan.py` — détection en ligne de commande, sans rien changer.

`gigabyte.py`, `gv_official.py` et les scripts de trace sont des restes du reverse. L'app ne s'en sert pas.
