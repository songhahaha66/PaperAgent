# PaperAgent Frontend

这是一个使用 Vue 3 + TypeScript + TDesign Vue Next 构建的前端应用，为用户提供直观易用的论文自动生成界面。

## 技术栈

- [Vue 3](https://vuejs.org/) - 渐进式 JavaScript 框架
- [TypeScript](https://www.typescriptlang.org/) - 类型安全的 JavaScript 超集
- [TDesign Vue Next](https://tdesign.tencent.com/vue-next) - 腾讯开源的企业级设计系统
- [TD Chat](https://tdesign.tencent.com/vue-next/components/chat) - 专为 AI 对话场景设计的专业聊天组件
- [Pinia](https://pinia.vuejs.org/) - Vue 官方状态管理工具
- [Vue Router](https://router.vuejs.org/) - Vue 官方路由管理器
- [Vite](https://vitejs.dev/) - 快速构建工具

## 功能特性

### 用户认证系统
- 用户注册（邮箱、用户名、密码）
- 用户登录（邮箱、密码）
- JWT Token 管理
- 路由守卫和权限控制
- 自动登录状态检查

### 论文工作区
- 智能论文生成对话
- 多模型协作系统（中枢、代码执行、论文生成）
- 历史工作管理
- 实时聊天界面
- 论文预览功能

### 用户体验
- 响应式设计
- 侧边栏折叠
- 消息分割线交互
- 系统状态标识
- 加载状态管理

## 项目结构

```
frontend/
├── src/
│   ├── api/           # API 服务
│   │   ├── auth.ts    # 认证相关 API
│   │   └── template.ts# 模板相关 API
│   ├── components/    # 可复用组件
│   │   ├── Sidebar.vue       # 侧边栏组件
│   │   └── TDChatExample.vue # TD Chat 示例组件
│   ├── lang/          # 国际化语言文件
│   ├── router/        # 路由配置
│   ├── stores/        # 状态管理
│   │   ├── app.ts     # 应用状态
│   │   ├── auth.ts    # 认证状态
│   │   └── index.ts   # 状态管理入口
│   ├── views/         # 页面组件
│   │   ├── Home.vue        # 主页
│   │   ├── Introduction.vue# 介绍页
│   │   ├── Login.vue       # 登录页
│   │   ├── Template.vue    # 模板页
│   │   └── Work.vue        # 工作页
│   ├── App.vue        # 根组件
│   ├── main.ts        # 应用入口文件
│   └── theme.css      # 自定义主题样式
├── env.d.ts           # 环境变量类型定义
├── index.html         # HTML 入口文件
├── package.json       # 项目依赖和脚本
├── tsconfig.*.json    # TypeScript 配置文件
└── vite.config.ts     # Vite 配置文件
```

## 快速开始

### 安装依赖
```bash
npm install
```

### 启动开发服务器
```bash
npm run dev
```

### 构建生产版本
```bash
npm run build
```

## 使用说明

### 用户认证
1. 访问登录页面进行注册或登录
2. 系统会自动管理 JWT Token
3. 登录状态会在页面刷新后保持

### 论文生成
1. 登录后进入主页
2. 点击"新建工作"开始新任务
3. 在聊天界面与 AI 系统对话
4. 查看实时生成的论文内容

## 组件使用

### TDesign 组件库
项目集成了 TDesign Vue Next 组件库，提供了丰富的 UI 组件：
- 布局组件（Layout、Header、Content、Sider）
- 表单组件（Input、Form、Select）
- 数据展示组件（Table、List、Card）
- 反馈组件（Dialog、Message、Notification）

### TD Chat 组件
TD Chat 是专为 AI 对话场景设计的聊天组件，具有以下特性：
- 消息展示和交互
- 头像和时间戳显示
- 打字机效果
- 响应式设计

## 开发指南

### 添加新页面
1. 在 `src/views/` 目录创建页面组件
2. 在 `src/router/index.ts` 添加路由配置
3. 设置适当的路由元信息

### 添加新组件
1. 在 `src/components/` 目录创建组件
2. 使用 TDesign 组件库
3. 遵循 Vue 3 Composition API 规范

### 添加新 API
1. 在 `src/api/` 目录创建 API 服务
2. 使用统一的请求处理逻辑
3. 添加适当的错误处理

## 相关链接

- [TDesign Vue Next 官方文档](https://tdesign.tencent.com/vue-next)
- [TD Chat 组件文档](https://tdesign.tencent.com/vue-next/components/chat)
- [Vue 3 官方文档](https://vuejs.org/)
- [TypeScript 官方文档](https://www.typescriptlang.org/)
