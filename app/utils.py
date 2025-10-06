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
from app.models import ChatCompletionRequest, Usage, ToolCall, Message


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


def normalize_tool_name(name: str) -> str:
    """将工具名统一标准化：将所有下划线替换为连字符"""
    return name.replace('_', '-')


def match_tool_name(tool_name: str, available_tools: list[str]) -> str:
    """
    匹配工具名称，如果不在列表中则尝试标准化匹配

    Args:
        tool_name: 需要匹配的工具名
        available_tools: 可用的工具名列表

    Returns:
        匹配到的实际工具名，如果没有匹配返回原名称
    """
    # 直接匹配
    if tool_name in available_tools:
        return tool_name

    # 标准化后匹配
    normalized_input = normalize_tool_name(tool_name)
    for available_tool in available_tools:
        if normalize_tool_name(available_tool) == normalized_input:
            return available_tool

    # 没有匹配，返回原名称
    return tool_name


async def non_stream_chat_completion(
        request: ChatCompletionRequest,
        generator: AsyncGenerator[str, None]
) -> Dict[str, Any]:
    """
    非流式响应：接受外部异步生成器，收集所有输出返回完整响应
    """
    # 收集所有流式输出
    full_content = ""
    tool_calls = []
    usage = Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0)
    async for chunk in generator:
        if isinstance(chunk, Usage):
            usage = chunk
            continue
        if isinstance(chunk, ToolCall):
            tool_calls.append({
                "id": chunk.toolId,
                "type": "function",
                "function": {
                    "name": chunk.toolName,
                    "arguments": chunk.toolInput,
                }
            })
            continue
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
                    "content": full_content,
                    "tool_calls": tool_calls
                },
                "finish_reason": "stop"
            }
        ],
        "usage": {
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens
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
    usage = None
    tool_call_idx = 0
    async for chunk in generator:
        if not is_send_init:
            yield {
                "data": json.dumps(initial_response, ensure_ascii=False)
            }
            is_send_init = True
        if isinstance(chunk, Usage):
            usage = chunk
            continue

        if isinstance(chunk, ToolCall):
            data = {
                "id": chat_id,
                "object": "chat.completion.chunk",
                "created": created_time,
                "model": request.model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": tool_call_idx,
                                    "id": chunk.toolId,
                                    "type": "function",
                                    "function": {
                                        "name": chunk.toolName,
                                        "arguments": chunk.toolInput,
                                    },
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ],
            }
            tool_call_idx += 1
            yield {'data': json.dumps(data, ensure_ascii=False)}
            continue

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
    if usage:
        usage_data = {"id": chat_id, "object": "chat.completion.chunk",
                      "created": created_time, "model": request.model,
                      "choices": [],
                      "usage": {"prompt_tokens": usage.prompt_tokens,
                                "completion_tokens": usage.completion_tokens,
                                "total_tokens": usage.total_tokens, "prompt_tokens_details": {
                              "cached_tokens": 0,
                              "text_tokens": 0,
                              "audio_tokens": 0,
                              "image_tokens": 0
                          },
                                "completion_tokens_details": {
                                    "text_tokens": 0,
                                    "audio_tokens": 0,
                                    "reasoning_tokens": 0
                                },
                                "input_tokens": 0,
                                "output_tokens": 0,
                                "input_tokens_details": None}
                      }

        yield {
            "data": json.dumps(usage_data, ensure_ascii=False)
        }
    yield {"data": "[DONE]"}


async def empty_retry_wrapper(
        cursor_chat_func: Callable,
        request: ChatCompletionRequest,
        max_retries: int = 3
) -> AsyncGenerator[Union[str, Usage, ToolCall], None]:
    """
    空回复重试包装器:检测到空回复时自动重试

    Args:
        cursor_chat_func: cursor_chat函数
        request: 聊天请求
        max_retries: 最大重试次数

    Yields:
        str/Usage/ToolCall: 流式输出

    Raises:
        CursorWebError: 重试后仍然空回复
    """
    for retry_count in range(max_retries + 1):
        generator = cursor_chat_func(request)
        has_content = False

        async for chunk in generator:
            if isinstance(chunk, ToolCall):
                # 工具调用算有内容
                has_content = True
                yield chunk
                return

            elif isinstance(chunk, Usage):
                # Usage直接透传
                yield chunk

            else:
                # 文本内容
                has_content = True
                yield chunk

        # 如果有内容,正常返回
        if has_content:
            return

        # 没有内容且还有重试次数,继续重试
        if retry_count < max_retries:
            continue

    # 达到最大重试次数仍然空回复,抛出异常
    raise CursorWebError(200, f"空回复重试{max_retries}次后仍然失败")


