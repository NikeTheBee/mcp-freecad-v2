"""compute.resolve: CPU by default everywhere; gpu/both use the CUDA twin only when it is
installed (else CPU, said in a note); the thread variables follow the profile; an unknown device
is refused. Runs against a throwaway workshop home. Sentinel: COMPUTE_OK
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "server"))


def main() -> int:
    home = Path(tempfile.mkdtemp(prefix="mcpfc_compute_"))
    old = os.environ.get("MCPFC_HOME")
    try:
        os.environ["MCPFC_HOME"] = str(home)
        # fake environment prefixes: env_python() only checks that the interpreter file exists
        def prefix(name):
            d = home / "envs" / name
            exe = d / ("python.exe" if os.name == "nt" else "bin/python")
            exe.parent.mkdir(parents=True, exist_ok=True)
            exe.write_bytes(b"")
            return str(d)
        cfg = {"envs": {"ai": prefix("ai"), "sim": prefix("sim")}}
        (home / "atelier.json").write_text(json.dumps(cfg), encoding="utf-8")
        import atelier
        from atelier import compute
        atelier.config.cache_clear() if hasattr(atelier.config, "cache_clear") else None
        assert compute.resolve("ai", None)[:2] == ("ai", "cpu"), "no profile: CPU"
        env, dev, note = compute.resolve("ai", "gpu")
        assert (env, dev) == ("ai", "cpu") and "not installed" in note, (env, dev, note)
        print("no twin installed: gpu asked -> CPU, with a note OK")
        cfg["envs"]["ai-gpu"] = prefix("ai-gpu")
        cfg["compute"] = {"device": "both", "cpu_workers": 7, "omp_threads": 7, "mpi_ranks": 8,
                          "gpu": {"name": "RTX"}, "gpu_envs": {"ai": "ai-gpu"}}
        (home / "atelier.json").write_text(json.dumps(cfg), encoding="utf-8")
        atelier.config.cache_clear() if hasattr(atelier.config, "cache_clear") else None
        assert compute.resolve("ai", None)[:2] == ("ai-gpu", "both")
        assert compute.resolve("ai", "cpu")[:2] == ("ai", "cpu")
        assert compute.resolve("sim", "gpu")[:2] == ("sim", "cpu"), "no twin for sim: CPU"
        v = compute.env_vars("cpu")
        assert v["OMP_NUM_THREADS"] == "7" and v["MCPFC_MPI_RANKS"] == "8" and v["CUDA_VISIBLE_DEVICES"] == ""
        assert "CUDA_VISIBLE_DEVICES" not in compute.env_vars("both")
        try:
            compute.resolve("ai", "tpu")
            raise AssertionError("unknown device accepted")
        except ValueError:
            pass
        print("profile both -> ai-gpu; cpu forced -> ai; threads 7, MPI 8, GPU hidden on CPU OK")
    finally:
        if old is None:
            os.environ.pop("MCPFC_HOME", None)
        else:
            os.environ["MCPFC_HOME"] = old
        shutil.rmtree(home, ignore_errors=True)
    print("COMPUTE_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
