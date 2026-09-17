"""Typed provider contracts, deterministic route resolution, and OpenAI-compatible transport.

This module deliberately contains no scoring, coaching, prompt, or rubric rules.
It converts legacy settings into explicit route contracts and returns normalized
results or stable typed failures before a network call is attempted.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
import json
import logging
import re
import time
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.parse import urlsplit

import requests


DEFAULT_CHAT_ENDPOINT = "https://api.openai.com/v1/chat/completions"
CONNECT_TIMEOUT = 20
READ_TIMEOUT = 300
NETWORK_RETRIES = 1
_logger = logging.getLogger(__name__)


def _record_io_failure(exc: requests.RequestException, phase: str) -> None:
    """Retain exception classes only; messages can contain keys, URLs and text."""
    names = []
    current: BaseException | None = exc
    for _ in range(4):
        if current is None or type(current).__name__ in names:
            break
        names.append(type(current).__name__)
        nested = next((value for value in current.args if isinstance(value, BaseException)), None)
        current = current.__cause__ or current.__context__ or nested
    _logger.warning("provider_io_failure phase=%s exceptions=%s", phase, "/".join(names))


class Capability(str, Enum):
    TEXT_INPUT = "text_input"
    IMAGE_INPUT = "image_input"
    STRUCTURED_OUTPUT = "structured_output"
    STREAMING = "streaming"
    TIMEOUT = "timeout"
    USAGE_REPORTING = "usage_reporting"


class ImageInputPolicy(str, Enum):
    REQUIRED = "required"
    FORBIDDEN = "forbidden"


class ProviderFailureCode(str, Enum):
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    UNKNOWN_ROUTE = "UNKNOWN_ROUTE"
    UNKNOWN_CAPABILITY = "UNKNOWN_CAPABILITY"
    INCOMPATIBLE_ROUTE = "INCOMPATIBLE_ROUTE"
    MISSING_CREDENTIALS = "MISSING_CREDENTIALS"
    MALFORMED_ENDPOINT = "MALFORMED_ENDPOINT"
    MISSING_MODEL = "MISSING_MODEL"
    TIMEOUT = "TIMEOUT"
    NETWORK_ERROR = "NETWORK_ERROR"
    HTTP_ERROR = "HTTP_ERROR"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    SCHEMA_ERROR = "SCHEMA_ERROR"
    EMPTY_RESPONSE = "EMPTY_RESPONSE"
    TRUNCATED_RESPONSE = "TRUNCATED_RESPONSE"
    CONTENT_FILTERED = "CONTENT_FILTERED"


@dataclass(frozen=True)
class ProviderFailure:
    code: ProviderFailureCode
    message: str
    retryable: bool = False


@dataclass(frozen=True)
class ModelCapability:
    """A declared capability set for one configured model, not a quality ranking."""

    model_id: str
    capabilities: frozenset[Capability]

    def supports(self, required: Capability) -> bool:
        return required in self.capabilities


@dataclass(frozen=True)
class ProviderConfigurationSnapshot:
    """Log-safe configuration evidence. It intentionally never stores credentials."""

    route_key: str
    provider_id: str
    model_id: str
    base_url: str
    has_credentials: bool

    def as_log_fields(self) -> dict[str, str | bool]:
        return {
            "route_key": self.route_key,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "base_url": self.base_url,
            "has_credentials": self.has_credentials,
        }


@dataclass(frozen=True)
class ProviderContract:
    provider_id: str
    endpoint: str
    model: ModelCapability
    snapshot: ProviderConfigurationSnapshot
    api_key: str = field(repr=False, compare=False)


@dataclass(frozen=True)
class ModelRoute:
    route_id: str
    task_type: str
    stage: str
    configuration_key: str
    required_capabilities: frozenset[Capability]
    optional: bool = False
    image_policy: ImageInputPolicy = ImageInputPolicy.FORBIDDEN

    def requirements_for(self, requires_image: bool) -> frozenset[Capability]:
        if not requires_image or self.image_policy is ImageInputPolicy.FORBIDDEN:
            return self.required_capabilities
        return self.required_capabilities | frozenset({Capability.IMAGE_INPUT})


@dataclass(frozen=True)
class ProviderUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class ProviderCallRequest:
    messages: Sequence[Mapping[str, Any]]
    display_name: str
    require_json_object: bool = False
    stream: bool = True
    temperature: float = 0.0
    timeout: tuple[int, int] = (CONNECT_TIMEOUT, READ_TIMEOUT)
    max_tokens: int | None = None
    # Services with a semantic repair loop own final JSON validation. JSON mode
    # is still requested from the provider; transport failures remain failures.
    validate_json_object: bool = True
    reasoning_effort: str | None = None


@dataclass(frozen=True)
class ProviderCallResult:
    content: str = ""
    usage: ProviderUsage | None = None
    failure: ProviderFailure | None = None
    attempts: int = 1

    @property
    def ok(self) -> bool:
        return self.failure is None

    @classmethod
    def failed(
        cls, code: ProviderFailureCode, message: str, retryable: bool = False
    ) -> "ProviderCallResult":
        return cls(failure=ProviderFailure(code, message, retryable))


@dataclass(frozen=True)
class RouteResolution:
    route: ModelRoute
    contract: ProviderContract | None = None
    failure: ProviderFailure | None = None

    @property
    def ok(self) -> bool:
        return self.contract is not None and self.failure is None


class ProviderTransport(Protocol):
    def call(self, contract: ProviderContract, request: ProviderCallRequest) -> ProviderCallResult:
        """Return normalized content, usage, or a typed provider failure."""


_REQUIRED_CALL_CAPABILITIES = frozenset(
    {
        Capability.TEXT_INPUT,
        Capability.STRUCTURED_OUTPUT,
        Capability.STREAMING,
        Capability.TIMEOUT,
    }
)
_LEGACY_DECLARED_CAPABILITIES = frozenset(Capability)

ROUTE_TABLE: dict[tuple[str, str], ModelRoute] = {
    (task_type, "main_review"): ModelRoute(
        route_id=f"{task_type}.main_review",
        task_type=task_type,
        stage="main_review",
        configuration_key="primary",
        required_capabilities=_REQUIRED_CALL_CAPABILITIES,
        image_policy=(
            ImageInputPolicy.REQUIRED
            if task_type == "task1"
            else ImageInputPolicy.FORBIDDEN
        ),
    )
    for task_type in ("task1", "task2")
}
ROUTE_TABLE.update(
    {
        (task_type, "syntax_enhancement"): ModelRoute(
            route_id=f"{task_type}.syntax_enhancement",
            task_type=task_type,
            stage="syntax_enhancement",
            configuration_key="syntax_enhancement",
            required_capabilities=_REQUIRED_CALL_CAPABILITIES,
            optional=True,
        )
        for task_type in ("task1", "task2")
    }
)
ROUTE_TABLE[("task2", "task2_understanding")] = ModelRoute(
    route_id="task2.understanding",
    task_type="task2",
    stage="task2_understanding",
    configuration_key="primary",
    required_capabilities=_REQUIRED_CALL_CAPABILITIES,
    image_policy=ImageInputPolicy.FORBIDDEN,
)
ROUTE_TABLE[("task2", "student_evidence")] = ModelRoute(
    route_id="task2.student_evidence",
    task_type="task2",
    stage="student_evidence",
    configuration_key="primary",
    required_capabilities=_REQUIRED_CALL_CAPABILITIES,
    image_policy=ImageInputPolicy.FORBIDDEN,
)
ROUTE_TABLE[("task2", "criterion_scoring")] = ModelRoute(
    route_id="task2.criterion_scoring",
    task_type="task2",
    stage="criterion_scoring",
    configuration_key="primary",
    required_capabilities=_REQUIRED_CALL_CAPABILITIES,
    image_policy=ImageInputPolicy.FORBIDDEN,
)
ROUTE_TABLE[("task1", "chart_facts_extraction")] = ModelRoute(
    route_id="task1.chart_facts_extraction",
    task_type="task1",
    stage="chart_facts_extraction",
    configuration_key="primary",
    required_capabilities=_REQUIRED_CALL_CAPABILITIES,
    image_policy=ImageInputPolicy.REQUIRED,
)
ROUTE_TABLE[("task1", "chart_facts_verification")] = ModelRoute(
    route_id="task1.chart_facts_verification",
    task_type="task1",
    stage="chart_facts_verification",
    configuration_key="primary",
    required_capabilities=_REQUIRED_CALL_CAPABILITIES,
    image_policy=ImageInputPolicy.REQUIRED,
)
ROUTE_TABLE[("task1", "chart_claim_extraction")] = ModelRoute(
    route_id="task1.chart_claim_extraction",
    task_type="task1",
    stage="chart_claim_extraction",
    configuration_key="primary",
    required_capabilities=_REQUIRED_CALL_CAPABILITIES,
    image_policy=ImageInputPolicy.FORBIDDEN,
)
ROUTE_TABLE[("task1", "criterion_scoring")] = ModelRoute(
    route_id="task1.criterion_scoring",
    task_type="task1",
    stage="criterion_scoring",
    configuration_key="primary",
    required_capabilities=_REQUIRED_CALL_CAPABILITIES,
    image_policy=ImageInputPolicy.FORBIDDEN,
)
ROUTE_TABLE.update(
    {
        (task_type, "final_validation"): ModelRoute(
            route_id=f"{task_type}.final_validation",
            task_type=task_type,
            stage="final_validation",
            configuration_key="primary",
            required_capabilities=_REQUIRED_CALL_CAPABILITIES,
            image_policy=(
                ImageInputPolicy.REQUIRED
                if task_type == "task1"
                else ImageInputPolicy.FORBIDDEN
            ),
        )
        for task_type in ("task1", "task2")
    }
)


def route_for(task_type: str, stage: str) -> ModelRoute | None:
    return ROUTE_TABLE.get((task_type, stage))


def endpoint_for(base_url: str) -> str | None:
    base = base_url.strip().rstrip("/")
    if not base:
        return DEFAULT_CHAT_ENDPOINT
    parsed = urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        return None
    if base.endswith("/chat/completions"):
        return base
    if re.search(r"/v\d+$", base):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def resolve_route(
    route: ModelRoute | None,
    *,
    provider_id: str,
    model_id: str,
    api_key: str,
    base_url: str,
    declared_capabilities: frozenset[Capability] = _LEGACY_DECLARED_CAPABILITIES,
    requires_image: bool = False,
) -> RouteResolution:
    if route is None:
        unknown = ModelRoute("unknown", "", "", "", frozenset())
        return RouteResolution(
            route=unknown,
            failure=ProviderFailure(
                ProviderFailureCode.UNKNOWN_ROUTE,
                "未找到可用的 Provider 路由。",
            ),
        )
    snapshot = ProviderConfigurationSnapshot(
        route_key=route.configuration_key,
        provider_id=provider_id,
        model_id=model_id.strip(),
        base_url=base_url.strip(),
        has_credentials=bool(api_key.strip()),
    )
    if not model_id.strip():
        return RouteResolution(
            route,
            failure=ProviderFailure(
                ProviderFailureCode.MISSING_MODEL,
                f"{route.stage} 路由未配置模型。",
            ),
        )
    if not api_key.strip():
        return RouteResolution(
            route,
            failure=ProviderFailure(
                ProviderFailureCode.MISSING_CREDENTIALS,
                f"{route.stage} 路由缺少 API Key。",
            ),
        )
    endpoint = endpoint_for(base_url)
    if endpoint is None:
        return RouteResolution(
            route,
            failure=ProviderFailure(
                ProviderFailureCode.MALFORMED_ENDPOINT,
                f"{route.stage} 路由的 Base URL 无效。",
            ),
        )
    required = route.requirements_for(requires_image)
    unknown = [item for item in required if not isinstance(item, Capability)]
    if unknown:
        return RouteResolution(
            route,
            failure=ProviderFailure(
                ProviderFailureCode.UNKNOWN_CAPABILITY,
                f"{route.stage} 路由声明了未知能力。",
            ),
        )
    missing = required - declared_capabilities
    if missing:
        names = ", ".join(sorted(item.value for item in missing))
        return RouteResolution(
            route,
            failure=ProviderFailure(
                ProviderFailureCode.INCOMPATIBLE_ROUTE,
                f"{route.stage} 路由不满足所需能力：{names}。",
            ),
        )
    capability = ModelCapability(model_id.strip(), declared_capabilities)
    return RouteResolution(
        route,
        contract=ProviderContract(
            provider_id=provider_id,
            endpoint=endpoint,
            model=capability,
            snapshot=snapshot,
            api_key=api_key,
        ),
    )


def provider_preflight(
    settings: Any, task_type: str, requires_image: bool
) -> dict[str, RouteResolution]:
    """Resolve every active stage locally; this function never performs network I/O."""
    results: dict[str, RouteResolution] = {}
    stages = (("task2_understanding", "student_evidence") if task_type == "task2" else ()) + (
        "main_review", "syntax_enhancement", "final_validation",
    )
    for stage in stages:
        route = route_for(task_type, stage)
        if route is None:
            results[stage] = resolve_route(
                None,
                provider_id="",
                model_id="",
                api_key="",
                base_url="",
            )
            continue
        config = settings.config_for_route(task_type, route.configuration_key)
        results[stage] = resolve_route(
            route,
            provider_id="openai-compatible",
            model_id=config.model,
            api_key=config.api_key,
            base_url=config.base_url,
            declared_capabilities=config.declared_capabilities,
            requires_image=requires_image,
        )
    return results


# A descriptive alias retained for callers that only need route resolution.
resolve_active_routes = provider_preflight


class OpenAICompatibleTransport:
    """A normalized transport for the existing OpenAI-compatible HTTP/SSE shape."""

    def __init__(
        self,
        post: Callable[..., requests.Response] = requests.post,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._post = post
        self._sleep = sleep

    def call(self, contract: ProviderContract, request: ProviderCallRequest) -> ProviderCallResult:
        payload: dict[str, Any] = {
            "model": contract.model.model_id,
            "messages": list(request.messages),
            "temperature": request.temperature,
            "stream": request.stream,
        }
        if request.require_json_object:
            payload["response_format"] = {"type": "json_object"}
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens

        if request.reasoning_effort is not None:
            payload["reasoning_effort"] = request.reasoning_effort

        last_failure: ProviderCallResult | None = None
        for attempt in range(NETWORK_RETRIES + 1):
            response: requests.Response | None = None
            try:
                response = self._post(
                    contract.endpoint,
                    headers={
                        "Authorization": f"Bearer {contract.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=request.timeout,
                    stream=request.stream,
                )
                if response.status_code != 200:
                    business_code = None
                    try:
                        error = response.json().get('error', {})
                        if isinstance(error, Mapping): business_code = str(error.get('code', ''))
                    except (ValueError, TypeError, AttributeError, requests.RequestException):
                        pass
                    failure = _http_failure(request.display_name, response.status_code, business_code)
                    if failure.retryable and attempt < NETWORK_RETRIES:
                        last_failure = ProviderCallResult(failure=failure)
                        self._sleep(1)
                        continue
                    return _with_transport_attempts(
                        ProviderCallResult(failure=failure), attempt + 1
                    )
                result = _read_response(response, request)
                if result.ok:
                    return _with_transport_attempts(result, attempt + 1)
                if result.failure and result.failure.retryable and attempt < NETWORK_RETRIES:
                    last_failure = result
                    self._sleep(1)
                    continue
                return _with_transport_attempts(result, attempt + 1)
            except requests.Timeout as exc:
                _record_io_failure(exc, "connect")
                last_failure = ProviderCallResult.failed(
                    ProviderFailureCode.TIMEOUT,
                    f"{request.display_name} 响应超时，系统已自动重试一次但仍未完成。",
                    retryable=True,
                )
                if attempt < NETWORK_RETRIES:
                    self._sleep(1)
                    continue
                return _with_transport_attempts(last_failure, attempt + 1)
            except requests.ConnectionError as exc:
                _record_io_failure(exc, "connect")
                last_failure = ProviderCallResult.failed(
                    ProviderFailureCode.NETWORK_ERROR,
                    f"{request.display_name} 连接中断，系统已自动重试一次。请检查网络后重新批改。",
                    retryable=True,
                )
                if attempt < NETWORK_RETRIES:
                    self._sleep(1)
                    continue
                return _with_transport_attempts(last_failure, attempt + 1)
            except requests.RequestException as exc:
                _record_io_failure(exc, "connect")
                last_failure = ProviderCallResult.failed(
                    ProviderFailureCode.NETWORK_ERROR,
                    f"{request.display_name} 网络请求失败，请检查网络或 API 地址后重试。",
                    retryable=True,
                )
                if attempt < NETWORK_RETRIES:
                    self._sleep(1)
                    continue
                return _with_transport_attempts(last_failure, attempt + 1)
            finally:
                if response is not None:
                    response.close()
        return _with_transport_attempts(
            last_failure or ProviderCallResult.failed(
                ProviderFailureCode.PROVIDER_FAILURE,
                f"{request.display_name} Provider 调用失败。",
            ),
            NETWORK_RETRIES + 1,
        )


def _with_transport_attempts(
    result: ProviderCallResult, attempts: int
) -> ProviderCallResult:
    return replace(result, attempts=attempts)


def _http_failure(display_name: str, status_code: int, business_code: str | None = None) -> ProviderFailure:
    if status_code == 429 and business_code in {'1113', 'insufficient_quota'}:
        return ProviderFailure(ProviderFailureCode.QUOTA_EXCEEDED,
            f"{display_name} 模型账户余额或额度不足，请恢复 API 额度后重试。", retryable=False)
    if status_code in (401, 403):
        return ProviderFailure(
            ProviderFailureCode.HTTP_ERROR,
            f"{display_name} 鉴权失败（{status_code}），请检查 API Key 和额度。",
        )
    if status_code == 429:
        return ProviderFailure(
            ProviderFailureCode.HTTP_ERROR,
            f"{display_name} 请求过于频繁或额度不足（429）。",
            retryable=True,
        )
    return ProviderFailure(
        ProviderFailureCode.HTTP_ERROR,
        f"{display_name} API 错误 {status_code}。",
        retryable=status_code in {408, 500, 502, 503, 504},
    )


def _read_response(response: requests.Response, request: ProviderCallRequest) -> ProviderCallResult:
    # Some compatible gateways return a regular JSON envelope despite stream=true.
    headers = getattr(response, "headers", {})
    media_type = headers.get("Content-Type", "") if isinstance(headers, Mapping) else ""
    streaming = request.stream and "application/json" not in media_type.lower()
    usage: ProviderUsage | None = None
    chunks: list[str] = []
    finish_reason = None

    def consume(event):
        nonlocal usage, finish_reason
        if not isinstance(event, dict):
            raise ValueError("INVALID_ENVELOPE")
        if event.get("error"):
            error = event["error"]
            code = str(error.get("code", "")) if isinstance(error, dict) else ""
            if code in {'1113', 'insufficient_quota'}:
                return ProviderCallResult(failure=_http_failure(request.display_name, 429, code))
            # Only fixed classifications escape; remote messages may echo inputs.
            retryable = code in {"429", "500", "502", "503", "504", "1302", "1305"}
            return ProviderCallResult.failed(ProviderFailureCode.HTTP_ERROR,
                f"{request.display_name} 模型服务返回错误，请稍后重试。", retryable)
        usage = _usage_from(event.get("usage")) or usage
        choices = event.get("choices")
        if not isinstance(choices, list) or not choices:
            return None
        choice = choices[0]
        if not isinstance(choice, dict):
            raise ValueError("INVALID_CHOICE")
        finish_reason = choice.get("finish_reason") or finish_reason
        message = choice.get("delta") or choice.get("message") or {}
        if not isinstance(message, dict):
            raise ValueError("INVALID_MESSAGE")
        content = message.get("content")
        # Reasoning and tool-call arguments never substitute for the answer.
        if isinstance(content, str):
            chunks.append(content)
        return None

    try:
        if streaming:
            pending: list[str] = []
            for raw_line in response.iter_lines(decode_unicode=False):
                line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
                line = line.strip()
                if line.startswith((":", "event:", "id:", "retry:")):
                    continue
                if not line:
                    if pending:
                        raise ValueError("MALFORMED_STREAM_EVENT")
                    continue
                if line.startswith("data:"):
                    line = line[5:].strip()
                if line == "[DONE]":
                    if pending:
                        raise ValueError("MALFORMED_STREAM_EVENT")
                    # A keepalive socket can remain open or reset after [DONE].
                    break
                pending.append(line)
                try:
                    event = json.loads("\n".join(pending))
                except json.JSONDecodeError:
                    continue
                pending.clear()
                failure = consume(event)
                if failure:
                    return failure
                if finish_reason is not None:
                    # An explicit terminal choice also completes the answer;
                    # gateways may reset the connection before the [DONE] frame.
                    break
            if pending:
                raise ValueError("MALFORMED_STREAM_EVENT")
        else:
            failure = consume(response.json())
            if failure:
                return failure
    except requests.Timeout as exc:
        _record_io_failure(exc, "response")
        return ProviderCallResult.failed(ProviderFailureCode.TIMEOUT,
            f"{request.display_name} 响应读取超时。", retryable=True)
    except (requests.ConnectionError, requests.exceptions.ChunkedEncodingError) as exc:
        _record_io_failure(exc, "response")
        return ProviderCallResult.failed(ProviderFailureCode.NETWORK_ERROR,
            f"{request.display_name} 响应传输中断，正在恢复连接。", retryable=True)
    except requests.RequestException as exc:
        _record_io_failure(exc, "response")
        return ProviderCallResult.failed(ProviderFailureCode.NETWORK_ERROR,
            f"{request.display_name} 响应读取失败。", retryable=True)
    except (ValueError, TypeError, UnicodeError):
        return ProviderCallResult.failed(ProviderFailureCode.SCHEMA_ERROR,
            f"{request.display_name} 返回了无效的响应数据。")

    if finish_reason in {"length", "max_tokens"}:
        return ProviderCallResult.failed(ProviderFailureCode.TRUNCATED_RESPONSE,
            f"{request.display_name} 达到输出长度上限，内容未生成完整。")
    if finish_reason in {"content_filter", "sensitive"}:
        return ProviderCallResult.failed(ProviderFailureCode.CONTENT_FILTERED,
            f"{request.display_name} 被模型服务的内容过滤规则中止。")
    content = "".join(chunks).strip()
    if not content:
        return ProviderCallResult.failed(ProviderFailureCode.EMPTY_RESPONSE,
            f"{request.display_name} 返回了空内容，请重试。")
    if request.require_json_object:
        # Unwrap only a single complete JSON code fence, never arbitrary prose,
        # guessed braces, repaired values, or multiple competing objects.
        fenced = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", content, re.S | re.I)
        if fenced:
            content = fenced.group(1).strip()
        if not request.validate_json_object:
            return ProviderCallResult(content=content, usage=usage)
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return ProviderCallResult.failed(ProviderFailureCode.SCHEMA_ERROR,
                f"{request.display_name} 未返回有效 JSON 对象。")
        if not isinstance(parsed, dict):
            return ProviderCallResult.failed(ProviderFailureCode.SCHEMA_ERROR,
                f"{request.display_name} 未返回 JSON 对象。")
    return ProviderCallResult(content=content, usage=usage)


def _usage_from(value: Any) -> ProviderUsage | None:
    if not isinstance(value, Mapping):
        return None

    def integer(name: str) -> int | None:
        item = value.get(name)
        return item if isinstance(item, int) else None

    return ProviderUsage(
        prompt_tokens=integer("prompt_tokens"),
        completion_tokens=integer("completion_tokens"),
        total_tokens=integer("total_tokens"),
    )
