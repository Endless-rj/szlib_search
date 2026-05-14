/**
 * Cloudflare Worker - 深圳图书馆 API 代理
 *
 * 部署方法：
 * 1. 打开 https://workers.cloudflare.com
 * 2. 用 GitHub/Google 登录（免费，不要信用卡）
 * 3. Create Worker → 粘贴本文件 → Save and Deploy
 * 4. 得到地址如: https://szlib-proxy.你的名字.workers.dev
 *
 * 然后在 Railway 设置环境变量:
 *   PROXY_URL = https://szlib-proxy.你的名字.workers.dev
 */

export default {
  async fetch(request, env, ctx) {
    // 处理 CORS 预检请求
    if (request.method === 'OPTIONS') {
      return new Response(null, {
        headers: {
          'Access-Control-Allow-Origin': '*',
          'Access-Control-Allow-Methods': 'GET, OPTIONS',
          'Access-Control-Allow-Headers': '*',
          'Access-Control-Max-Age': '86400',
        },
      });
    }

    // 解析请求路径
    const url = new URL(request.url);
    const targetPath = url.pathname + url.search;

    // 构建目标 URL
    const targetUrl = 'https://www.szlib.org.cn' + targetPath;

    try {
      // 转发请求到深圳图书馆
      const headers = new Headers(request.headers);
      headers.set('Host', 'www.szlib.org.cn');
      headers.set('Referer', 'https://www.szlib.org.cn/opac/searchShow');
      headers.set('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36');
      headers.set('Accept', 'application/json, text/plain, */*');
      headers.set('Accept-Language', 'zh-CN,zh;q=0.9');

      const response = await fetch(targetUrl, {
        method: request.method,
        headers: headers,
      });

      // 复制响应并添加 CORS 头
      const newResponse = new Response(response.body, {
        status: response.status,
        statusText: response.statusText,
      });

      // 复制原始响应头
      for (const [key, value] of response.headers.entries()) {
        newResponse.headers.set(key, value);
      }

      // 添加 CORS 头
      newResponse.headers.set('Access-Control-Allow-Origin', '*');
      newResponse.headers.set('Access-Control-Allow-Methods', 'GET, OPTIONS');

      return newResponse;

    } catch (error) {
      return new Response(JSON.stringify({
        error: 'Proxy request failed',
        detail: error.message,
        target: targetUrl,
      }), {
        status: 502,
        headers: {
          'Content-Type': 'application/json',
          'Access-Control-Allow-Origin': '*',
        },
      });
    }
  },
};