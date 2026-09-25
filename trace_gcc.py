"""
Lumino - trace les appels GvLedLib de GCC.exe via Frida.
But : capturer la séquence exacte utilisée pour la RTX 5080 INFINITY WOOD
(fonction VGA + struct), puis la rejouer sans GCC.

Usage :
  1. Ouvre GCC, va sur la page RGB de la 5080 (ne change rien encore).
  2. python trace_gcc.py        (laisse tourner)
  3. Dans GCC : change la couleur 2-3 fois (ex rouge -> bleu -> vert).
  4. Reviens ici, Ctrl+C, envoie le fichier gcc_trace.json.

Nécessite : pip install frida (déjà présent ici : frida 17.17).
"""
import json
import time
import frida

TARGETS = ("GCC.exe", "AorusLcdService.exe")
DLLS = ("GvIllumLib.dll", "GvLedLib.dll", "GvDisplay.dll", "GLedApi.dll", "nvapi64.dll")
OUT = "gcc_trace.json"
events = []


def on_msg(msg, data):
    if msg["type"] == "send":
        events.append(msg["payload"])
        print("[trace]", json.dumps(msg["payload"])[:300])
    elif msg["type"] == "error":
        print("[frida-error]", msg)


JS = """
const dlls = %s;
// 1) hooks DLLs Gigabyte + nvapi QueryInterface (comme avant)
let total = 0;
for (const dll of dlls) {
  let mod = null;
  try { mod = Process.getModuleByName(dll); }
  catch (x) { console.log("[!] " + dll + " non charge ici"); continue; }
  if (dll.toLowerCase() === "nvapi64.dll") {
    try {
      const qi = mod.getExportByName("nvapi_QueryInterface");
      Interceptor.attach(qi, {
        onEnter(args) { this._id = args[0].toUInt32().toString(16); },
        onLeave(retval) {
          send({proc: Process.id, fn: "nvapi_QueryInterface", id: this._id,
                ptr: retval.toString(), t: Date.now()});
          // si c'est une API I2C/Illum, hook le pointeur retourné pour les appels suivants
          const qid = this._id;
          if (ids.indexOf(qid.toLowerCase()) !== -1) {
            try {
              Interceptor.attach(retval, {
                onEnter(a) {
                  send({proc: Process.id, fn: "nvapi_fn_" + qid, t: Date.now()});
                }
              });
              console.log("[*] hook pointeur nvapi " + qid);
            } catch (x) { console.log("[!] hook ptr fail: " + x); }
          }
        }
      });
      console.log("[*] nvapi64.dll: hook nvapi_QueryInterface pose");
      total++;
    } catch (x) { console.log("[!] nvapi hook fail: " + x); }
    continue;
  }
  const exps = mod.enumerateExports();
  let n = 0;
  for (const e of exps) {
    if (e.name.indexOf("dllexp_") !== 0 && e.name.indexOf("Gv") !== 0) continue;
    try {
      Interceptor.attach(e.address, {
        onEnter(args) {
          this._name = e.name;
          this._args = [args[0], args[1], args[2], args[3]].map(a => {
            try { return a.toString(); } catch (x) { return "?"; }
          });
          try {
            const p = args[1];
            if (!p.isNull()) {
              const words = [];
              for (let i = 0; i < 11; i++) words.push(p.add(i * 4).readU32());
              this._cfg = words;
            }
          } catch (x) {}
        },
        onLeave(retval) {
          send({proc: Process.id, fn: this._name, args: this._args,
                cfg: this._cfg || null, ret: retval.toString(), t: Date.now()});
        }
      });
      n++;
    } catch (x) {}
  }
  console.log("[*] " + dll + ": hooks " + n + "/" + exps.length);
  total += n;
}
console.log("[*] total hooks: " + total);
// 2) intercepte les ouvertures de pilotes/périphériques (HID, I2C, NVIDIA...)
try {
  const k32 = Process.getModuleByName("kernel32.dll");
  const cf = k32.getExportByName("CreateFileW");
  Interceptor.attach(cf, {
    onEnter(args) {
      try { this._p = args[0].readUtf16String(); } catch (x) { this._p = "?"; }
    },
    onLeave(retval) {
      const p = (this._p || "").toLowerCase();
      if (p.indexOf("hid") !== -1 || p.indexOf("i2c") !== -1 || p.indexOf("nv") !== -1 ||
          p.indexOf("gpio") !== -1 || p.indexOf("led") !== -1 || p.indexOf("rgb") !== -1 ||
          p.indexOf("ite") !== -1 || p.indexOf("575") !== -1 || p.indexOf("8297") !== -1) {
        send({proc: Process.id, fn: "CreateFileW", path: this._p, h: retval.toString(), t: Date.now()});
      }
    }
  });
  console.log("[*] hook CreateFileW pose");
} catch (x) { console.log("[!] CreateFileW hook fail: " + x); }
try {
  const k32b = Process.getModuleByName("kernel32.dll");
  const dioc = k32b.getExportByName("DeviceIoControl");
  Interceptor.attach(dioc, {
    onEnter(args) {
      this._code = args[1].toUInt32().toString(16);
      try { this._in = args[4].toUInt32(); this._out = args[6].toUInt32(); }
      catch (x) { this._in = -1; this._out = -1; }
    },
    onLeave(retval) {
      if (retval.toUInt32() !== 0) {
        send({proc: Process.id, fn: "DeviceIoControl", code: this._code,
              inLen: this._in, outLen: this._out, t: Date.now()});
      }
    }
  });
  console.log("[*] hook DeviceIoControl pose (succès seuls)");
} catch (x) { console.log("[!] DeviceIoControl hook fail: " + x); }
// 3) ntdll direct (nvapi et drivers parlent souvent ici sans passer par kernel32)
// handle -> chemin (via NtCreateFile)
const handlePaths = {};
try {
  const nt2 = Process.getModuleByName("ntdll.dll");
  Interceptor.attach(nt2.getExportByName("NtCreateFile"), {
    onEnter(args) {
      this._hout = args[2];
      try {
        const oa = args[3];
        const namePtr = oa.add(Process.pointerSize * 2).readPointer();
        const len = oa.add(Process.pointerSize * 2 + Process.pointerSize).readU16();
        this._p = namePtr.readUtf16String(len);
      } catch (x) { this._p = "?"; }
    },
    onLeave(retval) {
      if (retval.toUInt32() === 0) {
        try {
          const h = this._hout.readPointer().toString();
          handlePaths[h] = this._p;
          const pl = (this._p || "").toLowerCase();
          if (pl.indexOf("hid") !== -1 || pl.indexOf("i2c") !== -1 || pl.indexOf("nv") !== -1 ||
              pl.indexOf("gpio") !== -1 || pl.indexOf("led") !== -1 || pl.indexOf("gv") !== -1 ||
              pl.indexOf("gigabyte") !== -1 || pl.indexOf("display") !== -1) {
            send({proc: Process.id, fn: "NtCreateFile", path: this._p, h: h, t: Date.now()});
          }
        } catch (x) {}
      }
    }
  });
  console.log("[*] hook NtCreateFile pose");
} catch (x) { console.log("[!] NtCreateFile hook fail: " + x); }
// résout le chemin d'un handle (même ouvert avant la trace) via NtQueryObject
let _qobj = null;
try {
  const ntq = Process.getModuleByName("ntdll.dll");
  _qobj = new NativeFunction(ntq.getExportByName("NtQueryObject"), "int",
    ["pointer", "int", "pointer", "uint", "pointer"]);
} catch (x) {}
function handlePath(h) {
  try {
    if (_qobj === null) return "?";
    const buf = Memory.alloc(1024);
    const rl = Memory.alloc(4);
    if (_qobj(h, 1, buf, 1024, rl) !== 0) return "?";
    const len = buf.readU16();
    if (len === 0 || len > 1000) return "?";
    return buf.add(8).readPointer().readUtf16String(len);
  } catch (x) { return "?"; }
}
try {
  const nt = Process.getModuleByName("ntdll.dll");
  Interceptor.attach(nt.getExportByName("NtDeviceIoControlFile"), {
    onEnter(args) {
      this._h = args[0].toString();
      this._code = args[5].toUInt32().toString(16);
      try {
        this._inlen = args[7].toUInt32();
        const n = Math.min(this._inlen, 256);
        const bytes = [];
        for (let i = 0; i < n; i++) bytes.push(args[6].add(i).readU8().toString(16).padStart(2, "0"));
        this._head = bytes.join(" ");
      } catch (x) { this._inlen = -1; this._head = ""; }
    },
    onLeave(retval) {
      if (retval.toUInt32() === 0) {
        let dev = handlePaths[this._h] || "?";
        if (dev === "?") { dev = handlePath(this._h); handlePaths[this._h] = dev; }
        send({proc: Process.id, fn: "NtIoctl", code: this._code, h: this._h,
              dev: dev,
              inLen: this._inlen, head: this._head, t: Date.now()});
      }
    }
  });
  console.log("[*] hook NtDeviceIoControlFile pose (succès seuls)");
} catch (x) { console.log("[!] NtDeviceIoControlFile hook fail: " + x); }
// 4) ExtEscape (canal GDI vers le pilote d'affichage, invisible pour les hooks ci-dessus)
try {
  const gdi = Process.getModuleByName("gdi32.dll");
  Interceptor.attach(gdi.getExportByName("ExtEscape"), {
    onEnter(args) {
      this._esc = args[1].toUInt32().toString(16);
      try { this._in = args[2].toUInt32(); this._out = args[4].toUInt32(); }
      catch (x) { this._in = -1; this._out = -1; }
    },
    onLeave(retval) {
      if (retval.toUInt32() > 0) {
        send({proc: Process.id, fn: "ExtEscape", esc: this._esc,
              inLen: this._in, outLen: this._out, t: Date.now()});
      }
    }
  });
  console.log("[*] hook ExtEscape pose");
} catch (x) { console.log("[!] ExtEscape hook fail: " + x); }
""" % (list(DLLS),)

print("Recherche des processus ...")
for p in frida.get_local_device().enumerate_processes():
    pname = getattr(p, "name", str(p))
    pid = getattr(p, "pid", "?")
    if pname.lower().startswith(("gcc", "launchgcc", "aorus")):
        print(" ", pid, pname)

sessions = []
for name in TARGETS:
    try:
        s = frida.attach(name)
        print(f"Attaché à {name}")
        sessions.append((name, s))
    except Exception as e:
        print(f"  {name} : {e}")
if not sessions:
    raise SystemExit("Aucun processus Gigabyte trouvé. Lance GCC puis relance (terminal admin si besoin).")
for name, session in sessions:
    script = session.create_script(JS)
    script.on("message", on_msg)
    script.load()
print("Hooks actifs. Dans GCC : choisis une couleur puis clique APPLY (2-3 fois), puis Ctrl+C.")
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    pass
with open(OUT, "w") as f:
    json.dump(events, f, indent=2)
print(f"{len(events)} appels capturés -> {OUT}")
