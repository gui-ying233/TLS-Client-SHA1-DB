#include <lua.h>
#include <lauxlib.h>
#include <dlfcn.h>

/* Query the TLS binding's own dependency, not an unrelated openssl executable. */
static int version(lua_State *L) {
    const char *path = lua_isnoneornil(L, 1) ? NULL : luaL_checkstring(L, 1);
    void *handle = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    if (!handle) return luaL_error(L, "cannot inspect loaded TLS binding: %s", dlerror());
    const char *(*get_version)(int) = (const char *(*)(int))dlsym(handle, "OpenSSL_version");
    if (!get_version) get_version = (const char *(*)(int))dlsym(handle, "SSLeay_version");
    if (!get_version) { dlclose(handle); return luaL_error(L, "TLS runtime has no version symbol"); }
    lua_pushstring(L, get_version(0));
    dlclose(handle);
    return 1;
}

int luaopen_fpdb_tlsmeta(lua_State *L) {
    lua_pushcfunction(L, version);
    return 1;
}
