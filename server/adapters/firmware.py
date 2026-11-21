"""Firmware without a board (env `elec`): compile C for a Cortex-M4 (STM32F4) with the Arm GNU
toolchain and run it in Renode; the UART output is the test result.

Kind `firmware_sim`, params:
  sources: {"main.c": "<C code>", "pid.h": "..."}  and/or files (project files copied in by the
           server, e.g. "fw/main.c"); simple names only, #include of absolute or ".." paths refused
  defines: {"KP": "1.5"}   opt: "-O0".."-O3" | "-Os" (default -O2)   run_s: virtual seconds (1)
  expect: {"sum": 5050, "root": [1.41421, 1e-5]}  numbers printed as "key=value" lines on the UART
          (a value alone = exact match; [value, rel_tol])
  contains: ["READY"]   texts that must appear
  limits: e.g. {"text_bytes": {"max": 65536}, "exit_code": {"min": 0, "max": 0}}
What the firmware gets: newlib-nano with printf (floats included) on USART2, the FPU enabled,
.data / .bss initialised, `main` called; returning from main prints "MCPFC_EXIT <code>"; any
fault prints "MCPFC_HARDFAULT". Platform: Renode's stm32f4.repl (STM32F407, 1 MB flash, 128 KB
RAM). Peripherals other than the UART are Renode's models: timing is instruction-accurate, not
cycle-accurate.

Safety: the firmware only ever runs in the emulator; flashing a real board stays a human action.
Renode is audited for sockets (non-loopback -> killed, UNKNOWN) and works in a scratch folder of
its own (Renode cannot take paths with spaces, and resolves relative ones in its install).
"""
from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List

from _common import result, run_exe, tool

ENV = "elec"
KINDS = {"firmware_sim": {"geometry": False, "timeout": 600,
                          "doc": "compile C for STM32F4 (Arm GNU) and run it in Renode; UART key=value results"}}
_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,60}\.(c|h|s|S)")
_OPT = {"-O0", "-O1", "-O2", "-O3", "-Os", "-Og"}
_INC = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]', re.M)
_KV = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_.]*)\s*=\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*$", re.M)

STARTUP = r'''#include <stdint.h>
extern uint32_t _estack, _sidata, _sdata, _edata, _sbss, _ebss;
int main(void);
#define USART2_SR (*(volatile uint32_t*)0x40004400)
#define USART2_DR (*(volatile uint32_t*)0x40004404)
#define USART2_CR1 (*(volatile uint32_t*)0x4000440C)
void mcpfc_putc(char c){ while(!(USART2_SR & (1u<<7))){} USART2_DR = (uint32_t)(unsigned char)c; }
static void say(const char*s){ while(*s) mcpfc_putc(*s++); }
void HardFault_Handler(void){ say("\nMCPFC_HARDFAULT\n"); for(;;){} }
void Reset_Handler(void){
  uint32_t *s=&_sidata,*d=&_sdata; while(d<&_edata) *d++=*s++;
  for(d=&_sbss; d<&_ebss;) *d++=0;
  (*(volatile uint32_t*)0xE000ED88) |= (0xFu<<20);
  USART2_CR1 = (1u<<13)|(1u<<3);
  int r = main();
  char b[24]; int n=0, neg=r<0; unsigned v = neg ? (unsigned)(-r) : (unsigned)r;
  say("\nMCPFC_EXIT "); if(neg) mcpfc_putc('-'); if(!v) b[n++]='0';
  while(v){b[n++]=(char)('0'+v%10); v/=10;} while(n) mcpfc_putc(b[--n]); say("\n");
  for(;;){}
}
__attribute__((section(".isr_vector"))) const void* mcpfc_vectors[16] = { &_estack, (void*)Reset_Handler,
  (void*)HardFault_Handler, (void*)HardFault_Handler, (void*)HardFault_Handler, (void*)HardFault_Handler,
  (void*)HardFault_Handler };
'''
SYSCALLS = r'''#include <sys/stat.h>
#include <errno.h>
extern void mcpfc_putc(char c);
extern char _end, _heap_limit;
static char *mcpfc_brk = &_end;
int _write(int f, const char *p, int n){ (void)f; for(int i=0;i<n;i++) mcpfc_putc(p[i]); return n; }
void *_sbrk(int inc){ char *o=mcpfc_brk; if(mcpfc_brk+inc>&_heap_limit){ errno=ENOMEM; return (void*)-1;} mcpfc_brk+=inc; return o; }
int _close(int f){(void)f;return -1;} int _fstat(int f, struct stat*s){(void)f;s->st_mode=S_IFCHR;return 0;}
int _isatty(int f){(void)f;return 1;} int _lseek(int f,int p,int d){(void)f;(void)p;(void)d;return 0;}
int _read(int f,char*p,int n){(void)f;(void)p;(void)n;return 0;} void _exit(int c){(void)c;for(;;){}}
int _kill(int p,int s){(void)p;(void)s;return -1;} int _getpid(void){return 1;}
'''
LINKER = r'''MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 1024K
         RAM (rwx) : ORIGIN = 0x20000000, LENGTH = 128K }
_estack = ORIGIN(RAM) + LENGTH(RAM);
_heap_limit = _estack - 8K;
SECTIONS {
  .isr_vector : { KEEP(*(.isr_vector)) } > FLASH
  .text : { *(.text*) *(.rodata*) KEEP(*(.init)) KEEP(*(.fini)) . = ALIGN(4); } > FLASH
  .ARM.exidx : { *(.ARM.exidx*) } > FLASH
  _sidata = LOADADDR(.data);
  .data : { _sdata = .; *(.data*) . = ALIGN(4); _edata = .; } > RAM AT > FLASH
  .bss : { _sbss = .; *(.bss*) *(COMMON) . = ALIGN(4); _ebss = .; } > RAM
  _end = .;
}
'''


