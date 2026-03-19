local M = {}

local function trim(text)
    return (tostring(text or ""):gsub("^%s*(.-)%s*$", "%1"))
end

local function strip_partial_marker_suffix(text, marker)
    for n = #marker - 1, 1, -1 do
        if text:sub(-n) == marker:sub(1, n) then
            return text:sub(1, #text - n)
        end
    end
    return text
end

function M.strip_reasoning(text)
    text = tostring(text or "")

    while true do
        local open_idx = string.find(text, "<think>", 1, true)
        if not open_idx then
            break
        end

        local close_idx = string.find(text, "</think>", open_idx + #"<think>", true)
        if not close_idx then
            text = string.sub(text, 1, open_idx - 1)
            break
        end

        text = string.sub(text, 1, open_idx - 1) .. string.sub(text, close_idx + #"</think>")
    end

    text = strip_partial_marker_suffix(text, "<think>")
    text = strip_partial_marker_suffix(text, "</think>")

    if text:sub(-#"[end of text]") == "[end of text]" then
        text = text:sub(1, -#"[end of text]" - 1)
    end

    return text
end

function M.remove_cot(text)
    return trim(M.strip_reasoning(text))
end

return M
