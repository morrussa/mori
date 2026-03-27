--[[
Working Decision System Test
工作中的决策系统测试
]]

package.path = package.path .. ";/home/morusa/AI/mori/?.lua;/home/morusa/AI/mori/mori_memory/?.lua"

-- 模拟所有必需的依赖模块
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

print("=== 工作中的四象限决策系统测试 ===\n")

-- 测试基本功能
print("1. 测试基本模块导入...")
local success, decision = pcall(require, "mori_memory.module.decision")
if not success then
    print("✗ 模块导入失败:", decision)
    return
end
print("✓ 模块导入成功")

-- 测试维度管理器
print("\n2. 测试维度管理器创建...")
local dimension_manager_success, dimension_manager = pcall(function()
    return decision.DimensionManager:new()
end)

if not dimension_manager_success then
    print("✗ 维度管理器创建失败:", dimension_manager)
    return
end
print("✓ 维度管理器创建成功")

-- 测试维度计算
print("\n3. 测试维度计算...")
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

local calc_success, dimensions = pcall(function()
    return dimension_manager:calculate_all(test_context)
end)

if not calc_success then
    print("✗ 维度计算失败:", dimensions)
    return
end

print("✓ 维度计算成功")
print("计算得到的维度:")
for name, value in pairs(dimensions) do
    print("  " .. name .. ": " .. string.format("%.3f", value))
end

-- 测试象限系统
print("\n4. 测试象限系统...")
local quadrant_success, quadrant_system = pcall(function()
    return decision.QuadrantSystem:new(dimension_manager)
end)

if not quadrant_success then
    print("✗ 象限系统创建失败:", quadrant_system)
    return
end
print("✓ 象限系统创建成功")

-- 测试象限确定
print("\n5. 测试象限确定...")
local determine_success, quadrant = pcall(function()
    return quadrant_system:determine_quadrant(test_context)
end)

if not determine_success then
    print("✗ 象限确定失败:", quadrant)
    return
end
print("✓ 象限确定成功")
print("当前象限:", quadrant)

-- 测试决策评估器
print("\n6. 测试决策评估器...")
local evaluator_success, evaluator = pcall(function()
    return decision.DecisionEvaluator:new(dimension_manager, quadrant_system)
end)

if not evaluator_success then
    print("✗ 决策评估器创建失败:", evaluator)
    return
end
print("✓ 决策评估器创建成功")

-- 测试评估过程
print("\n7. 测试决策评估...")
local evaluate_success, evaluation_result = pcall(function()
    return evaluator:evaluate(test_context)
end)

if not evaluate_success then
    print("✗ 决策评估失败:", evaluation_result)
    return
end
print("✓ 决策评估成功")
print("评估结果:")
print("  象限:", evaluation_result.quadrant)
print("  置信度:", string.format("%.3f", evaluation_result.confidence))
print("  得分:")
for name, score in pairs(evaluation_result.scores) do
    print("    " .. name .. ": " .. string.format("%.3f", score))
end

-- 测试控制器
print("\n8. 测试决策控制器...")
local controller_success, controller = pcall(function()
    return decision.DecisionController:new(dimension_manager, quadrant_system)
end)

if not controller_success then
    print("✗ 决策控制器创建失败:", controller)
    return
end
print("✓ 决策控制器创建成功")

-- 测试控制器更新
print("\n9. 测试控制器更新...")
local update_success, controller_state = pcall(function()
    return controller:update(test_context, 1)
end)

if not update_success then
    print("✗ 控制器更新失败:", controller_state)
    return
end
print("✓ 控制器更新成功")
print("控制器状态:")
print("  当前象限:", controller_state.current_quadrant)
print("  人口压力:", string.format("%.3f", controller_state.population_pressure))
print("  交互拓扑:", string.format("%.3f", controller_state.interaction_topology))

-- 测试融合引擎
print("\n10. 测试上下文融合引擎...")
local fusion_success, fusion_engine = pcall(function()
    return decision.ContextFusionEngine:new(quadrant_system)
end)

if not fusion_success then
    print("✗ 上下文融合引擎创建失败:", fusion_engine)
    return
end
print("✓ 上下文融合引擎创建成功")

print("\n=== 系统完整性测试通过 ===")
print("✓ 所有核心组件都能正常创建和运行")
print("✓ 四象限决策系统基础功能验证完成")

-- 简单性能测试
print("\n=== 简单性能测试 ===")
local start_time = os.clock()

for i = 1, 100 do
    local ctx = {
        message_count = math.random(10, 100),
        active_users = math.random(2, 20),
        message_pairs = math.random(5, 50),
        total_messages = math.random(20, 200),
        current_actor = "user" .. math.random(1, 10),
        last_actor = "user" .. math.random(1, 10),
        has_reply_cue = math.random() > 0.5,
        explicit_hits = math.random(0, 3),
        text = "测试消息 " .. i,
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
local avg_time = duration / 100

print("100次循环耗时:", string.format("%.3f", duration), "秒")
print("平均每次:", string.format("%.3f", avg_time * 1000), "毫秒")
print("处理速度:", string.format("%.0f", 1000 / avg_time), "次/秒")

if avg_time < 0.01 then
    print("✓ 性能表现优秀")
elseif avg_time < 0.05 then
    print("✓ 性能满足要求")
else
    print("! 性能有待优化")
end

print("\n=== 四象限决策系统测试完成 ===")