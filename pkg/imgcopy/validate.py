"""极简 JSON Schema 校验（够用即可，不引入依赖）。

支持：type / required / properties / items / enum / pattern /
      minItems / maxItems / minLength / minimum / maximum / additionalProperties(忽略)。
不支持的字段会跳过，不报错 —— 目标是拦住结构性错误（缺字段、类型不对、枚举写错），
不是做完整合规。
"""

from __future__ import annotations

import re

_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "null": type(None),
}


class SchemaError(ValueError):
    pass


def _check_type(value, expect, path: str, errors: list[str]) -> None:
    expects = expect if isinstance(expect, list) else [expect]
    for e in expects:
        t = _TYPES.get(e)
        if t is None:
            continue
        if e == "integer" and isinstance(value, bool):
            continue
        if e == "number" and isinstance(value, bool):
            continue
        if isinstance(value, t):
            return
    errors.append(f"{path}: 类型应为 {expect}，实际是 {type(value).__name__}")


def _walk(value, schema: dict, path: str, errors: list[str]) -> None:
    if not isinstance(schema, dict):
        return

    if "type" in schema and not isinstance(schema["type"], list):
        # boolean 会被 isinstance(True, int) 命中，这里单独挡一下
        if schema["type"] in ("number", "integer") and isinstance(value, bool):
            errors.append(f"{path}: 布尔值不能当数字用")
    if "type" in schema:
        _check_type(value, schema["type"], path, errors)

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: 取值 {value!r} 不在允许集合 {schema['enum']}")

    if isinstance(value, str):
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: {value!r} 不匹配 {schema['pattern']}")
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: 长度 {len(value)} < {schema['minLength']}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: {value} < 最小值 {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: {value} > 最大值 {schema['maximum']}")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: 元素数 {len(value)} < {schema['minItems']}")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: 元素数 {len(value)} > {schema['maxItems']}")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for i, item in enumerate(value):
                _walk(item, item_schema, f"{path}[{i}]", errors)

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: 缺字段 {key!r}")
        for key, sub in (schema.get("properties") or {}).items():
            if key in value:
                _walk(value[key], sub, f"{path}.{key}", errors)


def check(obj, schema: dict, where: str = "$") -> list[str]:
    errors: list[str] = []
    _walk(obj, schema, where, errors)
    return errors


def must_check(obj, schema: dict, where: str = "$") -> None:
    errors = check(obj, schema, where)
    if errors:
        raise SchemaError(f"{where} 校验失败：\n  - " + "\n  - ".join(errors))
