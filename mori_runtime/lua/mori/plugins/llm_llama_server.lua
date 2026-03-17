local protocol = require("mori.core.protocol")

local M = {
    id = "llm:llama-server",
    version = "0.1.0",
}

function M.setup(bus, ctx)
    if not ctx.py_llm then
        error("missing ctx.py_llm (python bridge)")
    end

    bus:on(protocol.events.LLM_STREAM, function(payload)
        payload = type(payload) == "table" and payload or {}
        local messages = payload.messages or {}
        local params = payload.params or {}
        local on_delta = payload.on_delta
        local should_abort = payload.should_abort
        return ctx.py_llm:stream_chat(messages, params, on_delta, should_abort)
    end)
end

return M

