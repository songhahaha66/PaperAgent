from run import setup_environment, MainAgent, LLMHandler, StreamOutputManager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
import os
import sys
import json
import asyncio
from contextlib import asynccontextmanager
import logging

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 添加当前目录到Python路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# 导入demo模块

# 全局变量存储环境状态
demo_environment_ready = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时初始化环境
    global demo_environment_ready
    try:
        setup_environment()
        demo_environment_ready = True
        logger.info("Demo环境初始化成功")
    except Exception as e:
        logger.error(f"Demo环境初始化失败: {e}")
        demo_environment_ready = False
    yield
    # 关闭时的清理工作
    logger.info("Demo服务关闭")

app = FastAPI(
    title="PaperAgent Demo API",
    description="Demo API for PaperAgent - AI论文生成演示",
    version="0.1.0",
    lifespan=lifespan
)

# CORS配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class DemoRequest(BaseModel):
    problem: str
    model: Optional[str] = "gemini/gemini-2.0-flash"


class DemoResponse(BaseModel):
    status: str
    message: str


@app.get("/")
async def root():
    return {"message": "PaperAgent Demo API", "status": "running"}


@app.get("/health")
async def health_check():
    global demo_environment_ready
    return {
        "status": "healthy" if demo_environment_ready else "initializing",
        "environment_ready": demo_environment_ready
    }


@app.post("/demo/stream")
async def run_demo_stream(request: DemoRequest):
    """
    运行demo并返回真正的流式响应
    """
    global demo_environment_ready

    if not demo_environment_ready:
        raise HTTPException(status_code=503, detail="Demo环境未准备好")

    logger.info(f"开始处理流式请求，问题长度: {len(request.problem)} 字符")

    async def generate_stream():
        try:
            logger.info("创建异步队列和流式输出管理器")
            # 创建一个异步队列来传递输出
            output_queue = asyncio.Queue()

            # 定义同步流式输出回调函数
            def stream_callback(content):
                # 使用线程安全的方式添加到队列
                try:
                    loop = asyncio.get_running_loop()
                    loop.call_soon_threadsafe(output_queue.put_nowait, content)
                    logger.debug(f"回调函数添加内容到队列: {repr(content[:50])}...")
                except RuntimeError:
                    # 如果没有事件循环，记录错误但不跳过
                    logger.error("没有运行中的事件循环，无法添加内容到队列")
                    # 尝试直接添加到队列（可能不安全，但总比丢失数据好）
                    try:
                        output_queue.put_nowait(content)
                        logger.debug("直接添加到队列成功")
                    except Exception as e:
                        logger.error(f"直接添加到队列失败: {e}")

            # 创建流式输出管理器
            stream_manager = StreamOutputManager(
                stream_callback=stream_callback)

            logger.info("初始化LLM处理器和主Agent")
            # 初始化LLM处理器和主Agent
            llm_handler = LLMHandler(
                model=request.model, stream_manager=stream_manager)
            main_agent = MainAgent(llm_handler, stream_manager)

            # 在后台运行主Agent
            async def run_agent():
                try:
                    logger.info("在后台线程中启动Agent")
                    # 在事件循环中运行同步代码
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, main_agent.run, request.problem)
                    logger.info("Agent执行完成，发送完成信号")
                    # 发送完成信号
                    await output_queue.put(None)
                except Exception as e:
                    error_msg = f"Agent执行出错: {str(e)}"
                    logger.error(f"Agent执行异常: {e}")
                    await output_queue.put(f"data: {json.dumps({'type': 'error', 'data': error_msg})}\n\n")

            # 启动Agent任务
            logger.info("创建Agent任务")
            agent_task = asyncio.create_task(run_agent())

            # 流式发送输出
            content_count = 0
            logger.info("开始流式发送输出")
            while True:
                try:
                    # 等待输出，设置超时避免无限等待
                    content = await asyncio.wait_for(output_queue.get(), timeout=30.0)

                    if content is None:
                        # Agent执行完成
                        logger.info(f"Agent执行完成，总共发送了 {content_count} 个内容块")
                        yield f"data: {json.dumps({'type': 'complete', 'data': 'Demo执行完成'})}\n\n"
                        break

                    # 发送内容
                    content_count += 1
                    logger.debug(
                        f"发送第 {content_count} 个内容块: {repr(content[:50])}...")
                    yield f"data: {json.dumps({'type': 'content', 'data': content})}\n\n"

                except asyncio.TimeoutError:
                    # 超时检查Agent是否还在运行
                    if agent_task.done():
                        logger.info("Agent任务已完成，退出流式循环")
                        break
                    logger.debug("等待超时，继续等待...")
                    continue
                except Exception as e:
                    error_msg = f"流式输出出错: {str(e)}"
                    logger.error(f"流式输出异常: {e}")
                    yield f"data: {json.dumps({'type': 'error', 'data': error_msg})}\n\n"
                    break

        except Exception as e:
            error_msg = f"Demo执行出错: {str(e)}"
            logger.error(f"Demo执行异常: {e}")
            yield f"data: {json.dumps({'type': 'error', 'data': error_msg})}\n\n"

    logger.info("返回StreamingResponse")
    return StreamingResponse(
        generate_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Content-Type": "text/event-stream",
        }
    )