def versions() -> Dict[str, str]:
    gcc = run_exe([tool("arm_gnu"), "--version"], Path.cwd(), 30).stdout.splitlines()[0]
    return {"arm-none-eabi-gcc": gcc.split(")")[-1].strip() or gcc, "renode": "1.17.0"}


def _sources(inp: Dict[str, Any], work: Path) -> List[Path]:
    src_dir = work / "fw"
    src_dir.mkdir(exist_ok=True)
    out: List[Path] = []
    for name, text in (inp.get("sources") or {}).items():
        if not _NAME.fullmatch(name):
            raise ValueError(f"source name {name!r}: a simple file name ending in .c/.h/.s")
        (src_dir / name).write_text(str(text), encoding="utf-8")
    for rel in inp.get("files") or []:
        p = (work / rel).resolve()
        if not p.is_relative_to(work.resolve()) or not p.is_file() or not _NAME.fullmatch(p.name):
            raise ValueError(f"files: {rel!r} is not a C/H/S file of the study")
        shutil.copy2(p, src_dir / p.name)
    for f in src_dir.iterdir():
        for inc in _INC.findall(f.read_text(encoding="utf-8", errors="replace")):
            if inc.startswith(("/", "\\")) or ":" in inc or ".." in inc.replace("\\", "/").split("/"):
                raise PermissionError(f"{f.name}: #include {inc!r} reaches outside the firmware folder")
        if f.suffix in (".c", ".s", ".S"):
            out.append(f)
    if not out:
        raise ValueError("no .c source given")
    (src_dir / "mcpfc_startup.c").write_text(STARTUP, encoding="utf-8")
    (src_dir / "mcpfc_syscalls.c").write_text(SYSCALLS, encoding="utf-8")
    (src_dir / "mcpfc.ld").write_text(LINKER, encoding="utf-8")
    return out + [src_dir / "mcpfc_startup.c", src_dir / "mcpfc_syscalls.c"]


