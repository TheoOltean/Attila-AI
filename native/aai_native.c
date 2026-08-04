/* aai_native.c -- Attila-AI in-process native code (loaded from Lua via
 * package.loadlib). Two exports:
 *
 *   luaopen_aai(L)   -- loadlib SPIKE (roadmap #2, DONE): proof-of-life only,
 *                       never touches the Lua stack.
 *   aai_probe_unit(L)-- STRUCT READS (roadmap #3): given ONE unit userdata on
 *                       the Lua stack, unwrap it with the engine's own
 *                       lua_touserdata, walk wrapper+0x04 to the engine unit
 *                       object, and dump the Ghidra-mapped combat-stat offsets
 *                       (ghidra/findings/unit_field_map.md). This CONFIRMS the
 *                       static offset map against live values: the DLL-read
 *                       0x44 must equal Lua unit:number_of_men_alive(), 0x20cc
 *                       must equal unit:ammo_left(). Confirming a read confirms
 *                       the matching write (same offset, store vs load).
 *
 * All output goes over the file channel to data/, same dir the Lua side logs to.
 */
#include <windows.h>
#include <stdio.h>
#include <string.h>

/* ---- engine addresses (empire.retail.dll, image base 0x10000000) ----------
 * Recovered statically in Ghidra. lua_touserdata (FUN_112bf1d0) is textbook
 * Lua 5.1: index2adr(L,idx); tt==2 -> lightuserdata value; tt==7 -> full
 * userdata payload (value + sizeof(Udata)=0x18); else NULL. */
#define IMAGE_BASE          0x10000000u
#define RVA_LUA_TOUSERDATA  (0x112bf1d0u - IMAGE_BASE)   /* void* (L,int)  */
#define RVA_LUA_GETTOP      (0x112be4d0u - IMAGE_BASE)   /* int   (L)      */

typedef void *(__cdecl *lua_touserdata_fn)(void *L, int idx);
typedef int   (__cdecl *lua_gettop_fn)(void *L);
typedef double(__cdecl *lua_tonumber_fn)(void *L, int idx);
#define RVA_LUA_TONUMBER    (0x112bf120u - IMAGE_BASE)   /* double (L,int)   */
/* BATTLE_OVERRIDE_GAME_SPEED CVar object global @0x11e5b090; float value @+0x48.
 * Ghidra: registrar FUN_100374e0 does MOV ECX,0x11e5b090; FUN_100bee00 stores
 * default at param_1[0x12]=+0x48. -1.0 = engine default; any other value forces
 * battle speed (0.0=frozen, <1 slow-mo, >1 fast). */
#define RVA_CVAR_GAME_SPEED (0x11e5b0d8u - IMAGE_BASE)

/* wrapper userdata payload -> engine unit object is at payload+0x04 (every
 * getter does *(this+4) before reading a field). */
#define WRAP_UNIT_OFF 0x04

static FILE *probe_out(void) { return fopen("data/aai_native_probe.txt", "a"); }

/* guarded scalar reads: a wrong offset can't fault the game (IsBadReadPtr) */
static int rd_i32(const void *base, unsigned off, int *out) {
    const void *p = (const char *)base + off;
    if (IsBadReadPtr(p, 4)) return 0;
    *out = *(const int *)p; return 1;
}
static int rd_f32(const void *base, unsigned off, float *out) {
    const void *p = (const char *)base + off;
    if (IsBadReadPtr(p, 4)) return 0;
    *out = *(const float *)p; return 1;
}

/* one field: "  0x20cc float ammo            = 12.0000" (or "= <unreadable>") */
static void dump_i(FILE *f, const void *u, unsigned off, const char *tag) {
    int v; if (rd_i32(u, off, &v)) fprintf(f, "  0x%04x int   %-22s = %d\n", off, tag, v);
    else                          fprintf(f, "  0x%04x int   %-22s = <unreadable>\n", off, tag);
}
static void dump_f(FILE *f, const void *u, unsigned off, const char *tag) {
    float v; if (rd_f32(u, off, &v)) fprintf(f, "  0x%04x float %-22s = %.4f\n", off, tag, v);
    else                            fprintf(f, "  0x%04x float %-22s = <unreadable>\n", off, tag);
}

static void proof(const char *tag) {
    FILE *f = fopen("data/aai_native_proof.txt", "a");
    if (f) { fprintf(f, "[native] pid=%lu  %s\n",
                     (unsigned long)GetCurrentProcessId(), tag); fclose(f); }
}

BOOL WINAPI DllMain(HINSTANCE inst, DWORD reason, LPVOID reserved) {
    (void)inst; (void)reserved;
    if (reason == DLL_PROCESS_ATTACH)
        proof("DllMain DLL_PROCESS_ATTACH -- DLL mapped into game process");
    return TRUE;
}

/* loadlib spike (roadmap #2): no Lua-stack contact, return 0 results. */
__declspec(dllexport) int luaopen_aai(void *L) {
    (void)L;
    proof("luaopen_aai invoked -- native export executed on demand");
    return 0;
}

/* dump N dwords at base (guarded) -- reveals pointer/struct layout live */
static void dump_words(FILE *f, const char *tag, const void *b, int n) {
    fprintf(f, "  %-10s [%p]:", tag, b);
    if (!b || IsBadReadPtr(b, (UINT_PTR)n * 4)) { fprintf(f, " <unreadable>\n"); return; }
    for (int i = 0; i < n; i++) fprintf(f, " +%02x=%08x", i * 4, ((const unsigned *)b)[i]);
    fprintf(f, "\n");
}

/* STRUCT READS (roadmap #3). Called from Lua as: pcall(f, unit_userdata).
 * Reads the unit off the Lua stack via the engine's lua_touserdata, walks to
 * the engine object, dumps mapped offsets. Returns 0 (no Lua results pushed). */
