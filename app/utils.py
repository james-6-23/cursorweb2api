import asyncio
import base64
import json
import random
import string
import time
import uuid
from functools import wraps
from typing import Union, Callable, Any, AsyncGenerator, Dict

from curl_cffi.requests.exceptions import RequestException
from sse_starlette import EventSourceResponse
from starlette.responses import JSONResponse

from app.errors import CursorWebError
from app.models import ChatCompletionRequest


async def safe_stream_wrapper(
        generator_func, *args, **kwargs
) -> Union[EventSourceResponse, JSONResponse]:
    """
    安全的流响应包装器
    先执行生成器获取第一个值，如果成功才创建流响应
    """
    # 创建生成器实例
    generator = generator_func(*args, **kwargs)

    # 尝试获取第一个值
    first_item = await generator.__anext__()

    # 如果成功获取第一个值，创建新的生成器包装原生成器
    async def wrapped_generator():
        # 先yield第一个值
        yield first_item
        # 然后yield剩余的值
        async for item in generator:
            yield item

    # 创建流响应
    return EventSourceResponse(
        wrapped_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def error_wrapper(func: Callable, *args, **kwargs) -> Any:
    from .config import MAX_RETRIES
    for attempt in range(MAX_RETRIES + 1):  # 包含初始尝试，所以是 MAX_RETRIES + 1
        try:
            return await func(*args, **kwargs)
        except (CursorWebError, RequestException) as e:

            # 如果已经达到最大重试次数，返回错误响应
            if attempt == MAX_RETRIES:
                if isinstance(e, CursorWebError):
                    return JSONResponse(
                        e.to_openai_error(),
                        status_code=e.response_status_code
                    )
                elif isinstance(e, RequestException):
                    return JSONResponse(
                        {
                            'error': {
                                'message': str(e),
                                "type": "http_error",
                                "code": "http_error"
                            }
                        },
                        status_code=500
                    )

            if attempt < MAX_RETRIES:
                continue
    return None


def decode_base64url_safe(data):
    """使用安全的base64url解码"""
    # 添加必要的填充
    missing_padding = len(data) % 4
    if missing_padding:
        data += '=' * (4 - missing_padding)

    return base64.urlsafe_b64decode(data)


def to_async(sync_func):
    @wraps(sync_func)
    async def async_wrapper(*args):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, sync_func, *args)

    return async_wrapper


def generate_random_string(length):
    """
    生成一个指定长度的随机字符串，包含大小写字母和数字。
    """
    # 定义所有可能的字符：大小写字母和数字
    characters = string.ascii_letters + string.digits

    # 使用 random.choice 从字符集中随机选择字符，重复 length 次，然后拼接起来
    random_string = ''.join(random.choice(characters) for _ in range(length))
    return random_string


async def non_stream_chat_completion(
        request: ChatCompletionRequest,
        generator: AsyncGenerator[str, None]
) -> Dict[str, Any]:
    """
    非流式响应：接受外部异步生成器，收集所有输出返回完整响应
    """
    # 收集所有流式输出
    full_content = ""
    async for chunk in generator:
        full_content += chunk

    # 构造OpenAI格式的响应
    response = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:29]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": request.model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": full_content
                },
                "finish_reason": "stop"
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0
        }
    }

    return response


async def stream_chat_completion(
        request: ChatCompletionRequest,
        generator: AsyncGenerator[str, None]
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    流式响应：接受外部异步生成器，包装成OpenAI SSE格式
    """
    chat_id = f"chatcmpl-{uuid.uuid4().hex[:29]}"
    created_time = int(time.time())

    is_send_init = False

    # 发送初始流式响应头
    initial_response = {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": created_time,
        "model": request.model,
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": ""},
                "finish_reason": None
            }
        ]
    }


    # 流式发送内容
    async for chunk in generator:
        if not is_send_init:
            yield {
                "data": json.dumps(initial_response, ensure_ascii=False)
            }
            is_send_init = True
        chunk_response = {
            "id": chat_id,
            "object": "chat.completion.chunk",
            "created": created_time,
            "model": request.model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": chunk},
                    "finish_reason": None
                }
            ]
        }
        yield {"data": json.dumps(chunk_response, ensure_ascii=False)}

    # 发送结束标记
    final_response = {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": created_time,
        "model": request.model,
        "choices": [
            {
                "index": 0,
                "delta": {},
                "finish_reason": "stop"
            }
        ]
    }
    yield {"data": json.dumps(final_response, ensure_ascii=False)}
    yield {"data": "[DONE]"}
