# MORI_MEMORY 一致性修复实施计划

## 总体目标
在4周内解决MORI_MEMORY系统的核心状态一致性问题，提升系统可靠性至生产级别标准。

## 阶段划分

### Phase 1: 基础防护层 (Week 1-2)

#### 1.1 锁管理器实现
**文件**: `mori_memory/module/lock_manager.lua`

```lua
local M = {}
local locks = {}
local timeouts = {}

function M.acquire(key, timeout_ms)
    timeout_ms = timeout_ms or 5000
    local start_time = get_current_time_ms()
    
    while locks[key] do
        if get_current_time_ms() - start_time > timeout_ms then
            return false, "acquire_timeout"
        end
        coroutine.yield()
    end
    
    locks[key] = true
    timeouts[key] = start_time + timeout_ms
    return true
end

function M.release(key)
    locks[key] = nil
    timeouts[key] = nil
end

function M.cleanup_expired_locks()
    local now = get_current_time_ms()
    for key, expire_time in pairs(timeouts) do
        if now > expire_time then
            M.release(key)
        end
    end
end

-- 定期清理过期锁
local timer = require("timer")
timer.every(30, M.cleanup_expired_locks)

return M
```

#### 1.2 序列号管理器增强
**文件**: `mori_memory/module/memory/sequence_manager.lua`

```lua
local M = {}
local next_seq = 0
local seq_pool = {}
local POOL_SIZE = 100

function M.init(start_seq)
    next_seq = math.max(1, math.floor(tonumber(start_seq) or 1))
    M.refill_pool()
end

function M.refill_pool()
    for i = 1, POOL_SIZE do
        seq_pool[#seq_pool + 1] = next_seq + i - 1
    end
    next_seq = next_seq + POOL_SIZE
end

function M.get_next()
    if #seq_pool == 0 then
        M.refill_pool()
    end
    return table.remove(seq_pool, 1)
end

function M.peek_next()
    if #seq_pool == 0 then
        M.refill_pool()
    end
    return seq_pool[1]
end

return M
```

#### 1.3 WAL增强实现
**修改文件**: `mori_memory/module/memory/recovery_log.lua`

```lua
-- 添加序列号管理器依赖
local sequence_manager = require("module.memory.sequence_manager")
local lock_manager = require("module.lock_manager")

function M.append(record)
    local lock_acquired, lock_err = lock_manager.acquire("wal_append", 2000)
    if not lock_acquired then
        return nil, "lock_acquire_failed: " .. tostring(lock_err)
    end
    
    local success, result, err = pcall(function()
        record = normalize_record(record)
        
        if record.seq <= 0 then
            record.seq = sequence_manager.get_next()
        end
        
        M.next_seq = math.max(M.next_seq, record.seq)
        
        local f, open_err = io.open(wal_path(), "a")
        if not f then
            return nil, "open_failed: " .. tostring(open_err)
        end
        
        local encoded = util.encode_lua_value(record, 0)
        local ok_write, write_err = f:write(encoded, "\n")
        if not ok_write then
            pcall(function() f:close() end)
            return nil, "write_failed: " .. tostring(write_err)
        end
        
        local close_ok, close_err = f:close()
        if close_ok == nil then
            return nil, "close_failed: " .. tostring(close_err)
        end
        
        return record.seq
    end)
    
    lock_manager.release("wal_append")
    
    if success then
        return result, err
    else
        return nil, "append_operation_failed: " .. tostring(result)
    end
end
```

### Phase 2: 状态协调层 (Week 2-3)

#### 2.1 版本向量管理
**文件**: `mori_memory/module/version_vector.lua`

```lua
local M = {}
local versions = {}

function M.update_module_version(module_name, version_info)
    versions[module_name] = {
        version = version_info.version or 1,
        timestamp = version_info.timestamp or os.time(),
        checksum = version_info.checksum or "",
        size = version_info.size or 0
    }
end

function M.get_module_version(module_name)
    return versions[module_name] or {
        version = 0,
        timestamp = 0,
        checksum = "",
        size = 0
    }
end

function M.validate_consistency()
    local timestamps = {}
    local max_ts = 0
    local min_ts = math.huge
    
    for module_name, info in pairs(versions) do
        timestamps[module_name] = info.timestamp
        max_ts = math.max(max_ts, info.timestamp)
        min_ts = math.min(min_ts, info.timestamp)
    end
    
    -- 时间戳偏差检查
    local timestamp_skew = max_ts - min_ts
    local MAX_SKEW_SECONDS = 30
    
    if timestamp_skew > MAX_SKEW_SECONDS then
        return false, "timestamp_skew_detected", {
            skew_seconds = timestamp_skew,
            max_timestamp = max_ts,
            min_timestamp = min_ts
        }
    end
    
    return true
end

function M.export()
    return util.deep_copy(versions)
end

function M.import(imported_versions)
    versions = util.deep_copy(imported_versions or {})
end

return M
```

