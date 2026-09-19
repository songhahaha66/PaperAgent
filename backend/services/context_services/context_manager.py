"""
智能上下文管理器
提供上下文压缩、摘要生成、长期记忆等功能
"""

import logging
import json
import re
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


@dataclass
class ContextSummary:
    """上下文摘要数据结构"""
    summary_id: str
    session_id: str
    content: str
    key_topics: List[str]
    important_points: List[str]
    created_at: datetime
    message_count: int
    token_estimate: int


@dataclass
class CompressedMessage:
    """压缩后的消息结构"""
    role: str
    content: str
    original_length: int
    compressed_length: int
    compression_ratio: float
    is_compressed: bool


class ContextManager:
    """
    智能上下文管理器
    负责对话上下文的压缩、摘要生成和长期记忆管理
    """
    
    def __init__(self, max_tokens: int = 20000, max_messages: int = 50):
        self.max_tokens = max_tokens
        self.max_messages = max_messages
        self.summaries: List[ContextSummary] = []
        self.compression_history: List[CompressedMessage] = []
        
        # 压缩策略配置
        self.compression_strategies = {
            'high': {'ratio': 0.3, 'priority': 'key_info'},
            'medium': {'ratio': 0.5, 'priority': 'balanced'},
            'low': {'ratio': 0.7, 'priority': 'preserve_context'}
        }
        
        logger.info(f"ContextManager初始化完成，最大token数: {max_tokens}, 最大消息数: {max_messages}")
    
    def estimate_tokens(self, text: str) -> int:
        """
        估算文本的token数量
        使用简单的字符数除以4的估算方法（适用于英文）
        中文按字符数计算
        """
        # 简单的token估算：英文按4字符/token，中文按1字符/token
        english_chars = len(re.findall(r'[a-zA-Z]', text))
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
        other_chars = len(text) - english_chars - chinese_chars
        
        estimated_tokens = (english_chars // 4) + chinese_chars + (other_chars // 4)
        return max(1, estimated_tokens)
    
    def extract_key_topics(self, messages: List[Dict[str, Any]]) -> List[str]:
        """
        从消息中提取关键主题
        基于用户问题和AI回答的关键词分析
        """
        topics = set()
        
        for msg in messages:
            if msg.get('role') == 'user':
                content = msg.get('content', '')
                # 提取用户问题中的关键词
                keywords = self._extract_keywords(content)
                topics.update(keywords)
            elif msg.get('role') == 'assistant':
                content = msg.get('content', '')
                # 提取AI回答中的关键概念
                concepts = self._extract_concepts(content)
                topics.update(concepts)
        
        return list(topics)[:10]  # 限制主题数量
    
    def _extract_keywords(self, text: str) -> List[str]:
        """提取文本中的关键词"""
        # 简单的关键词提取：去除停用词，提取名词短语
        stop_words = {'的', '是', '在', '有', '和', '与', '或', '但', '而', 'the', 'is', 'in', 'and', 'or', 'but'}
        
        # 分割文本
        words = re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z]+', text.lower())
        
        # 过滤停用词和短词
        keywords = [word for word in words if word not in stop_words and len(word) > 1]
        
        return keywords[:5]  # 返回前5个关键词
    
    def _extract_concepts(self, text: str) -> List[str]:
        """提取文本中的关键概念"""
        # 提取可能的概念词（通常是较长的词或专业术语）
        concepts = re.findall(r'[\u4e00-\u9fff]{2,}|[a-zA-Z]{6,}', text)
        return concepts[:5]
    
    def compress_context(self, messages: List[Dict[str, Any]], strategy: str = 'medium') -> Tuple[List[Dict[str, Any]], List[CompressedMessage]]:
        """
        智能压缩上下文
        根据策略压缩消息，保留关键信息
        """
        if len(messages) <= self.max_messages:
            return messages, []
        
        logger.info(f"开始压缩上下文，原始消息数: {len(messages)}")
        
        compressed_messages = []
        compression_results = []
        
        # 保留system message
        if messages and messages[0].get('role') == 'system':
            compressed_messages.append(messages[0])
        
        # 获取压缩策略
        compression_config = self.compression_strategies.get(strategy, self.compression_strategies['medium'])
        target_ratio = compression_config['ratio']
        
        # 计算需要保留的消息数量
        target_count = max(5, int(len(messages) * target_ratio))
        
        # 保留最新的消息
        recent_messages = messages[-target_count:]
        
        # 压缩中间的消息
        if len(messages) > target_count + 1:  # +1 for system message
            middle_messages = messages[1:-target_count]
            
            # 生成中间部分的摘要
            summary = self._generate_middle_summary(middle_messages)
            
            # 创建摘要消息
            summary_message = {
                "role": "system",
                "content": f"[上下文摘要] {summary}",
                "metadata": {
                    "is_summary": True,
                    "original_count": len(middle_messages),
                    "compression_ratio": target_ratio
                }
            }
            
            compressed_messages.append(summary_message)
            
            # 记录压缩结果
            compression_results.append(CompressedMessage(
                role="system",
                content=summary,
                original_length=sum(len(msg.get('content', '')) for msg in middle_messages),
                compressed_length=len(summary),
                compression_ratio=len(summary) / max(1, sum(len(msg.get('content', '')) for msg in middle_messages)),
                is_compressed=True
            ))
        
        # 添加最近的消息
        compressed_messages.extend(recent_messages)
        
        logger.info(f"上下文压缩完成，压缩后消息数: {len(compressed_messages)}")
        return compressed_messages, compression_results
    
    def _generate_middle_summary(self, messages: List[Dict[str, Any]]) -> str:
        """
        为中间消息生成摘要
        基于消息内容和角色生成简洁的摘要
        """
        if not messages:
            return "无中间对话内容"
        
        # 统计用户问题和AI回答
        user_questions = [msg.get('content', '') for msg in messages if msg.get('role') == 'user']
        ai_answers = [msg.get('content', '') for msg in messages if msg.get('role') == 'assistant']
        
        summary_parts = []
        
        if user_questions:
            # 提取用户问题的关键信息
            key_questions = self._extract_key_questions(user_questions)
            summary_parts.append(f"用户讨论了: {key_questions}")
        
        if ai_answers:
            # 提取AI回答的关键信息
            key_answers = self._extract_key_answers(ai_answers)
            summary_parts.append(f"AI提供了: {key_answers}")
        
        return "；".join(summary_parts) if summary_parts else "进行了多轮对话讨论"
    
    def _extract_key_questions(self, questions: List[str]) -> str:
        """提取用户问题的关键信息"""
        if not questions:
            return ""
        
        # 合并所有问题，提取共同主题
        combined_text = " ".join(questions)
        
        # 提取关键词
        keywords = self._extract_keywords(combined_text)
        
        if len(questions) == 1:
            return f"'{questions[0][:50]}...'"
        else:
            return f"{len(questions)}个相关问题，涉及{', '.join(keywords[:3])}"
    
    def _extract_key_answers(self, answers: List[str]) -> str:
        """提取AI回答的关键信息"""
        if not answers:
            return ""
        
        # 合并所有回答，提取关键概念
        combined_text = " ".join(answers)
        concepts = self._extract_concepts(combined_text)
        
        if len(answers) == 1:
            return f"'{answers[0][:50]}...'"
        else:
            return f"{len(answers)}个回答，涉及{', '.join(concepts[:3])}"
    
    async def generate_context_summary(self, messages: List[Dict[str, Any]], session_id: str) -> ContextSummary:
        """
        生成上下文摘要
        基于当前对话内容生成结构化的摘要
        """
        logger.info(f"开始生成上下文摘要，消息数: {len(messages)}")
        
        # 提取关键主题
        key_topics = self.extract_key_topics(messages)
        
        # 提取重要信息点
        important_points = self._extract_important_points(messages)
        
        # 估算token数量
        total_content = " ".join([msg.get('content', '') for msg in messages])
        token_estimate = self.estimate_tokens(total_content)
        
        # 生成摘要内容
        summary_content = self._format_summary_content(messages, key_topics, important_points)
        
        # 创建摘要对象
        summary = ContextSummary(
            summary_id=f"summary_{session_id}_{int(datetime.now().timestamp())}",
            session_id=session_id,
            content=summary_content,
            key_topics=key_topics,
            important_points=important_points,
            created_at=datetime.now(),
            message_count=len(messages),
            token_estimate=token_estimate
        )
        
        # 保存摘要
        self.summaries.append(summary)
        
        logger.info(f"上下文摘要生成完成，摘要ID: {summary.summary_id}")
        return summary
    
    def _extract_important_points(self, messages: List[Dict[str, Any]]) -> List[str]:
        """提取重要信息点"""
        important_points = []
        
        for msg in messages:
            if msg.get('role') == 'assistant':
                content = msg.get('content', '')
                
                # 提取可能的重要信息（如结论、结果、建议等）
                if any(keyword in content.lower() for keyword in ['结论', '结果', '建议', '总结', 'conclusion', 'result', 'recommendation', 'summary']):
                    # 提取包含关键词的句子
                    sentences = re.split(r'[。！？.!?]', content)
                    for sentence in sentences:
                        if any(keyword in sentence.lower() for keyword in ['结论', '结果', '建议', '总结', 'conclusion', 'result', 'recommendation', 'summary']):
                            important_points.append(sentence.strip()[:100])
                            break
        
        return important_points[:5]  # 限制重要点数量
    
    def _format_summary_content(self, messages: List[Dict[str, Any]], key_topics: List[str], important_points: List[str]) -> str:
        """格式化摘要内容"""
        summary_parts = []
        
        # 对话概览
        user_count = sum(1 for msg in messages if msg.get('role') == 'user')
        ai_count = sum(1 for msg in messages if msg.get('role') == 'assistant')
        summary_parts.append(f"对话概览：共{user_count}轮用户提问，{ai_count}轮AI回答")
        
        # 关键主题
        if key_topics:
            summary_parts.append(f"关键主题：{', '.join(key_topics)}")
        
        # 重要信息点
        if important_points:
            summary_parts.append(f"重要信息：{' | '.join(important_points)}")
        
        # 最近讨论内容
        recent_messages = messages[-3:]  # 最近3条消息
        if recent_messages:
            recent_content = []
            for msg in recent_messages:
                role = "用户" if msg.get('role') == 'user' else "AI"
                content = msg.get('content', '')[:50]
                recent_content.append(f"{role}: {content}...")
            summary_parts.append(f"最近讨论：{' | '.join(recent_content)}")
        
        return "；".join(summary_parts)
    
    def get_context_status(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """获取上下文状态信息"""
        total_tokens = sum(self.estimate_tokens(msg.get('content', '')) for msg in messages)
        
        return {
            "message_count": len(messages),
            "estimated_tokens": total_tokens,
            "token_usage_ratio": total_tokens / self.max_tokens,
            "compression_needed": total_tokens > self.max_tokens,
            "compression_ratio": min(1.0, self.max_tokens / max(1, total_tokens)),
            "last_summary_count": len(self.summaries),
            "compression_history_count": len(self.compression_history)
        }
    
    def get_long_term_memory(self, session_id: str, limit: int = 5) -> List[ContextSummary]:
        """获取长期记忆（历史摘要）"""
        session_summaries = [s for s in self.summaries if s.session_id == session_id]
        return sorted(session_summaries, key=lambda x: x.created_at, reverse=True)[:limit]
    
    def cleanup_old_summaries(self, max_age_days: int = 7):
        """清理过期的摘要"""
        cutoff_date = datetime.now() - timedelta(days=max_age_days)
        old_summaries = [s for s in self.summaries if s.created_at < cutoff_date]
        
        for summary in old_summaries:
            self.summaries.remove(summary)
        
        logger.info(f"清理了 {len(old_summaries)} 个过期摘要")
    
    def export_context_data(self) -> Dict[str, Any]:
        """导出上下文数据（用于调试和分析）"""
        return {
            "summaries": [
                {
                    "summary_id": s.summary_id,
                    "session_id": s.session_id,
                    "content": s.content,
                    "key_topics": s.key_topics,
                    "important_points": s.important_points,
                    "created_at": s.created_at.isoformat(),
                    "message_count": s.message_count,
                    "token_estimate": s.token_estimate
                }
                for s in self.summaries
            ],
            "compression_history": [
                {
                    "role": c.role,
                    "content": c.content,
                    "original_length": c.original_length,
                    "compressed_length": c.compressed_length,
                    "compression_ratio": c.compression_ratio,
                    "is_compressed": c.is_compressed
                }
                for c in self.compression_history
            ],
            "config": {
                "max_tokens": self.max_tokens,
                "max_messages": self.max_messages
            }
        }
