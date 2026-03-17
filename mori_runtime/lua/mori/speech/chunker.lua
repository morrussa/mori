local M = {}
M.__index = M

local HARD = {
    ["。"] = true,
    ["."] = true,
    ["?"] = true,
    ["？"] = true,
    ["!"] = true,
    ["！"] = true,
    ["…"] = true,
    ["\n"] = true,
    ["\r"] = true,
    ["\t"] = true,
}

local SOFT = {
    [","] = true,
    ["，"] = true,
    ["、"] = true,
    [":"] = true,
    ["："] = true,
    [";"] = true,
    ["；"] = true,
}

local function utf8_iter(s)
    local i = 1
    local n = #s
    return function()
        if i > n then
            return nil
        end
        local c = s:byte(i)
        local len = 1
        if c >= 0xF0 then
            len = 4
        elseif c >= 0xE0 then
            len = 3
        elseif c >= 0xC0 then
            len = 2
        else
            len = 1
        end
        local start_i = i
        local ch = s:sub(i, i + len - 1)
        i = i + len
        return ch, start_i, len
    end
end

local function trim(s)
    return (tostring(s or ""):gsub("^%s*(.-)%s*$", "%1"))
end

function M.new(opts)
    opts = type(opts) == "table" and opts or {}
    local self = setmetatable({}, M)
    self.boost = tonumber(opts.boost or 2) or 2
    self.min_chars = tonumber(opts.min_chars or 12) or 12
    self.max_chars = tonumber(opts.max_chars or 48) or 48
    self._buf = ""
    self._emitted = 0
    return self
end

function M:_cut_once()
    local buf = self._buf
    if buf == "" then
        return nil
    end

    local threshold = self.min_chars
    if self._emitted < self.boost then
        threshold = math.max(4, math.floor(self.min_chars / 3))
    end

    local char_idx = 0
    local last_hard_end = nil
    local last_hard_chars = 0
    local last_soft_end = nil
    local last_soft_chars = 0
    local max_end = nil

    for ch, start_i, len in utf8_iter(buf) do
        char_idx = char_idx + 1
        local byte_end = start_i + len - 1

        if char_idx == self.max_chars then
            max_end = byte_end
        end

        if HARD[ch] then
            last_hard_end = byte_end
            last_hard_chars = char_idx
        elseif SOFT[ch] then
            last_soft_end = byte_end
            last_soft_chars = char_idx
        end

        if char_idx >= self.max_chars then
            break
        end
    end

    local cut_end = nil
    if last_hard_end and last_hard_chars >= threshold then
        cut_end = last_hard_end
    elseif self._emitted < self.boost and last_soft_end and last_soft_chars >= threshold then
        cut_end = last_soft_end
    elseif max_end then
        cut_end = max_end
    end

    if not cut_end then
        return nil
    end

    local segment = trim(buf:sub(1, cut_end))
    self._buf = buf:sub(cut_end + 1)
    if segment ~= "" then
        self._emitted = self._emitted + 1
        return segment
    end
    return nil
end

function M:push(delta)
    delta = tostring(delta or "")
    if delta == "" then
        return {}
    end
    self._buf = self._buf .. delta
    local out = {}
    while true do
        local seg = self:_cut_once()
        if not seg then
            break
        end
        out[#out + 1] = seg
    end
    return out
end

function M:flush()
    local tail = trim(self._buf)
    self._buf = ""
    if tail == "" then
        return {}
    end
    self._emitted = self._emitted + 1
    return { tail }
end

return M

