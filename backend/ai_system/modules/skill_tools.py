"""On-demand method instructions for the same research Agent and Python tool."""
from pydantic import Field
from .registry import Inputs, CapabilityProduct
from .methods import allowed_method_keys, skill_catalog, skill_detail


class SkillTarget(Inputs):
    key: str = Field(min_length=1, description='从research_list_skills返回的key中选择。')


def policy(ctx):
    return ctx.research.skill_preferences()['policy']


def list_skills(ctx, inputs):
    allowed=allowed_method_keys(policy(ctx))
    return CapabilityProduct('research_state',{'skills':[s for s in skill_catalog() if s['key'] in allowed]})


def read_skill(ctx, inputs):
    key=inputs['key']
    detail=skill_detail(key)
    if key not in allowed_method_keys(policy(ctx)):
        raise ValueError('该技能未在当前项目启用，请遵守用户选择的技能范围。')
    return CapabilityProduct('research_state',detail)


def register(registry):
    registry.register('skills','research.list_skills','research_state',list_skills,Inputs,
        model_callable=True,access='read',purpose='列出当前项目可用的研究技能及适用条件，按需读取完整指导；不会启动计算或创建实验。')
    registry.register('skills','research.read_skill','research_state',read_skill,SkillTarget,
        model_callable=True,access='read',purpose='读取一个研究技能的方法、检查与产出建议；由当前Agent使用通用Python工具执行，并直接写入回复和正文。')
