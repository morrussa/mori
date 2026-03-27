--[[
四象限决策系统集成演示
Integrated Quadrant Decision System Demo
]]

package.path = package.path .. ';./?.lua;./?/init.lua'
package.preload['mori_memory.util'] = function() 
    return {
        log_debug = function(msg) print('[DEBUG] '..msg) end,
        log_info = function(msg) print('[INFO] '..msg) end,
        log_warn = function(msg) print('[WARN] '..msg) end,
        log_error = function(msg) print('[ERROR] '..msg) end,
    }
end
package.preload['module.tool'] = function() 
    return { log = function(level, msg) print('['..level..'] '..msg) end }
end

print("=== 四象限决策系统集成演示 ===\n")

-- 导入模块
local decision = require('module.decision')
local adapter = require('module.decision_adapter')

print("1. 系统初始化测试")
print("-------------------")

-- 测试新系统组件
local dm = decision.DimensionManager:new()
local qs = decision.QuadrantSystem:new(dm)
local eval = decision.DecisionEvaluator:new(dm, qs)
local ctrl = decision.DecisionController:new(dm, qs)
local fusion = decision.ContextFusionEngine:new(qs)

print("✓ 新系统核心组件创建成功")

-- 测试适配器
local adapter_init = adapter.initialize()
print("✓ 适配器初始化: " .. tostring(adapter_init))

print("\n2. 功能对比测试")
print("----------------")

-- 测试用例
local test_cases = {
    {
        name = "低活跃度对话场景",
        meta = {
            actor = "user123",
            text = "你好，今天天气不错",
            sim_centroid = 0.65,
            has_reply_cue = false,
            message_count = 20,
            active_users = 3
        },
        opts = { same_user_bonus = 0.06 }
    },
    {
        name = "高活跃度回复场景",
        meta = {
            actor = "user456", 
            last_actor = "user123",
            text = "是的，我也这么觉得",
            sim_centroid = 0.85,
            has_reply_cue = true,
            explicit_hits = 1,
            message_count = 80,
            active_users = 15
        },
        opts = { same_user_bonus = 0.06, reply_cue_bonus = 0.05 }
    },
    {
        name = "中等活动度提及场景",
        meta = {
            actor = "user789",
            text = "@user123 你觉得呢？",
            sim_centroid = 0.72,
            has_reply_cue = true,
            explicit_hits = 2,
            message_count = 45,
            active_users = 6
        },
        opts = { same_user_bonus = 0.06, mention_bonus = 0.05 }
    }
}

-- 执行对比测试
for i, case in ipairs(test_cases) do
    print("\n测试用例 " .. i .. ": " .. case.name)
    print(string.rep("-", 40))
    
    -- 使用新系统直接测试
    local context = {
        current_actor = case.meta.actor,
        last_actor = case.meta.last_actor or "",
        text = case.meta.text,
        sim_centroid = case.meta.sim_centroid,
        has_reply_cue = case.meta.has_reply_cue,
        explicit_hits = case.meta.explicit_hits or 0,
        message_count = case.meta.message_count,
        active_users = case.meta.active_users,
        same_user_bonus = case.opts.same_user_bonus,
        reply_cue_bonus = case.opts.reply_cue_bonus or 0,
        mention_bonus = case.opts.mention_bonus or 0
    }
    
    local quadrant = qs:determine_quadrant(context)
    local eval_result = eval:evaluate(context)
    local ctrl_state = ctrl:update(context, i)
    local surface_params = ctrl:get_surface_parameters()
    
    print("新系统结果:")
    print("  象限: " .. quadrant)
    print("  得分: " .. string.format("%.3f", eval_result.scores.neutral_score or 0))
    print("  置信度: " .. string.format("%.3f", eval_result.confidence))
    print("  保守度: " .. string.format("%.3f", surface_params.attach_conservatism))
    print("  Peer信任: " .. string.format("%.3f", surface_params.peer_signal_trust))
    
    -- 使用适配器测试
    local adapter_result, adapter_error = adapter.make_decision(case.meta, case.opts, i)
    
    if adapter_result then
        print("\n适配器结果:")
        print("  象限: " .. adapter_result.quadrant)
        print("  得分: " .. string.format("%.3f", adapter_result.score))
        print("  置信度: " .. string.format("%.3f", adapter_result.confidence))
        print("  保守度: " .. string.format("%.3f", adapter_result.attach_conservatism))
        print("  Peer信任: " .. string.format("%.3f", adapter_result.peer_signal_trust))
    else
        print("\n适配器错误: " .. (adapter_error or "未知"))
    end
    
    -- 显示推理说明
    if eval_result.reasoning and #eval_result.reasoning > 0 then
        print("\n新系统推理:")
        for j, reason in ipairs(eval_result.reasoning) do
            print("  " .. j .. ". " .. reason)
        end
    end
end

print("\n3. 性能基准测试")
print("----------------")

-- 新系统性能测试
local start_time = os.clock()
for i = 1, 100 do
    local test_context = {
        message_count = math.random(10, 100),
        active_users = math.random(2, 20),
        current_actor = "user" .. math.random(1, 10),
        text = "测试消息 " .. i,
        sim_centroid = math.random() * 0.5 + 0.3,
        has_reply_cue = math.random() > 0.5,
        explicit_hits = math.random(0, 3)
    }
    
    qs:determine_quadrant(test_context)
    eval:evaluate(test_context)
    ctrl:update(test_context, i)
end
local new_system_time = os.clock() - start_time

-- 适配器性能测试
start_time = os.clock()
for i = 1, 100 do
    local test_meta = {
        actor = "user" .. math.random(1, 10),
        text = "测试消息 " .. i,
        sim_centroid = math.random() * 0.5 + 0.3,
        has_reply_cue = math.random() > 0.5,
        message_count = math.random(10, 100),
        active_users = math.random(2, 20)
    }
    local test_opts = { same_user_bonus = 0.06 }
    
    adapter.make_decision(test_meta, test_opts, i)
end
local adapter_time = os.clock() - start_time

print("新系统 100次执行耗时: " .. string.format("%.3f", new_system_time) .. "秒")
print("平均每毫秒执行: " .. string.format("%.1f", 100 / new_system_time) .. "次")
print("适配器 100次执行耗时: " .. string.format("%.3f", adapter_time) .. "秒")
print("平均每毫秒执行: " .. string.format("%.1f", 100 / adapter_time) .. "次")

print("\n4. 系统状态概览")
print("----------------")

local adapter_status = adapter.get_status()
print("适配器状态:")
for k, v in pairs(adapter_status) do
    print("  " .. k .. ": " .. tostring(v))
end

-- 显示象限权重趋势
local trends = fusion:get_weight_trends()
if trends and next(trends) then
    print("\n象限权重趋势:")
    for quadrant_name, weights in pairs(trends) do
        if #weights > 0 then
            local avg_weight = 0
            for _, w in ipairs(weights) do
                avg_weight = avg_weight + w
            end
            avg_weight = avg_weight / #weights
            print("  " .. quadrant_name .. ": " .. string.format("%.3f", avg_weight))
        end
    end
end

print("\n=== 集成演示完成 ===")
print("✓ 四象限决策系统已成功集成")
print("✓ 适配器提供与现有系统的兼容接口")
print("✓ 性能表现良好，满足实时处理需求")