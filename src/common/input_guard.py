"""异常输入的统一闸口：嵌套深度 / 容器元素数量 / 文本合法性。

模糊测试暴露的三类问题都源出同一处：请求一旦被解析成 Python 对象，
深嵌套或控制字符就会一路渗到 Pydantic、JSON 序列化和磁盘写入，
最终以 500 / 400 / 英文错误体等形式外泄。这里把可判定的规则前置：

* :func:`scan_json_shape` —— 在**解析阶段**（``json.loads`` 之前）扫描原始文本，
  超过 ``MAX_JSON_DEPTH`` / ``MAX_JSON_ELEMENTS`` 立刻拒绝；
* :func:`ensure_json_serializable` —— 落库/写盘前再确认对象可被 JSON 编码，
  避免 ``ValueError: Circular reference detected`` 这类晚期故障；
* :func:`normalize_required_text` / :func:`reject_unsafe_text` ——
  空白内容、NUL、孤立代理对、控制字符的统一判定。

所有消息都是中文，状态码由调用方（网关中间件 / 路由）决定。
"""

from __future__ import annotations

import json
from typing import Any, Union, get_args, get_origin

#: 请求体允许的最大 JSON 嵌套层级（``{}``/``[]`` 各算一层）。
MAX_JSON_DEPTH = 64

#: 单个容器（对象或数组）允许的最大元素数量。
MAX_JSON_ELEMENTS = 32768

#: 允许出现在自由文本里的空白控制字符（换行/回车/制表符）。
_ALLOWED_CONTROL = frozenset({"\n", "\r", "\t"})


class JsonShapeError(ValueError):
    """请求体结构超出限制；消息已是可直接展示的中文。"""


class UnsafeTextError(ValueError):
    """文本包含不可接受的控制字符或无效 Unicode；消息已是中文。"""


def _raise_shape(message: str) -> None:
    raise JsonShapeError(message)


def scan_json_shape(payload: str) -> None:
    """扫描 JSON 文本的嵌套深度与单容器元素数量，超限抛 :class:`JsonShapeError`。

    这是一个不依赖 ``json.loads`` 的轻量词法扫描：字符串内容（含转义）会被整体跳过，
    因此不会把文本里的括号误判成结构；命中上限时立刻中止，不会把攻击样本完整读完。
    """

    depth = 0
    #: 每一层容器：[逗号数量, 是否出现过 token]
    frames: list[list[Any]] = [[0, False]]
    length = len(payload)
    index = 0
    while index < length:
        char = payload[index]
        if char == '"':
            # 跳过整个字符串字面量（处理 \\" 与 \\\\ 以及 \\uXXXX 内的字符）。
            index += 1
            while index < length:
                current = payload[index]
                if current == "\\":
                    index += 2
                    continue
                if current == '"':
                    break
                index += 1
            frames[-1][1] = True
        elif char in "{[":
            frames[-1][1] = True
            depth += 1
            if depth > MAX_JSON_DEPTH:
                _raise_shape(
                    f"请求嵌套层级过深，最多允许 {MAX_JSON_DEPTH} 层；请拆分后再试。"
                )
            frames.append([0, False])
        elif char in "}]":
            commas, saw_token = frames.pop()
            elements = commas + 1 if saw_token else 0
            if elements > MAX_JSON_ELEMENTS:
                _raise_shape(
                    f"请求中单个容器元素过多，最多允许 {MAX_JSON_ELEMENTS} 个；请分批提交。"
                )
            if frames:
                frames[-1][1] = True
            depth -= 1
        elif char == ",":
            commas, _ = frames[-1]
            commas += 1
            frames[-1][0] = commas
            if commas + 1 > MAX_JSON_ELEMENTS:
                _raise_shape(
                    f"请求中单个容器元素过多，最多允许 {MAX_JSON_ELEMENTS} 个；请分批提交。"
                )
        elif char in " \t\r\n":
            pass
        else:
            frames[-1][1] = True
        index += 1

    # 未闭合的容器交给 json.loads 报格式错误，这里只做最后一道元素数量兜底。
    commas, saw_token = frames[0]
    if (commas + 1 if saw_token else 0) > MAX_JSON_ELEMENTS:
        _raise_shape(
            f"请求中元素过多，最多允许 {MAX_JSON_ELEMENTS} 个；请分批提交。"
        )


def _exceeds_depth(value: Any, limit: int) -> bool:
    """迭代式深度检查（不递归，天然免疫 ``RecursionError``），同时跳过循环引用。"""

    stack: list[tuple[Any, int]] = [(value, 1)]
    seen: set[int] = set()
    while stack:
        current, depth = stack.pop()
        if depth > limit:
            return True
        if isinstance(current, dict):
            marker = id(current)
            if marker in seen:
                continue
            seen.add(marker)
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, (list, tuple, set, frozenset)):
            marker = id(current)
            if marker in seen:
                continue
            seen.add(marker)
            stack.extend((item, depth + 1) for item in current)
    return False