#### 2.2 增强的检查点机制
**修改文件**: `mori_memory/module/memory/thread_checkpoint.lua`

```lua
local version_vector = require("module.version_vector")
local util = require("mori_memory.util")

function M.save(state, last_seq, meta)
    meta = type(meta) == "table" and meta or {}
    
    -- 计算状态校验和
    local state_serialized = util.encode_lua_value(state, 0)
    local checksum = util.crc32(state_serialized)
    
    -- 获取各模块版本信息
    local module_versions = version_vector.export()
    
    local payload = {
        version = VERSION,
        last_seq = math.max(0, math.floor(tonumber(last_seq) or 0)),
        saved_turn = math.max(0, math.floor(tonumber(meta.turn) or 0)),
        saved_at = math.max(0, math.floor(tonumber(os.time()) or 0)),
        state = state,
        checksum = checksum,
        module_versions = module_versions,
        schema_version = "1.0"
    }
    
    return persistence.write_atomic(checkpoint_path(), "w", function(f)
        return f:write(util.encode_lua_value(payload, 0))
    end)
end

function M.load()
    local f = io.open(checkpoint_path(), "r")
    if not f then
        return default_state(), "missing_checkpoint"
    end
    
    local raw = f:read("*a")
    f:close()
    
    local parsed, err = util.parse_lua_table_literal(raw or "")
    if type(parsed) ~= "table" then
        return default_state(), err or "invalid_checkpoint"
    end
    
    -- 验证校验和
    local stored_checksum = parsed.checksum
    parsed.checksum = nil  -- 移除校验和字段进行计算
    
    local serialized = util.encode_lua_value(parsed, 0)
    local computed_checksum = util.crc32(serialized)
    
    if stored_checksum ~= computed_checksum then
        return default_state(), "checksum_mismatch"
    end
    
    -- 导入版本向量
    if parsed.module_versions then
        version_vector.import(parsed.module_versions)
    end
    
    return {
        version = math.max(1, math.floor(tonumber(parsed.version) or VERSION)),
        last_seq = math.max(0, math.floor(tonumber(parsed.last_seq) or 0)),
        saved_turn = math.max(0, math.floor(tonumber(parsed.saved_turn) or 0)),
        saved_at = math.max(0, math.floor(tonumber(parsed.saved_at) or 0)),
        state = type(parsed.state) == "table" and parsed.state or {},
    }
end
```

#### 2.3 事务协调器
**文件**: `mori_memory/module/transaction_coordinator.lua`

```lua
local M = {}
local active_transactions = {}

function M.begin(modules)
    local tx_id = generate_uuid()
    local tx = {
        id = tx_id,
        modules = modules,
        prepared_states = {},
        start_time = os.time(),
        status = "preparing"
    }
    
    active_transactions[tx_id] = tx
    return tx_id
end

function M.prepare(tx_id, module_name, prepare_fn)
    local tx = active_transactions[tx_id]
    if not tx or tx.status ~= "preparing" then
        return false, "invalid_transaction_state"
    end
    
    local success, prepared_state = pcall(prepare_fn)
    if not success then
        M.rollback(tx_id)
        return false, "prepare_failed: " .. tostring(prepared_state)
    end
    
    tx.prepared_states[module_name] = prepared_state
    return true
end

function M.commit(tx_id)
    local tx = active_transactions[tx_id]
    if not tx or tx.status ~= "preparing" then
        return false, "invalid_transaction_state"
    end
    
    -- 阶段1: 验证所有模块都已准备
    for _, module in ipairs(tx.modules) do
        if not tx.prepared_states[module] then
            M.rollback(tx_id)
            return false, "module_not_prepared: " .. module
        end
    end
    
    -- 阶段2: 提交所有模块
    tx.status = "committing"
    local committed_modules = {}
    
    for _, module in ipairs(tx.modules) do
        local success, err = pcall(function()
            local module_instance = require("module.memory." .. module)
            return module_instance.commit(tx.prepared_states[module])
        end)
        
        if not success then
            -- 提交失败，需要回滚已提交的模块
            M.rollback_partial(tx_id, committed_modules)
            return false, "commit_failed: " .. tostring(err)
        end
        
        committed_modules[#committed_modules + 1] = module
    end
    
    tx.status = "committed"
    tx.end_time = os.time()
    active_transactions[tx_id] = nil
    
    return true
end

function M.rollback(tx_id)
    local tx = active_transactions[tx_id]
    if not tx then return true end
    
    -- 回滚已提交的模块
    for _, module in ipairs(tx.modules) do
        if tx.prepared_states[module] then
            pcall(function()
                local module_instance = require("module.memory." .. module)
                module_instance.rollback(tx.prepared_states[module])
            end)
        end
    end
    
    active_transactions[tx_id] = nil
    return true
end

function M.rollback_partial(tx_id, committed_modules)
    for _, module in ipairs(committed_modules) do
        pcall(function()
            local module_instance = require("module.memory." .. module)
            module_instance.rollback_to_previous()
        end)
    end
    active_transactions[tx_id] = nil
end

return M
```

