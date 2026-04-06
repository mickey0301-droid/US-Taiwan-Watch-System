from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import wraps
import re
from typing import Any


_MOJIBAKE_MARKERS = ("Ã", "Â", "â€", "ï¼", "å", "ç", "é", "æ", "Ë†", "â€œ", "â€š")
_PATCHED_ATTR = "_taiwan_watch_text_repair_patched"


def repair_mojibake_text(value: str) -> str:
    repaired = value
    for _ in range(3):
        if not _looks_mojibake(repaired):
            break
        try:
            candidate = repaired.encode("latin-1").decode("utf-8")
        except UnicodeError:
            break
        if _score_text(candidate) >= _score_text(repaired):
            break
        repaired = candidate
    return repaired


def repair_nested_text(value: Any) -> Any:
    if isinstance(value, str):
        return repair_mojibake_text(value)
    if isinstance(value, Mapping):
        return {key: repair_nested_text(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(repair_nested_text(item) for item in value)
    if isinstance(value, list):
        return [repair_nested_text(item) for item in value]
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return type(value)(repair_nested_text(item) for item in value)
    return value


def install_streamlit_text_repair(streamlit_module: Any) -> None:
    if getattr(streamlit_module, _PATCHED_ATTR, False):
        return

    try:
        from streamlit.delta_generator import DeltaGenerator
    except Exception:
        DeltaGenerator = None

    method_names = [
        "button",
        "caption",
        "checkbox",
        "error",
        "expander",
        "form_submit_button",
        "header",
        "info",
        "markdown",
        "metric",
        "radio",
        "selectbox",
        "subheader",
        "success",
        "text",
        "text_area",
        "text_input",
        "title",
        "warning",
        "write",
    ]

    for method_name in method_names:
        _patch_method(streamlit_module, method_name)
        if DeltaGenerator is not None:
            _patch_method(DeltaGenerator, method_name)

    setattr(streamlit_module, _PATCHED_ATTR, True)


def _patch_method(target: Any, method_name: str) -> None:
    original = getattr(target, method_name, None)
    if original is None or getattr(original, _PATCHED_ATTR, False):
        return

    @wraps(original)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return original(*repair_nested_text(args), **repair_nested_text(kwargs))

    setattr(wrapper, _PATCHED_ATTR, True)
    setattr(target, method_name, wrapper)


def _looks_mojibake(value: str) -> bool:
    return any(marker in value for marker in _MOJIBAKE_MARKERS)


def _score_text(value: str) -> int:
    marker_penalty = sum(value.count(marker) for marker in _MOJIBAKE_MARKERS)
    cjk_bonus = sum(1 for char in value if "\u4e00" <= char <= "\u9fff")
    return marker_penalty * 4 - cjk_bonus


def compact_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
