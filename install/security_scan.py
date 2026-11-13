"""Security regression scan (cyber / NF5 / §13). Run with system Python.

Fails (exit 1) if any git-tracked file contains a secret-shaped string, if a
sensitive file is tracked, or if the socket server is bound to a non-local
address. Prints SECURITY_SCAN_OK when clean. Wired into run_all_tests.py.
"""
import re
import subprocess
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
               r"[ \t]*[=:][ \t]*['\"][^'\"\r\n]{8,}['\"]"),  # quoted credential, one line
    # unquoted env-style assignment of a long opaque value (e.g. in .ps1/.bat/.md)
    re.compile(r"(?m)^\s*(?:set\s+|export\s+|\$env:)?[A-Z][A-Z0-9_]*(?:API_KEY|TOKEN|SECRET)"
               r"\s*=\s*[A-Za-z0-9_\-]{24,}\s*$"),
]
# This is a fast local guard; CI also runs Gitleaks (.github/workflows/tests.yml).
# Files that legitimately contain the *patterns themselves* (this scanner + the policy).
SELF = {"install/security_scan.py", "SECURITY.md", ".gitleaks.toml"}
# Known FAKE secrets used as test inputs, by (file, exact match) — never a whole-file skip.
# The vendored base tests its crash-report redaction with these (.gitleaks.toml mirrors it).
FAKE_FIXTURES = {
    ("server/freecad-mcp/tests/unit/test_crash_watcher.py", "AKIAABCDEFGHIJKLMNOP"),
    ("server/freecad-mcp/tests/unit/test_crash_watcher.py", "password: 'hunter2222'"),
    ("server/freecad-mcp/tests/unit/test_crash_watcher.py",
     'secret = "sk-abcdef1234567890ABCDEFGHIJ"'),
}
SENSITIVE = re.compile(r"(^|/)(\.env|.*\.pem|.*\.key|id_rsa|credentials\.json|secrets\.)")


def tracked_files():
    out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True)
    return [l for l in out.stdout.splitlines() if l.strip()]


_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def bind_problems(source: str) -> list:
    """The TCP listener must be bound to the loopback interface only (NF5).

    Security audit 2026-11-12: the former check looked for the literal
    bind(("0.0.0.0", but the handler binds (WINDOWS_HOST, WINDOWS_PORT):
    setting WINDOWS_HOST to "0.0.0.0", "" (every interface) or an environment
    variable passed the scan. Now: WINDOWS_HOST is a loopback literal, and every
    TCP bind uses it or a loopback literal.
    """
    out = []
    hosts = re.findall(r"(?m)^WINDOWS_HOST\s*=\s*(.+?)\s*(?:#.*)?$", source)
    for h in hosts:
        m = re.fullmatch(r"(['\"])(.*)\1", h)
        if not m or m.group(2) not in _LOOPBACK:
            out.append(f"WINDOWS_HOST = {h} (must be a loopback literal) — NF5")
    if not hosts:
        out.append("WINDOWS_HOST not found in the handler: bind address not verifiable — NF5")
    for arg in re.findall(r"\.bind\(\(\s*([^,()]+?)\s*,", source):   # (host, port) tuples = TCP
        m = re.fullmatch(r"(['\"])(.*)\1", arg)
        if arg != "WINDOWS_HOST" and not (m and m.group(2) in _LOOPBACK):
            out.append(f"socket bound to {arg} (must be loopback) — NF5")
    return out


def workflow_problems(rel: str, text: str) -> list:
    """CI hygiene: a workflow states its token permissions (the default can be
    read-write on the whole repository) and never runs on pull_request_target
    (untrusted fork code with the repository's secrets)."""
    out = []
    if not re.search(r"(?m)^permissions:", text):
        out.append(f"{rel}: no top-level 'permissions:' (GITHUB_TOKEN keeps the repository default)")
    if re.search(r"(?m)^\s*pull_request_target\s*:", text):
        out.append(f"{rel}: pull_request_target runs fork code with repository secrets")
    return out


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
            for m in rx.finditer(text):
                if (rel, m.group(0)) in FAKE_FIXTURES:
                    continue
                problems.append(f"secret-shaped string in {rel}: {m.group(0)[:24]}…")
                break

    # Bind must be local only (best-effort: check the base handler if cloned)
    handler = REPO / "server" / "freecad-mcp" / "AICopilot" / "freecad_mcp_handler.py"
    if handler.exists():
        problems += bind_problems(handler.read_text(encoding="utf-8", errors="ignore"))
    # the detector itself must see the regressions it is there for
    for doctored in ('WINDOWS_HOST = "0.0.0.0"\ns.bind((WINDOWS_HOST, WINDOWS_PORT))',
                     'WINDOWS_HOST = ""\ns.bind((WINDOWS_HOST, WINDOWS_PORT))',
                     'WINDOWS_HOST = os.environ.get("H", "localhost")\ns.bind((WINDOWS_HOST, 1))',
                     'WINDOWS_HOST = "localhost"\ns.bind(("0.0.0.0", 1))',
                     'WINDOWS_HOST = "localhost"\ns.bind((host, 1))'):
        if not bind_problems(doctored):
            problems.append(f"bind check is blind to: {doctored!r}")

    for wf in files:
        if wf.startswith(".github/workflows/") and wf.endswith((".yml", ".yaml")):
            problems += workflow_problems(wf, (REPO / wf).read_text(encoding="utf-8", errors="ignore"))

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
