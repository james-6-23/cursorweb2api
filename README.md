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
      - "8080:8080"
    volumes:
      - ./config:/app/config
    environment:
      - API_KE=aaa
      - FP=eyJVTk1BU0tFRF9WRU5ET1JfV0VCR0wiOiJHb29nbGUgSW5jLiAoSW50ZWwpIiwiVU5NQVNLRURfUkVOREVSRVJfV0VCR0wiOiJBTkdMRSAoSW50ZWwsIEludGVsKFIpIFVIRCBHcmFwaGljcyAoMHgwMDAwOUJBNCkgRGlyZWN0M0QxMSB2c181XzAgcHNfNV8wLCBEM0QxMS0yNi4yMC4xMDAuNzk4NSkiLCJ1c2VyQWdlbnQiOiJNb3ppbGxhLzUuMCAoV2luZG93cyBOVCAxMC4wOyBXaW42NDsgeDY0KSBBcHBsZVdlYktpdC81MzcuMzYgKEtIVE1MLCBsaWtlIEdlY2tvKSBDaHJvbWUvMTM5LjAuMC4wIFNhZmFyaS81MzcuMzYifQ
      - SCRIPT_URL=https://cursor.com/149e9513-01fa-4fb0-aad4-566afd725d1b/2d206a39-8ed7-437e-a3be-862e0f06eea3/a-4-a/c.js?i=0&v=3&h=cursor.com
      - MODELS=claude-sonnet-4-20250514,claude-opus-4-1-20250805,claude-opus-4-20250514,gpt-5,gemini-2.5-pro,deepseek-v3.1
    restart: unless-stopped
```

## 🎯 特性

- ✅ 完全兼容 OpenAI API 格式
- ✅ 支持流式和非流式响应

## 环境变量配置

| 环境变量         | 默认值                                                    | 说明                   |
|--------------|--------------------------------------------------------|----------------------|
| `FP`         | `...`                                                  | 浏览器指纹                |
| `SCRIPT_URL` | `https://cursor.com/149e9513-0...`                     | 反爬动态js url           |
| `API_KEY`    | `aaa`                                                  | 接口鉴权的api key，将其改为随机值 |
| `MODELS`     | `claude-sonnet-4-20250514,claude-opus-4-1-20250805...` | 模型列表，用,号分隔           |


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