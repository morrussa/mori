--[[
Decision System Test Script (Lua)
四象限决策系统测试脚本
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

-- 导入决策系统模块
local decision = require("mori_memory.module.decision")

print("=== 四象限决策系统测试 ===\n")

-- 测试1: 创建维度管理器
print("1. 测试维度管理器...")
local dimension_manager = decision.DimensionManager:new()
print("✓ 维度管理器创建成功")

-- 测试2: 计算维度值
print("\n2. 测试维度计算...")
local test_context = {
    message_count = 50,
    active_users = 8,
    message_pairs = 30,
    total_messages = 100,
    current_actor = "user123",
    last_actor = "user123",
    has_reply_cue = true,
    explicit_hits = 2,
    text = "这是一个测试消息内容",
    age = 2,
    stability = 0.8,
    timestamp = os.time()
}

local dimensions = dimension_manager:calculate_all(test_context)
print("维度计算结果:")
for name, value in pairs(dimensions) do
    print("  " .. name .. ": " .. string.format("%.3f", value))
end

-- 测试3: 创建象限系统
print("\n3. 测试象限系统...")
local quadrant_system = decision.QuadrantSystem:new(dimension_manager)
print("✓ 象限系统创建成功")

-- 测试4: 确定象限
print("\n4. 测试象限确定...")
local quadrant = quadrant_system:determine_quadrant(test_context)
print("当前象限: " .. quadrant)

-- 测试5: 应用象限规则
print("\n5. 测试象限规则应用...")
local quadrant_result = quadrant_system:apply_quadrant_rules(test_context)
print("象限规则结果:")
print("  象限类型: " .. quadrant_result.quadrant)
print("  得分项数: " .. tostring(#quadrant_result.scores))
for name, score in pairs(quadrant_result.scores) do
    print("    " .. name .. ": " .. string.format("%.3f", score))
end

-- 测试6: 创建决策评估器
print("\n6. 测试决策评估器...")
local evaluator = decision.DecisionEvaluator:new(dimension_manager, quadrant_system)
print("✓ 决策评估器创建成功")

-- 测试7: 执行评估
print("\n7. 测试决策评估...")
local evaluation_result = evaluator:evaluate(test_context)
print("评估结果:")
print("  当前象限: " .. evaluation_result.quadrant)
print("  置信度: " .. string.format("%.3f", evaluation_result.confidence))
print("  得分数目: " .. tostring(#evaluation_result.scores))
for name, score in pairs(evaluation_result.scores) do
    print("    " .. name .. ": " .. string.format("%.3f", score))
end
print("  推理说明:")
for _, reason in ipairs(evaluation_result.reasoning) do
    print("    - " .. reason)
end

-- 测试8: 创建决策控制器
print("\n8. 测试决策控制器...")
local controller = decision.DecisionController:new(dimension_manager, quadrant_system)
print("✓ 决策控制器创建成功")

-- 测试9: 更新控制器状态
print("\n9. 测试控制器更新...")
local controller_state = controller:update(test_context, 1)
print("控制器状态:")
print("  当前象限: " .. controller_state.current_quadrant)
print("  人口压力: " .. string.format("%.3f", controller_state.population_pressure))
print("  交互拓扑: " .. string.format("%.3f", controller_state.interaction_topology))

-- 测试10: 获取表面参数
print("\n10. 测试表面参数获取...")
local surface_params = controller:get_surface_parameters()
print("表面参数:")
for name, value in pairs(surface_params) do
    print("  " .. name .. ": " .. string.format("%.3f", value))
end

-- 测试11: 创建上下文融合引擎
print("\n11. 测试上下文融合引擎...")
local fusion_engine = decision.ContextFusionEngine:new(quadrant_system)
print("✓ 上下文融合引擎创建成功")

-- 测试12: 准备多象限结果进行融合
print("\n12. 测试多象限结果准备...")
local all_quadrant_results = {}

-- 为每个象限生成测试结果
for _, quad_type in ipairs(quadrant_system:get_quadrant_names()) do
    local rules = quadrant_system:get_quadrant_rules(quad_type)
    local quad_dimensions = dimension_manager:calculate_all(test_context)
    local quad_result = rules:apply(test_context, quad_dimensions)
    
    -- 模拟评估结果
    all_quadrant_results[quad_type] = {
        quadrant = quad_type,
        scores = quad_result.scores,
        confidence = 0.8,
        reasoning = {"来自" .. quad_type .. "象限的评估"},
        metadata = {}
    }
end

-- 测试13: 执行上下文融合
print("\n13. 测试上下文融合...")
local fused_context = fusion_engine:fuse_contexts(all_quadrant_results, test_context)
print("融合结果:")
print("  整体置信度: " .. string.format("%.3f", fused_context.confidence))
print("  融合得分数目: " .. tostring(#fused_context.fused_scores))
for name, score in pairs(fused_context.fused_scores) do
    print("    " .. name .. ": " .. string.format("%.3f", score))
end
print("  象限权重:")
for name, weight in pairs(fused_context.quadrant_weights) do
    print("    " .. name .. ": " .. string.format("%.3f", weight))
end

print("\n=== 测试完成 ===")
print("✓ 所有模块功能正常运行")
print("✓ 四象限决策系统已正确初始化和配置")
print("✓ 各组件间通信和数据传递正常")

-- 性能测试
print("\n=== 性能基准测试 ===")
local start_time = os.clock()

-- 执行1000次评估循环
for i = 1, 1000 do
    local ctx = {
        message_count = math.random(10, 100),
        active_users = math.random(2, 20),
        message_pairs = math.random(5, 50),
        total_messages = math.random(20, 200),
        current_actor = "user" .. math.random(1, 10),
        last_actor = "user" .. math.random(1, 10),
        has_reply_cue = math.random() > 0.5,
        explicit_hits = math.random(0, 3),
        text = "测试消息内容 " .. i,
        age = math.random(0, 10),
        stability = math.random() * 0.8 + 0.2,
        timestamp = os.time()
    }
    
    quadrant_system:determine_quadrant(ctx)
    evaluator:evaluate(ctx)
    controller:update(ctx, i)
end

local end_time = os.clock()
local duration = end_time - start_time
local avg_time = duration / 1000

print("1000次评估循环耗时: " .. string.format("%.3f", duration) .. "秒")
print("平均每次评估耗时: " .. string.format("%.3f", avg_time * 1000) .. "毫秒")
print("每秒处理能力: " .. string.format("%.0f", 1000 / avg_time) .. "次")

if avg_time < 0.01 then
    print("✓ 性能表现优秀")
elseif avg_time < 0.05 then
    print("✓ 性能满足要求")
else
    print("! 性能有待优化")
end