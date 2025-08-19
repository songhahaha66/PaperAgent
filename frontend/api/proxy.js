export default async function handler(req, res) {
  try {
    // 目标后端地址
    const targetUrl = `http://118.178.124.124:8000${req.url}`;

    // 读取请求体（支持 JSON、文本、文件等）
    let body;
    if (req.method !== 'GET' && req.method !== 'HEAD') {
      body = await new Promise((resolve, reject) => {
        const chunks = [];
        req.on('data', chunk => chunks.push(chunk));
        req.on('end', () => resolve(Buffer.concat(chunks)));
        req.on('error', err => reject(err));
      });
    }

    // 转发请求，过滤掉有问题的头
    const filteredHeaders = Object.fromEntries(
      Object.entries(req.headers).filter(([k]) =>
        !['host', 'content-length', 'connection'].includes(k.toLowerCase())
      )
    );

    const response = await fetch(targetUrl, {
      method: req.method,
      headers: filteredHeaders,
      body: body && body.length > 0 ? body : undefined
    });

    // 转发响应头
    response.headers.forEach((value, key) => {
      if (!['transfer-encoding', 'content-encoding', 'connection'].includes(key)) {
        res.setHeader(key, value);
      }
    });

    // 返回内容
    const contentType = response.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      const data = await response.json();
      res.status(response.status).json(data);
    } else {
      const data = await response.arrayBuffer();
      res.status(response.status).send(Buffer.from(data));
    }

  } catch (err) {
    console.error(err);
    res.status(500).json({ error: 'Proxy error', message: err.message });
  }
}
