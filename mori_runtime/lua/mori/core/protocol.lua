local M = {}

M.events = {
    BUS_ERROR = "bus:error",

    MODULE_ANNOUNCE = "module:announce",
    MODULE_READY = "module:ready",
    MODULE_ERROR = "module:error",

    INPUT_TEXT = "input:text",

    CONTEXT_COMPOSE = "context:compose",

    MEMORY_COMPILE_CONTEXT = "memory:compile_context",
    MEMORY_INGEST_TURN = "memory:ingest_turn",
    MEMORY_SHUTDOWN = "memory:shutdown",

    SPEECH_INTENT_START = "speech:intent:start",
    SPEECH_INTENT_CANCEL = "speech:intent:cancel",
    SPEECH_INTENT_END = "speech:intent:end",

    LLM_STREAM = "llm:stream",

    TTS_SUBMIT = "tts:submit",
    TTS_DRAIN = "tts:drain",
    TTS_CANCEL_INTENT = "tts:cancel_intent",
    TTS_RESULT = "tts:result",

    OUTPUT_SUBTITLE = "output:subtitle",
    OUTPUT_EVENT = "output:event",
    OUTPUT_PRINT = "output:print",
}

return M
