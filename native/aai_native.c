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