def ensure_json_serializable(value: Any, field_label: str = "该字段") -> None:
    """确认 ``value`` 能被 JSON 编码且嵌套不过深，否则抛中文 :class:`JsonShapeError`。

    用于知识库 metadata 这类「用户可塞任意结构、落库前必须可序列化」的字段。
    """

    if _exceeds_depth(value, MAX_JSON_DEPTH):
        raise JsonShapeError(
            f"{field_label}嵌套层级过深，最多允许 {MAX_JSON_DEPTH} 层；请拆分后再试。"
        )
    try:
        json.dumps(value, ensure_ascii=False)
    except (RecursionError, ValueError, TypeError) as exc:  # pragma: no cover - 兜底分支
        raise JsonShapeError(
            f"{field_label}包含无法序列化的内容（嵌套层级过深或存在循环引用）。"
        ) from exc


def is_unsafe_text(value: str) -> bool:
    """是否包含 NUL、孤立代理对或其他不允许的控制字符。"""

    for char in value:
        code = ord(char)
        if code < 0x20 and char not in _ALLOWED_CONTROL:
            return True
        if code == 0x7F:
            return True
        if 0xD800 <= code <= 0xDFFF:
            return True
    return False


def reject_unsafe_text(value: str, field_label: str) -> None:
    """发现 NUL / 控制字符 / 孤立代理对时抛中文 :class:`UnsafeTextError`。"""

    for char in value:
        code = ord(char)
        if code < 0x20 and char not in _ALLOWED_CONTROL:
            raise UnsafeTextError(f"{field_label}包含不允许的控制字符，请检查后重试。")
        if code == 0x7F:
            raise UnsafeTextError(f"{field_label}包含不允许的控制字符，请检查后重试。")
        if 0xD800 <= code <= 0xDFFF:
            raise UnsafeTextError(f"{field_label}包含无效的 Unicode 字符，请检查后重试。")


def _length_prefix(field_label: str) -> str:
    """ASCII 结尾的标签（如「会话 ID」）后补空格，避免「ID过长」的粘连文案。"""
    if field_label and field_label[-1].isascii() and field_label[-1].isalnum():
        return f"{field_label} "
    return field_label


def normalize_required_text(
    value: str,
    field_label: str,
    *,
    min_chars: int = 1,
    max_chars: int | None = None,
) -> str:
    """校验必填自由文本：拒绝控制字符，``strip()`` 后不足 ``min_chars`` 即判空。

    返回原始值（不静默改写用户内容）。
    """

    reject_unsafe_text(value, field_label)
    if len(value.strip()) < min_chars:
        raise UnsafeTextError(f"{field_label}不能为空，请填写有效内容。")
    if max_chars is not None and len(value) > max_chars:
        raise UnsafeTextError(
            f"{_length_prefix(field_label)}过长，最多允许 {max_chars} 个字符（当前 {len(value)} 个）。"
        )
    return value


def normalize_required_name(
    value: str,
    field_label: str,
    *,
    max_chars: int | None = None,
) -> str:
    """名称类字段：除空白判定外还要拒绝换行/制表符（名称不应含换行）。

    语义约定（与项目名、员工名一致）：调用方落库前统一 ``strip()`` 两侧空白，
    因此 ``"\\u3000x"`` 存成 ``"x"`` 是预期结果，不是丢字符；只有 ``strip()``
    后为空才 422「不能为空」。
    """

    reject_unsafe_text(value, field_label)
    for char in value:
        if char in ("\n", "\r", "\t"):
            raise UnsafeTextError(f"{field_label}不能包含换行或制表符，请检查后重试。")
    if not value.strip():
        raise UnsafeTextError(f"{field_label}不能为空，请填写有效内容。")
    if max_chars is not None and len(value) > max_chars:
        raise UnsafeTextError(
            f"{_length_prefix(field_label)}过长，最多允许 {max_chars} 个字符（当前 {len(value)} 个）。"
        )
    return value