__declspec(dllexport) int aai_probe_unit(void *L) {
    FILE *f = probe_out();
    if (!f) return 0;
    fprintf(f, "==== aai_probe_unit  pid=%lu ====\n",
            (unsigned long)GetCurrentProcessId());

    HMODULE base = GetModuleHandleA("empire.retail.dll");
    if (!base) { fprintf(f, "  ERROR: empire.retail.dll not found in process\n"); fclose(f); return 0; }
    lua_touserdata_fn lua_touserdata = (lua_touserdata_fn)((char *)base + RVA_LUA_TOUSERDATA);
    lua_gettop_fn     lua_gettop     = (lua_gettop_fn)    ((char *)base + RVA_LUA_GETTOP);
    fprintf(f, "  module base   = %p  (RVA touserdata=0x%06x)\n", (void *)base, RVA_LUA_TOUSERDATA);
    fprintf(f, "  lua_gettop(L) = %d\n", lua_gettop(L));

    /* Lua stack: unit userdata at index 1. lua_touserdata returns the userdata
     * PAYLOAD (udata+0x18). The payload is a LUA::Pointer<T>: payload[0] holds the
     * C++ object W (what the binding trampoline passes as ECX to a getter). The
     * getters then read *(W+4) to reach the engine stats object P; fields (ammo
     * 0x20cc, men 0x44, ...) are offsets in P. So the chain is payload -> W -> P. */
    void *payload = lua_touserdata(L, 1);
    fprintf(f, "  payload(arg1) = %p\n", payload);
    if (!payload || IsBadReadPtr(payload, 8)) { fprintf(f, "  ERROR: arg1 not readable userdata\n"); fclose(f); return 0; }
    dump_words(f, "payload", payload, 8);
    fprintf(f, "  legacy *(payload+4) = %08x  (old wrong guess)\n", *(unsigned *)((char *)payload + 4));

    void *W = *(void **)payload;                      /* payload[0] = C++ object */
    fprintf(f, "  W = *(payload) = %p\n", W);
    if (!W || IsBadReadPtr(W, 8)) { fprintf(f, "  ERROR: W (payload[0]) not readable\n"); fclose(f); return 0; }
    dump_words(f, "W", W, 8);

    void *P = *(void **)((char *)W + 4);              /* W+4 = engine stats obj  */
    fprintf(f, "  P = *(W+4) = %p\n", P);
    if (!P || IsBadReadPtr(P, 0x2200)) { fprintf(f, "  ERROR: P (unit stats obj) not readable\n"); fclose(f); return 0; }

    fprintf(f, "  -- mapped unit fields at P (compare vs Lua getters) --\n");
    dump_i(f, P, 0x0044, "strength/men");      /* == number_of_men_alive (Lua ref) */
    dump_f(f, P, 0x1cb4, "fatigue[0..1]");
    dump_f(f, P, 0x20cc, "ammo");              /* ROUND() == ammo_left             */
    dump_i(f, P, 0x20d0, "max ammo");
    dump_f(f, P, 0x20d8, "ammo frac[0..1]");
    dump_i(f, P, 0x1f28, "rout-state(7=rout)");
    dump_i(f, P, 0x1b5c, "order-state");
    dump_i(f, P, 0x212c, "rout counter");
    dump_i(f, P, 0x1ac0, "defeated flag");
    dump_i(f, P, 0x0438, "prng seed");
    fprintf(f, "\n");
    fclose(f);
    return 0;
}

/* CONFIG WRITE (roadmap #3 control side). Force battle game-speed by writing the
 * BATTLE_OVERRIDE_GAME_SPEED CVar's value cell directly (no registry lookup).
 * Called from Lua as: aai_set_game_speed(speed_number). -1 restores normal. */
__declspec(dllexport) int aai_set_game_speed(void *L) {
    HMODULE base = GetModuleHandleA("empire.retail.dll");
    if (!base) return 0;
    lua_tonumber_fn p_tonum = (lua_tonumber_fn)((char *)base + RVA_LUA_TONUMBER);
    float speed = (float)p_tonum(L, 1);
    float *cell = (float *)((char *)base + RVA_CVAR_GAME_SPEED);
    float old = 0.0f;
    if (!IsBadReadPtr(cell, 4)) old = *cell;
    if (!IsBadWritePtr(cell, 4)) *cell = speed;
    FILE *f = fopen("data/aai_native_probe.txt", "a");
    if (f) {
        fprintf(f, "[cvar] BATTLE_OVERRIDE_GAME_SPEED %.3f -> %.3f  @%p (base %p)\n",
                old, speed, (void *)cell, (void *)base);
        fclose(f);
    }
    return 0;
}

/* ===== GENERIC FIELD PROVIDER (roadmap #3, the real API) ================
 * aai_read_field(unit, field_id) -> number ; aai_write_field(unit, id, value).
 * One export covers every offset: the id->(offset,type) map is DATA below,
 * mirrored in src/battle/native.lua. Offsets from ghidra/findings/unit_field_map.md.
 * Only men@0x44 is exactly confirmed live; the rest are best-known + guarded. */
enum { AAI_T_I32 = 0, AAI_T_F32 = 1 };
typedef struct { int id; unsigned off; int type; } aai_field_t;
static const aai_field_t AAI_FIELDS[] = {
    { 1, 0x0044, AAI_T_I32 },  /* men / strength (== number_of_men_alive)  CONFIRMED */
    { 2, 0x1cb4, AAI_T_F32 },  /* fatigue [0..1]                          needs-confirm */
    { 3, 0x20cc, AAI_T_F32 },  /* ammo (current)                          needs-confirm */
    { 4, 0x20d0, AAI_T_I32 },  /* ammo max */
    { 5, 0x20d8, AAI_T_F32 },  /* ammo frac [0..1] */
    { 6, 0x1f28, AAI_T_I32 },  /* rout/stance state (7 = routing)         needs-confirm */
    { 7, 0x1b5c, AAI_T_I32 },  /* order-state enum */
    { 8, 0x212c, AAI_T_I32 },  /* rout counter (>=7 -> disband) */
};
#define AAI_NFIELDS ((int)(sizeof(AAI_FIELDS)/sizeof(AAI_FIELDS[0])))
static const aai_field_t *aai_field(int id) {
    for (int i = 0; i < AAI_NFIELDS; i++) if (AAI_FIELDS[i].id == id) return &AAI_FIELDS[i];
    return NULL;
}

