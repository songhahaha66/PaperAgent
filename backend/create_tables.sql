-- PaperAgent 数据库表创建脚本
-- 根据 DATABASE_DESIGN.md 设计生成

-- 0. 系统基本配置表
CREATE TABLE system_config (
    id SERIAL PRIMARY KEY,
    is_allow_register BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 1. 用户表
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    email VARCHAR(100) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE
);

-- 2. 论文模板表
CREATE TABLE paper_templates (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    category VARCHAR(50),
    file_path VARCHAR(500) NOT NULL,  -- 模板文件路径
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_public BOOLEAN DEFAULT FALSE,
    created_by INTEGER NOT NULL,
    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
);

-- 3. 工作历史表
CREATE TABLE works (
    id SERIAL PRIMARY KEY,
    work_id VARCHAR(50) UNIQUE NOT NULL,  -- 唯一工作ID
    title VARCHAR(200) NOT NULL,  -- 工作标题
    description TEXT,  -- 工作描述
    status VARCHAR(50) NOT NULL DEFAULT 'created',  -- 工作状态
    progress INTEGER DEFAULT 0,  -- 进度百分比 (0-100)
    tags TEXT,  -- 标签，JSON格式存储
    template_id INTEGER,  -- 关联的论文模板ID
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by INTEGER NOT NULL,
    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (template_id) REFERENCES paper_templates(id) ON DELETE SET NULL
);

-- 5. 模型配置表
CREATE TABLE model_configs (
    id SERIAL PRIMARY KEY,
    type VARCHAR(50) NOT NULL,  -- 模型种类：brain(中枢大脑), code(代码实验), writing(论文写作)
    model_id VARCHAR(50) NOT NULL,  -- 模型ID
    base_url VARCHAR(100) NOT NULL,  -- 模型URL
    api_key VARCHAR(255) NOT NULL,  -- API密钥
    is_active BOOLEAN DEFAULT TRUE,  -- 是否激活
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP  -- 创建时间
);

-- 6. 聊天会话表
CREATE TABLE chat_sessions (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(100) UNIQUE NOT NULL,  -- 唯一会话ID
    work_id VARCHAR(50) NOT NULL,  -- 关联的工作ID
    system_type VARCHAR(20) NOT NULL,  -- 系统类型：brain(中枢大脑), code(代码实验), writing(论文写作)
    title VARCHAR(200),  -- 会话标题
    status VARCHAR(20) DEFAULT 'active',  -- 会话状态：active, archived, deleted
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by INTEGER NOT NULL,
    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE CASCADE
);

-- 7. 工作流状态表
CREATE TABLE work_flow_states (
    id SERIAL PRIMARY KEY,
    work_id VARCHAR(50) NOT NULL,  -- 关联的工作ID
    current_state VARCHAR(50) NOT NULL,  -- 当前状态
    previous_state VARCHAR(50),  -- 前一个状态
    state_data JSON,  -- 状态相关的数据
    transition_reason TEXT,  -- 状态转换原因
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 创建索引以提高查询性能
CREATE INDEX idx_users_username ON users(username);
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_works_work_id ON works(work_id);
CREATE INDEX idx_works_created_by ON works(created_by);
CREATE INDEX idx_works_created_at ON works(created_at);
CREATE INDEX idx_works_status ON works(status);
CREATE INDEX idx_works_template_id ON works(template_id);
CREATE INDEX idx_paper_templates_created_by ON paper_templates(created_by);
CREATE INDEX idx_paper_templates_category ON paper_templates(category);
CREATE INDEX idx_model_configs_type ON model_configs(type);
CREATE INDEX idx_model_configs_is_active ON model_configs(is_active);
CREATE INDEX idx_chat_sessions_session_id ON chat_sessions(session_id);
CREATE INDEX idx_chat_sessions_work_id ON chat_sessions(work_id);
CREATE INDEX idx_chat_sessions_created_by ON chat_sessions(created_by);
CREATE INDEX idx_work_flow_states_work_id ON work_flow_states(work_id);

-- 插入默认系统配置
INSERT INTO system_config (is_allow_register) VALUES (TRUE);

-- 创建更新时间触发器函数
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- 为需要自动更新updated_at的表创建触发器
CREATE TRIGGER update_users_updated_at BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_works_updated_at BEFORE UPDATE ON works
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_paper_templates_updated_at BEFORE UPDATE ON paper_templates
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_chat_sessions_updated_at BEFORE UPDATE ON chat_sessions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- 添加注释
COMMENT ON TABLE system_config IS '系统基本配置表';
COMMENT ON TABLE users IS '用户表';
COMMENT ON TABLE works IS '工作历史表';
COMMENT ON TABLE paper_templates IS '论文模板表';
COMMENT ON TABLE model_configs IS '模型配置表';
COMMENT ON TABLE chat_sessions IS '聊天会话表';
COMMENT ON TABLE work_flow_states IS '工作流状态表';