def trim_for_error(
    value: Any, *, max_text: int = 96, max_items: int = 8, _depth: int = 0
) -> Any:
    """把回显用的 ``input`` 截断到安全长度，避免错误体被超长原文撑爆。"""
    if isinstance(value, str):
        if len(value) <= max_text:
            return value
        return value[:max_text] + f"…（已截断，共 {len(value)} 字符）"
    if isinstance(value, (bytes, bytearray)):
        return trim_for_error(
            value.decode("utf-8", "replace"),
            max_text=max_text,
            max_items=max_items,
            _depth=_depth,
        )
    if _depth >= 6:
        return "…（嵌套过深，已省略）"
    if isinstance(value, dict):
        return {
            str(key): trim_for_error(
                item, max_text=max_text, max_items=max_items, _depth=_depth + 1
            )
            for key, item in list(value.items())[:max_items]
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [
            trim_for_error(item, max_text=max_text, max_items=max_items, _depth=_depth + 1)
            for item in list(value)[:max_items]
        ]
        if len(value) > max_items:
            items.append(f"…（共 {len(value)} 项，已截断）")
        return items
    return value


def body_model_from_field(body_field: Any) -> Any:
    """从 FastAPI 的 ``ModelField`` 取出请求体 Pydantic 模型类（没有则 ``None``）。

    兼容新旧 FastAPI：新版本把类型放在 ``field_info.annotation``，旧版本在
    ``type_``；``Optional[Model]`` 会自动拆掉 ``None``。
    """

    if body_field is None:
        return None
    annotation = getattr(getattr(body_field, "field_info", None), "annotation", None)
    if annotation is None:
        annotation = getattr(body_field, "type_", None)
    if annotation is None:
        return None
    origin = get_origin(annotation)
    if origin is Union:
        args = [item for item in get_args(annotation) if item is not type(None)]
        if len(args) == 1:
            annotation = args[0]
    return annotation if getattr(annotation, "model_fields", None) else None


def field_label_from_info(info: Any, fallback: str = "") -> str:
    """从 Pydantic ``FieldInfo`` 的 ``title``/``description`` 提炼用户可读字段名。

    绝不回退到 snake_case 内部名：没有可用中文描述时统一叫「该字段」。
    """

    for candidate in (
        getattr(info, "title", None),
        getattr(info, "description", None),
    ):
        if not candidate:
            continue
        text = str(candidate).strip()
        for separator in ("（", "(", "：", ":", "，", ",", "。", "."):
            if separator in text:
                text = text.split(separator)[0]
        text = text.strip()
        if text:
            return text
    return fallback or "该字段"


_STRING_TYPES = frozenset({"string_type", "string_unicode"})
_INT_TYPES = frozenset({
    "int_type",
    "int_parsing",
    "int_from_float",
    "int_unsigned",
    "int_negative",
    "int_non_negative",
    "int_non_positive",
})
_FLOAT_TYPES = frozenset({
    "float_type",
    "float_parsing",
    "finite_number",
    "decimal_type",
    "decimal_parsing",
    "decimal_max_digits",
})
_BOOL_TYPES = frozenset({"bool_type", "bool_parsing"})
_LIST_TYPES = frozenset({"list_type", "tuple_type", "set_type", "frozenset_type"})
_DICT_TYPES = frozenset({"dict_type"})


def translate_validation_error(
    err_type: str, label: str, raw_message: str | None = None
) -> str:
    """把 Pydantic 校验错误 ``type`` 映射成中文，禁止透传英文 ``msg``。

    ``raw_message`` 仅在 ``value_error`` 时使用：自写 validator 抛出的中文
    说明会以 ``Value error, ...`` 的形式带出来，去掉前缀后直接复用。
    """

    label = label or "该字段"
    err_type = str(err_type or "")
    if err_type == "missing":
        return f"缺少必填字段：{label}"
    if err_type == "model_attributes_type":
        return "请求体必须是 JSON 对象（键值对形式）"
    if err_type == "json_invalid":
        return "请求体不是合法的 JSON，请检查格式后重试。"
    if err_type in _STRING_TYPES:
        return f"{label}必须是文本"
    if err_type in _INT_TYPES:
        return f"{label}必须是整数，请使用半角数字（0-9）"
    if err_type in _FLOAT_TYPES:
        return f"{label}必须是数字，请使用半角数字（0-9）"
    if err_type in _BOOL_TYPES:
        return f"{label}必须是 true 或 false"
    if err_type in _LIST_TYPES:
        return f"{label}必须是列表"
    if err_type in _DICT_TYPES:
        return f"{label}必须是对象"
    if err_type in ("string_pattern_mismatch", "string_too_long", "string_too_short"):
        return f"{label}格式不符合要求，请检查后重试。"
    if err_type in ("enum", "literal_error"):
        return f"{label}取值不在允许范围内"
    if err_type == "extra_forbidden":
        return f"不支持的字段：{label}"
    if err_type in ("date_type", "date_parsing", "datetime_type", "datetime_parsing",
                    "time_type", "time_parsing", "duration_type", "duration_parsing",
                    "uuid_type", "uuid_parsing", "url_type", "url_parsing"):
        return f"{label}格式不正确，请检查后重试。"
    if err_type == "value_error":
        message = (raw_message or "").strip()
        if message.startswith("Value error, "):
            message = message[len("Value error, "):].strip()
        if message:
            return message
        return f"{label}不符合要求，请检查后重试。"
    if err_type in ("less_than", "greater_than", "less_than_equal",
                    "greater_than_equal", "multiple_of"):
        return f"{label}超出允许范围"
    return f"{label}格式不正确，请检查后重试。"