async def truncation_continue_wrapper(
        cursor_chat_func: Callable,
        request: ChatCompletionRequest,
        max_retries: int = 10
) -> AsyncGenerator[Union[str, Usage, ToolCall], None]:
    """
    截断继续包装器:实时流式输出,检测到截断时自动重试

    Args:
        cursor_chat_func: cursor_chat函数
        request: 聊天请求
        max_retries: 最大重试次数

    Yields:
        str/Usage/ToolCall: 流式输出
    """
    full_content = ""  # 累积的完整内容
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_tokens = 0
    current_usage = None

    for retry_count in range(max_retries + 1):
        generator = cursor_chat_func(request)
        current_content = ""  # 当前轮次的内容
        is_truncated = False
        buffer = ""  # 缓冲区,仅在重试时使用
        buffer_yielded = False  # 标记是否已经处理并输出过缓冲区

        async for chunk in generator:
            if isinstance(chunk, Usage):
                current_usage = chunk
                # 累加token统计
                total_prompt_tokens += chunk.prompt_tokens
                total_completion_tokens += chunk.completion_tokens
                total_tokens += chunk.total_tokens

                # 检查是否截断
                is_truncated = chunk.completion_tokens == 4096
                break

            elif isinstance(chunk, ToolCall):
                # 工具调用直接返回
                yield chunk
                return

            else:
                # 文本内容
                current_content += chunk

                if retry_count == 0:
                    # 第一次请求,实时输出
                    yield chunk
                else:
                    # 重试时,使用缓冲区
                    buffer += chunk
                    last_10_chars = full_content[-10:] if len(full_content) >= 10 else full_content

                    if not buffer_yielded:
                        # 检查缓冲区是否包含last_10_chars
                        if last_10_chars and last_10_chars in buffer:
                            # 找到匹配,移除并输出剩余部分
                            buffer = buffer.replace(last_10_chars, "", 1)
                            if buffer:
                                yield buffer
                            buffer = ""
                            buffer_yielded = True
                        elif len(buffer) > 20:
                            # 缓冲区超过20字符还没匹配,直接输出
                            yield buffer
                            buffer = ""
                            buffer_yielded = True
                    else:
                        # 已经处理过缓冲区,直接实时输出
                        yield chunk
                        buffer = ""

        # 处理流结束后的缓冲区
        if retry_count > 0 and buffer:
            last_10_chars = full_content[-10:] if len(full_content) >= 10 else full_content
            if not buffer_yielded and last_10_chars and last_10_chars in buffer:
                buffer = buffer.replace(last_10_chars, "", 1)
            if buffer:
                yield buffer

        # 更新累积内容
        full_content += current_content

        # 检查是否被截断
        if not is_truncated:
            # 未被截断,返回最终usage
            if current_usage:
                yield current_usage
            return

            # 被截断,构造继续对话
        last_10_chars = full_content[-10:] if len(full_content) >= 10 else full_content
        continue_prompt = f'''你的回复在"{last_10_chars}"处意外中断。

        请直接从该处继续输出，遵循以下规则：
        1. 以"{last_10_chars}"开头，紧接新内容
        2. 若在代码块中，直接续写代码，禁止重复```标记或语言标识
        3. 保持原有的格式、缩进和上下文

        错误示例：截断于"document."
        ❌ ```javascript\nlet a=1;\ndocument.createElement...

        正确示例：
        ✅ document.createElement...

        立即继续，不要解释或重新开始。'''

        # 重新构造上下文
        new_messages = request.messages.copy()
        new_messages.append(Message(role="assistant", content=full_content, tool_calls=None, tool_call_id=None))
        new_messages.append(Message(role="user", content=continue_prompt, tool_calls=None, tool_call_id=None))

        request = ChatCompletionRequest(
            messages=new_messages,
            stream=request.stream,
            model=request.model,
            tools=request.tools
        )

    # 达到最大重试次数,返回最终usage

    if current_usage:
        yield current_usage
