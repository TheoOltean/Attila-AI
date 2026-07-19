"""aai_mem.py -- Tier-1 external memory access to Total War: Attila.

Reads/writes values in the LIVE game process from the Windows side, with no
in-process code and no decompilation. Discovery of *where* values live is done
in Cheat Engine; the resulting pointer paths get pasted into TARGETS below.

WHY THIS FILE IS SPECIAL vs the rest of the project:
  * It MUST run under WINDOWS Python (uses kernel32/psapi Win32 calls against a
    Windows process). WSL's Linux kernel cannot touch Attila.exe's memory.
    Your viz already runs as Windows pythonw, so this imports cleanly into it.
  * Host Python here is 64-bit but Attila.exe is 32-bit (WOW64). A 64-bit
    process CAN rpm/wpm a 32-bit one, BUT every pointer *inside* the target is
    4 bytes. PTR_SIZE is pinned to 4 accordingly -- do not "fix" it to 8.

PHASE 0 (run this file directly): with Attila running, it finds the process,
locates empire.retail.dll, and reads the 'MZ' header out of it. That alone
proves we can read engine memory. No Cheat Engine needed for this step.

PHASE 1: in Cheat Engine, find a value + pointer-scan a stable path, paste it
into TARGETS, and read_target()/write_target() it. Start with a value Lua ALSO
reports (e.g. faction gold) so you can cross-check the address is right before
chasing values Lua can't see (unit morale/fatigue/ability cooldowns).
"""

import ctypes as C
from ctypes import wintypes as W
import struct
import sys

# ---- target facts (measured) -----------------------------------------------
PROCESS_NAME = "Attila.exe"        # the running process (598KB launcher stub)
ENGINE_MODULE = "empire.retail.dll"  # 32MB Warscape engine + Lua VM = hook target
PTR_SIZE = 4                        # Attila is 32-bit: in-target pointers are 4 bytes

# ---- Win32 plumbing --------------------------------------------------------
k32 = C.WinDLL("kernel32", use_last_error=True)

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ           = 0x0010
PROCESS_VM_WRITE          = 0x0020
PROCESS_VM_OPERATION      = 0x0008
ACCESS_RW = (PROCESS_QUERY_INFORMATION | PROCESS_VM_READ |
             PROCESS_VM_WRITE | PROCESS_VM_OPERATION)

TH32CS_SNAPPROCESS = 0x00000002
TH32CS_SNAPMODULE  = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010  # needed to see a 32-bit target's modules from 64-bit
INVALID_HANDLE_VALUE = C.c_void_p(-1).value


class PROCESSENTRY32(C.Structure):
    _fields_ = [("dwSize", W.DWORD), ("cntUsage", W.DWORD),
                ("th32ProcessID", W.DWORD), ("th32DefaultHeapID", C.POINTER(C.c_ulong)),
                ("th32ModuleID", W.DWORD), ("cntThreads", W.DWORD),
                ("th32ParentProcessID", W.DWORD), ("pcPriClassBase", C.c_long),
                ("dwFlags", W.DWORD), ("szExeFile", C.c_char * 260)]


class MODULEENTRY32(C.Structure):
    _fields_ = [("dwSize", W.DWORD), ("th32ModuleID", W.DWORD),
                ("th32ProcessID", W.DWORD), ("GlblcntUsage", W.DWORD),
                ("ProccntUsage", W.DWORD), ("modBaseAddr", C.POINTER(C.c_byte)),
                ("modBaseSize", W.DWORD), ("hModule", W.HMODULE),
                ("szModule", C.c_char * 256), ("szExePath", C.c_char * 260)]


k32.CreateToolhelp32Snapshot.restype = W.HANDLE
k32.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
k32.OpenProcess.restype = W.HANDLE
k32.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
k32.ReadProcessMemory.argtypes = [W.HANDLE, W.LPCVOID, W.LPVOID, C.c_size_t, C.POINTER(C.c_size_t)]
k32.WriteProcessMemory.argtypes = [W.HANDLE, W.LPVOID, W.LPCVOID, C.c_size_t, C.POINTER(C.c_size_t)]


def find_pid(name=PROCESS_NAME):
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID_HANDLE_VALUE:
        raise OSError("process snapshot failed: %d" % C.get_last_error())
    try:
        e = PROCESSENTRY32(); e.dwSize = C.sizeof(PROCESSENTRY32)
        if not k32.Process32First(snap, C.byref(e)):
            return None
        want = name.lower().encode()
        while True:
            if e.szExeFile.lower() == want:
                return e.th32ProcessID
            if not k32.Process32Next(snap, C.byref(e)):
                return None
    finally:
        k32.CloseHandle(snap)