def _build(inp, work: Path) -> Dict[str, Any]:
    srcs = _sources(inp, work)
    opt = inp.get("opt", "-O2")
    if opt not in _OPT:
        raise ValueError(f"opt: one of {sorted(_OPT)}")
    defs = []
    for k, v in (inp.get("defines") or {}).items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,40}", str(k)) or not re.fullmatch(r"[-+.A-Za-z0-9_]{0,40}", str(v)):
            raise ValueError(f"define {k}={v}: letters, digits, _ . + - only")
        defs.append(f"-D{k}={v}" if v != "" else f"-D{k}")
    elf = work / "firmware.elf"
    args = [tool("arm_gnu"), "-mcpu=cortex-m4", "-mthumb", "-mfloat-abi=hard", "-mfpu=fpv4-sp-d16", opt,
            "-std=c11", "-Wall", "--specs=nano.specs", "-u", "_printf_float", "-ffunction-sections",
            "-fdata-sections", "-Wl,--gc-sections", "-Wl,--no-warn-rwx-segments",
            f"-Wl,-Map={work / 'firmware.map'}", "-T", str(work / "fw" / "mcpfc.ld"),
            "-I", str(work / "fw"), *defs, *map(str, srcs), "-lm", "-o", str(elf)]
    r = run_exe(args, work, 180)
    (work / "build.log").write_text(r.stdout + r.stderr, encoding="utf-8")
    if r.returncode != 0 or not elf.is_file():
        errs = [ln for ln in (r.stderr or "").splitlines() if "error" in ln.lower()]
        raise RuntimeError("compilation failed: " + " | ".join(errs[:4])[:500])
    size = run_exe([tool("arm_gnu", "arm-none-eabi-size.exe"), str(elf)], work, 30).stdout.splitlines()
    t, d, b = (int(x) for x in size[-1].split()[:3])
    warn = sum(1 for ln in (r.stderr or "").splitlines() if "warning:" in ln)
    return {"elf": elf, "text_bytes": t, "data_bytes": d, "bss_bytes": b,
            "flash_bytes": t + d, "ram_static_bytes": d + b, "warnings": warn}


def _audit(pid: int) -> List[str]:
    import psutil
    bad = []
    try:
        procs = [psutil.Process(pid)] + psutil.Process(pid).children(recursive=True)
    except psutil.Error:
        return bad
    for p in procs:
        try:
            for c in p.net_connections(kind="inet"):
                if c.laddr and c.laddr.ip not in ("127.0.0.1", "::1"):
                    bad.append(f"{c.laddr.ip}:{c.laddr.port}")
        except psutil.Error:
            pass
    return bad


def _emulate(elf: Path, run_s: float, timeout: float, work: Path) -> Dict[str, Any]:
    import subprocess
    # Renode's scratch folder lives INSIDE the job folder: the runner gives every run its own
    # and removes it afterwards, so nothing is left in the system temp. Renode cannot take a
    # path with a space in it, which is checked below rather than assumed.
    root = work / "renode"
    root.mkdir(parents=True, exist_ok=True)
    try:
        if " " in str(root):
            raise RuntimeError(f"Renode needs a scratch folder without spaces (got {root})")
        shutil.copy2(elf, root / "fw.elf")
        r = str(root).replace("\\", "/")
        (root / "run.resc").write_text(
            "mach create\nmachine LoadPlatformDescription @platforms/cpus/stm32f4.repl\n"
            f"sysbus LoadELF @{r}/fw.elf\nsysbus.usart2 CreateFileBackend @{r}/uart.txt true\n"
            f"emulation RunFor \"{run_s:g}\"\nquit\n", encoding="utf-8")
        flags = 0x08000000 if os.name == "nt" else 0
        proc = subprocess.Popen([tool("renode"), "--disable-gui", "--console", "-e", f"include @{r}/run.resc"],
                                cwd=str(root), stdout=open(root / "renode.log", "w"), stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, creationflags=flags)
        t0, bad = time.time(), []
        try:
            while proc.poll() is None:
                bad = _audit(proc.pid)
                if bad or time.time() - t0 > timeout:
                    break
                time.sleep(0.2)
        finally:
            if proc.poll() is None:
                import psutil
                for c in psutil.Process(proc.pid).children(recursive=True):
                    c.kill()
                proc.kill()
                proc.wait(10)
        if bad:
            raise PermissionError(f"Renode listened on {', '.join(bad)}: stopped")
        if time.time() - t0 > timeout:
            raise TimeoutError(f"Renode did not finish within {timeout:.0f} s")
        uart = (root / "uart.txt").read_text(encoding="utf-8", errors="replace") if (root / "uart.txt").is_file() else ""
        log = (root / "renode.log").read_text(encoding="utf-8", errors="replace")
        return {"uart": uart, "log": log}
    finally:
        shutil.rmtree(root, ignore_errors=True)