/* payload -> W -> P (the confirmed 3-hop chain). Returns P or NULL (all guarded). */
static void *aai_unit_P(void *L, lua_touserdata_fn p_tud) {
    void *payload = p_tud(L, 1);
    if (!payload || IsBadReadPtr(payload, 8)) return NULL;
    void *W = *(void **)payload;
    if (!W || IsBadReadPtr(W, 8)) return NULL;
    void *P = *(void **)((char *)W + WRAP_UNIT_OFF);
    if (!P || IsBadReadPtr(P, 8)) return NULL;
    return P;
}

/* Push one Lua number WITHOUT lua_pushnumber. This build is stock Lua 5.1 with
 * float32 LUA_NUMBER: TValue = 8 bytes { lua_Number value @+0; int tt @+4 },
 * LUA_TNUMBER = 3, and L->top is the TValue* at *(L+8). (Confirmed: lua_gettop
 * computes (top-base)>>3 => sizeof(TValue)==8; lua_type treats USERDATA==7 =>
 * stock type tags.) Write the value + tag at top, bump top by one TValue. */
#define LUA_TNUMBER_TAG 3
static int aai_push_number(void *L, float n) {
    char **ptop = (char **)((char *)L + 8);
    char *top = *ptop;
    if (!top || IsBadWritePtr(top, 8)) return 0;
    *(float *)(top + 0) = n;
    *(int   *)(top + 4) = LUA_TNUMBER_TAG;
    *ptop = top + 8;
    return 1;   /* one result on the Lua stack */
}

/* aai_read_field(unit_userdata, field_id) -> number  (nil on any failure). */
__declspec(dllexport) int aai_read_field(void *L) {
    HMODULE base = GetModuleHandleA("empire.retail.dll");
    if (!base) return 0;
    lua_touserdata_fn p_tud = (lua_touserdata_fn)((char *)base + RVA_LUA_TOUSERDATA);
    lua_tonumber_fn   p_tn  = (lua_tonumber_fn)  ((char *)base + RVA_LUA_TONUMBER);
    void *P = aai_unit_P(L, p_tud);
    if (!P) return 0;
    const aai_field_t *fd = aai_field((int)p_tn(L, 2));
    if (!fd) return 0;
    const void *fp = (const char *)P + fd->off;
    if (IsBadReadPtr(fp, 4)) return 0;
    float num = (fd->type == AAI_T_F32) ? *(const float *)fp : (float)*(const int *)fp;
    return aai_push_number(L, num);
}

/* aai_write_field(unit_userdata, field_id, value) -> 1 on success (nil on fail).
 * Guarded store; groundwork for morale/fatigue pokes once offsets are confirmed. */
__declspec(dllexport) int aai_write_field(void *L) {
    HMODULE base = GetModuleHandleA("empire.retail.dll");
    if (!base) return 0;
    lua_touserdata_fn p_tud = (lua_touserdata_fn)((char *)base + RVA_LUA_TOUSERDATA);
    lua_tonumber_fn   p_tn  = (lua_tonumber_fn)  ((char *)base + RVA_LUA_TONUMBER);
    void *P = aai_unit_P(L, p_tud);
    if (!P) return 0;
    const aai_field_t *fd = aai_field((int)p_tn(L, 2));
    if (!fd) return 0;
    double val = p_tn(L, 3);
    void *fp = (char *)P + fd->off;
    if (IsBadWritePtr(fp, 4)) return 0;
    if (fd->type == AAI_T_F32) *(float *)fp = (float)val;
    else                       *(int   *)fp = (int)val;
    return aai_push_number(L, 1.0f);
}

/* ===== ATTACH PROBE (custom-battle door, rung 3) =======================
 * READ-ONLY. No engine calls, no writes -- every access is a guarded load.
 *
 * What it tests: the battle script-attach gate found statically 2026-08-04
 * (reference/CUSTOM_BATTLES.md). In the BATTLE_ENV ctor, if byte B+0x64340==0
 * AND the std::string at B+0x64128 is non-empty, the engine builds a script
 * interface and stores it at B+0x6440c; the chunk is then loaded on the first
 * tick. A scenario battle has that string set (from <battle_script>); a custom
 * battle is believed to leave it empty. Confirming exactly that is the point.
 *
 * TIMING (important): the bootstrap battle world -- where this probe runs --
 * is created and executed INSIDE the BATTLE_ENV ctor (EmpireLuaEnv load-and-run
 * at 0x102c92de), which is AFTER the setup-info copy (0x102c8f8c) but BEFORE
 * the gate (0x102ca25e). So at probe time:
 *     B+0x64128  ALREADY populated  <- the discriminator we want
 *     B+0x6440c  still NULL         (gate has not run) -- expected, not a failure
 *     singleton 0x11e63058 still 0  (published by the ctor we are inside of)
 * Hence we cannot start from the singleton: we locate B by IDENTITY instead.
 *
 * Identity walk (two independent confirmations, so a false positive is
 * essentially impossible):
 *   1. scan committed private RW memory for a dword == the BATTLE_ENV vtable,
 *      which sits at offset 0 of the object -> candidate B;
 *   2. accept only if H = *(B+0x64408) is an EmpireLuaEnv (its own vtable
 *      matches) AND the std::string at H+0x18 is the bootstrap script path.
 *
 * std::string here is {size@+0, capacity@+4, char*@+8} (from the default ctor
 * 0x100db490 and c_str 0x100cfc20) -- NOT the usual MSVC SSO layout. */
#define VA_BATTLE_ENV_VTABLE   0x11adab20u   /* stamped at 0x102c8e73        */
#define VA_EMPIRELUAENV_VTABLE 0x11b64758u   /* stamped by ctor 0x10d5c160   */
#define VA_SCRIPT_IFACE_SINGLE 0x11e63058u   /* published at 0x101af61b      */
#define VA_EMPIRELUAENV_LIST   0x11cce44cu   /* registry container           */
#define VA_STR_EMPTY_LIT       0x11e52f86u   /* what the default str ctor stores */