class Mem:
    """Handle to the live game process. read_*/write_* take absolute addresses."""

    def __init__(self, pid=None, access=ACCESS_RW):
        self.pid = pid or find_pid()
        if not self.pid:
            raise RuntimeError("%s is not running -- start the game first." % PROCESS_NAME)
        self.h = k32.OpenProcess(access, False, self.pid)
        if not self.h:
            err = C.get_last_error()
            hint = "  (err 5 = access denied: run this Python as Administrator)" if err == 5 else ""
            raise OSError("OpenProcess failed: %d%s" % (err, hint))
        self._mod_cache = {}

    def close(self):
        if self.h:
            k32.CloseHandle(self.h); self.h = None

    def __enter__(self): return self
    def __exit__(self, *a): self.close()

    # ---- modules ----
    def module(self, name=ENGINE_MODULE):
        """Return (base_addr, size) of a module in the target. Cached."""
        key = name.lower()
        if key in self._mod_cache:
            return self._mod_cache[key]
        snap = k32.CreateToolhelp32Snapshot(
            TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, self.pid)
        if snap == INVALID_HANDLE_VALUE:
            raise OSError("module snapshot failed: %d" % C.get_last_error())
        try:
            m = MODULEENTRY32(); m.dwSize = C.sizeof(MODULEENTRY32)
            if not k32.Module32First(snap, C.byref(m)):
                raise OSError("Module32First failed: %d" % C.get_last_error())
            want = key.encode()
            while True:
                if m.szModule.lower() == want:
                    base = C.cast(m.modBaseAddr, C.c_void_p).value
                    self._mod_cache[key] = (base, m.modBaseSize)
                    return self._mod_cache[key]
                if not k32.Module32Next(snap, C.byref(m)):
                    raise KeyError("module %r not found in target" % name)
        finally:
            k32.CloseHandle(snap)

    def module_base(self, name=ENGINE_MODULE):
        return self.module(name)[0]

    # ---- raw read/write ----
    def read(self, addr, size):
        buf = (C.c_char * size)()
        got = C.c_size_t(0)
        if not k32.ReadProcessMemory(self.h, W.LPCVOID(addr), buf, size, C.byref(got)):
            raise OSError("RPM @ 0x%X (%d bytes) failed: %d" % (addr, size, C.get_last_error()))
        return bytes(buf[:got.value])

    def write(self, addr, data):
        n = len(data)
        wrote = C.c_size_t(0)
        if not k32.WriteProcessMemory(self.h, W.LPVOID(addr), data, n, C.byref(wrote)):
            raise OSError("WPM @ 0x%X (%d bytes) failed: %d" % (addr, n, C.get_last_error()))
        return wrote.value

    # ---- typed read ----
    def read_u32(self, addr):   return struct.unpack("<I", self.read(addr, 4))[0]
    def read_i32(self, addr):   return struct.unpack("<i", self.read(addr, 4))[0]
    def read_u64(self, addr):   return struct.unpack("<Q", self.read(addr, 8))[0]
    def read_float(self, addr): return struct.unpack("<f", self.read(addr, 4))[0]
    def read_ptr(self, addr):   return struct.unpack("<I", self.read(addr, PTR_SIZE))[0]

    # ---- typed write ----
    def write_i32(self, addr, v):   return self.write(addr, struct.pack("<i", v))
    def write_u32(self, addr, v):   return self.write(addr, struct.pack("<I", v))
    def write_float(self, addr, v): return self.write(addr, struct.pack("<f", v))

    # ---- pointer-path resolution (Cheat Engine semantics) ----
    def resolve(self, offsets, module=ENGINE_MODULE):
        """Resolve a Cheat Engine pointer path to a final absolute address.

        `offsets` is the list CE shows, base first. Interpreted as:
            addr = [module_base + offsets[0]]        # deref the base pointer
            for o in offsets[1:-1]: addr = [addr + o]  # deref each hop
            return addr + offsets[-1]                 # last offset: no deref
        A single-element list [X] returns module_base + X (a static address, no
        deref) -- use that for a value that sits at a fixed engine offset.
        """
        if not offsets:
            raise ValueError("offsets must have at least one element")
        base = self.module_base(module)
        if len(offsets) == 1:
            return base + offsets[0]
        addr = self.read_ptr(base + offsets[0])
        for o in offsets[1:-1]:
            addr = self.read_ptr(addr + o)
        return addr + offsets[-1]


# ---- Phase-1 targets: paste Cheat Engine pointer paths here ----------------
# Each entry: name -> (offsets, type). Offsets are ints (hex ok). type in
# {"i32","u32","float"}. Fill these from CE, then read_target("gold") etc.
# Discovered engine addresses live in targets.py (source of truth; human mirror
# = reference/STRUCTS.md). Imported so read_target/write_target and aai_scan
# keep working via `am.TARGETS`.
try:
    from targets import TARGETS
except ImportError:
    TARGETS = {}


def read_target(mem, name):
    offsets, typ = TARGETS[name]
    addr = mem.resolve(offsets)
    return {"i32": mem.read_i32, "u32": mem.read_u32,
            "float": mem.read_float}[typ](addr)


def write_target(mem, name, value):
    offsets, typ = TARGETS[name]
    addr = mem.resolve(offsets)
    return {"i32": mem.write_i32, "u32": mem.write_u32,
            "float": mem.write_float}[typ](addr, value)


# ---- Phase 0 self-test -----------------------------------------------------
def _phase0():
    print("== aai_mem Phase-0 external access test ==")
    pid = find_pid()
    if not pid:
        print("FAIL: %s not running. Start the game and retry." % PROCESS_NAME)
        return 1
    print("found %s pid=%d" % (PROCESS_NAME, pid))
    with Mem(pid) as mem:
        base, size = mem.module(ENGINE_MODULE)
        print("%s base=0x%08X size=%.1f MB" % (ENGINE_MODULE, base, size / 1e6))
        head = mem.read(base, 2)
        ok = head == b"MZ"
        print("read @ base = %r  (%s DOS 'MZ' header)" % (head, "OK" if ok else "WRONG"))
        # read the PE machine field to prove deeper reads + confirm 32-bit
        e_lfanew = mem.read_u32(base + 0x3C)
        machine = struct.unpack("<H", mem.read(base + e_lfanew + 4, 2))[0]
        print("PE machine = 0x%04X (%s)" % (
            machine, "i386/32-bit" if machine == 0x14C else "??"))
        if TARGETS:
            print("-- targets --")
            for name in TARGETS:
                try:
                    print("  %s = %r" % (name, read_target(mem, name)))
                except Exception as ex:
                    print("  %s ERR %s" % (name, ex))
        print("PASS" if ok else "PARTIAL: base found but header mismatch")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(_phase0())
