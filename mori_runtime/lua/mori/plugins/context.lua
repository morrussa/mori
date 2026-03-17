local protocol = require("mori.core.protocol")

local M = {
    id = "context",
    version = "0.1.0",
}

local function trim(s)
    return (tostring(s or ""):gsub("^%s*(.-)%s*$", "%1"))
end

local function iter_sequence(value)
    if value == nil then
        return function()
            return nil
        end
    end
    if type(value) == "table" then
        local i = 0
        return function()
            i = i + 1
            return value[i]
        end
    end
    return function()
        return nil
    end
end

function M.setup(bus, ctx)
    bus:on(protocol.events.CONTEXT_COMPOSE, function(payload)
        payload = type(payload) == "table" and payload or {}
        local turn = tonumber(payload.turn or 0) or 0
        local user_input = trim(payload.user_input)
        local system_prompt = trim(payload.system_prompt)

        local memory_meta = {
            turn = turn,
            user_input = user_input,
            raw_user_input = payload.raw_user_input,
            source = payload.source,
            nickname = payload.nickname,
            user_id = payload.user_id,
            room_id = payload.room_id,
            timeline = payload.timeline,
        }
        if payload.max_selected_turns ~= nil then
            memory_meta.max_selected_turns = tonumber(payload.max_selected_turns) or payload.max_selected_turns
        end
        if payload.query_vec ~= nil then
            memory_meta.query_vec = payload.query_vec
        end

        local blocks = bus:call(protocol.events.MEMORY_COMPILE_CONTEXT, memory_meta) or {}

        local messages = {}
        messages[#messages + 1] = { role = "system", content = system_prompt }

        for block in iter_sequence(blocks) do
            if type(block) == "table" then
                local role = trim(block.role or block["role"])
                local content = trim(block.content or block["content"])
                if role ~= "" and content ~= "" then
                    messages[#messages + 1] = { role = role, content = content }
                end
            end
        end

        messages[#messages + 1] = { role = "user", content = user_input }

        return {
            messages = messages,
            blocks = blocks,
        }
    end)
end

return M
