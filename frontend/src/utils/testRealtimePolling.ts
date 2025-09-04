/**
 * 测试实时轮询功能
 * 这个文件用于验证轮询API是否正常工作
 */

import { chatAPI } from '@/api/chat';

export async function testRealtimePolling(token: string, workId: string) {
  console.log('🧪 开始测试实时轮询功能...');
  
  try {
    // 测试获取实时消息
    console.log('1. 测试获取实时消息...');
    const realtimeResponse = await chatAPI.getRealtimeMessages(token, workId, 0);
    console.log('实时消息响应:', realtimeResponse);
    
    // 测试获取临时消息
    console.log('2. 测试获取临时消息...');
    const tempResponse = await chatAPI.getTempMessage(token, workId);
    console.log('临时消息响应:', tempResponse);
    
    // 测试轮询更新
    console.log('3. 测试轮询更新...');
    let lastModified = realtimeResponse.last_modified;
    
    for (let i = 0; i < 5; i++) {
      await new Promise(resolve => setTimeout(resolve, 1000)); // 等待1秒
      
      const pollResponse = await chatAPI.getRealtimeMessages(token, workId, lastModified);
      console.log(`轮询 ${i + 1}:`, {
        has_updates: pollResponse.has_updates,
        is_generating: pollResponse.is_generating,
        is_complete: pollResponse.is_complete,
        last_modified: pollResponse.last_modified
      });
      
      if (pollResponse.has_updates) {
        lastModified = pollResponse.last_modified;
        console.log('检测到更新!');
      }
    }
    
    console.log('✅ 实时轮询功能测试完成');
    
  } catch (error) {
    console.error('❌ 实时轮询功能测试失败:', error);
    throw error;
  }
}

// 在浏览器控制台中使用的测试函数
(window as any).testRealtimePolling = testRealtimePolling;