#define OFF_B_CTOR_TID    0x34u      /* GetCurrentThreadId(), ctor line 91   */
#define OFF_B_SETUPINFO   0x640e4u   /* BATTLE_SETUP_INFO copy (0x1d8 bytes) */
#define OFF_B_SCRIPTPATH  0x64128u   /* = setupinfo+0x44, the gate's string  */
#define OFF_B_GUARD       0x64340u   /* gate's guard byte (must be 0)        */
#define OFF_B_IFACE       0x6440cu   /* script-interface slot                */
#define OFF_B_BOOTENV     0x64408u   /* EmpireLuaEnv* (bootstrap world)      */
#define OFF_H_PATH        0x18u      /* EmpireLuaEnv's script path string    */
#define OFF_I_BATTLE      0x324u     /* iface -> BATTLE*                     */
#define OFF_I_PATH        0x328u     /* iface's copy of the path             */
#define OFF_I_LOADED      0x334u     /* one-shot "chunk loaded" byte         */
#define SETUPINFO_LEN     0x1d8u

#define BOOTSTRAP_PATH "data/lua_scripts/battle_scripted.lua"
/* Budget covers the whole ~4 GB user space of this LARGE_ADDRESS_AWARE process:
 * an RPM-chunked dword scan runs at GB/s, so a full sweep costs a fraction of a
 * second on a loading screen -- far cheaper than a false "not found". Hitting
 * the cap sets `truncated`, which downgrades a negative result to INCONCLUSIVE
 * rather than letting it read as "the model is wrong".  SCAN_MAX_HITS bounds
 * LOGGING only; it never stops the scan. */
#define SCAN_MAX_BYTES  (3072ull * 1024ull * 1024ull)
#define SCAN_MAX_HITS   64

/* EVERY read below goes through ReadProcessMemory on our OWN process. That is
 * deliberate and load-bearing:
 *  - a raw dereference of scan-derived memory can fault (a VirtualQuery result
 *    is stale the instant it returns; another thread can decommit mid-scan) and
 *    MinGW has no __try, so the fault would be an unhandled AV on the game's
 *    main thread. RPM returns FALSE instead of raising.
 *  - IsBadReadPtr must NOT be used here: it probes by touching the page, so
 *    hitting a thread stack's PAGE_GUARD page consumes the guard and clears it,
 *    breaking that stack's auto-growth and crashing the game minutes later in
 *    unrelated code. It is also a check-then-use race.
 * RPM validates and copies in one kernel call, which fixes both. */
static int rpm(const void *addr, void *buf, unsigned n) {
    SIZE_T got = 0;
    if (!addr || !n) return 0;
    if (!ReadProcessMemory(GetCurrentProcess(), addr, buf, n, &got)) return 0;
    return got == n;
}
static int rd_u32(const void *p, unsigned *out) { return rpm(p, out, 4); }
static int rd_u8(const void *p, unsigned char *out) { return rpm(p, out, 1); }

/* Read a {size,cap,char*} std::string. Returns 1 only when the object really
 * looks like a string; `out` always ends NUL-terminated and sanitised, and
 * *len_out is set ONLY on success so a caller can never print a garbage length.
 * raw_out (optional) receives the three raw dwords for the log. */
static int rd_stdstr(const void *s, char *out, unsigned outsz,
                     unsigned *len_out, unsigned raw_out[3]) {
    unsigned hdr[3], len, cap, ptr, i, n;
    unsigned char tmp[512];
    if (!out || outsz == 0) return 0;
    out[0] = '\0';
    if (!rpm(s, hdr, sizeof(hdr))) return 0;
    len = hdr[0]; cap = hdr[1]; ptr = hdr[2];
    if (raw_out) { raw_out[0] = len; raw_out[1] = cap; raw_out[2] = ptr; }
    /* shape check before believing ANY verdict, including "empty" */
    if (len > (1u << 20) || cap > (1u << 20) || cap < len) return 0;
    if (len == 0) { if (len_out) *len_out = 0; return 1; }   /* empty is valid */
    n = (len < outsz - 1) ? len : outsz - 1;
    if (n > sizeof(tmp)) n = sizeof(tmp);
    if (!rpm((const void *)(UINT_PTR)ptr, tmp, n)) return 0;
    for (i = 0; i < n; i++)
        out[i] = (tmp[i] >= 0x20 && tmp[i] < 0x7f) ? (char)tmp[i] : '?';
    out[n] = '\0';
    if (len_out) *len_out = len;
    return 1;
}

/* Candidate identity. CRITICAL TIMING: the bootstrap Lua (this probe) runs from
 * within FUN_10d5c160 at ctor line 203, and the ctor only assigns +0x64408 at
 * line 205 and zeroes +0x6440c at line 206. So BOTH of those fields are still
 * uninitialised garbage right now and CANNOT be used to identify the object.
 * The usable anchor is +0x34, which the ctor sets to GetCurrentThreadId() at
 * line 91 -- long before us, and equal to the thread we are running on. */
static int battle_env_ok(const void *B, unsigned this_tid) {
    unsigned tid = 0;
    if (!rd_u32((const char *)B + OFF_B_CTOR_TID, &tid)) return 0;
    return tid == this_tid;
}

static void dump_bytes(FILE *f, const char *tag, const void *p, unsigned n) {
    unsigned char buf[SETUPINFO_LEN];
    unsigned i;
    fprintf(f, "  %s [%p] %u bytes:\n", tag, p, n);
    if (n > sizeof(buf)) n = sizeof(buf);
    if (!rpm(p, buf, n)) { fprintf(f, "    <unreadable>\n"); return; }
    for (i = 0; i < n; i += 16) {
        unsigned j;
        fprintf(f, "    +%04x ", i);
        for (j = 0; j < 16 && i + j < n; j++) fprintf(f, "%02x ", buf[i + j]);
        fprintf(f, "\n");
    }
}

/* Upper bound of this thread's stack. GetCurrentThreadStackLimits is Win8+, so
 * it is resolved dynamically; the fallback derives the bound from the committed
 * region containing a local. */
static const void *stack_top(void) {
    typedef void (WINAPI *pfn_limits)(ULONG_PTR *, ULONG_PTR *);
    pfn_limits p = (pfn_limits)(void *)GetProcAddress(
        GetModuleHandleA("kernel32.dll"), "GetCurrentThreadStackLimits");
    if (p) {
        ULONG_PTR lo = 0, hi = 0;
        p(&lo, &hi);
        if (hi) return (const void *)hi;
    }
    {
        MEMORY_BASIC_INFORMATION mbi;
        int here;
        if (VirtualQuery(&here, &mbi, sizeof(mbi)) == sizeof(mbi))
            return (const char *)mbi.BaseAddress + mbi.RegionSize;
    }
    return NULL;
}

