--[[
Minimal Dimension Manager Test
最小化维度管理器测试
]]

package.path = package.path .. ";/home/morusa/AI/mori/?.lua;/home/morusa/AI/mori/mori_memory/?.lua"

-- 模拟必要的依赖模块
package.preload["mori_memory.util"] = function()
    return {
        log_debug = function(msg) print("[DEBUG] " .. msg) end,
    }
end

print("=== 最小化维度管理器测试 ===")

-- 直接从文件加载维度模块
local dimensions_code = io.open("/home/morusa/AI/mori/mori_memory/module/decision/dimensions.lua", "r"):read("*all")
local func, err = load(dimensions_code, "dimensions.lua", "t", {util = {log_debug = print}})

if not func then
    print("加载错误:", err)
    return
end

local dimensions_module = func()

print("维度模块加载成功")
print("维度管理器:", dimensions_module.DimensionManager)
print("维度管理器类型:", type(dimensions_module.DimensionManager))

if dimensions_module.DimensionManager then
    print("维度管理器.new方法:", dimensions_module.DimensionManager.new)
    print("维度管理器方法:")
    for k, v in pairs(dimensions_module.DimensionManager) do
        print("  " .. k .. ": " .. type(v))
    end
    
    -- 尝试创建实例
    print("\n尝试创建维度管理器实例...")
    local dm = dimensions_module.DimensionManager:new()
    print("实例创建结果:", dm)
    print("实例类型:", type(dm))
    
    if dm then
        print("实例方法:")
        local mt = getmetatable(dm)
        print("元表:", mt)
        if mt then
            for k, v in pairs(mt) do
                if type(v) == "function" then
                    print("  " .. k .. "()")
                end
            end
        end
        
        -- 测试方法调用
        print("\n测试方法调用...")
        local success, result = pcall(function() 
            return dm:calculate_all({}) 
        end)
        print("calculate_all调用结果:", success, result)
    end
end

print("\n=== 测试结束 ===")