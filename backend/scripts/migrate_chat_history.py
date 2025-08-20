#!/usr/bin/env python3
"""
聊天记录格式迁移脚本
将旧格式的聊天记录转换为新的JSON卡片格式
"""

from services.chat_history_manager import ChatHistoryManager
import json
import os
import sys
import logging
from pathlib import Path
from typing import Dict, List, Optional
import uuid
from datetime import datetime

# 添加项目根目录到Python路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def migrate_work_history(work_id: str, workspace_base: str = "../pa_data/workspaces") -> bool:
    """迁移单个work的聊天记录"""
    try:
        history_manager = ChatHistoryManager(workspace_base)

        # 检查文件是否存在
        history_file = history_manager._get_history_file_path(work_id)
        if not os.path.exists(history_file):
            logger.info(f"聊天记录文件不存在: {work_id}")
            return False

        # 读取现有历史记录
        with open(history_file, 'r', encoding='utf-8') as f:
            history = json.load(f)

        # 检查是否已经是新格式
        if history.get("version") == "2.0":
            logger.info(f"聊天记录已经是新格式: {work_id}")
            return True

        logger.info(f"开始迁移聊天记录: {work_id}")

        # 执行迁移
        history_manager.migrate_old_format(work_id)

        logger.info(f"聊天记录迁移完成: {work_id}")
        return True

    except Exception as e:
        logger.error(f"迁移聊天记录失败 {work_id}: {e}")
        return False


def migrate_all_works(workspace_base: str = "../pa_data/workspaces") -> Dict[str, bool]:
    """迁移所有work的聊天记录"""
    results = {}

    try:
        workspace_path = Path(workspace_base)
        if not workspace_path.exists():
            logger.error(f"工作空间目录不存在: {workspace_base}")
            return results

        # 遍历所有work目录
        for work_dir in workspace_path.iterdir():
            if work_dir.is_dir():
                work_id = work_dir.name
                chat_file = work_dir / "chat_history.json"

                if chat_file.exists():
                    logger.info(f"发现聊天记录: {work_id}")
                    success = migrate_work_history(work_id, workspace_base)
                    results[work_id] = success
                else:
                    logger.info(f"未发现聊天记录: {work_id}")
                    results[work_id] = False

        return results

    except Exception as e:
        logger.error(f"迁移所有works失败: {e}")
        return results


def get_migration_statistics(workspace_base: str = "../pa_data/workspaces") -> Dict:
    """获取迁移统计信息"""
    stats = {
        "total_works": 0,
        "migrated_works": 0,
        "failed_works": 0,
        "already_new_format": 0,
        "no_chat_history": 0,
        "details": {}
    }

    try:
        workspace_path = Path(workspace_base)
        if not workspace_path.exists():
            return stats

        history_manager = ChatHistoryManager(workspace_base)

        for work_dir in workspace_path.iterdir():
            if work_dir.is_dir():
                work_id = work_dir.name
                stats["total_works"] += 1

                chat_file = work_dir / "chat_history.json"
                if not chat_file.exists():
                    stats["no_chat_history"] += 1
                    stats["details"][work_id] = "no_chat_history"
                    continue

                try:
                    # 读取历史记录
                    with open(chat_file, 'r', encoding='utf-8') as f:
                        history = json.load(f)

                    if history.get("version") == "2.0":
                        stats["already_new_format"] += 1
                        stats["details"][work_id] = "already_new_format"
                    else:
                        # 统计消息数量
                        messages = history.get("messages", [])
                        stats["details"][work_id] = {
                            "format": "old",
                            "message_count": len(messages),
                            "needs_migration": True
                        }

                except Exception as e:
                    stats["failed_works"] += 1
                    stats["details"][work_id] = f"error: {str(e)}"

        return stats

    except Exception as e:
        logger.error(f"获取迁移统计信息失败: {e}")
        return stats


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="聊天记录格式迁移工具")
    parser.add_argument("--workspace", default="../pa_data/workspaces",
                        help="工作空间目录路径")
    parser.add_argument("--work-id", help="指定要迁移的work ID")
    parser.add_argument("--all", action="store_true", help="迁移所有works")
    parser.add_argument("--stats", action="store_true", help="显示迁移统计信息")
    parser.add_argument("--dry-run", action="store_true", help="仅显示统计信息，不执行迁移")

    args = parser.parse_args()

    if args.stats or args.dry_run:
        logger.info("获取迁移统计信息...")
        stats = get_migration_statistics(args.workspace)

        print("\n=== 迁移统计信息 ===")
        print(f"总works数量: {stats['total_works']}")
        print(f"已有聊天记录: {stats['total_works'] - stats['no_chat_history']}")
        print(f"无聊天记录: {stats['no_chat_history']}")
        print(f"已经是新格式: {stats['already_new_format']}")
        print(
            f"需要迁移: {sum(1 for detail in stats['details'].values() if isinstance(detail, dict) and detail.get('needs_migration'))}")

        print("\n=== 详细信息 ===")
        for work_id, detail in stats["details"].items():
            if isinstance(detail, dict) and detail.get("needs_migration"):
                print(f"{work_id}: {detail['message_count']} 条消息需要迁移")
            else:
                print(f"{work_id}: {detail}")

        if args.dry_run:
            print("\n这是预览模式，未执行实际迁移")
            return

    if args.work_id:
        # 迁移指定的work
        logger.info(f"迁移指定的work: {args.work_id}")
        success = migrate_work_history(args.work_id, args.workspace)
        if success:
            logger.info(f"迁移成功: {args.work_id}")
        else:
            logger.error(f"迁移失败: {args.work_id}")
            sys.exit(1)

    elif args.all:
        # 迁移所有works
        logger.info("开始迁移所有works...")
        results = migrate_all_works(args.workspace)

        success_count = sum(1 for success in results.values() if success)
        total_count = len(results)

        print(f"\n=== 迁移结果 ===")
        print(f"总计: {total_count}")
        print(f"成功: {success_count}")
        print(f"失败: {total_count - success_count}")

        if total_count - success_count > 0:
            print("\n失败的works:")
            for work_id, success in results.items():
                if not success:
                    print(f"  - {work_id}")

    else:
        print("请指定 --work-id 或 --all 参数")
        print("使用 --stats 查看统计信息")
        print("使用 --dry-run 预览迁移")


if __name__ == "__main__":
    main()