/* Scan OUR OWN thread stack for a spilled pointer to a live BATTLE_ENV.
 *
 * Why this beats the address-space scan: the BATTLE_ENV ctor is on our call
 * stack right now -- it called the bootstrap Lua that called us -- and it spills
 * its `this` repeatedly into its frame. A stack dword pointing at an object that
 * passes the identity test is therefore THE ctor currently running. A stale
 * BATTLE_ENV from an earlier battle keeps the same vtable and the same ctor
 * thread id, so the heap scan cannot tell it apart; a stale object is not
 * referenced by our live frames, so this can. Scans upward from a local (the
 * ctor's frame is at HIGHER addresses, the stack grows down). */
static unsigned stack_scan(unsigned vt_battle, unsigned this_tid,
                           const void **first, FILE *f) {
    const void *hi = stack_top();
    int anchor;
    const unsigned *p = (const unsigned *)(((UINT_PTR)&anchor) & ~3u);
    unsigned found = 0, i;
    const void *seen[8];
    if (!hi || (const void *)p >= hi) return 0;
    for (; (const void *)p < hi; p++) {
        unsigned v, vt = 0, tid = 0;
        if (!rpm(p, &v, 4)) break;           /* off the end of committed stack */
        if (v < 0x10000u) continue;          /* not a plausible pointer */
        if (!rd_u32((const void *)(UINT_PTR)v, &vt) || vt != vt_battle) continue;
        if (!rd_u32((const char *)(UINT_PTR)v + OFF_B_CTOR_TID, &tid) ||
            tid != this_tid) continue;
        for (i = 0; i < found && i < 8; i++)
            if (seen[i] == (const void *)(UINT_PTR)v) break;
        if (i < found) continue;             /* same object spilled again */
        if (found < 8) seen[found] = (const void *)(UINT_PTR)v;
        if (!found && first) *first = (const void *)(UINT_PTR)v;
        found++;
        fprintf(f, "  stack-scan: BATTLE_ENV %08x referenced from stack slot %p\n",
                v, (const void *)p);
    }
    return found;
}

