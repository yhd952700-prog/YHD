"""First-generation timeout honesty: warm-up, retry-once, and correct copy.

The defect
----------
A local Ollama model that takes ~36 s to load was killed by a 30 s read
timeout on the FIRST generation. The UI then said "check your API Key and
network" even though the configuration was perfectly correct. The user had to
press send again to get a reply.

What this file pins down
------------------------
* the generation read timeout defaults to 120 s and the first-token wait has its
  own (longer) threshold **only when the timeout is not configured**;
* only timeout/connection failures are retried, and only once -- a 401/400 is
  a permanent answer that must not burn retries;
* a timeout shorter than 5 s (``DEFAULT_RETRY_MIN_TIMEOUT``) is not retried at
  all: the user already said "fail fast", and one retry turns 2 s into ~5 s;
* the streamed retry is VISIBLE (a ``notice`` event) instead of a silent gap;
* a timeout never turns into "your API Key is invalid" copy, and the failure
  names the actual model.
"""

from __future__ import annotations

import ssl
import time

import pytest

from src.ai.net_guard import RuntimeTargetRejected
from src.ai.providers import (
    BaseProvider,
    CONTEXT_LENGTH_EXCEEDED_MESSAGE,
    DEFAULT_RETRY_MIN_TIMEOUT,
    INVALID_KEY_MESSAGE,
    ProviderHTTPError,
    ProviderTimeoutError,
    RETRY_MIN_TIMEOUT_ENV,
    RETRY_NOTICE_MESSAGE,
    TIMEOUT_MESSAGE,
    _is_retryable_failure,
    _iter_sse_text,
    classify_upstream_error,
    provider_from_config,
    user_facing_failure,
)


# --------------------------------------------------------------------------- #
# 1. Copy: a timeout is not a key error, and must name the model
# --------------------------------------------------------------------------- #
class TestTimeoutCopy:
    def test_timeout_does_not_point_at_the_api_key(self):
        text = user_facing_failure(
            ProviderTimeoutError("模型服务", 120.0, before_first_token=True),
            model="qwen2.5:7b",
        )
        assert TIMEOUT_MESSAGE in text
        assert "首次加载较慢" in text
        # 配置完全正确也会超时：不能再把用户引去查 Key/网络。
        assert "Key" not in text
        assert "密钥" not in text
        assert "网络连接" not in text
        assert "qwen2.5:7b" in text, "失败文案必须说明是哪个模型"

    def test_three_timeout_variants_have_distinct_copy(self):
        """首字慢 / 续写卡住 / 连不上，三句话必须能区分，且都带模型名与动作。"""
        model = "qwen2.5:7b"
        first_slow = user_facing_failure(
            ProviderTimeoutError("模型服务", 180.0, before_first_token=True),
            model=model,
        )
        stalled = user_facing_failure(
            ProviderTimeoutError("模型服务", 30.0, before_first_token=False),
            model=model,
        )
        unreachable = user_facing_failure(ConnectionError("refused"), model=model)

        assert "首次加载较慢" in first_slow and "请稍候或重试" in first_slow
        assert "生成途中卡住" in stalled and "30 秒" in stalled and "请重试" in stalled
        assert "连不上模型服务" in unreachable and "已启动" in unreachable
        assert "首次加载较慢" not in stalled, "续写卡住不能说成冷启动"
        assert len({first_slow, stalled, unreachable}) == 3, "三种超时不能共用同一句文案"
        for text in (first_slow, stalled, unreachable):
            assert model in text, "三种文案都必须说明是哪个模型"

    def test_sdk_timeout_names_do_not_fall_back_to_key_copy(self):
        """OpenAI SDK 的 APITimeoutError 不是内建 TimeoutError，文案也不能跑偏。"""

        class APITimeoutError(Exception):
            pass

        text = user_facing_failure(
            APITimeoutError("Request timed out."), model="qwen2.5:7b"
        )
        assert "首次加载较慢" in text
        assert "API Key" not in text
        assert "qwen2.5:7b" in text

    def test_only_a_real_401_says_the_key_is_invalid(self):
        exc = ProviderHTTPError(
            "模型服务返回 HTTP 401", status_code=401, upstream_kind="invalid_key"
        )
        text = user_facing_failure(exc, model="gpt-4o-mini")
        assert INVALID_KEY_MESSAGE in text
        assert "gpt-4o-mini" in text

    def test_failure_reasons_are_machine_distinguishable(self):
        from src.ai.liuhao import _turn_failure_reason

        assert (
            _turn_failure_reason(
                ProviderTimeoutError("模型服务", 120.0, before_first_token=True)
            )
            == "timeout"
        )
        assert (
            _turn_failure_reason(
                ProviderHTTPError("HTTP 401", status_code=401, upstream_kind="invalid_key")
            )
            == "invalid_key"
        )


