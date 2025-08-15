# AI系统使用说明

## 概述

AI系统是PaperAgent的核心组件，集成了LiteLLM客户端、工具调用框架和聊天系统，支持多种AI模型的统一接口和工具调用功能。

## 系统架构

```
ai_system/
├── __init__.py              # 模块初始化
├── litellm_client.py        # LiteLLM客户端
├── tool_framework.py        # 工具调用框架
├── tools.py                 # 核心工具实现
|__ chat_system.py           # 聊天系统
```

## 主要组件

### 1. LiteLLM客户端 (litellm_client.py)

- 支持多种AI模型（brain、code、writing）
- 自动从数据库加载模型配置
- 提供同步和异步接口
- 支持工具调用

**使用方法：**
```python
from ai_system.litellm_client import litellm_client

# 异步调用
response = await litellm_client.chat_completion(
    model_type="brain",
    messages=[{"role": "user", "content": "你好"}],
    tools=tools_list
)

# 同步调用
response = litellm_client.chat_completion_sync(
    model_type="brain",
    messages=[{"role": "user", "content": "你好"}]
)
```

### 2. 工具调用框架 (tool_framework.py)

- 工具注册和管理
- 工具执行器
- 支持OpenAI工具格式

**核心类：**
- `Tool`: 工具基类
- `ToolRegistry`: 工具注册表
- `ToolExecutor`: 工具执行器

### 3. 核心工具 (tools.py)

#### 文件修改工具 (FileModifyTool)
- 支持文件的读取、写入、追加、删除操作
- 自动创建工作空间目录结构

#### Python代码执行工具 (PythonCodeExecutionTool)
- 安全执行Python代码
- 支持超时设置
- 捕获标准输出和错误输出

#### 文件列表工具 (FileListTool)
- 列出工作空间中的文件和目录
- 提供文件类型和大小信息

**工具注册：**
```python
from ai_system.tools import register_core_tools

# 注册所有核心工具
register_core_tools()
```

### 4. 聊天系统 (chat_system.py)

- 多系统协作对话（brain、code、writing）
- 上下文管理和维护
- 工具调用集成
- 会话管理

**使用方法：**
```python
from ai_system.chat_system import chat_system

# 处理用户消息
result = await chat_system.process_message(
    session_id="user_work_brain",
    user_message="请帮我分析这个数据",
    work_id="work_123",
    system_type="brain"
)

# 获取聊天历史
history = chat_system.get_session_history("session_id")
```

## API接口

### 聊天相关接口

- `POST /chat/send` - 发送聊天消息
- `GET /chat/history/{session_id}` - 获取聊天历史
- `DELETE /chat/session/{session_id}` - 删除聊天会话
- `GET /chat/tools` - 获取可用工具列表
- `POST /chat/refresh-configs` - 刷新模型配置

### 请求示例

```json
POST /chat/send
{
    "message": "请帮我创建一个Python脚本来分析数据",
    "work_id": "work_123",
    "system_type": "code"
}
```

### 响应示例

```json
{
    "success": true,
    "response": "我来帮你创建一个Python脚本来分析数据...",
    "tool_calls": [
        {
            "id": "call_123",
            "function": {
                "name": "file_modify",
                "arguments": "{\"work_id\": \"work_123\", \"file_path\": \"analysis.py\", \"operation\": \"write\", \"content\": \"import pandas as pd\\n\\n# 数据分析脚本\"}"
            }
        }
    ],
    "tool_results": [...],
    "session_id": "user_work_code",
    "timestamp": "2024-01-01T12:00:00"
}
```

## 配置要求

### 环境变量

确保在数据库中配置了正确的模型配置：

```sql
INSERT INTO model_configs (type, model_id, base_url, api_key, is_active) 
VALUES 
('brain', 'gpt-4', 'https://api.openai.com/v1', 'your-api-key', true),
('code', 'gpt-4', 'https://api.openai.com/v1', 'your-api-key', true),
('writing', 'gpt-4', 'https://api.openai.com/v1', 'your-api-key', true);
```

### 依赖包

```toml
dependencies = [
    "litellm>=1.0.0"
]
```

## 测试

运行测试脚本验证系统功能：

```bash
cd backend
python test_litellm.py
```

## 注意事项

1. **安全性**: 工具执行在受控环境中进行，避免执行危险代码
2. **超时设置**: Python代码执行有默认30秒超时限制
3. **文件权限**: 确保工作空间目录有适当的读写权限
4. **模型配置**: 需要先在数据库中配置有效的AI模型信息

## 扩展开发

### 添加新工具

1. 继承`Tool`基类
2. 实现`execute`方法
3. 在`tools.py`中注册新工具

```python
class MyCustomTool(Tool):
    def __init__(self):
        super().__init__(
            name="my_tool",
            description="我的自定义工具",
            parameters={...}
        )
    
    async def execute(self, **kwargs):
        # 实现工具逻辑
        return {"result": "success"}

# 注册工具
tool_registry.register(MyCustomTool())
```

### 添加新的AI模型类型

1. 在数据库中添加新的模型配置
2. 在聊天系统中使用新的系统类型
3. 根据需要调整工具集

