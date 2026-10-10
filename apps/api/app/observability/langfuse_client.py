"""Langfuse 客户端：生命周期、降级策略、trace_id 规范化。

三条设计约束（M0-8 定下，后续里程碑一直受用）：

1. **可降级**。观测是横切能力，不该成为可用性的单点。未配置 key 时 SDK 根本不初始化，
   所有辅助函数退化成 no-op —— 业务链路照常跑，只是没有 trace。
2. **trace_id 复用请求 ID**。`TraceIdMiddleware` 已经为每个请求确定了 `X-Request-Id`，
   这里把它作为 Langfuse 的 trace_id，于是「前端日志 → 后端日志 → LLM 调用」共用一个 ID，
   M8-1 直接就能按这个 ID 回放整条链路。
3. **不向外暴露 SDK**。业务代码只用 `get_observability()` 与 `span()` / `generation()`，
   换观测后端只改本文件。

关于 trace_id 格式：Langfuse 要求 32 位小写十六进制（W3C trace id）。
前端 `crypto.randomUUID()` 是 36 位带横线的 UUID，**去掉横线正好 32 位**，因此优先走这条路；
不是这个形状的输入则退化为 md5（确定性，同一个输入永远得到同一个 ID，便于按 ID 反查）。
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import re
import uuid
from collections.abc import Iterator
from functools import lru_cache
from typing import Any

import httpx
from langfuse import Langfuse

from app import __version__
from app.config import Settings, get_settings

logger = logging.getLogger("app.observability")

_ZERO_TRACE_ID = "0" * 32
_NON_HEX = re.compile(r"[^0-9a-fA-F]")


class SafeObservation:
    """SDK export/update failures must never change a business result."""

    def __init__(self, observation: Any) -> None:
        self.observation = observation

    def update(self, **kwargs: Any) -> None:
        try:
            self.observation.update(**kwargs)
        except Exception:
            logger.warning("langfuse update failed", exc_info=True)

    def update_trace(self, **kwargs: Any) -> None:
        try:
            self.observation.update_trace(**kwargs)
        except Exception:
            logger.warning("langfuse trace update failed", exc_info=True)


def normalize_trace_id(raw: str | None = None) -> str:
    """把任意请求 ID 规整为 Langfuse 能接受的 32 位小写十六进制 trace_id。

    - UUID（带横线）：去横线后正好 32 位十六进制 —— 最理想的输入，直接用
    - 其他字符串：取 md5，保证「同一输入 → 同一 trace_id」
    - 空输入：随机生成
    """
    candidate = (raw or "").strip()
    if not candidate:
        return uuid.uuid4().hex

    cleaned = _NON_HEX.sub("", candidate).lower()
    # 全 0 是非法 trace id，落到 md5 分支
    if len(cleaned) == 32 and cleaned != _ZERO_TRACE_ID:
        return cleaned
    return hashlib.md5(candidate.encode("utf-8")).hexdigest()


class Observability:
    """Langfuse 包装。`enabled=False` 时全部方法退化为安全 no-op。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: Langfuse | None = None

        if not settings.langfuse_configured:
            logger.info(
                "langfuse 未配置（缺 LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY），观测降级为 no-op"
            )
            return

        try:
            self._client = Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                host=settings.langfuse_host,
                environment=settings.environment,
                release=__version__,
                tracing_enabled=True,
                httpx_client=httpx.Client(follow_redirects=True, timeout=5),
            )
            logger.info(
                "langfuse 已启用 host=%s env=%s",
                settings.langfuse_host,
                settings.environment,
            )
        except Exception:
            # 初始化失败也不抛：观测挂了不能把服务带下水
            logger.exception("langfuse 初始化失败，观测降级为 no-op")
            self._client = None

    # ------------------------------------------------------------ 状态
    @property
    def enabled(self) -> bool:
        return self._client is not None

    @property
    def host(self) -> str:
        return self.settings.langfuse_host

    def auth_check(self) -> bool:
        """探测 Langfuse 是否可达且 key 有效。仅用于自检接口，失败不抛。"""
        if self._client is None:
            return False
        try:
            return bool(self._client.auth_check())
        except Exception:
            logger.warning("langfuse auth_check 失败（实例未起或 key 不对）", exc_info=True)
            return False

    def trace_url(self, trace_id: str) -> str | None:
        """拼出 Langfuse UI 里该 trace 的详情页地址。

        刻意**不用** SDK 的 `get_trace_url()`：它会先调 `self.api.projects.get()`
        拉项目列表来解析 baseUrl，而本地实例对 `/api/public/projects` 返回 308 重定向，
        SDK 的 httpx 客户端不跟随 → 抛 ApiError，URL 永远为 None。
        项目 ID 是启动时由 LANGFUSE_INIT_PROJECT_ID 固定下来的，直接拼更稳。
        """
        if self._client is None:
            return None
        host = self.settings.langfuse_host.rstrip("/")
        project_id = self.settings.langfuse_project_id
        return f"{host}/project/{project_id}/traces/{trace_id}"

    # ------------------------------------------------ trace / span 辅助
    @contextlib.contextmanager
    def _observation(self, **kwargs: Any) -> Iterator[Any | None]:
        if self._client is None:
            yield None
            return
        try:
            scope = self._client.start_as_current_observation(**kwargs)
            observation = SafeObservation(scope.__enter__())
        except Exception:
            logger.warning("langfuse observation failed; continuing", exc_info=True)
            yield None
            return
        try:
            yield observation
        finally:
            try:
                scope.__exit__(None, None, None)
            except Exception:
                logger.warning("langfuse observation close failed", exc_info=True)

    #
    # 统一用 `start_as_current_observation(as_type=...)`：
    # langfuse 3.x 正在陆续废弃 `start_as_current_span` / `start_as_current_generation`
    # 这类专用方法（后者在 3.15 已发 DeprecationWarning，本项目把告警当错误处理，
    # 一调用就炸），而 `start_as_current_observation` 是它们共同的前进方向。
    @contextlib.contextmanager
    def span(
        self,
        name: str,
        *,
        trace_id: str | None = None,
        input: Any = None,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> Iterator[Any | None]:
        """开一个 span（作为 trace 的根）。未启用时 yield None。"""
        if self._client is None:
            yield None
            return

        # 兜底规范化：Langfuse SDK 拿 trace_id 做 int(id, 16)，带横线的 UUID / 任意字符串
        # 会直接 ValueError（且 SDK 先 warn「非法」再照用，自己不防）。
        # 这里统一转成 32 位 hex，调用方忘了 normalize 也不会把请求打成 500。
        trace_context = {"trace_id": normalize_trace_id(trace_id)} if trace_id else None
        with self._observation(
            name=name,
            as_type="span",
            trace_context=trace_context,
            input=input,
            metadata=metadata,
        ) as span:
            if tags and span is not None:
                span.update_trace(tags=tags)
            yield span

    @contextlib.contextmanager
    def generation(
        self,
        name: str,
        *,
        model: str | None = None,
        input: Any = None,
        metadata: dict[str, Any] | None = None,
        model_parameters: dict[str, Any] | None = None,
    ) -> Iterator[Any | None]:
        """开一个 generation observation（LLM 调用专用）。未启用时 yield None。"""
        if self._client is None:
            yield None
            return

        with self._observation(
            name=name,
            as_type="generation",
            model=model,
            input=input,
            metadata=metadata,
            model_parameters=model_parameters,
        ) as generation:
            yield generation

    # ---------------------------------------------------------- 生命周期
    def flush(self) -> None:
        """把缓冲中的 span 推给 Langfuse。

        SDK 是异步批量上报的；开发期调用量极低，若不等 flush，
        刚发的 trace 在 UI 里可能几十秒后才出现，容易误判成「没上报成功」。
        """
        if self._client is None:
            return
        try:
            self._client.flush()
        except Exception:
            logger.warning("langfuse flush 失败", exc_info=True)

    def shutdown(self) -> None:
        if self._client is None:
            return
        try:
            self._client.shutdown()
        except Exception:
            logger.warning("langfuse shutdown 失败", exc_info=True)
        finally:
            self._client = None


@lru_cache(maxsize=1)
def get_observability() -> Observability:
    return Observability(get_settings())
