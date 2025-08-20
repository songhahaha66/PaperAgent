# 聊天记录格式 V2.0 - JSON卡片格式

## 概述

聊天记录格式已升级到V2.0，采用JSON卡片格式存储，支持结构化数据展示和实时渲染。

## 新格式特性

### 1. 结构化JSON块
- 支持预解析的JSON块，避免运行时解析
- 每个JSON块包含类型、内容等结构化信息
- 支持多种类型的JSON块（工具调用、代码执行、结果等）

### 2. 消息类型标识
- `text`: 纯文本消息
- `json_card`: 包含JSON块的消息

### 3. 版本控制
- 添加版本标识，便于格式迁移和兼容性处理
- 自动迁移旧格式到新格式

## 数据格式

### 消息结构

```json
{
  "id": "uuid-string",
  "role": "user|assistant|system",
  "content": "消息文本内容",
  "timestamp": "2025-01-01T12:00:00.000000",
  "metadata": {
    "system_type": "brain|code|writing"
  },
  "json_blocks": [
    {
      "type": "call_code_agent",
      "content": "代码执行内容"
    },
    {
      "type": "code_agent_result",
      "content": "执行结果"
    }
  ],
  "message_type": "text|json_card"
}
```

### 完整历史记录结构

```json
{
  "work_id": "work-uuid",
  "session_id": "work-uuid_session",
  "messages": [
    // 消息数组
  ],
  "context": {
    "current_topic": "",
    "generated_files": [],
    "workflow_state": "created"
  },
  "created_at": "2025-01-01T12:00:00.000000",
  "version": "2.0"
}
```

## JSON块类型

### 1. 工具调用类型
- `call_code_agent`: 代码代理调用
- `call_writing_agent`: 写作代理调用
- `call_exec_py`: Python代码执行

### 2. 执行结果类型
- `code_agent_result`: 代码执行结果
- `writing_agent_result`: 写作结果
- `exec_py_result`: Python执行结果

### 3. 系统消息类型
- `tool_call`: 工具调用
- `tool_result`: 工具结果
- `system_message`: 系统消息

## API接口

### 获取聊天记录（前端格式）
```http
GET /api/chat/work/{work_id}/history
```

返回适合前端渲染的格式，包含预解析的JSON块。

### 获取聊天记录（原始格式）
```http
GET /api/chat/work/{work_id}/history/raw
```

返回原始存储格式，包含完整的JSON块数据。

### 获取统计信息
```http
GET /api/chat/work/{work_id}/history/stats
```

返回聊天记录统计信息，包括消息数量、JSON块类型统计等。

## 迁移工具

### 自动迁移
系统会在首次访问时自动迁移旧格式到新格式。

### 手动迁移
使用迁移脚本进行批量迁移：

```bash
# 查看迁移统计
python scripts/migrate_chat_history.py --stats

# 预览迁移（不执行）
python scripts/migrate_chat_history.py --dry-run

# 迁移指定work
python scripts/migrate_chat_history.py --work-id 7dffc718

# 迁移所有works
python scripts/migrate_chat_history.py --all
```

## 前端渲染

### 支持的特性
1. **预解析JSON块**: 直接使用后端解析的JSON块，无需前端解析
2. **类型化渲染**: 根据JSON块类型显示不同的样式和图标
3. **实时更新**: 支持WebSocket实时接收和渲染JSON块
4. **兼容性**: 向后兼容旧格式的消息

### 渲染组件
- `JsonChatRenderer.vue`: 主要的聊天渲染组件
- 支持JSON块的类型化显示
- 支持系统类型标识和头像

## 存储位置

聊天记录存储在：
```
pa_data/workspaces/{work_id}/chat_history.json
```

## 兼容性

### 向后兼容
- 支持读取旧格式的聊天记录
- 自动迁移到新格式
- 前端组件兼容旧格式渲染

### 向前兼容
- 新格式包含版本标识
- 支持未来格式扩展
- 保持API接口稳定性

## 性能优化

### 存储优化
- JSON块预解析，减少运行时解析开销
- 结构化数据，便于查询和统计
- 版本控制，支持增量更新

### 渲染优化
- 预解析JSON块，减少前端解析时间
- 类型化渲染，提高渲染效率
- 流式传输，支持实时更新

## 监控和统计

### 统计信息
- 消息总数统计
- JSON块类型统计
- 格式版本统计
- 迁移状态监控

### 日志记录
- 格式迁移日志
- JSON块处理日志
- 错误和异常日志

## 最佳实践

### 开发建议
1. 使用新的API接口获取前端格式数据
2. 利用JSON块类型进行条件渲染
3. 实现适当的错误处理和回退机制

### 部署建议
1. 在部署前运行迁移脚本
2. 监控迁移过程和结果
3. 备份原始数据

### 维护建议
1. 定期检查格式版本
2. 监控存储空间使用
3. 清理过期的聊天记录