/* aai_attach_probe([tag]) -> nothing. Writes data/aai_attach_probe.txt. */
__declspec(dllexport) int aai_attach_probe(void *L) {
    static unsigned char scanbuf[64 * 1024];   /* one-shot probe: not reentrant */
    FILE *f;
    HMODULE base;
    unsigned bias, vt_battle, vt_emplua, single = 0, this_tid;
    unsigned char *addr = NULL;
    MEMORY_BASIC_INFORMATION mbi;
    unsigned long long scanned = 0;
    unsigned hits = 0, confirmed = 0;
    int truncated = 0, single_ok;
    const void *B = NULL, *B_stack = NULL;
    unsigned n_stack = 0;
    char pathbuf[512];

    (void)L;                       /* no Lua stack contact: nothing pushed */
    f = fopen("data/aai_attach_probe.txt", "a");
    if (!f) return 0;
    fprintf(f, "\n==== aai_attach_probe pid=%lu ====\n",
            (unsigned long)GetCurrentProcessId());

    base = GetModuleHandleA("empire.retail.dll");
    if (!base) { fprintf(f, "  ERROR: engine module not found\n"); fclose(f); return 0; }
    bias = (unsigned)(UINT_PTR)base - IMAGE_BASE;      /* ASLR bias */
    vt_battle = VA_BATTLE_ENV_VTABLE + bias;
    vt_emplua = VA_EMPIRELUAENV_VTABLE + bias;
    fprintf(f, "  module base = %p  bias = 0x%08x\n", (void *)base, bias);
    fprintf(f, "  vtable BATTLE_ENV = %08x   EmpireLuaEnv = %08x\n",
            vt_battle, vt_emplua);

    this_tid = GetCurrentThreadId();
    fprintf(f, "  this thread id = %u (the ctor thread, per ctor line 91)\n", this_tid);

    /* The script-interface singleton. Expected 0 here even in a scenario battle
     * (we run inside the very ctor that publishes it). NOTE it is a persistent
     * process-global: a non-zero value may be left over from an EARLIER battle,
     * so it is not per-battle evidence. */
    single_ok = rd_u32((const char *)base + (VA_SCRIPT_IFACE_SINGLE - IMAGE_BASE), &single);
    if (!single_ok) fprintf(f, "  singleton 0x11e63058 = <unreadable>\n");
    else fprintf(f, "  singleton 0x11e63058 = %08x %s\n", single,
                 single ? "(non-zero: may be STALE from an earlier battle)"
                        : "(0 as expected at bootstrap time)");
    {   /* container dump: reference only, the walk does not depend on it */
        const void *c = (const char *)base + (VA_EMPIRELUAENV_LIST - IMAGE_BASE);
        dump_words(f, "envlist", c, 6);
    }

    /* Preferred anchor first: our own thread stack (see stack_scan). */
    n_stack = stack_scan(vt_battle, this_tid, &B_stack, f);
    fprintf(f, "  stack-scan: %u distinct BATTLE_ENV%s referenced from our frames\n",
            n_stack, n_stack == 1 ? "" : "s");

    /* Locate BATTLE by identity. Reads go through RPM into scanbuf, so a page
     * decommitted by another thread mid-scan yields a skipped chunk, never a
     * fault. The whole user address space is walked (this process is
     * LARGE_ADDRESS_AWARE, so the object can live above 2 GB); the walk ends
     * when VirtualQuery fails past the last region. */
    {
        SYSTEM_INFO si;
        UINT_PTR maxaddr;
        GetSystemInfo(&si);
        maxaddr = (UINT_PTR)si.lpMaximumApplicationAddress;

        while (VirtualQuery(addr, &mbi, sizeof(mbi)) == sizeof(mbi)) {
            unsigned char *rbase = (unsigned char *)mbi.BaseAddress;
            unsigned char *next = rbase + mbi.RegionSize;
            int usable = (mbi.State == MEM_COMMIT) && (mbi.Type == MEM_PRIVATE) &&
                         !(mbi.Protect & (PAGE_GUARD | PAGE_NOACCESS |
                                          PAGE_NOCACHE | PAGE_WRITECOMBINE)) &&
                         ((mbi.Protect & (PAGE_READWRITE | PAGE_WRITECOPY |
                                          PAGE_EXECUTE_READWRITE)) != 0);
            if (next <= addr) break;                       /* wrap guard */
            while (usable) {
                SIZE_T off = 0;
                if (scanned >= SCAN_MAX_BYTES) { truncated = 1; break; }
                while (off < mbi.RegionSize) {
                    unsigned chunk = (unsigned)((mbi.RegionSize - off < sizeof(scanbuf))
                                                ? (mbi.RegionSize - off) : sizeof(scanbuf));
                    unsigned i, wn = chunk / 4;
                    const unsigned *w = (const unsigned *)scanbuf;
                    if (scanned >= SCAN_MAX_BYTES) { truncated = 1; break; }
                    if (!rpm(rbase + off, scanbuf, chunk)) { off += chunk; continue; }
                    scanned += chunk;
                    for (i = 0; i < wn; i++) {
                        const void *cand;
                        if (w[i] != vt_battle) continue;
                        cand = rbase + off + (SIZE_T)i * 4;
                        hits++;
                        if (battle_env_ok(cand, this_tid)) {
                            confirmed++;
                            if (!B) B = cand;
                            if (confirmed <= 8)
                                fprintf(f, "  CONFIRMED BATTLE_ENV at %p"
                                           " (vtable + ctor-thread match)\n", cand);
                        } else if (hits <= SCAN_MAX_HITS) {
                            fprintf(f, "  vtable hit at %p -- ctor-thread mismatch"
                                       " (stale/other battle)\n", cand);
                        }
                    }
                    off += chunk;
                }
                break;
            }
            addr = next;
            if ((UINT_PTR)addr >= maxaddr) break;
        }
    }
    fprintf(f, "  scan: %llu MB, %u vtable hits, %u confirmed%s\n",
            (unsigned long long)(scanned / (1024u * 1024u)), hits, confirmed,
            truncated ? "  [TRUNCATED]" : "");

    /* Prefer the stack anchor; note any disagreement loudly. */
    if (n_stack == 1 && B && B_stack != B)
        fprintf(f, "  *** DISAGREEMENT: stack says %p, heap scan first-match %p.\n"
                   "      The installer MUST refuse in this state.\n", B_stack, B);
    if (n_stack == 1) B = B_stack;

    if (!B) {
        fprintf(f, truncated
                ? "  NO BATTLE_ENV FOUND but the scan was TRUNCATED -- INCONCLUSIVE\n"
                : "  NO BATTLE_ENV FOUND (scan complete) -- expected in the frontend\n"
                  "    control; in a battle world it would mean the model is wrong\n");
        fclose(f); return 0;
    }
    if (confirmed > 1)
        fprintf(f, "  NOTE: %u heap candidates (a stale BATTLE_ENV keeps the same\n"
                   "    vtable AND ctor thread id). The stack anchor disambiguates.\n",
                confirmed);

    /* THE MEASUREMENT. Only fields the ctor has already written are meaningful
     * here; everything at/after +0x64408 is still garbage (ctor lines 205-206),
     * so those are printed raw with no interpretation. */
    {
        unsigned len = 0, iface = 0, raw[3] = { 0, 0, 0 };
        unsigned char guard = 0xff;
        int have;
        fprintf(f, "  BATTLE_ENV = %p\n", B);
        have = rd_stdstr((const char *)B + OFF_B_SCRIPTPATH, pathbuf,
                         sizeof(pathbuf), &len, raw);
        fprintf(f, "  B+0x64128 script path : raw{size=%u cap=%u ptr=%08x}\n",
                raw[0], raw[1], raw[2]);
        if (!have)
            fprintf(f, "     -> UNREADABLE / not a plausible std::string:"
                       " NO VERDICT (wrong offset? wrong object?)\n");
        else if (len == 0)
            fprintf(f, "     -> EMPTY  => no attach. THE CUSTOM-BATTLE CASE.\n");
        else
            fprintf(f, "     -> NON-EMPTY \"%s\"  => the engine will attach this.\n",
                    pathbuf);
        if (rd_u8((const char *)B + OFF_B_GUARD, &guard))
            fprintf(f, "  B+0x64340 guard byte  : %u  (the REPLAY-PLAYBACK flag: the\n"
                       "     engine skips attach during a deterministic re-sim, so must we)\n",
                    guard);
        else
            fprintf(f, "  B+0x64340 guard byte  : <unreadable>\n");
        rd_u32((const char *)B + OFF_B_IFACE, &iface);
        fprintf(f, "  B+0x6440c iface slot  : %08x  (UNINITIALISED at this point in\n"
                   "     the ctor -- zeroed at line 206, after us. Raw value, no meaning.)\n",
                iface);
        /* Timing self-check: +0x64408 is assigned at ctor line 205, i.e. AFTER
         * the call that runs us. If it already holds a valid EmpireLuaEnv then
         * we ran later than the model predicts and every "uninitialised" caveat
         * above is wrong -- worth knowing loudly either way. */
        {
            unsigned h = 0, hv = 0;
            int hok = rd_u32((const char *)B + OFF_B_BOOTENV, &h) &&
                      rd_u32((const void *)(UINT_PTR)h, &hv) && hv == vt_emplua;
            if (hok && rd_stdstr((const char *)(UINT_PTR)h + OFF_H_PATH, pathbuf,
                                 sizeof(pathbuf), &len, NULL) &&
                strstr(pathbuf, BOOTSTRAP_PATH) != NULL)
                fprintf(f, "  TIMING: B+0x64408 already a valid EmpireLuaEnv (\"%s\")\n"
                           "     => we ran AFTER ctor line 205: re-read the caveats above.\n",
                        pathbuf);
            else
                fprintf(f, "  TIMING: B+0x64408 = %08x, not yet an EmpireLuaEnv\n"
                           "     => confirms we are pre-line-205, as the model predicts.\n", h);
        }
        /* ---- PRECONDITION CHECKLIST -------------------------------------
         * Exactly what Route A's installer will evaluate before its single
         * write, decided here with ZERO writes. If this prints PROCEED, the
         * only untested line in the real installer is the copy-ctor call. */
        {
            unsigned h2 = 0, hv2 = 0, empty_lit = VA_STR_EMPTY_LIT + bias;
            int p_single = (n_stack == 1) && (confirmed <= 1 || B == B_stack);
            int p_guard  = (guard == 0);
            int p_prist  = have && raw[0] == 0 && raw[1] == 0 && raw[2] == empty_lit;
            int p_timing;
            rd_u32((const char *)B + OFF_B_BOOTENV, &h2);
            rd_u32((const void *)(UINT_PTR)h2, &hv2);
            p_timing = (hv2 != vt_emplua);
            fprintf(f, "\n  ---- INSTALLER PRECONDITIONS (Route A) ----\n");
            fprintf(f, "   P1 engine module resolved            : PASS (bias %08x)\n", bias);
            fprintf(f, "   P2 exactly one BATTLE_ENV, stack-anchored: %s"
                       " (stack=%u heap=%u)\n", p_single ? "PASS" : "FAIL",
                    n_stack, confirmed);
            fprintf(f, "   P3 guard byte == 0 (not replay)      : %s (%u)\n",
                    p_guard ? "PASS" : "FAIL", guard);
            fprintf(f, "   P4 script string PRISTINE {0,0,lit}  : %s"
                       " (want ptr=%08x got %08x)\n", p_prist ? "PASS" : "FAIL",
                    empty_lit, raw[2]);
            fprintf(f, "   P5 pre-line-205 (bootenv not yet set): %s\n",
                    p_timing ? "PASS" : "FAIL");
            fprintf(f, "   P6 VFS pre-flight (loadfile)         : done in Lua, see\n"
                       "      the PREFLIGHT line in this file's world header\n");
            fprintf(f, "   => INSTALLER WOULD: %s\n",
                    (p_single && p_guard && p_prist && p_timing)
                    ? "PROCEED (one copy-ctor call to B+0x64128)"
                    : "REFUSE -- and that refusal is the correct behaviour");
        }

        /* the whole setup-info block: diff scenario vs custom to answer
         * "what else differs besides +0x44" in one shot */
        dump_bytes(f, "B+0x640e4 BATTLE_SETUP_INFO", (const char *)B + OFF_B_SETUPINFO,
                   SETUPINFO_LEN);
    }
    fclose(f);
    return 0;
}

