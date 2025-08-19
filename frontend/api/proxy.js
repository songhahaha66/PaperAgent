import fetch from 'node-fetch';

export default async function handler(req, res) {
  try {
    // 构造目标 URL，包括查询参数
    const query = req.url.includes('?') ? '' : req.url.split('?')[1] || '';
    const targetUrl = `http://118.178.124.124:8000${req.url}${query ? '?' + query : ''}`;

    // 构造请求体
    let body;
    if (req.method !== 'GET' && req.method !== 'HEAD') {
      body = req.body;
      // 如果是 JSON，需要转换为字符串
      if (req.headers['content-type']?.includes('application/json') && typeof body !== 'string') {
        body = JSON.stringify(body);
      }
    }

    // 转发请求
    const response = await fetch(targetUrl, {
      method: req.method,
      headers: {
        ...req.headers,
        host: undefined, // 避免 host 冲突
      },
      body,
    });

    // 设置响应头
    response.headers.forEach((value, key) => {
      // 避免某些受限头导致 Vercel 报错
      if (!['transfer-encoding', 'content-encoding', 'connection'].includes(key)) {
        res.setHeader(key, value);
      }
    });

    // 返回响应状态码和内容
    const contentType = response.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      const data = await response.json();
      res.status(response.status).json(data);
    } else {
      const data = await response.text();
      res.status(response.status).send(data);
    }

  } catch (err) {
    console.error(err);
    res.status(500).send({ error: 'Proxy error' });
  }
}
