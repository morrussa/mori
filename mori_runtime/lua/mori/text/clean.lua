local M = {}

function M.remove_cot(text)
    text = tostring(text or "")
    local marker = "</think>"
    local idx = string.find(text, marker, 1, true)
    if idx then
        text = string.sub(text, idx + #marker)
    end
    if text:sub(-#"[end of text]") == "[end of text]" then
        text = text:sub(1, -#"[end of text]" - 1)
    end
    return (text:gsub("^%s*(.-)%s*$", "%1"))
end

return M