# --------------------------------------------------------------------------- #
# 2. Retry policy: timeout/connection once, never 401/400
# --------------------------------------------------------------------------- #
class _TimeoutOnceProvider(BaseProvider):
    def __init__(self, *, fail_stream_after_token=False):
        super().__init__("stub", "stub-model", timeout=120.0, retry_base_delay=0.0)
        self.calls = 0
        self.fail_stream_after_token = fail_stream_after_token

    def generate(self, prompt, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise ProviderTimeoutError("模型服务", 120.0, before_first_token=True)
        return "ok"

    def chat(self, messages, **kwargs):
        return self.generate("chat")

    def chat_stream(self, messages, **kwargs):
        self.calls += 1
        if self.calls == 1:
            if self.fail_stream_after_token:
                yield "partial"
            raise ProviderTimeoutError("模型服务", 120.0, before_first_token=True)
        yield "ok"


class _InvalidKeyProvider(BaseProvider):
    def __init__(self):
        super().__init__("stub", "stub-model", timeout=120.0, retry_base_delay=0.0)
        self.calls = 0

    def _fail(self):
        self.calls += 1
        raise ProviderHTTPError(
            "模型服务返回 HTTP 401", status_code=401, upstream_kind="invalid_key"
        )

    def generate(self, prompt, **kwargs):
        self._fail()

    def chat(self, messages, **kwargs):
        return self.generate("chat")

    def chat_stream(self, messages, **kwargs):
        self._fail()
        yield ""  # pragma: no cover - never reached


class _AlwaysFailingProvider(BaseProvider):
    """同一种失败连续抛：数"到底试了几次"，比看总耗时更可靠。"""

    def __init__(self, *, timeout: float, exc: BaseException):
        super().__init__(
            "stub", "stub-model", timeout=timeout, retry_base_delay=0.0
        )
        self._exc = exc
        self.calls = 0

    def generate(self, prompt, **kwargs):
        self.calls += 1
        raise self._exc

    def chat(self, messages, **kwargs):
        return self.generate("chat")

    def chat_stream(self, messages, **kwargs):
        self.calls += 1
        raise self._exc
        yield ""  # pragma: no cover - never reached


class TestRetryPolicy:
    def test_generate_retries_a_timeout_once_then_succeeds(self):
        provider = _TimeoutOnceProvider()
        assert provider.generate_with_retry("hi") == "ok"
        assert provider.calls == 2, "超时只应重试一次"

    def test_chat_retries_a_timeout_once_then_succeeds(self):
        provider = _TimeoutOnceProvider()
        assert provider.chat_with_retry([{"role": "user", "content": "hi"}]) == "ok"
        assert provider.calls == 2

    def test_stream_retry_is_visible_to_the_user(self):
        provider = _TimeoutOnceProvider()
        events = list(provider.stream_with_retry([{"role": "user", "content": "hi"}]))
        assert events[0] == {
            "type": "notice",
            "code": "retrying",
            "message": RETRY_NOTICE_MESSAGE.format(model="stub-model"),
        }, "重试前必须给出可见的『正在重试』反馈"
        assert events[1:] == ["ok"]
        assert provider.calls == 2

    def test_stream_with_partial_output_is_not_replayed(self):
        provider = _TimeoutOnceProvider(fail_stream_after_token=True)
        received = []
        with pytest.raises(ProviderTimeoutError):
            for token in provider.stream_with_retry([{"role": "user", "content": "hi"}]):
                received.append(token)
        assert received == ["partial"]
        assert provider.calls == 1, "已经产出 token 的轮次不能重放，否则用户看到两段回答"

    def test_invalid_key_is_never_retried(self):
        provider = _InvalidKeyProvider()
        with pytest.raises(ProviderHTTPError):
            provider.generate_with_retry("hi")
        assert provider.calls == 1

        provider = _InvalidKeyProvider()
        with pytest.raises(ProviderHTTPError):
            provider.chat_with_retry([{"role": "user", "content": "hi"}])
        assert provider.calls == 1

        provider = _InvalidKeyProvider()
        with pytest.raises(ProviderHTTPError):
            list(provider.stream_with_retry([{"role": "user", "content": "hi"}]))
        assert provider.calls == 1

    # 短超时收敛（b）：用户把 timeout 压到 5 秒以下，就是在说"宁可失败也别等"。
    def test_short_timeout_is_not_retried(self, monkeypatch):
        monkeypatch.delenv(RETRY_MIN_TIMEOUT_ENV, raising=False)
        provider = _AlwaysFailingProvider(timeout=2, exc=ProviderTimeoutError("模型服务", 2.0))
        notices: list[str] = []
        with pytest.raises(ProviderTimeoutError):
            provider.chat_with_retry(
                [{"role": "user", "content": "hi"}], on_retry=notices.append
            )
        assert provider.calls == 1, "2 秒超时不许再重试，否则要等近 5 秒才看到同一句错"
        assert notices == [], "没有第二次尝试就不该出现『正在重试』"

    def test_short_timeout_stream_is_not_retried(self, monkeypatch):
        monkeypatch.delenv(RETRY_MIN_TIMEOUT_ENV, raising=False)
        provider = _AlwaysFailingProvider(timeout=2, exc=ProviderTimeoutError("模型服务", 2.0))
        events: list[object] = []
        with pytest.raises(ProviderTimeoutError):
            for event in provider.stream_with_retry([{"role": "user", "content": "hi"}]):
                events.append(event)
        assert provider.calls == 1
        assert events == []

    def test_short_timeout_keeps_the_same_chinese_copy(self, monkeypatch):
        """不重试只影响等待时长，文案与分类不能跟着变。"""
        monkeypatch.delenv(RETRY_MIN_TIMEOUT_ENV, raising=False)
        exc = ProviderTimeoutError("模型服务", 2.0, before_first_token=True)
        text = user_facing_failure(exc, model="qwen2.5:7b")
        assert TIMEOUT_MESSAGE in text and "qwen2.5:7b" in text
        assert "Key" not in text

    def test_timeout_at_threshold_still_retries_once_with_notice(self, monkeypatch):
        monkeypatch.delenv(RETRY_MIN_TIMEOUT_ENV, raising=False)
        provider = _AlwaysFailingProvider(
            timeout=DEFAULT_RETRY_MIN_TIMEOUT,
            exc=ProviderTimeoutError("模型服务", DEFAULT_RETRY_MIN_TIMEOUT),
        )
        notices: list[str] = []
        with pytest.raises(ProviderTimeoutError):
            provider.chat_with_retry(
                [{"role": "user", "content": "hi"}], on_retry=notices.append
            )
        assert provider.calls == 2, "阈值本身仍保留一次重试（>= 5 秒）"
        assert len(notices) == 1 and "重试" in notices[0]

    def test_retry_threshold_is_configurable(self, monkeypatch):
        """阈值不是魔法数：设 0 恢复"任何超时都重试"，设大则更早放弃。"""
        monkeypatch.setenv(RETRY_MIN_TIMEOUT_ENV, "0")
        provider = _AlwaysFailingProvider(timeout=2, exc=ProviderTimeoutError("模型服务", 2.0))
        with pytest.raises(ProviderTimeoutError):
            provider.chat_with_retry([{"role": "user", "content": "hi"}])
        assert provider.calls == 2

        monkeypatch.setenv(RETRY_MIN_TIMEOUT_ENV, "10")
        provider = _AlwaysFailingProvider(timeout=8, exc=ProviderTimeoutError("模型服务", 8.0))
        with pytest.raises(ProviderTimeoutError):
            provider.chat_with_retry([{"role": "user", "content": "hi"}])
        assert provider.calls == 1

    def test_certificate_and_rejected_targets_never_retry_regardless_of_timeout(self):
        """证书 / DNS / 地址拒绝重试不会变好，与超时长短无关，始终 attempt=1。"""
        cert = ssl.SSLCertVerificationError(
            "certificate verify failed: self-signed certificate"
        )
        for exc in (
            cert,
            RuntimeTargetRejected("模型服务地址当前无法解析"),
            RuntimeTargetRejected("模型服务地址指向内网/本机，已按安全策略拒绝。"),
        ):
            provider = _AlwaysFailingProvider(timeout=60, exc=exc)
            with pytest.raises(type(exc)):
                provider.chat_with_retry([{"role": "user", "content": "hi"}])
            assert provider.calls == 1


# --------------------------------------------------------------------------- #
# 3. Two thresholds: first-token wait vs idle after first token
# --------------------------------------------------------------------------- #
class _Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now


class _ScriptedLines:
    """yield ``line`` after advancing the fake clock by ``step`` seconds."""

    def __init__(self, script, clock: _Clock):
        self._script = script
        self._clock = clock

    def __iter__(self):
        for step, line in self._script:
            self._clock.now += step
            yield line


def _sse(content: str) -> str:
    import json

    return "data: " + json.dumps(
        {"choices": [{"delta": {"content": content}}]}, ensure_ascii=False
    )


class TestFirstTokenThreshold:
    def test_slow_first_token_is_allowed(self, monkeypatch):
        """首字之前用 first_token_timeout（2.0s），不能被 idle（0.5s）提前判死。"""
        clock = _Clock()
        monkeypatch.setattr(time, "monotonic", clock.monotonic)
        lines = _ScriptedLines(
            [
                (0.3, ": keepalive"),
                (0.3, "event: ping"),
                (0.3, ": keepalive"),
                (0.3, ": keepalive"),
                (0.3, _sse("hi")),
                (0.2, "data: [DONE]"),
            ],
            clock,
        )
        tokens = list(
            _iter_sse_text(
                lines,
                label="模型服务",
                idle_timeout=0.5,
                first_token_timeout=2.0,
            )
        )
        assert tokens == ["hi"]

    def test_idle_threshold_applies_after_the_first_token(self, monkeypatch):
        """首字之后按 idle（0.5s）判定卡住：两个阈值分别生效。"""
        clock = _Clock()
        monkeypatch.setattr(time, "monotonic", clock.monotonic)
        lines = _ScriptedLines(
            [
                (0.0, _sse("hi")),
                (0.3, ": keepalive"),
                (0.3, ": keepalive"),
            ],
            clock,
        )
        with pytest.raises(ProviderTimeoutError) as exc:
            list(
                _iter_sse_text(
                    lines,
                    label="模型服务",
                    idle_timeout=0.5,
                    first_token_timeout=2.0,
                )
            )
        assert exc.value.before_first_token is False
        assert exc.value.timeout == 0.5

    def test_first_token_threshold_eventually_fires(self, monkeypatch):
        """首字迟迟不来时，按 first_token_timeout 判超时并说明"还没有返回第一个字"。"""
        clock = _Clock()
        monkeypatch.setattr(time, "monotonic", clock.monotonic)
        lines = _ScriptedLines([(0.3, ": keepalive")] * 8, clock)
        with pytest.raises(ProviderTimeoutError) as exc:
            list(
                _iter_sse_text(
                    lines,
                    label="模型服务",
                    idle_timeout=0.5,
                    first_token_timeout=2.0,
                )
            )
        assert exc.value.before_first_token is True
        assert exc.value.timeout == 2.0
        assert "还没有返回第一个字" in str(exc.value)


# --------------------------------------------------------------------------- #
# 4. Defaults and configuration
# --------------------------------------------------------------------------- #
class TestTimeoutDefaults:
    def test_default_generation_timeout_is_at_least_120s(self):
        provider = BaseProvider("stub", "stub-model")
        assert provider.timeout == 120.0
        assert provider.first_token_timeout >= 120.0
        assert provider.first_token_timeout > provider.timeout

    def test_explicit_short_registry_timeout_is_honoured(self):
        """验收脚本用 2 秒超时验证"超时轮不入账"，不能被静默抬高成慢成功。"""
        cfg = {"provider": "ollama", "model": "qwen2.5", "timeout": 2.0}
        provider = provider_from_config(cfg)
        assert provider.timeout == 2.0
        assert provider.first_token_timeout == 2.0

    def test_explicit_longer_timeout_is_honoured(self):
        cfg = {"provider": "ollama", "model": "qwen2.5", "timeout": 240.0}
        assert provider_from_config(cfg).timeout == 240.0

    @pytest.mark.parametrize(
        "configured, expected_timeout, expected_first",
        [
            (None, 120.0, 180.0),   # 没配：类默认 120，首字放宽到 180
            (30.0, 30.0, 30.0),     # 用户自己写 30：就是 30，不做任何特判
            (123.0, 123.0, 123.0),  # 任意显式值：整体与首字都原样生效
        ],
    )
    def test_configured_timeout_is_used_verbatim(
        self, configured, expected_timeout, expected_first
    ):
        """配什么就是什么：显式值是硬约束，首字等待跟随它。"""
        direct = BaseProvider("stub", "stub-model", timeout=configured)
        assert (direct.timeout, direct.first_token_timeout) == (
            expected_timeout,
            expected_first,
        )

        cfg = {"provider": "ollama", "model": "qwen2.5"}
        if configured is not None:
            cfg["timeout"] = configured
        from_config = provider_from_config(cfg)
        assert (from_config.timeout, from_config.first_token_timeout) == (
            expected_timeout,
            expected_first,
        )


def test_model_suffix_is_appended_only_once():
    """已经有「（模型：…）」时不再追加，避免卡片上出现两个模型名。"""
    from src.ai.providers import _with_model

    once = _with_model("模型没有返回内容，请重试或换一个模型。", "qwen2.5:7b")
    twice = _with_model(once, "另一个模型")
    assert twice == once
    assert twice.count("模型：") == 1


def test_double_timeout_stops_after_one_retry():
    """重试也超时：只允许两次尝试，总等待约为两个阈值之和，不会无限重试。"""

    class _AlwaysTimeout(BaseProvider):
        def __init__(self):
            # 用 >= 阈值（5 秒）的配置，验证的是"只重试一次"而不是短超时收敛。
            super().__init__(
                "stub", "stub-model", timeout=30.0, retry_base_delay=0.0
            )
            self.calls = 0

        def chat_stream(self, messages, **kwargs):
            self.calls += 1

            def _gen():
                time.sleep(0.5)
                raise ProviderTimeoutError(
                    "模型服务", 30.0, before_first_token=True
                )
                yield ""

            return _gen()

    provider = _AlwaysTimeout()
    events = []
    started = time.monotonic()
    with pytest.raises(ProviderTimeoutError) as caught:
        for event in provider.stream_with_retry([]):
            events.append(event)
    elapsed = time.monotonic() - started

    assert provider.calls == 2, "超时只能重试一次"
    # 真实不变量是"只有两次尝试"：第三次至少要再多等一个阈值，calls 断言
    # 已经拦住；这里只防"退化成无限重试/长时间挂起"，给 CI 留出抖动余量。
    assert elapsed < 4.0, f"两次 0.5 秒超时不该无限拖长（实际 {elapsed:.2f}s）"
    assert [e.get("code") for e in events if e.get("type") == "notice"] == [
        "retrying"
    ], "只提示一次重试"
    final_copy = user_facing_failure(caught.value, model=provider.model)
    assert "首次加载较慢" in final_copy
    assert "stub-model" in final_copy


# --------------------------------------------------------------------------- #
# 5. Legacy 30s migration: four contracts
# --------------------------------------------------------------------------- #
def _seed_db(tmp_path, rows):
    """建一个旧库：``rows`` 是 ``(id, timeout)`` 列表。"""
    import sqlite3

    from src.ai.model_config import _SCHEMA

    db = tmp_path / "models.db"
    conn = sqlite3.connect(db)
    conn.executescript(_SCHEMA)
    for model_id, timeout in rows:
        conn.execute(
            "INSERT INTO ai_model_configs "
            "(id, name, provider, model, timeout, created_at, updated_at) "
            "VALUES (?, ?, 'ollama', 'qwen2.5', ?, 'now', 'now')",
            (model_id, model_id, timeout),
        )
    conn.commit()
    conn.close()
    return db


def _read_db(db):
    import sqlite3

    conn = sqlite3.connect(db)
    rows = dict(conn.execute("SELECT id, timeout FROM ai_model_configs"))
    meta = dict(conn.execute("SELECT key, value FROM ai_model_config_meta"))
    conn.close()
    return rows, meta


def test_migration_keeps_user_configured_values(tmp_path):
    """用户显式设过的值（含短于旧默认的 2 秒）一律不动。"""
    db = _seed_db(tmp_path, [("explicit_45", 45.0), ("explicit_2", 2.0)])
    from src.ai.model_config import ModelConfigStore

    ModelConfigStore(db_path=str(db), key_path=str(tmp_path / "k"))._connect()
    rows, _meta = _read_db(db)
    assert rows == {"explicit_45": 45.0, "explicit_2": 2.0}


def test_migration_maps_legacy_default_30s_to_120s(tmp_path):
    """恰好等于旧默认 30 秒的行迁到新默认 120 秒。"""
    db = _seed_db(tmp_path, [("old", 30.0)])
    from src.ai.model_config import ModelConfigStore

    ModelConfigStore(db_path=str(db), key_path=str(tmp_path / "k"))._connect()
    rows, _meta = _read_db(db)
    assert rows == {"old": 120.0}


def test_migration_runs_only_once(tmp_path):
    """跑第二次不再改：用户之后显式改回 30 秒也不会被再次"迁移"。"""
    db = _seed_db(tmp_path, [("old", 30.0)])
    from src.ai.model_config import ModelConfigStore

    ModelConfigStore(db_path=str(db), key_path=str(tmp_path / "k"))._connect()
    assert _read_db(db)[0] == {"old": 120.0}

    import sqlite3

    conn = sqlite3.connect(db)
    conn.execute("UPDATE ai_model_configs SET timeout = 30.0 WHERE id = 'old'")
    conn.commit()
    ModelConfigStore._migrate_schema(conn)
    conn.commit()
    rows, _meta = _read_db(db)
    conn.close()
    assert rows == {"old": 30.0}, "迁移标记已存在时不得再改第二次"


# --------------------------------------------------------------------------- #
# 6. Context-length overflow: own class, actionable copy, never retried
# --------------------------------------------------------------------------- #
class _ContextOverflowProvider(BaseProvider):
    def __init__(self):
        super().__init__("stub", "stub-model", timeout=120.0, retry_base_delay=0.0)
        self.calls = 0

    def _fail(self):
        self.calls += 1
        raise ProviderHTTPError(
            "模型服务返回 HTTP 400",
            status_code=400,
            upstream_kind="context_length_exceeded",
        )

    def generate(self, prompt, **kwargs):
        self._fail()

    def chat(self, messages, **kwargs):
        self._fail()

    def chat_stream(self, messages, **kwargs):
        self._fail()
        yield ""  # pragma: no cover - never reached


class TestContextLengthExceeded:
    @pytest.mark.parametrize(
        "raw",
        [
            '{"error": {"code": "context_length_exceeded", "message": "too long"}}',
            (
                "This model's maximum context length is 8192 tokens. "
                "However, your messages resulted in 9000 tokens."
            ),
            "prompt is too long: 213456 tokens > 200000 maximum",
            "input token count exceeds the maximum number of tokens allowed",
            '{"error": "context length exceeded"}',
        ],
    )
    def test_classified_separately_from_upstream_error(self, raw):
        assert classify_upstream_error(status=400, text=raw) == "context_length_exceeded"

    def test_copy_tells_the_user_to_shorten_the_conversation(self):
        exc = ProviderHTTPError(
            "模型服务返回 HTTP 400",
            status_code=400,
            upstream_kind="context_length_exceeded",
        )
        text = user_facing_failure(exc, model="gpt-4o-mini")
        assert CONTEXT_LENGTH_EXCEEDED_MESSAGE in text
        assert "对话太长" in text and "新开会话" in text and "分段发送" in text
        # 不能把用户引去改一个完全正确的 Key / 网络。
        assert "密钥" not in text
        assert "网络连接" not in text
        assert "gpt-4o-mini" in text, "失败文案必须说明是哪个模型"

    def test_never_retried_even_when_status_looks_transient(self):
        exc = ProviderHTTPError(
            "上游 500", status_code=500, upstream_kind="context_length_exceeded"
        )
        assert _is_retryable_failure(exc) is False

        provider = _ContextOverflowProvider()
        with pytest.raises(ProviderHTTPError):
            provider.generate_with_retry("hi")
        assert provider.calls == 1, "上下文超限重试不会让历史变短，只会多等几轮"


def test_migration_is_atomic_when_it_fails(tmp_path):
    """中途失败不写坏配置：改值回滚、标记不落，下次打开还能重试。"""
    import sqlite3

    from src.ai.model_config import ModelConfigStore

    db = _seed_db(tmp_path, [("old", 30.0)])
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TRIGGER fail_marker BEFORE INSERT ON ai_model_config_meta "
        "BEGIN SELECT RAISE(ABORT, 'disk full'); END"
    )
    conn.commit()

    with pytest.raises(sqlite3.DatabaseError):
        ModelConfigStore._migrate_schema(conn)

    rows, meta = _read_db(db)
    assert rows == {"old": 30.0}, "失败时必须回滚已改的超时"
    assert "timeout_floor_120" not in meta, "失败时不得写迁移标记"

    # 故障排除后（去掉触发器）重试：改值 + 标记一次完成。
    conn.execute("DROP TRIGGER fail_marker")
    conn.commit()
    ModelConfigStore._migrate_schema(conn)
    conn.commit()
    conn.close()
    rows, meta = _read_db(db)
    assert rows == {"old": 120.0}
    assert meta["timeout_floor_120"] == "1"
