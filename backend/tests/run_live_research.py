"""Explicit model-driven acceptance; creates a visible work owned by an existing work's user."""
import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from database.database import SessionLocal
from models.models import Project
from schemas.schemas import WorkCreate
from services.data_services.crud import create_work, update_work_status
from config.paths import get_workspace_path
from ai_system.config.environment import DatabaseConfigManager
from ai_system.core_handlers.llm_providers import create_llm_from_model_config
from ai_system.graph.agent import PaperGraphAgent
from ai_system.core_managers.stream_manager import StreamOutputManager, SimpleStreamCallback
from services.chat_services.chat_history_manager import ChatHistoryManager
from services.chat_services.conversation_timeline import ReplyWriter, TIMELINE
from ai_system.research.runtime import ResearchRuntime
from ai_system.research.views import completed_work_status

QUESTION = '''请研究食堂排队等待时间样本[3,4,4,5,5,6,7,22]分钟。计算样本量、均值、中位数、样本标准差；比较保留全部数据与去掉22后的结果，说明敏感性和小样本局限，不能把极端值直接认定为错误。保留waiting_summary.csv和中文图表、完整代码，交付简短中文研究报告及PDF，报告包含假设、方法、结果和局限，不必外部检索。在同一次计算中分三阶段print进度（每阶段间隔2秒）以核对实时输出；计算指标使用emit_result输出，使用自动返回的引用，不要为登记或报告重算。'''

async def main(owner_work=None, resume_work=None):
    with SessionLocal() as db:
        owner = db.query(Project).filter_by(work_id=resume_work or owner_work).one().created_by
        configs = DatabaseConfigManager(db)
        llms = {role: create_llm_from_model_config(configs.get_model_config(role, owner)) for role in ('brain','code','writing')}
        work = (db.query(Project).filter_by(work_id=resume_work).one() if resume_work else
            create_work(db, WorkCreate(title='执行与成果交付验证', description=QUESTION, output_mode='latex'), owner))
        work_id = work.work_id
        update_work_status(db, work_id, 'in_progress')
    root = get_workspace_path(work_id)
    print('WORK_ID=' + work_id, flush=True)
    print('WORKSPACE=' + str(root), flush=True)
    if not resume_work:
        ResearchRuntime(root).configure({'compact_paper': True, 'request_limit': 100})
    history = ChatHistoryManager()
    if not resume_work: history.save_message(work_id, 'user', QUESTION)
    started = time.monotonic()
    events = root / ('.system/live_acceptance_resume.jsonl' if resume_work else '.system/live_acceptance.jsonl')
    def publish(update):
        with events.open('a') as stream:
            stream.write(json.dumps({'elapsed': round(time.monotonic()-started,2), 'update': update}, ensure_ascii=False) + '\n')
    writer = ReplyWriter(history, work_id, 'live-acceptance-'+work_id+('-resume' if resume_work else ''), root, publish)
    if resume_work:
        writer.text('验收中已修正报告的排版、图引用和样本来源表述；现在从交付阶段恢复，保留原有计算与文件。\n')
    class Callback(SimpleStreamCallback):
        async def on_content(self, content): writer.text(content)
        async def on_message_complete(self, role, content): writer.flush()
        async def on_json_block(self, block):
            if block['type'] in {'research_commentary','paper_stage','main_agent_error','main_agent_complete'}:
                print(block['type'] + ': ' + str(block['content'])[:500], flush=True)
            if block['type'] not in {'file_changed','research_state','research_snapshot'}:
                writer.block(block)
    stream = StreamOutputManager(Callback())
    agent = PaperGraphAgent(llms['brain'], stream, str(root), work_id,
        codeagent_llm=llms['code'], writer_llm=llms['writing'], output_mode='latex')
    token = TIMELINE.set(writer)
    try:
        await agent.run(QUESTION, action='resume' if resume_work else 'run')
        status = completed_work_status(root)
        writer.finish('completed' if status == 'completed' else 'failed')
        with SessionLocal() as db: update_work_status(db, work_id, status)
        print('FINAL_STATUS=' + status, flush=True)
    except asyncio.CancelledError:
        writer.finish('interrupted', '验收运行已暂停，已有计算和文件保留。')
        with SessionLocal() as db: update_work_status(db, work_id, 'paused')
        raise
    finally:
        TIMELINE.reset(token)

if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--owner-work')
    source.add_argument('--resume-work')
    args=parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main(args.owner_work,args.resume_work))
