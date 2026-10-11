"""Security regression scan (cyber / NF5 / §13). Run with system Python.

Fails (exit 1) if any git-tracked file contains a secret-shaped string, if a
sensitive file is tracked, or if the socket server is bound to a non-local
address. Prints SECURITY_SCAN_OK when clean. Wired into run_all_tests.py.
"""
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Strict secret patterns (prefixes / key blocks / quoted credential assignments)
SECRET_RES = [
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA |PGP )?PRIVATE KEY-----"),
    re.compile(r"AKIA[0-9A-Z]{16}"),                       # AWS access key id
    re.compile(r"ghp_[A-Za-z0-9]{36}"),                    # GitHub PAT
    re.compile(r"github_pat_[A-Za-z0-9_]{40,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),           # Slack
    re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),              # Anthropic API key
    re.compile(r"sk-(?:proj|svcacct|admin)-[A-Za-z0-9_-]{20,}"),  # OpenAI project/service keys
    re.compile(r"sk-[A-Za-z0-9]{32,}"),                    # OpenAI legacy
    re.compile(r"hf_[A-Za-z0-9]{30,}"),                    # Hugging Face
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),                  # Google API key
    re.compile(r"gh[ousr]_[A-Za-z0-9]{36}"),               # GitHub OAuth/user/server tokens
    re.compile(r"(?i)(password|passwd|api[_-]?key|secret|access[_-]?token)"
               r"\s*[=:]\s*['\"][^'\"]{8,}['\"]"),         # quoted credential
    # unquoted env-style assignment of a long opaque value (e.g. in .ps1/.bat/.md)
    re.compile(r"(?m)^\s*(?:set\s+|export\s+|\$env:)?[A-Z][A-Z0-9_]*(?:API_KEY|TOKEN|SECRET)"
               r"\s*=\s*[A-Za-z0-9_\-]{24,}\s*$"),
]
# This is a fast local guard; CI also runs Gitleaks (.github/workflows/tests.yml).
# Files that legitimately contain the *patterns themselves* (this scanner + the policy).
SELF = {"install/security_scan.py", "SECURITY.md"}
SENSITIVE = re.compile(r"(^|/)(\.env|.*\.pem|.*\.key|id_rsa|credentials\.json|secrets\.)")


def tracked_files():
    out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True)
    return [l for l in out.stdout.splitlines() if l.strip()]


def main() -> int:
    problems = []
    files = tracked_files()

    for rel in files:
        if SENSITIVE.search(rel):
            problems.append(f"sensitive file tracked: {rel}")
        if rel in SELF:
            continue
        p = REPO / rel
        try:
            if p.stat().st_size > 1_000_000:
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for rx in SECRET_RES:
            m = rx.search(text)
            if m:
                problems.append(f"secret-shaped string in {rel}: {m.group(0)[:24]}…")

    # Bind must be local only (best-effort: check the base handler if cloned)
    handler = REPO / "server" / "freecad-mcp" / "AICopilot" / "freecad_mcp_handler.py"
    if handler.exists():
        h = handler.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"bind\(\(\s*['\"]0\.0\.0\.0['\"]", h):
            problems.append("socket bound to 0.0.0.0 (must be localhost) — NF5")

    if problems:
        print("SECURITY_SCAN_FAILED:")
        for p in problems:
            print("  -", p)
        return 1
    print(f"scanned {len(files)} tracked files — no secrets, no sensitive files, bind local")
    print("SECURITY_SCAN_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
