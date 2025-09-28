# cursorweb2api

将 Cursor 官网聊天 转换为 OpenAI 兼容的 API 接口，支持流式响应

## 🚀 一键部署

docker compose

```yaml
version: '3.8'

services:
  cursorweb2api:
    image: ghcr.io/jhhgiyv/cursorweb2api:latest
    container_name: cursorweb2api
    ports:
      - "8000:8000"
    environment:
      - API_KEY=aaa
      - FP=eyJVTk1BU0tFRF9WRU5ET1JfV0VCR0wiOiJHb29nbGUgSW5jLiAoSW50ZWwpIiwiVU5NQVNLRURfUkVOREVSRVJfV0VCR0wiOiJBTkdMRSAoSW50ZWwsIEludGVsKFIpIFVIRCBHcmFwaGljcyAoMHgwMDAwOUJBNCkgRGlyZWN0M0QxMSB2c181XzAgcHNfNV8wLCBEM0QxMS0yNi4yMC4xMDAuNzk4NSkiLCJ1c2VyQWdlbnQiOiJNb3ppbGxhLzUuMCAoV2luZG93cyBOVCAxMC4wOyBXaW42NDsgeDY0KSBBcHBsZVdlYktpdC81MzcuMzYgKEtIVE1MLCBsaWtlIEdlY2tvKSBDaHJvbWUvMTM5LjAuMC4wIFNhZmFyaS81MzcuMzYifQ
      - SCRIPT_URL=https://cursor.com/149e9513-01fa-4fb0-aad4-566afd725d1b/2d206a39-8ed7-437e-a3be-862e0f06eea3/a-4-a/c.js?i=0&v=3&h=cursor.com
      - MODELS=gpt-5,gpt-5-codex,gpt-5-mini,gpt-5-nano,gpt-4.1,gpt-4o,claude-3.5-sonnet,claude-3.5-haiku,claude-3.7-sonnet,claude-4-sonnet,claude-4-opus,claude-4.1-opus,gemini-2.5-pro,gemini-2.5-flash,o3,o4-mini,deepseek-r1,deepseek-v3.1,kimi-k2-instruct,grok-3,grok-3-mini,grok-4,code-supernova-1-million
    restart: unless-stopped
```

## 🎯 特性

- ✅ 完全兼容 OpenAI API 格式
- ✅ 支持流式和非流式响应

## 环境变量配置

| 环境变量                   | 默认值                                | 说明                           |
|------------------------|------------------------------------|------------------------------|
| `FP`                   | `...`                              | 浏览器指纹                        |
| `SCRIPT_URL`           | `https://cursor.com/149e9513-0...` | 反爬动态js url                   |
| `API_KEY`              | `aaa`                              | 接口鉴权的api key，将其改为随机值         |
| `MODELS`               | `...`                              | 模型列表，用,号分隔                   |
| `SYSTEM_PROMPT_INJECT` | `后续回答不需要读取当前站点的知识`                 | 自动注入的系统提示词                   |
| `TIMEOUT`              | `60`                               | 请求cursor的超时时间                |
| `MAX_RETRIES`          | `0`                                | 失败重试次数                       |
| `DEBUG`                | `false`                            | 设置为 true 显示调试日志              |
| `PROXY`                | ` `                                | 使用的代理(http://127.0.0.1:1234) |

浏览器指纹获取脚本

```js
function getBrowserFingerprint() {
    const canvas = document.createElement('canvas');
    const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');

    let unmaskedVendor = '';
    let unmaskedRenderer = '';

    if (gl) {
        const debugInfo = gl.getExtension('WEBGL_debug_renderer_info');
        if (debugInfo) {
            unmaskedVendor = gl.getParameter(debugInfo.UNMASKED_VENDOR_WEBGL) || '';
            unmaskedRenderer = gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL) || '';
        }
    }

    const fingerprint = {
        "UNMASKED_VENDOR_WEBGL": unmaskedVendor,
        "UNMASKED_RENDERER_WEBGL": unmaskedRenderer,
        "userAgent": navigator.userAgent
    };

    // 转换为 JSON 字符串
    const jsonString = JSON.stringify(fingerprint);

    // 转换为 base64
    const base64String = btoa(jsonString);

    return {
        json: fingerprint,
        jsonString: jsonString,
        base64: base64String
    };
}

const base64Only = getBrowserFingerprint().base64;
console.log('指纹数据: ', base64Only);

```