### Phase 3: 高级特性层 (Week 3-4)

#### 3.1 增量持久化
**文件**: `mori_memory/module/incremental_persister.lua`

```lua
local M = {}
local dirty_modules = {}

function M.mark_dirty(module_name, change_info)
    dirty_modules[module_name] = {
        change_info = change_info or {},
        timestamp = os.time(),
        size_estimate = change_info.size or 0
    }
end

function M.get_dirty_modules(threshold_seconds)
    threshold_seconds = threshold_seconds or 60
    local now = os.time()
    local result = {}
    
    for module_name, info in pairs(dirty_modules) do
        if now - info.timestamp >= threshold_seconds then
            result[#result + 1] = {
                name = module_name,
                info = info
            }
        end
    end
    
    return result
end

function M.save_incremental(changes)
    local payload = {
        timestamp = os.time(),
        changes = changes,
        base_checkpoint = thread_checkpoint.load().last_seq
    }
    
    local path = runtime_root() .. "/incremental_" .. payload.timestamp .. ".dat"
    return persistence.write_atomic(path, "w", function(f)
        return f:write(util.encode_lua_value(payload, 0))
    end)
end

function M.apply_incremental(incremental_data)
    local base_seq = incremental_data.base_checkpoint
    local current_checkpoint = thread_checkpoint.load().last_seq
    
    if current_checkpoint < base_seq then
        return false, "checkpoint_too_old"
    end
    
    -- 应用增量变化
    for module_name, change_data in pairs(incremental_data.changes) do
        local success, err = pcall(function()
            local module = require("module.memory." .. module_name)
            module.apply_incremental_change(change_data)
        end)
        
        if not success then
            return false, "apply_failed_" .. module_name .. ": " .. tostring(err)
        end
    end
    
    return true
end

return M
```

#### 3.2 智能恢复管理器
**文件**: `mori_memory/module/smart_recovery.lua`

```lua
local M = {}

function M.analyze_recovery_need()
    local checkpoint = thread_checkpoint.load()
    local wal_records = recovery_log.load_after(checkpoint.last_seq)
    
    local analysis = {
        checkpoint_valid = checkpoint.last_seq > 0,
        wal_records_count = #wal_records,
        sequence_gaps = M.find_sequence_gaps(wal_records),
        data_integrity_issues = M.check_integrity(wal_records),
        estimated_recovery_time = M.estimate_recovery_time(#wal_records)
    }
    
    return analysis
end

function M.find_sequence_gaps(records)
    local gaps = {}
    local expected_seq = 1
    
    for _, record in ipairs(records) do
        if record.seq > expected_seq then
            for i = expected_seq, record.seq - 1 do
                gaps[#gaps + 1] = i
            end
        elseif record.seq < expected_seq then
            -- 序列号回退，严重问题
            return nil, "sequence_number_regression"
        end
        expected_seq = record.seq + 1
    end
    
    return gaps
end

function M.check_integrity(records)
    local issues = {}
    
    for i, record in ipairs(records) do
        -- 检查记录完整性
        if not record.seq or not record.turn or not record.kind then
            issues[#issues + 1] = {
                position = i,
                issue = "missing_required_fields",
                record_preview = string.sub(util.encode_lua_value(record, 0), 1, 100)
            }
        end
        
        -- 检查时间顺序
        if i > 1 then
            local prev_record = records[i-1]
            if record.turn < prev_record.turn then
                issues[#issues + 1] = {
                    position = i,
                    issue = "turn_order_violation",
                    current_turn = record.turn,
                    previous_turn = prev_record.turn
                }
            end
        end
    end
    
    return issues
end

function M.perform_recovery(strategy)
    strategy = strategy or "auto"
    
    local analysis = M.analyze_recovery_need()
    
    if strategy == "auto" then
        if #analysis.sequence_gaps == 0 and #analysis.data_integrity_issues == 0 then
            return M.fast_forward_recovery()
        else
            return M.full_recovery()
        end
    elseif strategy == "fast_forward" then
        return M.fast_forward_recovery()
    elseif strategy == "full" then
        return M.full_recovery()
    end
end

function M.fast_forward_recovery()
    local checkpoint = thread_checkpoint.load()
    local wal_records = recovery_log.load_after(checkpoint.last_seq)
    
    local applied_count = 0
    local errors = {}
    
    for _, record in ipairs(wal_records) do
        local success, err = pcall(function()
            return M.apply_wal_record(record)
        end)
        
        if success then
            applied_count = applied_count + 1
        else
            errors[#errors + 1] = {
                seq = record.seq,
                error = tostring(err)
            }
        end
    end
    
    return {
        success = #errors == 0,
        applied_records = applied_count,
        errors = errors,
        recovery_type = "fast_forward"
    }
end

function M.full_recovery()
    -- 实现完整的恢复逻辑
    -- 包括从多个备份源恢复等高级功能
end

return M
```

