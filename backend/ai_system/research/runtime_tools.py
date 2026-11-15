"""Explicit runtime reset; inspection never submits code or creates a sandbox."""
import json
from pydantic import Field
from ai_system.modules.registry import Inputs,CapabilityProduct


class Reset(Inputs):
    reason: str = Field(min_length=1,description='说明为什么需要更换沙箱；未取全的任务日志和代码会阻止销毁，先恢复文件交付。')


async def reset(ctx,inputs):
    kernel=ctx.kernels.get('research')
    await kernel.close()
    return CapabilityProduct('research_state',{'status':'reset','reason':inputs['reason'],
        'runtime_note':'原沙箱已确认结束；下一次计算使用新沙箱，已保存文件和回执保留，不自动重放代码。'})


async def inspect(ctx,inputs):
    from services.file_services.workspace_layout import private_root
    from ai_system.sandbox.settings import SandboxSettings
    settings=SandboxSettings.from_env()
    folder=private_root(ctx.workspace)/'.system/runtimes'
    rows=[json.loads(p.read_text()) for p in sorted(folder.glob('*.json'))] if folder.exists() else []
    return CapabilityProduct('research_state',{'runtimes':rows,
        'resources':{'cpu':settings.cpu,'memory':settings.memory,'image':settings.image},
        'note':'配置资源与持久记录；执行后的environment包含实际限额和库版本。'})


def register(registry):
    registry.register('research','research.inspect_runtime','research_state',inspect,Inputs,model_callable=True,access='read',
        purpose='查看沙箱持久身份、代次、到期与清理状态，不创建沙箱或执行代码。')
    registry.register('research','research.reset_runtime','research_state',reset,Reset,model_callable=True,
        purpose='沙箱无法继续或明确需要重置时，确认结束主运行时；先取回原任务日志和代码，未交付完成时不销毁。普通代码超时只停止进程，准备或下载超时先重试对应阶段，不重置健康沙箱。')
