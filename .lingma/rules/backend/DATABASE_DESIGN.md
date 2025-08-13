# PaperAgent 数据库设计

## 概述

PaperAgent 是一个AI驱动的论文生成系统，数据库设计需要支持用户管理、工作历史、论文模板、模型配置等功能。

## 数据库表结构

### 0.系统基本配置表

| 字段名            | 类型 | 约束     | 描述         |
| ----------------- | ---- | -------- | ------------ |
| is_allow_register | BOOL | NOT NULL | 是否允许注册 |

### 1. 用户表 (users)

存储用户基本信息和认证数据。

| 字段名 | 类型 | 约束 | 描述 |
|-------|------|-----|-----|
| id | SERIAL | PRIMARY KEY | 用户ID |
| username | VARCHAR(50) | UNIQUE, NOT NULL | 用户名 |
| email | VARCHAR(100) | UNIQUE, NOT NULL | 邮箱 |
| password_hash | VARCHAR(255) | NOT NULL | 密码哈希值 |
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | 创建时间 |
| updated_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | 更新时间 |
| is_active | BOOLEAN | DEFAULT TRUE | 账户是否激活 |

### 2. 工作历史表 (works)

存储用户与AI的工作历史。

| 字段名 | 类型 | 约束 | 描述 |
|-------|------|-----|-----|
| id | SERIAL | PRIMARY KEY | 工作ID |
| user_id | INTEGER | FOREIGN KEY (users.id) | 用户ID |
| title | VARCHAR(255) | NOT NULL | 工作标题 |
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | 创建时间 |
| updated_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | 更新时间 |
| is_active | BOOLEAN | DEFAULT TRUE | 工作是否仍在进行 |

### 3. 消息表 (messages)

存储工作中的具体消息。

| 字段名 | 类型 | 约束 | 描述 |
|-------|------|-----|-----|
| id | SERIAL | PRIMARY KEY | 消息ID |
| work_id | INTEGER | FOREIGN KEY (works.id) | 工作ID |
| sender_type | VARCHAR(20) | NOT NULL | 发送者类型('user', 'ai') |
| content | TEXT | NOT NULL | 消息内容 |
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | 创建时间 |

### 4. 论文模板表 (paper_templates)

存储论文模板信息。

| 字段名 | 类型 | 约束 | 描述 |
|-------|------|-----|-----|
| id | SERIAL | PRIMARY KEY | 模板ID |
| name | VARCHAR(100) | NOT NULL | 模板名称 |
| description | TEXT |  | 模板描述 |
| content | TEXT | NOT NULL | 模板内容(LaTeX格式) |
| category | VARCHAR(50) |  | 模板分类 |
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | 创建时间 |
| updated_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | 更新时间 |
| is_public | BOOLEAN | DEFAULT FALSE | 是否公开模板 |
| created_by | INTEGER | FOREIGN KEY (users.id) | 创建者ID |

### 5. 模型配置表 (model_configs)

存储AI模型配置信息。

| 字段名 | 类型 | 约束 | 描述 |
|-------|------|-----|-----|
| id | SERIAL | PRIMARY KEY | 配置ID |
| name | VARCHAR(100) | NOT NULL | 配置名称 |
| provider | VARCHAR(50) | NOT NULL | 模型提供商 |
| model_name | VARCHAR(100) | NOT NULL | 模型名称 |
| api_key | VARCHAR(255) |  | API密钥 |
| config | JSON |  | 其他配置参数 |
| is_active | BOOLEAN | DEFAULT TRUE | 是否激活 |
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | 创建时间 |

## 索引

为提高查询性能，建议在以下字段上创建索引：

1. users表: username, email
2. works表: user_id, created_at
3. messages表: conversation_id, created_at

## 关系图

```
users 1-----n works
works 1-----n messages
users 1-----n paper_templates
```