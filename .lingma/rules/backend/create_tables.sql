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

-- 2. 工作历史表
CREATE TABLE works (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    title VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 3. 消息表
CREATE TABLE messages (
    id SERIAL PRIMARY KEY,
    work_id INTEGER NOT NULL,
    sender_type VARCHAR(20) NOT NULL CHECK (sender_type IN ('user', 'ai')),
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (work_id) REFERENCES works(id) ON DELETE CASCADE
);

-- 4. 论文模板表
CREATE TABLE paper_templates (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    category VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_public BOOLEAN DEFAULT FALSE,
    created_by INTEGER,
    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
);

-- 5. 模型配置表
CREATE TABLE model_configs (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    provider VARCHAR(50) NOT NULL,
    model_name VARCHAR(100) NOT NULL,
    api_key VARCHAR(255),
    config JSONB,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 创建索引以提高查询性能
CREATE INDEX idx_users_username ON users(username);
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_works_user_id ON works(user_id);
CREATE INDEX idx_works_created_at ON works(created_at);
CREATE INDEX idx_messages_work_id ON messages(work_id);
CREATE INDEX idx_messages_created_at ON messages(created_at);
CREATE INDEX idx_paper_templates_created_by ON paper_templates(created_by);
CREATE INDEX idx_paper_templates_category ON paper_templates(category);
CREATE INDEX idx_model_configs_provider ON model_configs(provider);
CREATE INDEX idx_model_configs_is_active ON model_configs(is_active);

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

-- 添加注释
COMMENT ON TABLE system_config IS '系统基本配置表';
COMMENT ON TABLE users IS '用户表';
COMMENT ON TABLE works IS '工作历史表';
COMMENT ON TABLE messages IS '消息表';
COMMENT ON TABLE paper_templates IS '论文模板表';
COMMENT ON TABLE model_configs IS '模型配置表';
