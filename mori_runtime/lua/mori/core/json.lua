local M = {}

local function is_array_like_table(tbl)
    if type(tbl) ~= "table" then
        return false, 0
    end
    local count = 0
    local max_idx = 0
    for k, _ in pairs(tbl) do
        if type(k) ~= "number" or k < 1 or k % 1 ~= 0 then
            return false, 0
        end
        count = count + 1
        if k > max_idx then
            max_idx = k
        end
    end
    if max_idx ~= count then
        return false, 0
    end
    return true, max_idx
end

local function json_escape_str(s)
    s = tostring(s or "")
    s = s:gsub("\\", "\\\\")
    s = s:gsub('"', '\\"')
    s = s:gsub("\r", "\\r")
    s = s:gsub("\n", "\\n")
    s = s:gsub("\t", "\\t")
    return s
end

local function encode(v, depth)
    depth = tonumber(depth) or 0
    if depth > 32 then
        return "null"
    end

    local vt = type(v)
    if v == nil then
        return "null"
    end
    if vt == "string" then
        return '"' .. json_escape_str(v) .. '"'
    end
    if vt == "number" then
        if v ~= v or v == math.huge or v == -math.huge then
            return "0"
        end
        return tostring(v)
    end
    if vt == "boolean" then
        return v and "true" or "false"
    end
    if vt == "table" then
        local is_arr, n = is_array_like_table(v)
        if is_arr then
            local parts = {}
            for i = 1, n do
                parts[#parts + 1] = encode(v[i], depth + 1)
            end
            return "[" .. table.concat(parts, ",") .. "]"
        end

        local entries = {}
        for k, value in pairs(v) do
            entries[#entries + 1] = { key = tostring(k), value = value }
        end
        table.sort(entries, function(a, b)
            return a.key < b.key
        end)

        local parts = {}
        for _, item in ipairs(entries) do
            parts[#parts + 1] = '"' .. json_escape_str(item.key) .. '":' .. encode(item.value, depth + 1)
        end
        return "{" .. table.concat(parts, ",") .. "}"
    end

    return '"' .. json_escape_str(tostring(v)) .. '"'
end

function M.encode(v)
    return encode(v, 0)
end

return M

