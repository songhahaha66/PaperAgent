# PaperAgent 前端

## 概述

PaperAgent前端是一个基于Vue 3 + TypeScript + TDesign的现代化Web应用，实现了完整的用户认证系统和论文生成工作区。

## 技术栈

- **Vue 3** - 渐进式JavaScript框架
- **TypeScript** - 类型安全的JavaScript
- **TDesign** - 企业级设计系统
- **Pinia** - Vue状态管理
- **Vue Router** - 官方路由管理器
- **Vite** - 快速构建工具

## 功能特性

### ✅ 用户认证系统
- 用户注册（邮箱、用户名、密码）
- 用户登录（邮箱、密码）
- JWT Token管理
- 路由守卫和权限控制
- 自动登录状态检查

### ✅ 工作区功能
- 智能论文生成对话
- 多模型协作系统（中枢、代码执行、论文生成）
- 历史工作管理
- 实时聊天界面
- 论文预览功能

### ✅ 用户体验
- 响应式设计
- 侧边栏折叠
- 消息分割线交互
- 系统状态标识
- 加载状态管理

## 项目结构

```
frontend/
├── src/
│   ├── api/           # API服务
│   │   └── auth.ts    # 认证API
│   ├── components/    # 组件
│   ├── router/        # 路由配置
│   ├── stores/        # 状态管理
│   │   ├── app.ts     # 应用状态
│   │   └── auth.ts    # 认证状态
│   ├── views/         # 页面组件
│   │   ├── Introduction.vue  # 介绍页
│   │   ├── Login.vue         # 登录页
│   │   └── Home.vue          # 主页
│   └── main.ts        # 应用入口
├── env_example        # 环境配置示例
└── README_FRONTEND.md # 本文件
```

## 安装和运行

### 1. 安装依赖
```bash
npm install
```

### 2. 环境配置
复制 `env_example` 为 `.env.local` 并配置：
```bash
# 后端API地址
VITE_API_BASE_URL=http://localhost:8000
```

### 3. 启动开发服务器
```bash
npm run dev
```

### 4. 构建生产版本
```bash
npm run build
```

## 使用说明

### 用户注册
1. 访问 `/login?mode=register` 或点击注册按钮
2. 填写邮箱、用户名和密码
3. 提交注册表单
4. 注册成功后自动切换到登录模式

### 用户登录
1. 访问 `/login` 页面
2. 输入邮箱和密码
3. 点击登录按钮
4. 登录成功后跳转到工作区

### 工作区使用
1. 登录后自动进入工作区
2. 点击"新建工作"开始新任务
3. 在聊天界面与AI对话
4. 查看论文预览和生成结果
5. 管理历史工作记录

## API集成

前端通过RESTful API与后端通信：

- **POST** `/register` - 用户注册
- **POST** `/login` - 用户登录  
- **GET** `/me` - 获取当前用户信息

## 状态管理

使用Pinia进行状态管理：

- **app store** - 应用全局状态（标题、语言等）
- **auth store** - 用户认证状态（用户信息、token等）

## 路由配置

- `/` - 介绍页面（公开访问）
- `/login` - 登录页面（未登录用户）
- `/home` - 工作区（需要登录）

## 安全特性

- JWT Token认证
- 路由守卫保护
- 自动登录状态检查
- 敏感操作权限验证

## 开发指南

### 添加新组件
1. 在 `src/components/` 目录创建组件
2. 使用TDesign组件库
3. 遵循Vue 3 Composition API规范

### 添加新页面
1. 在 `src/views/` 目录创建页面组件
2. 在 `src/router/index.ts` 添加路由配置
3. 设置适当的路由元信息

### 添加新API
1. 在 `src/api/` 目录创建API服务
2. 使用统一的请求处理逻辑
3. 添加适当的错误处理

## 注意事项

1. 确保后端服务正在运行
2. 检查环境配置中的API地址
3. 用户认证状态会自动持久化
4. 路由守卫会自动处理权限检查
