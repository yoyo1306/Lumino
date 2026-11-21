"""Lumino - scan CLI (Blackwell 0x75 d'abord, legacy ensuite)."""
import sys
from nvapi import NvAPI
import blackwell
import gigabyte


def main():
    nv = NvAPI()
    nv.initialize()
    gpus = nv.enum_gpus()
    print(f"GPUs trouvés : {len(gpus)}")
    if not gpus:
        return 1
    for i, h in enumerate(gpus):
        name = nv.get_name(h)
        pci = nv.get_pci(h)
        print(f"\n[GPU {i}] {name}")
        print(f"  PCI VEN_{pci['vendor_id']:04X} DEV_{pci['device_id']:04X} "
              f"SUBSYS_{pci['sub_device']:04X}{pci['sub_vendor']:04X}")
        ok, detail = blackwell.probe(nv, h, port=1)
        if ok:
            print(f"  -> Blackwell Fusion2 détecté à 0x75, réponse={detail['reply']}")
        else:
            print(f"  -> Pas de Blackwell à 0x75 ({detail}). Essai legacy...")
            found = gigabyte.scan_gpu(nv, h, port=1)
            for f in found:
                print(f"  -> Legacy à 0x{f['addr']:02X}")
            if not found:
                print("  -> Rien détecté.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
