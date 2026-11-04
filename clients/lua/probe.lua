local json = require "dkjson"
local file = assert(io.open(assert(os.getenv("FPDB_LUA_METADATA")), "r"))
local meta = assert(json.decode(file:read("*a"))); file:close()
local endpoint = assert(arg[1])
assert(endpoint:match("^https://"), "HTTPS required")
local disabled = os.getenv("FPDB_TLS_VERIFY") == "false"
local ca = os.getenv("FPDB_LOCAL_CA")
local echo, actual, backend, verified

local function native_path(name)
    -- Lua's all-in-one C loader also resolves ssl.core from ssl.so.
    for _, candidate in ipairs({name, name:match("^[^.]+")}) do
        local filename = candidate:gsub("%.", "/")
        for template in package.cpath:gmatch("[^;]+") do
            local path = template:gsub("%?", filename)
            local handle = io.open(path, "rb")
            if handle then
                handle:close()
                assert(path:sub(1, #meta.tree + 1) == meta.tree .. "/", "TLS module escaped the case-local rocks tree")
                return path
            end
        end
    end
    error("Cannot locate native module " .. name)
end

if meta.package == "luasec" then
    local ssl = require "ssl"
    local https = require "ssl.https"
    local ltn12 = require "ltn12"
    local body = {}
    local options = {url=endpoint, sink=ltn12.sink.table(body),
                     headers={Accept="application/json", Connection="close"}, cafile=ca}
    if disabled then options.verify = "none" end
    local ok, status = https.request(options)
    assert(ok and tonumber(status) == 200, tostring(status))
    echo = assert(json.decode(table.concat(body)))
    actual = ssl._VERSION or https._VERSION
    backend = require("fpdb_tlsmeta")(native_path("ssl.core"))
    -- ssl.https defaults to verify="none" in these releases; do not relabel it as peer verification.
    verified = options.verify ~= "none"
elseif meta.package == "http" then
    local request = require "http.request"
    local tls = require "http.tls"
    local context = tls.new_client_context()
    if ca then
        local cert = assert(io.open(ca, "r"))
        context:getStore():add(require("openssl.x509").new(cert:read("*a")))
        cert:close()
    end
    if disabled then context:setVerify(require("openssl.ssl.context").VERIFY_NONE) end
    local req = assert(request.new_from_uri(endpoint))
    req.ctx = context
    req.follow_redirects = false
    req.headers:upsert("accept", "application/json")
    req.headers:upsert("connection", "close")
    local headers, stream = assert(req:go(25))
    assert(headers:get(":status") == "200", headers:get(":status"))
    local body = assert(stream:get_body_as_string(25))
    stream:shutdown()
    echo = assert(json.decode(body))
    actual = require("http.version").version
    backend = require("fpdb_tlsmeta")(native_path("_openssl"))
    verified = not disabled
else
    local http = require "resty.http"
    assert(debug.getinfo(http.new, "S").source == "@" .. meta.tree .. "/share/lua/" .. meta.abi .. "/resty/http.lua",
           "HTTP module escaped the case-local rocks tree")
    local httpc = http.new()
    httpc:set_timeout(25000)
    local host, port, path = endpoint:match("^https://([^/:]+):?(%d*)(/.*)$")
    assert(host, "Invalid HTTPS endpoint")
    port = tonumber(port) or 443
    local connect_host = host == "echo.fpdb.test" and "127.0.0.1" or host
    -- The legacy API supports the historical library versions and retains native cosocket TLS defaults.
    assert(httpc:connect(connect_host, port))
    assert(httpc:ssl_handshake(nil, host, not disabled))
    local response = assert(httpc:request({method="GET", path=path,
        headers={Host=host .. (port == 443 and "" or ":" .. port), Accept="application/json", Connection="close"}}))
    assert(response.status == 200, response.status)
    echo = assert(json.decode(assert(response:read_body())))
    httpc:close()
    actual = http._VERSION
    backend = require("fpdb_tlsmeta")()
    verified = not disabled
end

assert(actual == (meta.module_expected_version or meta.client_version:gsub("%-%d+$", "")),
       "Loaded HTTP client version differs from requested rock")
local deps = meta.dependencies
if jit then deps.luajit = jit.version end
if meta.source_commit then deps.luajit_commit = meta.source_commit end
print(json.encode({runtime_version=meta.runtime_version, client_version=meta.client_version,
    verify=verified, environment={os=os.getenv("FPDB_LUA_OS"), arch=os.getenv("FPDB_LUA_ARCH"),
    tls_backend={name="OpenSSL",version=backend},dependencies=deps}, echo=echo}))
