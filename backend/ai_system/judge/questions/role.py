from __future__ import annotations

from ...judge.base import Choice, Noul, Question


def role_questions(blocks) -> dict[str, Question]:
    questions: dict[str, Question] = {}
    for block in blocks:
        questions[f"{block.id}.role"] = Choice(
            instructions=f"`blocks` 中 id 为 {block.id} 的段落在这份模板里扮演什么角色？",
            criteria={
                "heading": "章节标题：短句，通常带编号或大字号/加粗，成稿后必须保留",
                "fixed_text": "模板固定文字（封面、学号姓名栏、签字栏）：原样保留",
                "placeholder_fill": "要求作者在此处填写内容的提示语，成稿时其后要有正文",
                "example_delete": "示例内容（示例代码、示例表格行、示例文字），成稿时必须替换或删除",
                "instruction_delete": "写给作者的格式/写作说明，成稿后应整段删除",
                "caption": "图题或表题",
                "table": "需要填写的表格",
                "figure_slot": "需要插入图片的位置",
                "other": "以上都不是",
            },
        )
        questions[f"{block.id}.format_rule"] = Noul(
            instructions=f"id 为 {block.id} 的段落是否陈述了字体、字号、行距、缩进、对齐或页边距要求？"
        )
    return questions