@app.post("/demo/sync")
async def run_demo_sync(request: DemoRequest):
    """
    运行demo并返回同步响应（用于测试）
    """
    global demo_environment_ready

    if not demo_environment_ready:
        raise HTTPException(status_code=503, detail="Demo环境未准备好")

    try:
        logger.info("开始处理同步请求")
        # 捕获输出
        import io
        import contextlib

        output_buffer = io.StringIO()

        with contextlib.redirect_stdout(output_buffer):
            # 创建流式输出管理器
            stream_manager = StreamOutputManager()

            # 初始化LLM处理器和主Agent
            llm_handler = LLMHandler(
                model=request.model, stream_manager=stream_manager)
            main_agent = MainAgent(llm_handler, stream_manager)

            # 运行主Agent
            main_agent.run(request.problem)

        output = output_buffer.getvalue()
        logger.info(f"同步请求完成，输出长度: {len(output)} 字符")

        return {
            "status": "success",
            "output": output,
            "message": "Demo执行完成"
        }

    except Exception as e:
        logger.error(f"同步请求异常: {e}")
        raise HTTPException(status_code=500, detail=f"Demo执行出错: {str(e)}")


@app.get("/demo/workspace")
async def get_workspace_files():
    """
    获取workspace目录中的文件列表
    """
    # 从环境变量获取workspace路径
    workspace_dir = os.getenv("WORKSPACE_DIR")
    if not workspace_dir:
        workspace_dir = os.path.join(os.path.dirname(__file__), "workspace")
        logger.warning("未找到WORKSPACE_DIR环境变量，使用默认路径")

    if not os.path.exists(workspace_dir):
        return {"files": [], "message": "workspace目录不存在"}

    files = []
    for filename in os.listdir(workspace_dir):
        file_path = os.path.join(workspace_dir, filename)
        if os.path.isfile(file_path):
            file_size = os.path.getsize(file_path)
            files.append({
                "name": filename,
                "size": file_size,
                "path": file_path
            })

    logger.info(f"获取workspace文件列表，共 {len(files)} 个文件，路径: {workspace_dir}")
    return {"files": files, "message": "获取文件列表成功"}


@app.get("/demo/workspace/{filename}")
async def download_workspace_file(filename: str):
    """
    下载workspace目录中的文件
    """
    # 从环境变量获取workspace路径
    workspace_dir = os.getenv("WORKSPACE_DIR")
    if not workspace_dir:
        workspace_dir = os.path.join(os.path.dirname(__file__), "workspace")
        logger.warning("未找到WORKSPACE_DIR环境变量，使用默认路径")

    file_path = os.path.join(workspace_dir, filename)

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="文件不存在")

    if not os.path.isfile(file_path):
        raise HTTPException(status_code=400, detail="不是有效的文件")

    logger.info(f"下载文件: {filename}，路径: {file_path}")
    from fastapi.responses import FileResponse
    return FileResponse(file_path, filename=filename)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
