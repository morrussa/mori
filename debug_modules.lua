--[[
Simple Decision System Test
简单的决策系统测试
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

print("=== 测试模块加载 ===")

-- 直接测试各个模块
print("\n1. 测试维度模块加载...")
local dimensions_module = require("mori_memory.module.decision.dimensions")
print("✓ 维度模块加载成功")
print("维度管理器方法:", dimensions_module.new and "存在" or "不存在")

print("\n2. 测试象限模块加载...")
local quadrants_module = require("mori_memory.module.decision.quadrants")
print("✓ 象限模块加载成功")
print("象限系统方法:", quadrants_module.QuadrantSystem and quadrants_module.QuadrantSystem.new and "存在" or "不存在")

print("\n3. 测试评估器模块加载...")
local evaluator_module = require("mori_memory.module.decision.evaluator")
print("✓ 评估器模块加载成功")
print("决策评估器方法:", evaluator_module.DecisionEvaluator and evaluator_module.DecisionEvaluator.new and "存在" or "不存在")

print("\n4. 测试控制器模块加载...")
local controller_module = require("mori_memory.module.decision.controller")
print("✓ 控制器模块加载成功")
print("决策控制器方法:", controller_module.DecisionController and controller_module.DecisionController.new and "存在" or "不存在")

print("\n5. 测试融合模块加载...")
local fusion_module = require("mori_memory.module.decision.fusion")
print("✓ 融合模块加载成功")
print("上下文融合引擎方法:", fusion_module.ContextFusionEngine and fusion_module.ContextFusionEngine.new and "存在" or "不存在")

print("\n6. 测试主模块加载...")
local decision_main = require("mori_memory.module.decision")
print("✓ 主模块加载成功")
print("导出的模块:")
for k, v in pairs(decision_main) do
    print("  " .. k .. ": " .. type(v))
end

print("\n=== 模块加载测试完成 ===")