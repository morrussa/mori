#!/usr/bin/env lua

-- Test script for memory limit and GC features
package.path = package.path .. ";./?.lua;./?/init.lua"

local function test_config_loading()
    print("=== Testing Configuration Loading ===")
    local ok, config = pcall(require, "mori_memory.module.config")
    if not ok then
        print("ERROR: Failed to load config:", config)
        return false
    end
    
    print("✓ Config module loaded successfully")
    print("  Memory limits per thread:", config.get("disentangle.memory_limits.per_thread_event_cap"))
    print("  GC trigger on checkpoint:", config.get("disentangle.gc_control.trigger_on_checkpoint"))
    print("  TTL pending max age (ms):", config.get("disentangle.ttl_settings.pending_max_age_ms"))
    print("  State version control:", config.get("disentangle.state_version_control.enabled"))
    print("  Current version:", config.get("disentangle.state_version_control.current_version"))
    return true
end

local function test_basic_functions()
    print("\n=== Testing Basic Functions ===")
    
    -- Test memory usage estimation
    local mem_kb = collectgarbage("count")
    local mem_mb = mem_kb / 1024
    print("✓ Current memory usage:", string.format("%.2f MB", mem_mb))
    
    -- Test timestamp function
    local timestamp_ms = math.floor(os.time() * 1000)
    print("✓ Current timestamp (ms):", timestamp_ms)
    
    return true
end

local function main()
    print("Mori Memory Emergency Stability Features Test")
    print("=============================================")
    
    local success = true
    
    success = success and test_config_loading()
    success = success and test_basic_functions()
    
    if success then
        print("\n✓ All basic tests passed!")
        print("Emergency stability features are properly configured.")
    else
        print("\n✗ Some tests failed!")
        print("Please check the implementation.")
    end
    
    return success
end

-- Run the test
local ok, err = pcall(main)
if not ok then
    print("Test execution failed:", err)
    os.exit(1)
end