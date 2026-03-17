local protocol = require("mori.core.protocol")

local Bus = {}
Bus.__index = Bus

local function safe_tostring(x)
    local ok, s = pcall(tostring, x)
    if ok then
        return s
    end
    return "<tostring_error>"
end

function Bus.new()
    local self = setmetatable({}, Bus)
    self._handlers = {}
    self._next_id = 1
    return self
end

function Bus:on(event_name, handler)
    if type(event_name) ~= "string" or event_name == "" then
        error("bus:on requires event_name")
    end
    if type(handler) ~= "function" then
        error("bus:on requires handler function")
    end
    local id = self._next_id
    self._next_id = id + 1

    if not self._handlers[event_name] then
        self._handlers[event_name] = {}
    end
    self._handlers[event_name][id] = handler

    return id
end

function Bus:off(event_name, id)
    if type(event_name) ~= "string" or event_name == "" then
        return
    end
    local group = self._handlers[event_name]
    if not group then
        return
    end
    group[id] = nil
end

function Bus:emit(event_name, payload)
    local group = self._handlers[event_name]
    if not group then
        return
    end
    for id, handler in pairs(group) do
        local ok, err = pcall(handler, payload)
        if not ok then
            local onerr = self._handlers[protocol.events.BUS_ERROR]
            if onerr then
                for _, h in pairs(onerr) do
                    pcall(h, {
                        event = event_name,
                        handler_id = id,
                        error = safe_tostring(err),
                    })
                end
            end
        end
    end
end

-- Call handlers in registration order-ish (pairs is undefined), return first non-nil value.
function Bus:call(event_name, payload)
    local group = self._handlers[event_name]
    if not group then
        return nil
    end
    for id, handler in pairs(group) do
        local ok, res = pcall(handler, payload)
        if not ok then
            self:emit(protocol.events.BUS_ERROR, {
                event = event_name,
                handler_id = id,
                error = safe_tostring(res),
            })
        elseif res ~= nil then
            return res
        end
    end
    return nil
end

return Bus