def run(kind: str, inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    b = _build(inp, work)
    run_s = float(inp.get("run_s", 1.0))
    if not 0 < run_s <= 60:
        raise ValueError("run_s: 0..60 virtual seconds")
    em = _emulate(b["elf"], run_s, float(inp.get("timeout_s", 300)), work)
    uart = em["uart"]
    (work / "uart.txt").write_text(uart, encoding="utf-8")
    (work / "renode.log").write_text(re.sub(r"\x1b\[[0-9;]*m", "", em["log"]), encoding="utf-8")
    values: Dict[str, Any] = {k: b[k] for k in ("text_bytes", "data_bytes", "bss_bytes", "flash_bytes",
                                                "ram_static_bytes", "warnings")}
    for k, v in _KV.findall(uart):
        values[k] = float(v)
    m = re.search(r"MCPFC_EXIT (-?\d+)", uart)
    values["exited"] = 1 if m else 0
    if m:
        values["exit_code"] = int(m.group(1))
    values["hardfault"] = 1 if "MCPFC_HARDFAULT" in uart else 0
    notes: List[str] = []
    failed: List[str] = []
    for key, want in (inp.get("expect") or {}).items():
        exp, tol = (want[0], float(want[1])) if isinstance(want, (list, tuple)) else (want, 0.0)
        got = values.get(key)
        ok = isinstance(got, float) and abs(got - float(exp)) <= tol * max(abs(float(exp)), 1e-300)
        if not ok:
            failed.append(f"{key} = {got} (expected {exp}{f' ±{tol:.0e} rel' if tol else ''})")
    for text in inp.get("contains") or []:
        if str(text) not in uart:
            failed.append(f"UART lacks {text!r}")
    if values["hardfault"]:
        failed.append("the firmware faulted (HardFault)")
    head = (f"firmware_sim: flash {b['flash_bytes']} B, RAM {b['ram_static_bytes']} B static, "
            f"{'exited ' + str(values.get('exit_code')) if values['exited'] else 'still running'} "
            f"after {run_s:g} s; UART: {uart.strip()[:160]!r}")
    res = result(values, inp.get("limits"), head, ["uart.txt", "firmware.elf", "firmware.map", "build.log"], notes)
    checks_given = bool(inp.get("expect") or inp.get("contains"))
    if failed:
        res["verdict"] = "FAIL"
        res["summary"] += "; " + "; ".join(failed)
    elif checks_given and res["verdict"] == "UNKNOWN":
        res["verdict"] = "OK"                           # the expected outputs were checked
        res["summary"] += "; expected UART outputs match"
    return res


REFERENCES = [
    {   # integer and float printf through newlib on the emulated UART; exact values
        "name": "printf_sum_root", "kind": "firmware_sim",
        "inp": {"sources": {"main.c": "#include <stdio.h>\n#include <math.h>\nint main(void){int s=0;"
                            "for(int i=1;i<=100;i++)s+=i;printf(\"sum=%d\\nroot=%.7f\\n\",s,(double)sqrtf(2.0f));return 0;}\n"},
                "expect": {"sum": 5050, "root": [1.4142135, 2e-7]}},
        "expect": {"sum": [5050, 0], "root": [1.41421356, 1e-6], "exit_code": [0, 0], "hardfault": [0, 0]},
    },
    {   # discrete first-order filter y += a (1 - y), 100 steps: y = 1 - (1 - a)^100 (single precision)
        "name": "filter_step", "kind": "firmware_sim",
        "inp": {"sources": {"main.c": "#include <stdio.h>\nint main(void){float y=0.f;const float a=A;"
                            "for(int n=0;n<100;n++)y+=a*(1.f-y);printf(\"y100=%.6f\\n\",(double)y);return 3;}\n"},
                "defines": {"A": "0.05f"}},
        "expect": {"y100": [1 - 0.95 ** 100, 1e-5], "exit_code": [3, 0]},
    },
    {   # a trap instruction ends in the fault handler: detected, verdict FAIL
        "name": "hardfault_detected", "kind": "firmware_sim",
        "inp": {"sources": {"main.c": "int main(void){__builtin_trap();return 0;}\n"}},
        "expect": {"hardfault": [1, 0], "exited": [0, 0]},
    },
]
