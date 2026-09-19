from __future__ import annotations

from ..base import Choice, Question


def intent_questions() -> dict[str, Question]:
    return {
        "kind": Choice(
            instructions="用户这条消息的意图是什么？",
            criteria={
                "write": "开始或继续写论文、生成章节",
                "edit": "修改、重写或调整已有章节",
                "question": "询问进度、计划或已写内容，不要改文档",
                "chat": "闲聊或致谢",
                "confirm": "确认采用当前稿、停止自动修复",
                "other": "以上都不是",
            },
        )
    }
