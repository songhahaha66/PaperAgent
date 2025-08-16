# 数据库迁移说明

## 概述

本文档说明如何为AI系统迁移创建必要的数据库表。

## 需要创建的表

### 1. 聊天会话表 (chat_sessions)

```sql
CREATE TABLE chat_sessions (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(100) UNIQUE NOT NULL,
    work_id VARCHAR(50) NOT NULL,
    system_type VARCHAR(20) NOT NULL,
    title VARCHAR(200),
    status VARCHAR(20) DEFAULT 'active',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    created_by INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE
);

-- 添加约束
ALTER TABLE chat_sessions ADD CONSTRAINT chk_system_type 
    CHECK (system_type IN ('brain', 'code', 'writing'));

ALTER TABLE chat_sessions ADD CONSTRAINT chk_status 
    CHECK (status IN ('active', 'archived', 'deleted'));

-- 创建索引
CREATE INDEX idx_chat_sessions_work_id ON chat_sessions(work_id);
CREATE INDEX idx_chat_sessions_system_type ON chat_sessions(system_type);
CREATE INDEX idx_chat_sessions_created_by ON chat_sessions(created_by);
CREATE INDEX idx_chat_sessions_status ON chat_sessions(status);
```

### 2. 聊天消息表 (chat_messages)

```sql
CREATE TABLE chat_messages (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(100) NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL,
    content TEXT NOT NULL,
    tool_calls JSONB,
    tool_results JSONB,
    metadata JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 添加约束
ALTER TABLE chat_messages ADD CONSTRAINT chk_role 
    CHECK (role IN ('user', 'assistant', 'system', 'tool'));

-- 创建索引
CREATE INDEX idx_chat_messages_session_id ON chat_messages(session_id);
CREATE INDEX idx_chat_messages_role ON chat_messages(role);
CREATE INDEX idx_chat_messages_created_at ON chat_messages(created_at);
```

### 3. 工作流状态表 (work_flow_states)

```sql
CREATE TABLE work_flow_states (
    id SERIAL PRIMARY KEY,
    work_id VARCHAR(50) NOT NULL,
    current_state VARCHAR(50) NOT NULL,
    previous_state VARCHAR(50),
    state_data JSONB,
    transition_reason TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 添加约束
ALTER TABLE work_flow_states ADD CONSTRAINT chk_current_state 
    CHECK (current_state IN (
        'CREATED', 'PLANNING', 'MODELING', 'CODING', 
        'EXECUTING', 'ANALYZING', 'WRITING', 'REVIEWING', 
        'COMPLETED', 'ARCHIVED'
    ));

-- 创建索引
CREATE INDEX idx_work_flow_states_work_id ON work_flow_states(work_id);
CREATE INDEX idx_work_flow_states_current_state ON work_flow_states(current_state);
```

## 创建表的步骤

### 方法1: 使用Python脚本

1. 确保数据库连接配置正确
2. 运行迁移脚本：
   ```bash
   cd backend
   uv run python create_chat_tables.py
   ```
3. 选择选项1创建表

### 方法2: 手动执行SQL

1. 连接到PostgreSQL数据库
2. 依次执行上述SQL语句
3. 验证表创建成功

### 方法3: 使用SQLAlchemy自动创建

1. 确保所有模型都已导入
2. 运行以下Python代码：
   ```python
   from backend.models.models import Base
   from backend.database.database import get_database_url
   from sqlalchemy import create_engine
   
   engine = create_engine(get_database_url())
   Base.metadata.create_all(bind=engine)
   ```

## 验证表创建

执行以下SQL查询验证表是否创建成功：

```sql
-- 查看所有表
SELECT table_name FROM information_schema.tables 
WHERE table_schema = 'public' 
AND table_name IN ('chat_sessions', 'chat_messages', 'work_flow_states');

-- 查看表结构
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_name = 'chat_sessions'
ORDER BY ordinal_position;
```

## 注意事项

1. **外键约束**: 确保users表已存在
2. **JSONB类型**: 需要PostgreSQL 9.4+
3. **时区支持**: 使用TIMESTAMP WITH TIME ZONE
4. **级联删除**: 删除会话时会自动删除相关消息

## 故障排除

### 常见问题

1. **权限错误**: 确保数据库用户有CREATE TABLE权限
2. **外键错误**: 检查引用的表是否存在
3. **类型错误**: 确保PostgreSQL版本支持JSONB类型

### 回滚操作

如果需要删除表，按以下顺序执行：

```sql
DROP TABLE IF EXISTS chat_messages CASCADE;
DROP TABLE IF EXISTS chat_sessions CASCADE;
DROP TABLE IF EXISTS work_flow_states CASCADE;
```

## 下一步

表创建完成后，可以：

1. 测试聊天API接口
2. 验证数据持久化功能
3. 继续迁移核心AI逻辑
4. 完善前端聊天界面

