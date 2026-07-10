"""collect_pipeline.llm — 通用 LLM utils

- LLMConfig：env-driven 配置 + 文件 cfg 合并
- load_llm_cfg：读 JSON cfg（sp/up/config 三段）
- build_chat_model：构造 langchain ChatOpenAI（任意 OpenAI 兼容端点）
- invoke_with_retry：指数退避重试
- extract_text：AIMessage.content → str
- extract_json_array：从 LLM 输出抠 JSON 数组（带中英文引号修复 + 正则兜底）

这些 utils 跟具体业务无关（不依赖 aiseclect 的 cfg 文件路径），
适合 collect_pipeline 持有，下游项目复用。
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class LLMConfig:
    model: str
    temperature: float = 0.5
    top_p: float = 0.95
    max_tokens: int = 4096
    api_key: str = ""
    base_url: str = ""

    @classmethod
    def from_env(cls, default_model: str = "ark-code-latest") -> "LLMConfig":
        return cls(
            model=os.getenv("OPENAI_MODEL", default_model),
            temperature=float(os.getenv("OPENAI_TEMPERATURE", "0.5")),
            top_p=float(os.getenv("OPENAI_TOP_P", "0.95")),
            max_tokens=int(os.getenv("OPENAI_MAX_TOKENS", "4096")),
            api_key=os.getenv("OPENAI_API_KEY", ""),
            base_url=os.getenv("OPENAI_BASE_URL", "https://ark.cn-beijing.volces.com/api/plan/v3"),
        )

    def merged(self, override: Dict[str, Any]) -> "LLMConfig":
        """与 LLM cfg 文件中的 config 段合并，env 中的 api_key/base_url 始终优先生效。"""
        return LLMConfig(
            model=override.get("model", self.model),
            temperature=override.get("temperature", self.temperature),
            top_p=override.get("top_p", self.top_p),
            max_tokens=override.get(
                "max_completion_tokens", override.get("max_tokens", self.max_tokens)
            ),
            api_key=self.api_key,
            base_url=self.base_url,
        )


def load_llm_cfg(
    cfg_path: str,
    workspace_path: Optional[str] = None,
) -> Dict[str, Any]:
    """读取 LLM cfg JSON 文件，返回 {sp, up, config}。"""
    candidates = []
    if os.path.isabs(cfg_path):
        candidates.append(cfg_path)
    else:
        if workspace_path:
            candidates.append(os.path.join(workspace_path, cfg_path))
        candidates.append(os.path.join(os.getcwd(), cfg_path))
        # 兜底：相对仓库根目录
        candidates.append(os.path.abspath(cfg_path))

    for p in candidates:
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
    raise FileNotFoundError(f"LLM cfg 找不到: {candidates}")


def build_chat_model(cfg: LLMConfig):
    """构造 langchain ChatOpenAI。"""
    from langchain_openai import ChatOpenAI

    if not cfg.api_key:
        raise ValueError("OPENAI_API_KEY 未配置（请在 .env 或环境变量中设置）")
    return ChatOpenAI(
        model=cfg.model,
        temperature=cfg.temperature,
        top_p=cfg.top_p,
        max_tokens=cfg.max_tokens,
        api_key=cfg.api_key,
        base_url=cfg.base_url,
        max_retries=0,  # 禁用 langchain 自带重试；用我们自己的指数退避
    )


def invoke_with_retry(model, messages, max_attempts: int = 3, base_delay: float = 1.0):
    """带指数退避的 LLM 调用封装。

    火山方舟 plan 端点偶尔 Connection error，1s/2s/4s 重试。
    重试耗尽抛最后一次的异常。
    """
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            return model.invoke(messages)
        except Exception as e:
            last_exc = e
            if attempt >= max_attempts:
                break
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                f"LLM invoke 失败（第 {attempt}/{max_attempts} 次），"
                f"{delay:.1f}s 后重试: {type(e).__name__}: {str(e)[:200]}"
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def extract_text(content: Any) -> str:
    """把 langchain AIMessage.content 规范成纯字符串（兼容 str / list / 混合类型）。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        buf = []
        for item in content:
            if isinstance(item, str):
                buf.append(item)
            elif isinstance(item, dict):
                if item.get("type") == "text":
                    buf.append(item.get("text", ""))
                elif "text" in item:
                    buf.append(str(item["text"]))
        return "".join(buf)
    return str(content)


def extract_json_array(text: str) -> List[Any]:
    """从 LLM 输出文本中抠出 JSON 数组。

    鲁棒性增强：
    1. 优先匹配 ```json``` 代码块
    2. 否则找最外层 [...]，尝试直接 json.loads
    3. 失败时尝试修复：把字符串值内的 ASCII 双引号 " 替换成中文全角「」
    4. 仍失败则尝试正则逐项抽取 {} 对象
    """
    text = (text or "").strip()
    if "```json" in text:
        s = text.find("```json") + 7
        e = text.find("```", s)
        if e > s:
            candidate = text[s:e].strip()
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass
    if "[" in text:
        s = text.find("[")
        e = text.rfind("]") + 1
        if e > s:
            candidate = text[s:e]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                # 尝试把字符串内的 ASCII 双引号替换成全角
                repaired = _escape_inner_quotes(candidate)
                try:
                    return json.loads(repaired)
                except json.JSONDecodeError:
                    pass
            # 仍失败：逐项抽取
            return _extract_objects_fallback(candidate)
    # 最后兜底
    try:
        result = json.loads(text)
        return result if isinstance(result, list) else [result]
    except json.JSONDecodeError:
        return _extract_objects_fallback(text)


def _escape_inner_quotes(s: str) -> str:
    """把 JSON 字符串值内的 ASCII 双引号替换成中文全角引号。

    状态机：维护 in_string 标志，识别 key/value 边界；遇到 value 内部
    意外出现的 " 时，按其前后是中文字符判断为内嵌引号，替换为全角。
    """
    out: list[str] = []
    in_string = False
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if not in_string:
            if ch == '"':
                in_string = True
                out.append(ch)
                i += 1
                continue
            out.append(ch)
            i += 1
            continue
        # in_string == True
        if ch == "\\" and i + 1 < n:
            out.append(ch)
            out.append(s[i + 1])
            i += 2
            continue
        if ch == '"':
            # 判断这个 " 是字符串结束，还是 value 内的内嵌引号
            prev = ""
            j = len(out) - 1
            while j >= 0 and out[j] in " \t\n":
                j -= 1
            if j >= 0:
                prev = out[j]
            nxt = ""
            k = i + 1
            while k < n and s[k] in " \t\n":
                k += 1
            if k < n:
                nxt = s[k]
            is_chinese = lambda c: c and (
                "一" <= c <= "鿿"
                or c in "，。！？、；：（）【】《》""''…—-·"
            )
            if is_chinese(prev) and (is_chinese(nxt) or nxt in ",}]"):
                # 内嵌引号：替换为全角
                out.append("「")
                i += 1
                continue
            in_string = False
            out.append(ch)
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _extract_objects_fallback(text: str) -> List[Any]:
    """最后兜底：用正则逐项抓 {...} 并尝试解析，跳过失败的。"""
    results: List[Any] = []
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                candidate = text[start : i + 1]
                try:
                    results.append(json.loads(candidate))
                except json.JSONDecodeError:
                    try:
                        results.append(json.loads(_escape_inner_quotes(candidate)))
                    except json.JSONDecodeError:
                        pass
                start = -1
    return results