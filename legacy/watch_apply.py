"""
Lumino - surveille quel processus travaille pendant un Apply GCC.
Usage :
  1. python watch_apply.py        (laisse tourner)
  2. Dans GCC : couleur -> APPLY (2-3 fois)
  3. Ctrl+C -> envoie apply_watch.json
"""
import json
import time
import psutil  # pip install psutil (ou remplace par WMI si absent)

OUT = "apply_watch.json"
seen = {}
for p in psutil.process_iter(["pid", "name", "cmdline"]):
    try:
        seen[p.info["pid"]] = p.info["name"]
    except Exception:
        pass

events = []
print(f"{len(seen)} processus de base. Clique APPLY dans GCC, puis Ctrl+C.")
try:
    while True:
        for p in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
            try:
                if p.info["pid"] not in seen:
                    seen[p.info["pid"]] = p.info["name"]
                    ev = {"t": time.time(), "pid": p.info["pid"],
                          "name": p.info["name"],
                          "cmd": " ".join(p.info.get("cmdline") or [])[:300]}
                    events.append(ev)
                    print("[nouveau]", ev["name"], ev["pid"], ev["cmd"][:120])
            except Exception:
                pass
        time.sleep(0.3)
except KeyboardInterrupt:
    pass
with open(OUT, "w") as f:
    json.dump(events, f, indent=2)
print(f"{len(events)} nouveaux processus -> {OUT}")
