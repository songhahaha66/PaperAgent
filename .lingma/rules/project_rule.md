你是一个高级软件工程师，项目是开发一款论文Agent。可以辅助生成论文。

# 开发说明

1. 项目分为前后端，注意前端在frontend文件夹，后端在backend文件夹。
2. 前端相关规则在`.lingma/rules/frontend/frontend.md`，后端在`.lingma/rules/backend/backend.md`.你不必在意你当前没有修改的东西，比如你写前端不必太在意后端的提示词。
3. 你每次不用完成太多，完成我指定给你的任务后就请让我查看一下完成情况，让我审阅一下，我允许后请新建分支并提交PR。
4. 基本原则：保持代码库整洁易读，不要使代码过于臃肿。
5. 你需要做的任务在`.lingma/rules/backend/TODO.md`（或`.lingma/rules/frontend/TODO.md`），完成后请在里面打勾表示完成，同样的，你也不用管已经打勾的项目
6. 每次与你对话后，请立刻先把我说的话追加到`chat.md`中，用于记录ai对话。每一行的格式是`[username][MM:DD HH:MM:SS]：[我说的话]`，至于username是什么，调用`git config user.name`获取并记住它。