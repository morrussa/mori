local protocol = require("mori.core.protocol")

local M = {
    id = "memory",
    version = "0.1.0",
}

function M.setup(bus, ctx)
    local memory = require("mori_memory")
    ctx.memory = memory

    bus:on(protocol.events.MEMORY_COMPILE_CONTEXT, function(meta)
        return memory.compile_context(meta)
    end)

    bus:on(protocol.events.MEMORY_INGEST_TURN, function(meta)
        return memory.ingest_turn(meta)
    end)

    bus:on(protocol.events.MEMORY_SHUTDOWN, function()
        pcall(function()
            memory.shutdown()
        end)
        return true
    end)
end

return M

