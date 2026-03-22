local protocol = require("mori.core.protocol")
local json = require("mori.core.json")

local M = {
    id = "outputs:live",
    version = "0.1.0",
}

local function trim(s)
    return (tostring(s or ""):gsub("^%s*(.-)%s*$", "%1"))
end

local function normalize_subtitle_text(text)
    local s = tostring(text or "")
    s = s:gsub("\r\n", "\n")
    s = s:gsub("\r", "\n")
    s = s:gsub("```[%w_-]*\n", "")
    s = s:gsub("```", "")
    s = s:gsub("%*%*(.-)%*%*", "%1")
    s = s:gsub("%*(.-)%*", "%1")
    s = s:gsub("__(.-)__", "%1")
    s = s:gsub("~~(.-)~~", "%1")
    s = s:gsub("^%s*>%s?", "")
    s = s:gsub("\n%s*>%s?", "\n")
    s = s:gsub("\n\n\n+", "\n\n")
    local lines = {}
    for line in s:gmatch("([^\n]*)\n?") do
        if line == "" and #lines > 0 and lines[#lines] == "" then
            -- collapse repeated blank lines
        else
            local cleaned = line:gsub("^%s*[%-%*]%s+", "")
            cleaned = cleaned:gsub("^%s*%d+%.%s+", "")
            cleaned = cleaned:gsub("^%s*#+%s*", "")
            cleaned = cleaned:gsub("^%s*(.-)%s*$", "%1")
            lines[#lines + 1] = cleaned
        end
    end
    s = table.concat(lines, "\n")
    s = s:gsub("\n\n\n+", "\n\n")
    return trim(s)
end

local function write_text(path, text)
    local f = io.open(path, "w")
    if not f then
        return nil, "open_failed"
    end
    f:write(tostring(text or ""))
    f:close()
    return true
end

local function append_line(path, line)
    local f = io.open(path, "a")
    if not f then
        return nil, "open_failed"
    end
    f:write(tostring(line or ""), "\n")
    f:close()
    return true
end

function M.setup(bus, ctx)
    local cfg = ctx.config or {}
    local subtitle_path = trim(cfg.subtitle_path or "")
    local event_log_path = trim(cfg.event_log_path or "")
    local print_to_stdout = cfg.print_to_stdout == true

    if subtitle_path ~= "" then
        pcall(function()
            write_text(subtitle_path, "")
        end)
    end
    if event_log_path ~= "" then
        pcall(function()
            write_text(event_log_path, "")
        end)
    end

    bus:on(protocol.events.OUTPUT_SUBTITLE, function(payload)
        payload = type(payload) == "table" and payload or {}
        local text = normalize_subtitle_text(payload.text or "")
        if subtitle_path ~= "" then
            pcall(function()
                write_text(subtitle_path, text)
            end)
        end
        if print_to_stdout and payload.final then
            io.stdout:write("mori> ", text, "\n")
        end
        return true
    end)

    bus:on(protocol.events.OUTPUT_PRINT, function(payload)
        payload = type(payload) == "table" and payload or {}
        io.stdout:write(tostring(payload.text or ""), "\n")
        return true
    end)

    bus:on(protocol.events.OUTPUT_EVENT, function(payload)
        payload = type(payload) == "table" and payload or {}
        if event_log_path == "" then
            return nil
        end
        local line = json.encode(payload)
        pcall(function()
            append_line(event_log_path, line)
        end)
        return true
    end)
end

return M
