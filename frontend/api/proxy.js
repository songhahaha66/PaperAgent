export default async function handler(req, res) {
  const target = "http://118.178.124.124:8000" + req.url.replace(/^\/api/, "");

  try {
    const response = await fetch(target, {
      method: req.method,
      headers: {
        ...req.headers,
        host: undefined  // 避免 Vercel 的 host 传过去
      },
      body: req.method === "GET" || req.method === "HEAD" ? undefined : req.body
    });

    // 透传返回内容
    const data = await response.arrayBuffer();
    res.status(response.status);
    response.headers.forEach((value, key) => {
      if (key.toLowerCase() !== "content-encoding") {
        res.setHeader(key, value);
      }
    });
    res.send(Buffer.from(data));
  } catch (err) {
    res.status(500).json({ error: "Proxy failed", detail: err.toString() });
  }
}