/* ===== ROUTE A INSTALLER: arm the attach string ========================
 * THE ONE WRITE. Everything else in this file's attach work is read-only.
 *
 * What it does: copy a script path into the engine's own std::string at
 * BATTLE+0x64128, using the ENGINE'S OWN copy-constructor, from the bootstrap
 * Lua call site -- which runs at ctor line 203, i.e. BEFORE the attach gate at
 * line 893 on the same thread. The engine then runs its own gate, its own
 * operator new(0x440), its own interface ctor and its own publish, in its own
 * order, at the moment it chose. On the first tick it loads our chunk into a
 * privileged battle Lua thread.
 *
 * Why this is the safe shape: 0x100db350 reads only {size, ptr} from our
 * struct (never cap) and hands off to 0x100d68a0, which allocates len+1 FROM
 * THE ENGINE'S ALLOCATOR and byte-copies. So the engine owns the buffer and
 * frees it with the ordinary std::string destructor at teardown. We pass no
 * pointer the engine keeps, allocate nothing and free nothing. It is a
 * copy-CONSTRUCTOR, so it does not free an existing value -- which is why
 * precondition P4 (destination is the pristine {0,0,&empty_literal}) is not
 * optional: it guarantees there is no allocation to leak. */
#define VA_STR_COPYCTOR 0x100db350u   /* thiscall(dst)(const estr* src), ret 4 */

typedef struct { unsigned size, cap; const char *ptr; } estr;

/* MinGW has no __thiscall. __fastcall passes arg1 in ECX and arg2 in EDX with
 * the remaining args pushed right-to-left and the CALLEE cleaning them -- which
 * is bit-for-bit what MSVC __thiscall expects. The dummy EDX is never read. */
typedef void *(__attribute__((__fastcall__)) *pfn_strcopy)(void *dst, void *edx,
                                                           const estr *src);

/* The engine path of our chunk: pack entry aai\aai_attach.lua is reached as
 * data/aai/aai_attach.lua, the same way the bootstrap shim is loaded. Keep it
 * SHORT: the path normaliser 0x100fb5b0 uses a fixed 2 KB stack buffer with no
 * bounds check, so an overlong path is a stack smash rather than a truncation. */
static const char AAI_CHUNK_PATH[] = "data/aai/aai_attach.lua";

/* aai_attach_arm() -> nothing pushed. Report: data/aai_attach_install.txt.
 * Refuses (writing nothing) unless every precondition passes. */
