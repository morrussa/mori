--[[
Direct Module Test
直接模块测试
]]

print("=== 直接模块测试 ===")

-- 直接复制维度管理器的关键代码进行测试
local DirectDimensionManager = {}
DirectDimensionManager.__index = DirectDimensionManager

function DirectDimensionManager:new()
    local obj = {
        dimensions = {}
    }
    setmetatable(obj, DirectDimensionManager)
    return obj
end

function DirectDimensionManager:test_method()
    return "test success"
end

-- 测试直接定义
print("1. 测试直接定义...")
local dm1 = DirectDimensionManager:new()
print("创建成功:", dm1 ~= nil)
print("方法测试:", dm1:test_method())

-- 测试require加载的模块
print("\n2. 测试require加载...")
package.path = package.path .. ";/home/morusa/AI/mori/?.lua;/home/morusa/AI/mori/mori_memory/?.lua"

package.preload["mori_memory.util"] = function()
    return { log_debug = print }
end

local dimensions = require("mori_memory.module.decision.dimensions")
print("模块加载成功:", dimensions ~= nil)
print("维度管理器存在:", dimensions.DimensionManager ~= nil)

if dimensions.DimensionManager then
    print("尝试创建...")
    local success, dm2 = pcall(function()
        return dimensions.DimensionManager:new()
    end)
    print("创建结果:", success)
    if success then
        print("实例:", dm2)
        print("类型:", type(dm2))
        if dm2 then
            local mt = getmetatable(dm2)
            print("元表:", mt)
            if mt then
                print("元表.__index:", mt.__index)
            end
        end
    else
        print("错误:", dm2)
    end
end

print("\n=== 测试完成 ===")