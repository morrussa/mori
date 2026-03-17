local protocol = require("mori.core.protocol")

local M = {}

local function safe_tostring(x)
    local ok, s = pcall(tostring, x)
    if ok then
        return s
    end
    return "<tostring_error>"
end

function M.load_all(plugin_names, bus, ctx)
    local loaded = {}
    for _, name in ipairs(plugin_names or {}) do
        local ok, plugin = pcall(require, name)
        if not ok then
            bus:emit(protocol.events.MODULE_ERROR, {
                id = name,
                error = "require_failed: " .. safe_tostring(plugin),
            })
        elseif type(plugin) ~= "table" or type(plugin.setup) ~= "function" then
            bus:emit(protocol.events.MODULE_ERROR, {
                id = name,
                error = "invalid_plugin: must return { setup = function(bus, ctx) ... end }",
            })
        else
            local pid = plugin.id or name
            bus:emit(protocol.events.MODULE_ANNOUNCE, {
                id = pid,
                version = plugin.version or "",
            })
            local ok_setup, err = pcall(plugin.setup, bus, ctx)
            if not ok_setup then
                bus:emit(protocol.events.MODULE_ERROR, {
                    id = pid,
                    error = "setup_failed: " .. safe_tostring(err),
                })
            else
                bus:emit(protocol.events.MODULE_READY, {
                    id = pid,
                })
                loaded[pid] = plugin
            end
        end
    end
    return loaded
end

return M

