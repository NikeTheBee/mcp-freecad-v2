"""hw_scan: no consent -> nothing is read; the plan follows the hardware (CUDA build only for a
suitable NVIDIA GPU, CPU profile otherwise); writing keeps the rest of atelier.json.
Uses fake probes: the real machine is not scanned by the test. Sentinel: HWSCAN_TEST_OK
"""
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "install"))
import hw_scan as hs  # noqa: E402


def fake(ram, cores, gpus):
    called = []

    def mk(name, val):
        def f(*a):
            called.append(name)
            return val
        return f
    return {"ram": mk("ram", ram), "cpu": mk("cpu", {"name": "Test CPU", "physical_cores": cores,
                                                       "logical_cores": 2 * cores}),
            "gpus": mk("gpus", gpus), "disk": mk("disk", 500.0)}, called


def main() -> int:
    # consent
    assert hs.ask(yes=False, stream=io.StringIO("")) is False      # not a terminal: no
    assert hs.ask(yes=True) is True
    print("no consent in a non-interactive shell, --yes is consent OK")

    rtx = [{"vendor": "NVIDIA", "name": "NVIDIA GeForce RTX 5090", "vram_gb": 31.8,
            "driver": "581.57", "compute_capability": "12.0"}]
    pr, called = fake(31.9, 8, rtx)
    home = Path(tempfile.mkdtemp(prefix="mcpfc_hw_"))
    try:
        hw = hs.scan(home, pr)
        pl = hs.plan(hw, ["tout"])
        assert pl["add_packs"] == ["ia-gpu"], pl
        assert pl["compute"]["device"] == "both" and pl["compute"]["cpu_workers"] == 7
        assert pl["compute"]["gpu_envs"] == {"ai": "ai-gpu"}
        assert pl["compute"]["mpi_ranks"] == 8
        print("RTX 5090 + 8 cores + 32 GB -> ia-gpu, profile both, 7 workers OK")
        assert hs.plan(hw, ["socle"])["add_packs"] == [], "no AI pack asked: nothing GPU downloaded"
        old = [dict(rtx[0], driver="551.23")]
        pl_old = hs.plan(dict(hw, gpus=old), ["tout"])
        assert pl_old["add_packs"] == [] and pl_old["compute"]["device"] == "cpu"
        assert any("update the NVIDIA driver" in n for n in pl_old["notes"])
        amd = [{"vendor": "AMD", "name": "AMD Radeon RX 7900", "vram_gb": 0.0, "driver": "", "compute_capability": ""}]
        pl_amd = hs.plan(dict(hw, gpus=amd, ram_gb=8.0), ["tout"])
        assert pl_amd["compute"]["device"] == "cpu" and pl_amd["add_packs"] == []
        assert any("8 GB RAM" in n for n in pl_amd["notes"])
        print("old driver / AMD / 8 GB -> CPU profile, no CUDA download, advice OK")
        (home / "atelier.json").write_text(json.dumps({"envs": {"sim": "x"}}), encoding="utf-8")
        hs.write(home, hw, pl)
        cfg = json.loads((home / "atelier.json").read_text(encoding="utf-8"))
        assert cfg["envs"] == {"sim": "x"} and cfg["compute"]["device"] == "both"
        assert json.loads((home / "hardware.json").read_text(encoding="utf-8"))["ram_gb"] == 31.9
        print("compute profile written, the rest of atelier.json kept OK")
        print(hs.describe(hw, pl).splitlines()[-1])
    finally:
        shutil.rmtree(home, ignore_errors=True)
    print("HWSCAN_TEST_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
