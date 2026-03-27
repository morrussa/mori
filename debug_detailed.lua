--[[
Detailed Module Debug Test
详细模块调试测试
]]

package.path = package.path .. ";/home/morusa/AI/mori/?.lua;/home/morusa/AI/mori/mori_memory/?.lua"

-- 模拟必要的依赖模块
package.preload["mori_memory.util"] = function()
    return {
        log_debug = function(msg) print("[DEBUG] " .. msg) end,
        log_info = function(msg) print("[INFO] " .. msg) end,
        log_warn = function(msg) print("[WARN] " .. msg) end,
        log_error = function(msg) print("[ERROR] " .. msg) end,
    }
end

package.preload["module.tool"] = function()
    return {
        log = function(level, msg) print("[" .. level .. "] " .. msg) end,
    }
end

print("=== 详细模块调试测试 ===")

-- 测试维度模块
print("\n--- 维度模块测试 ---")
local dimensions_module = require("mori_memory.module.decision.dimensions")

print("维度模块类型:", type(dimensions_module))
print("维度模块内容:")
for k, v in pairs(dimensions_module) do
    print("  " .. k .. ": " .. type(v))
    if type(v) == "function" then
        print("    是函数")
    elseif type(v) == "table" then
        print("    表内容数量:", #v)
    end
end

print("\n尝试调用 DimensionManager:new():")
if dimensions_module.DimensionManager and dimensions_module.DimensionManager.new then
    local success, result = pcall(function()
        return dimensions_module.DimensionManager:new()
    end)
    if success then
        print("✓ 成功创建维度管理器")
        print("维度管理器类型:", type(result))
        if type(result) == "table" then
            print("维度管理器方法:")
            for k, v in pairs(result) do
                if type(v) == "function" then
                    print("  " .. k .. "()")
                end
            end
        end
    else
        print("✗ 创建失败:", result)
    end
else
    print("✗ DimensionManager 或 new 方法不存在")
    print("DimensionManager:", dimensions_module.DimensionManager)
    print("DimensionManager.new:", dimensions_module.DimensionManager and dimensions_module.DimensionManager.new)
end

-- 测试其他模块
print("\n--- 象限模块测试 ---")
local quadrants_module = require("mori_memory.module.decision.quadrants")
print("象限系统可用:", quadrants_module.QuadrantSystem and "是" or "否")

print("\n--- 评估器模块测试 ---")
local evaluator_module = require("mori_memory.module.decision.evaluator")
print("决策评估器可用:", evaluator_module.DecisionEvaluator and "是" or "否")

print("\n--- 控制器模块测试 ---")
local controller_module = require("mori_memory.module.decision.controller")
print("决策控制器可用:", controller_module.DecisionController and "是" or "否")

print("\n--- 融合模块测试 ---")
local fusion_module = require("mori_memory.module.decision.fusion")
print("上下文融合引擎可用:", fusion_module.ContextFusionEngine and "是" or "否")

print("\n=== 调试测试完成 ===")