## 监控和告警系统

### 核心监控指标收集
**文件**: `mori_memory/module/metrics_collector.lua`

```lua
local M = {}
local metrics = {
    consistency_rate = {value = 1.0, samples = 0},
    wal_latency_ms = {value = 0, samples = 0},
    checkpoint_lag = {value = 0, samples = 0},
    memory_efficiency = {value = 0, samples = 0},
    recovery_time_ms = {value = 0, samples = 0},
    sequence_gaps = {value = 0, samples = 0}
}

function M.record_metric(name, value)
    local metric = metrics[name]
    if not metric then return end
    
    metric.samples = metric.samples + 1
    metric.value = (metric.value * (metric.samples - 1) + value) / metric.samples
end

function M.get_metrics()
    local result = {}
    for name, metric in pairs(metrics) do
        result[name] = {
            value = metric.value,
            samples = metric.samples,
            timestamp = os.time()
        }
    end
    return result
end

function M.check_alerts()
    local alerts = {}
    local current_metrics = M.get_metrics()
    
    -- 一致性率告警
    if current_metrics.consistency_rate.value < 0.95 then
        alerts[#alerts + 1] = {
            level = "warning",
            metric = "consistency_rate",
            value = current_metrics.consistency_rate.value,
            threshold = 0.95,
            message = "一致性率低于阈值"
        }
    end
    
    -- 序列号间隙告警
    if current_metrics.sequence_gaps.value > 5 then
        alerts[#alerts + 1] = {
            level = "critical",
            metric = "sequence_gaps",
            value = current_metrics.sequence_gaps.value,
            threshold = 5,
            message = "检测到序列号间隙"
        }
    end
    
    -- 内存使用率告警
    local memory_mb = collectgarbage("count") / 1024
    if memory_mb > 150 then
        alerts[#alerts + 1] = {
            level = "warning",
            metric = "memory_usage",
            value = memory_mb,
            threshold = 150,
            message = "内存使用率过高"
        }
    end
    
    return alerts
end

return M
```

## 实施优先级和时间安排

### Week 1 (基础防护)
- [ ] 实现锁管理器
- [ ] 增强序列号管理
- [ ] 改进WAL写入安全性
- [ ] 基础测试验证

### Week 2 (状态协调)
- [ ] 实现版本向量管理
- [ ] 增强检查点机制
- [ ] 实现事务协调器原型
- [ ] 集成测试

### Week 3 (高级特性)
- [ ] 实现增量持久化
- [ ] 实现智能恢复管理器
- [ ] 完善事务协调器
- [ ] 性能基准测试

### Week 4 (监控完善)
- [ ] 实现监控指标收集
- [ ] 设置告警机制
- [ ] 文档完善
- [ ] 生产环境部署准备

## 风险评估和缓解措施

### 技术风险
1. **性能影响**: 锁机制可能降低吞吐量
   - 缓解: 使用细粒度锁，优化锁竞争

2. **复杂性增加**: 新增组件增加系统复杂性
   - 缓解: 模块化设计，清晰的接口定义

3. **兼容性问题**: 与现有系统集成的风险
   - 缓解: 渐进式部署，向后兼容设计

### 实施建议
1. **分阶段部署**: 每个阶段完成后进行充分测试
2. **监控先行**: 先部署监控系统，再实施改进
3. **文档同步**: 实时更新设计文档和API文档
4. **回滚预案**: 准备快速回滚机制应对意外情况

通过这个系统性的改进计划，MORI_MEMORY系统将达到企业级的可靠性标准。