# PaperAgent Frontend

这是一个使用 Vue 3 + TypeScript + TDesign Vue Next 构建的前端应用。

## 已集成的组件库

### TDesign Vue Next
- **版本**: ^1.15.2
- **描述**: 腾讯开源的企业级设计系统，基于 Vue 3 和 TypeScript 构建
- **官网**: https://tdesign.tencent.com/vue-next

### TD Chat for AI
- **版本**: ^0.4.5
- **描述**: 专为 AI 对话场景设计的专业聊天组件
- **文档**: https://tdesign.tencent.com/vue-next/components/chat

## 功能特性

### 1. TDesign 组件示例
- 按钮组件 (主要、默认、危险、警告)
- 输入框组件 (单行输入、多行输入)
- 表单组件
- 卡片组件
- 布局组件 (头部、底部、侧边栏)

### 2. 聊天功能
- 自定义聊天界面
- TD Chat 专业组件示例
- 消息发送和接收
- 模拟 AI 回复
- 响应式设计

### 3. 界面特性
- 现代化 UI 设计
- 响应式布局
- 主题切换支持
- 组件大小调整

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

## 项目结构

```
src/
├── components/
│   └── TDChatExample.vue    # TD Chat 专业示例组件
├── App.vue                  # 主应用组件
├── main.ts                  # 应用入口文件
└── router/                  # 路由配置
```

## 使用说明

### 1. 查看 TDesign 组件
应用左侧面板展示了各种 TDesign 组件的使用方法，包括按钮、输入框、表单等。

### 2. 体验聊天功能
- 右侧面板提供了自定义聊天界面示例
- 点击"显示 TD Chat 示例"按钮可以查看专业的 TD Chat 组件
- 支持发送消息和接收 AI 回复

### 3. 组件配置
TD Chat 组件支持多种配置选项：
- 主题切换 (浅色/深色)
- 组件大小调整
- 头像显示
- 时间戳显示
- 打字机效果

## 技术栈

- **Vue 3**: 渐进式 JavaScript 框架
- **TypeScript**: 类型安全的 JavaScript 超集
- **TDesign Vue Next**: 企业级设计系统
- **TD Chat**: AI 聊天组件
- **Vite**: 快速构建工具
- **Vue Router**: 官方路由管理器

## 开发指南

### 添加新的 TDesign 组件
1. 在 `main.ts` 中引入组件库
2. 在 Vue 组件中使用 `<t-component-name>` 标签
3. 参考官方文档了解组件属性和事件

### 自定义 TD Chat
1. 导入 `ChatPlugin` 组件
2. 配置 `ChatOptions` 选项
3. 管理 `ChatMessage` 消息数据
4. 处理发送和接收事件

## 相关链接

- [TDesign Vue Next 官方文档](https://tdesign.tencent.com/vue-next)
- [TD Chat 组件文档](https://tdesign.tencent.com/vue-next/components/chat)
- [Vue 3 官方文档](https://vuejs.org/)
- [TypeScript 官方文档](https://www.typescriptlang.org/)
