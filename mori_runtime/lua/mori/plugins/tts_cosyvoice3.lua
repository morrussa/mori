local protocol = require("mori.core.protocol")

local M = {
    id = "tts:cosyvoice3",
    version = "0.1.0",
}

function M.setup(bus, ctx)
    if not ctx.py_tts then
        error("missing ctx.py_tts (python bridge)")
    end

    bus:on(protocol.events.TTS_SUBMIT, function(payload)
        payload = type(payload) == "table" and payload or {}
        return ctx.py_tts:submit(payload)
    end)

    bus:on(protocol.events.TTS_DRAIN, function(payload)
        payload = type(payload) == "table" and payload or {}
        return ctx.py_tts:drain(payload)
    end)

    bus:on(protocol.events.TTS_CANCEL_INTENT, function(payload)
        payload = type(payload) == "table" and payload or {}
        local intent_id = payload.intent_id or ""
        return ctx.py_tts:cancel_intent(intent_id)
    end)
end

return M

