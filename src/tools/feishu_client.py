"""
飞书 Bitable 客户端（不走 Coze，直接用 tenant_access_token）
- 环境变量: FEISHU_APP_ID, FEISHU_APP_SECRET
- 文档: https://open.feishu.cn/document/server-docs/docs/bitable-v1/bitable-overview
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

FEISHU_BASE = "https://open.feishu.cn/open-apis"
TOKEN_TTL = 7000  # tenant_access_token 有效期 2h，刷新阈值 1.9h


@dataclass
class FeishuField:
    field_id: str
    field_name: str
    type: int


class FeishuClient:
    def __init__(self, app_id: Optional[str] = None, app_secret: Optional[str] = None, timeout: int = 30):
        self.app_id = app_id or os.getenv("FEISHU_APP_ID", "")
        self.app_secret = app_secret or os.getenv("FEISHU_APP_SECRET", "")
        self.timeout = timeout
        self._token: str = ""
        self._token_expire_at: float = 0.0

    # ---------- token ----------

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expire_at:
            return self._token
        if not self.app_id or not self.app_secret:
            raise ValueError("FEISHU_APP_ID / FEISHU_APP_SECRET 未配置")
        resp = requests.post(
            f"{FEISHU_BASE}/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=self.timeout,
        )
        data = self._safe_json(resp)
        if data.get("code") != 0:
            raise RuntimeError(f"获取 tenant_access_token 失败: {data.get('msg')} (code={data.get('code')})")
        self._token = data["tenant_access_token"]
        self._token_expire_at = time.time() + TOKEN_TTL
        return self._token

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json; charset=utf-8",
        }

    @staticmethod
    def _safe_json(resp: requests.Response) -> Dict[str, Any]:
        if resp.status_code != 200:
            raise RuntimeError(f"飞书 HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            return resp.json()
        except ValueError as e:
            raise RuntimeError(f"飞书响应非 JSON: {resp.text[:200]} ({e})")

    # ---------- wiki ----------

    def get_wiki_app_token(self, wiki_token: str) -> Optional[str]:
        """从 Wiki 节点 token 反查 app_token。"""
        resp = requests.get(
            f"{FEISHU_BASE}/wiki/v2/spaces/get_node",
            params={"token": wiki_token},
            headers=self._headers(),
            timeout=self.timeout,
        )
        data = self._safe_json(resp)
        if data.get("code") != 0:
            logger.error(f"wiki get_node 失败: {data.get('msg')}")
            return None
        node = (data.get("data") or {}).get("node") or {}
        if node.get("obj_type") != "bitable":
            logger.error(f"wiki 节点不是 bitable: {node.get('obj_type')}")
            return None
        return node.get("obj_token") or None

    # ---------- 字段 ----------

    def list_fields(self, app_token: str, table_id: str) -> List[FeishuField]:
        resp = requests.get(
            f"{FEISHU_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
            headers=self._headers(),
            timeout=self.timeout,
        )
        data = self._safe_json(resp)
        if data.get("code") != 0:
            logger.error(f"list_fields 失败: {data.get('msg')}")
            return []
        items = (data.get("data") or {}).get("items") or []
        return [
            FeishuField(
                field_id=item.get("field_id", ""),
                field_name=item.get("field_name", ""),
                type=int(item.get("type", 0) or 0),
            )
            for item in items
        ]

    def add_field(self, app_token: str, table_id: str, field_name: str, field_type: int) -> bool:
        """field_type: 1=文本 2=数字 3=单选 5=日期 7=复选 15=URL 17=附件"""
        resp = requests.post(
            f"{FEISHU_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
            headers=self._headers(),
            json={"field_name": field_name, "type": field_type},
            timeout=self.timeout,
        )
        data = self._safe_json(resp)
        if data.get("code") != 0:
            logger.error(f"add_field '{field_name}' 失败: {data.get('msg')}")
            return False
        return True

    def ensure_fields(self, app_token: str, table_id: str, required: List[Dict[str, Any]]) -> List[str]:
        """确保字段都存在；返回新建的字段名列表。required: [{name, type}]"""
        existing = {f.field_name for f in self.list_fields(app_token, table_id)}
        created: List[str] = []
        for spec in required:
            name = spec["name"]
            ftype = spec["type"]
            if name in existing:
                continue
            if self.add_field(app_token, table_id, name, ftype):
                created.append(name)
        return created

    def delete_field(self, app_token: str, table_id: str, field_id: str) -> bool:
        resp = requests.delete(
            f"{FEISHU_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/fields/{field_id}",
            headers=self._headers(),
            timeout=self.timeout,
        )
        data = self._safe_json(resp)
        if data.get("code") != 0:
            logger.error(f"delete_field '{field_id}' 失败: {data.get('msg')}")
            return False
        return True

    def remove_fields(self, app_token: str, table_id: str, obsolete_names: List[str]) -> List[str]:
        """删除指定名字的字段，返回已成功删除的字段名列表。字段不存在则跳过。"""
        target = set(obsolete_names)
        removed: List[str] = []
        for f in self.list_fields(app_token, table_id):
            if f.field_name not in target:
                continue
            if self.delete_field(app_token, table_id, f.field_id):
                removed.append(f.field_name)
        return removed

    # ---------- 记录 ----------

    def batch_create_records(
        self,
        app_token: str,
        table_id: str,
        records: List[Dict[str, Any]],
        with_shared_url: bool = True,
    ) -> List[Dict[str, Any]]:
        """records: [{"fields": {...}}, ...]；返回服务器返回的 record 列表（含 record_id / shared_url）"""
        if not records:
            return []
        url = f"{FEISHU_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create"
        params = {"user_id_type": "open_id"} if with_shared_url else {}
        resp = requests.post(
            url,
            headers=self._headers(),
            params=params,
            json={"records": records, "with_shared_url": with_shared_url},
            timeout=self.timeout,
        )
        data = self._safe_json(resp)
        if data.get("code") != 0:
            logger.error(f"batch_create 失败: {data.get('msg')}")
            return []
        return (data.get("data") or {}).get("records") or []

    def list_records(self, app_token: str, table_id: str) -> List[Dict[str, Any]]:
        """列出表内全部记录（自动翻页），返回 record_id + fields。"""
        records: List[Dict[str, Any]] = []
        page_token: Optional[str] = None
        for _ in range(200):  # 200 页 × 500 条 = 10 万条上限，防死循环
            params: Dict[str, Any] = {"page_size": 500}
            if page_token:
                params["page_token"] = page_token
            resp = requests.get(
                f"{FEISHU_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/records",
                headers=self._headers(),
                params=params,
                timeout=self.timeout,
            )
            data = self._safe_json(resp)
            if data.get("code") != 0:
                logger.error(f"list_records 失败: {data.get('msg')}")
                break
            body = data.get("data") or {}
            records.extend(body.get("items") or [])
            if not body.get("has_more"):
                break
            page_token = body.get("page_token")
            if not page_token:
                break
        return records

    def list_all_record_ids(self, app_token: str, table_id: str) -> List[str]:
        """列出表内全部 record_id（自动翻页）。仅返回 id，用于批量删除。"""
        record_ids: List[str] = []
        for item in self.list_records(app_token, table_id):
            rid = item.get("record_id")
            if rid:
                record_ids.append(rid)
        return record_ids

    def batch_delete_records(self, app_token: str, table_id: str, record_ids: List[str]) -> int:
        """批量删除记录。单次 500 条上限，自动分批。返回成功删除数量。"""
        if not record_ids:
            return 0
        deleted = 0
        for i in range(0, len(record_ids), 500):
            chunk = record_ids[i:i + 500]
            resp = requests.post(
                f"{FEISHU_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_delete",
                headers=self._headers(),
                json={"records": chunk},
                timeout=self.timeout,
            )
            data = self._safe_json(resp)
            if data.get("code") != 0:
                logger.error(f"batch_delete 失败: {data.get('msg')}")
                continue
            deleted += len(chunk)
        return deleted