__declspec(dllexport) int aai_attach_arm(void *L) {
    FILE *f;
    HMODULE base;
    unsigned bias, vt_battle, vt_emplua, this_tid, n_stack;
    unsigned raw[3] = { 0, 0, 0 }, len = 0, h = 0, hv = 0, empty_lit;
    unsigned char guard = 0xff;
    const void *B = NULL;
    char pathbuf[512];
    int have, ok_guard, ok_prist, ok_timing;
    estr src;
    pfn_strcopy copy;
    static const void *armed_for;      /* one-shot per process, keyed on B */

    (void)L;
    f = fopen("data/aai_attach_install.txt", "a");
    if (!f) return 0;
    fprintf(f, "\n==== aai_attach_arm pid=%lu ====\n",
            (unsigned long)GetCurrentProcessId());

    base = GetModuleHandleA("empire.retail.dll");
    if (!base) { fprintf(f, "  REFUSE: engine module not found\n"); fclose(f); return 0; }
    bias      = (unsigned)(UINT_PTR)base - IMAGE_BASE;
    vt_battle = VA_BATTLE_ENV_VTABLE + bias;
    vt_emplua = VA_EMPIRELUAENV_VTABLE + bias;
    empty_lit = VA_STR_EMPTY_LIT + bias;
    this_tid  = GetCurrentThreadId();

    /* P2: the stack anchor ONLY. A stale BATTLE_ENV keeps the same vtable and
     * the same ctor thread id, so the address-space scan cannot exclude one --
     * but a dead object is not referenced by our live frames. Anything other
     * than exactly one is a refusal, never a "pick the first". */
    n_stack = stack_scan(vt_battle, this_tid, &B, f);
    if (n_stack != 1 || !B) {
        fprintf(f, "  REFUSE: stack anchor found %u BATTLE_ENV (need exactly 1)\n",
                n_stack);
        fclose(f); return 0;
    }
    if (armed_for == B) {
        fprintf(f, "  REFUSE: already armed for %p this process\n", B);
        fclose(f); return 0;
    }
    fprintf(f, "  BATTLE_ENV = %p (stack-anchored)  bias=%08x\n", B, bias);

    have     = rd_stdstr((const char *)B + OFF_B_SCRIPTPATH, pathbuf,
                         sizeof(pathbuf), &len, raw);
    ok_prist = have && raw[0] == 0 && raw[1] == 0 && raw[2] == empty_lit;
    ok_guard = rd_u8((const char *)B + OFF_B_GUARD, &guard) && guard == 0;
    rd_u32((const char *)B + OFF_B_BOOTENV, &h);
    rd_u32((const void *)(UINT_PTR)h, &hv);
    ok_timing = (hv != vt_emplua);

    fprintf(f, "  P3 guard==0        : %s (%u)\n", ok_guard ? "PASS" : "FAIL", guard);
    fprintf(f, "  P4 string PRISTINE : %s raw{%u,%u,%08x} want ptr %08x\n",
            ok_prist ? "PASS" : "FAIL", raw[0], raw[1], raw[2], empty_lit);
    fprintf(f, "  P5 pre-line-205    : %s\n", ok_timing ? "PASS" : "FAIL");
    if (!ok_guard || !ok_prist || !ok_timing) {
        fprintf(f, "  REFUSE: precondition failed -- nothing written\n");
        fclose(f); return 0;
    }

    /* THE WRITE: one call into the engine's own std::string copy-ctor. */
    src.size = (unsigned)(sizeof(AAI_CHUNK_PATH) - 1);
    src.cap  = src.size;                  /* ignored; 0x100d68a0 sets cap:=size */
    src.ptr  = AAI_CHUNK_PATH;            /* deep-copied by the engine */
    copy = (pfn_strcopy)(void *)((char *)base + (VA_STR_COPYCTOR - IMAGE_BASE));
    armed_for = B;                        /* set BEFORE the call: never retry */
    fprintf(f, "  calling str_copyctor(%p, \"%s\")\n",
            (const char *)B + OFF_B_SCRIPTPATH, AAI_CHUNK_PATH);
    fflush(f);                            /* survive a fault at the call */
    copy((char *)B + OFF_B_SCRIPTPATH, 0, &src);

    /* read back through RPM -- never a raw deref */
    if (rd_stdstr((const char *)B + OFF_B_SCRIPTPATH, pathbuf, sizeof(pathbuf),
                  &len, raw) && len == src.size &&
        strcmp(pathbuf, AAI_CHUNK_PATH) == 0)
        fprintf(f, "  ARMED: raw{size=%u cap=%u ptr=%08x} \"%s\"\n"
                   "  => the engine's own gate should now attach on this battle\n",
                raw[0], raw[1], raw[2], pathbuf);
    else
        fprintf(f, "  *** READ-BACK FAILED raw{%u,%u,%08x} -- expect no attach\n",
                raw[0], raw[1], raw[2]);
    fclose(f);
    return 0;
}

/* ===== DIFFERENTIAL MAPPER (in-process "gold hunt") =====================
 * Delta-encode the whole unit struct each tick: compare P[0..SNAP_LEN) to the
 * stored previous snapshot (per key) and log only the dwords that CHANGED, with
 * old/new. Lua logs the oracle (routing/moving/ammo/men...) series separately;
 * the offline correlator joins them on (tick,key) and labels offsets by which
 * game event their changes track. Only surfaces LIVE fields -- by construction. */
#define SNAP_LEN     0x2400
#define SNAP_MAX_KEYS 160
static unsigned char snap_prev[SNAP_MAX_KEYS][SNAP_LEN];
static unsigned char snap_have[SNAP_MAX_KEYS];

__declspec(dllexport) int aai_snapshot_reset(void *L) {
    (void)L;
    memset(snap_have, 0, sizeof(snap_have));
    FILE *f = fopen("data/aai_diff_struct.log", "w");   /* truncate for a fresh battle */
    if (f) fclose(f);
    return 0;
}

/* aai_snapshot(unit_userdata, key_int, tick_int) -> logs changed dwords. */
__declspec(dllexport) int aai_snapshot(void *L) {
    HMODULE base = GetModuleHandleA("empire.retail.dll");
    if (!base) return 0;
    lua_touserdata_fn p_tud = (lua_touserdata_fn)((char *)base + RVA_LUA_TOUSERDATA);
    lua_tonumber_fn   p_tn  = (lua_tonumber_fn)  ((char *)base + RVA_LUA_TONUMBER);
    void *payload = p_tud(L, 1);
    if (!payload || IsBadReadPtr(payload, 8)) return 0;
    void *W = *(void **)payload;
    if (!W || IsBadReadPtr(W, 8)) return 0;
    void *P = *(void **)((char *)W + 4);
    if (!P || IsBadReadPtr(P, SNAP_LEN)) return 0;
    int key  = (int)p_tn(L, 2);
    int tick = (int)p_tn(L, 3);
    if (key < 0 || key >= SNAP_MAX_KEYS) return 0;
    unsigned char *cur = (unsigned char *)P;
    if (!snap_have[key]) { memcpy(snap_prev[key], cur, SNAP_LEN); snap_have[key] = 1; return 0; }
    FILE *f = NULL;
    for (unsigned off = 0; off + 4 <= SNAP_LEN; off += 4) {
        unsigned ov = *(unsigned *)(snap_prev[key] + off);
        unsigned nv = *(unsigned *)(cur + off);
        if (ov != nv) {
            if (!f) f = fopen("data/aai_diff_struct.log", "a");
            if (f) fprintf(f, "%d %d 0x%04x %08x %08x\n", tick, key, off, ov, nv);
            *(unsigned *)(snap_prev[key] + off) = nv;
        }
    }
    if (f) fclose(f);
    return 0;
}
