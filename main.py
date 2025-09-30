import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
from typing import Optional

from curl_cffi import AsyncSession, Response
from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from loguru import logger
from starlette.middleware.cors import CORSMiddleware

from app.config import SCRIPT_URL, FP, API_KEY, MODELS, SYSTEM_PROMPT_INJECT, TIMEOUT, PROXY, USER_PROMPT_INJECT, \
    X_IS_HUMAN_SERVER_URL
from app.errors import CursorWebError
from app.models import ChatCompletionRequest, Message, ModelsResponse, Model, Usage, OpenAIMessageContent
from app.utils import error_wrapper, to_async, generate_random_string, non_stream_chat_completion, \
    stream_chat_completion, safe_stream_wrapper

main_code = open('./jscode/main.js', 'r', encoding='utf-8').read()
env_code = open('./jscode/env.js', 'r', encoding='utf-8').read()
app = FastAPI()

security = HTTPBearer()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/v1/chat/completions")
async def chat_completions(
        request: ChatCompletionRequest,
        credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """处理聊天完成请求"""

    if credentials.credentials != API_KEY:
        raise HTTPException(401, 'api key 错误')

    chat_generator = cursor_chat(request)
    # async for c in chat_generator:
    #     logger.debug(c)

    if request.stream:
        return await error_wrapper(safe_stream_wrapper, stream_chat_completion, request, chat_generator)
    else:
        return await error_wrapper(non_stream_chat_completion, request, chat_generator)


@app.get("/v1/models")
async def list_models(credentials: HTTPAuthorizationCredentials = Depends(security)):
    models = MODELS.split(',')
    model_list = []

    for model_id in models:
        model_list.append(
            Model(
                id=model_id,  # 使用model name作为对外的id
                object="model",
                created=int(time.time()),
                owned_by='',
            )
        )

    return ModelsResponse(object="list", data=model_list)


def inject_system_prompt(list_openai_message: list[Message], inject_prompt: str):
    # 查找是否存在system角色的消息
    system_message_found = False

    for message in list_openai_message:
        if message.role == "system":
            system_message_found = True
            # 处理content字段，需要考虑不同的数据类型
            if message.content is None:
                message.content = inject_prompt
            elif isinstance(message.content, str):
                message.content += f'\n{inject_prompt}'
            elif isinstance(message.content, list):
                # 如果content是列表，需要找到text类型的内容进行追加
                # 或者添加一个新的text内容项
                text_content_found = False
                for content_item in message.content:
                    if content_item.type == "text" and content_item.text:
                        content_item.text += f'\n{inject_prompt}'
                        text_content_found = True
                        break

                # 如果没有找到text内容，添加一个新的text内容项
                if not text_content_found:
                    new_text_content = OpenAIMessageContent(
                        type="text",
                        text=inject_prompt
                        , image_url=None)
                    message.content.append(new_text_content)
            break  # 找到第一个system消息后就退出循环

    # 如果没有找到system消息，在列表开头插入一个新的system消息
    if not system_message_found:
        system_message = Message(
            role="system",
            content=inject_prompt
            , tool_call_id=None, tool_calls=None)
        list_openai_message.insert(0, system_message)


def collect_developer_messages(list_openai_message: list[Message]) -> str:
    collected_contents = []

    # 从后往前遍历，避免删除元素时索引变化的问题
    for i in range(len(list_openai_message) - 1, -1, -1):
        message = list_openai_message[i]

        if message.role == "developer":
            # 提取消息内容
            content_text = ""

            if message.content is None:
                content_text = ""
            elif isinstance(message.content, str):
                content_text = message.content
            elif isinstance(message.content, list):
                # 如果content是列表，提取所有text类型的内容
                text_parts = []
                for content_item in message.content:
                    if content_item.type == "text" and content_item.text:
                        text_parts.append(content_item.text)
                content_text = " ".join(text_parts)  # 多个text内容用空格连接

            # 将内容添加到收集列表的开头，保持原始顺序
            collected_contents.insert(0, content_text)

            # 删除该消息
            list_openai_message.pop(i)

    # 将收集到的内容按\n拼接并返回
    return "\n".join(collected_contents)


def to_cursor_messages(list_openai_message: list[Message]):
    if list_openai_message is None:
        list_openai_message = []

    developer_messages = collect_developer_messages(list_openai_message)
    inject_system_prompt(list_openai_message, developer_messages)
    if SYSTEM_PROMPT_INJECT:
        inject_system_prompt(list_openai_message, SYSTEM_PROMPT_INJECT)
    if USER_PROMPT_INJECT:
        list_openai_message.append(Message(role='user', content=USER_PROMPT_INJECT, tool_calls=None, tool_call_id=None))

    result: list[dict[str, str]] = []

    for m in list_openai_message:
        if not m:
            continue
        text = ''
        if isinstance(m.content, str):
            text = m.content
        else:
            for content in m.content:
                if not content.text:
                    continue
                text = text + content.text
        message = {
            'role': m.role,
            'parts': [{
                'type': 'text',
                'text': text
            }]
        }
        result.append(message)

    if result[0]['role'] == 'system' and not result[0]['parts'][0]['text']:
        result.pop(0)

    return result


def parse_sse_line(line: str) -> Optional[str]:
    """解析SSE数据行"""
    line = line.strip()
    if line.startswith("data: "):
        return line[6:]  # 去掉 'data: ' 前缀
    return None


async def cursor_chat(request: ChatCompletionRequest):
    json_data = {
        "context": [

        ],
        "model": request.model,
        "id": generate_random_string(16),
        "messages": to_cursor_messages(request.messages),
        "trigger": "submit-message"
    }
    async with AsyncSession(impersonate='chrome', timeout=TIMEOUT, proxy=PROXY) as session:
        if X_IS_HUMAN_SERVER_URL:
            x_is_human = await get_x_is_human_server(session)
        else:
            x_is_human = await get_x_is_human(session)
        logger.debug(x_is_human)
        headers = {
            'User-Agent': FP.get("userAgent"),
            # 'Accept-Encoding': 'gzip, deflate, br, zstd',
            'Content-Type': 'application/json',
            'sec-ch-ua-platform': '"Windows"',
            'x-path': '/api/chat',
            'sec-ch-ua': '"Chromium";v="140", "Not=A?Brand";v="24", "Google Chrome";v="140"',
            'x-method': 'POST',
            'sec-ch-ua-bitness': '"64"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-arch': '"x86"',
            'x-is-human': x_is_human,
            'sec-ch-ua-platform-version': '"19.0.0"',
            'origin': 'https://cursor.com',
            'sec-fetch-site': 'same-origin',
            'sec-fetch-mode': 'cors',
            'sec-fetch-dest': 'empty',
            'referer': 'https://cursor.com/en-US/learn/how-ai-models-work',
            'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'priority': 'u=1, i',
        }
        logger.debug(json_data)
        async with session.stream("POST", 'https://cursor.com/api/chat', headers=headers, json=json_data,
                                  impersonate='chrome') as response:
            response: Response
            # logger.debug(await response.atext())

            if response.status_code != 200:
                text = await response.atext()
                if 'Attention Required! | Cloudflare' in text:
                    text = 'Cloudflare 403'
                raise CursorWebError(response.status_code, text)
            content_type = response.headers['content-type']
            if 'text/event-stream' not in content_type:
                text = await response.atext()
                raise CursorWebError(response.status_code, "响应非事件流: " + text)
            async for line in response.aiter_lines():
                line = line.decode("utf-8")
                logger.debug(line)
                data = parse_sse_line(line)
                if not data:
                    continue
                if data and data.strip():
                    try:
                        event_data = json.loads(data)
                        if event_data.get('type') == 'error':
                            err_msg = event_data.get('errorText', 'errorText为空')
                            if 'The content field in the Message object at' in err_msg:
                                err_msg = "消息为空，很可能你的消息只包含图片，本接口不支持图片\n" + err_msg
                            raise CursorWebError(response.status_code, err_msg)
                        if event_data.get('type') == 'finish':
                            usage = event_data.get('messageMetadata', {}).get('usage')
                            if not usage:
                                continue
                            yield Usage(prompt_tokens=usage.get('inputTokens'),
                                        completion_tokens=usage.get('outputTokens'),
                                        total_tokens=usage.get('totalTokens'))
                            return
                        delta = event_data.get('delta')
                        # logger.debug(delta)
                        if not delta:
                            continue
                        yield delta
                    except json.JSONDecodeError:
                        continue


async def get_x_is_human_server(session: AsyncSession):
    headers = {
        'User-Agent': FP.get("userAgent"),
        # 'Accept-Encoding': 'gzip, deflate, br, zstd',
        'sec-ch-ua-arch': '"x86"',
        'sec-ch-ua-platform': '"Windows"',
        'sec-ch-ua': '"Chromium";v="140", "Not=A?Brand";v="24", "Google Chrome";v="140"',
        'sec-ch-ua-bitness': '"64"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform-version': '"19.0.0"',
        'sec-fetch-site': 'same-origin',
        'sec-fetch-mode': 'no-cors',
        'sec-fetch-dest': 'script',
        'referer': 'https://cursor.com/en-US/learn/how-ai-models-work',
        'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8',
    }

    response = await session.get(SCRIPT_URL,
                                 headers=headers,
                                 impersonate='chrome')
    cursor_js = response.text
    js_b64 = base64.b64encode(cursor_js.encode('utf-8')).decode("utf-8")

    response = await session.post(X_IS_HUMAN_SERVER_URL, json={
        "jscode": js_b64,
        "fp": FP
    })
    try:
        s = response.json().get('s')
    except json.decoder.JSONDecodeError:
        raise CursorWebError(response.status_code, '纯算服务器返回结果错误: ' + response.text)
    if not s:
        raise CursorWebError(response.status_code, '纯算服务器返回结果错误: ' + response.text)

    return response.text


async def get_x_is_human(session: AsyncSession):
    headers = {
        'User-Agent': FP.get("userAgent"),
        # 'Accept-Encoding': 'gzip, deflate, br, zstd',
        'sec-ch-ua-arch': '"x86"',
        'sec-ch-ua-platform': '"Windows"',
        'sec-ch-ua': '"Chromium";v="140", "Not=A?Brand";v="24", "Google Chrome";v="140"',
        'sec-ch-ua-bitness': '"64"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform-version': '"19.0.0"',
        'sec-fetch-site': 'same-origin',
        'sec-fetch-mode': 'no-cors',
        'sec-fetch-dest': 'script',
        'referer': 'https://cursor.com/en-US/learn/how-ai-models-work',
        'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8',
    }

    response = await session.get(SCRIPT_URL,
                                 headers=headers,
                                 impersonate='chrome')
    cursor_js = response.text

    # 替换指纹
    main = (main_code.replace("$$currentScriptSrc$$", SCRIPT_URL)
            .replace("$$UNMASKED_VENDOR_WEBGL$$", FP.get("UNMASKED_VENDOR_WEBGL"))
            .replace("$$UNMASKED_RENDERER_WEBGL$$", FP.get("UNMASKED_RENDERER_WEBGL"))
            .replace("$$userAgent$$", FP.get("userAgent")))

    # 替换代码
    main = main.replace('$$env_jscode$$', env_code)
    main = main.replace("$$cursor_jscode$$", cursor_js)
    return await runjs(main)


@to_async
def runjs(jscode: str) -> str:
    """
    执行 JavaScript 代码并返回标准输出内容。

    Args:
        jscode: 要执行的 JavaScript 代码字符串

    Returns:
        Node.js 程序的标准输出内容

    Raises:
        FileNotFoundError: Node.js 未安装或不在系统 PATH 中
        subprocess.CalledProcessError: Node.js 程序执行失败，异常信息包含 stdout 和 stderr
    """
    temp_dir = tempfile.mkdtemp()
    try:
        js_file_path = os.path.join(temp_dir, "script.js")
        with open(js_file_path, "w", encoding="utf-8") as f:
            f.write(jscode)

        result = subprocess.run(
            ['node', js_file_path],
            capture_output=True,
            text=True,
            encoding="utf-8"
        )

        if result.returncode != 0:
            error_msg = f"Node.js 执行失败 (退出码: {result.returncode})\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            logger.error(error_msg)
            raise subprocess.CalledProcessError(result.returncode, ['node', js_file_path], result.stdout, result.stderr)

        return result.stdout.strip()
    finally:
        shutil.rmtree(temp_dir)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )
