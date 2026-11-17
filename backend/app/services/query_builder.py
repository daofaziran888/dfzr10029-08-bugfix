"""OLAP查询业务规则：状态流转、字段校验与筛选口径都收在这里。

模板校验口径：查询模板读取数据集当前可用版本的 schema，读到不完整 schema 时
给出明确失败原因（缺哪些字段、字段缺失说明），并写入模板自己的字段完整性报告，
供字段契约接口跨业务模块复核。
"""
from __future__ import annotations

from typing import Any

from app.services.dataset import append_report, today
from app.store import store

MODULE = "query_builder"
LIST_FIELDS = ["模板编号", "模板名称", "查询语句", "参数定义", "数据集范围", "超时秒数", "缓存策略", "模板状态"]
REQUIRED_FIELDS = ["模板编号", "模板名称", "查询语句"]
STATUS_ORDER = ["草稿", "已保存", "已发布", "已停用"]
ACTION_RULES = {"保存模板": "已保存", "发布模板": "已发布", "停用模板": "已停用"}
NEGATIVE_ACTIONS = ["停用模板"]


class QueryBuilderService:
    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        for field, expected in (filters or {}).items():
            rows = [row for row in rows if expected.casefold() in str(row.get(field, "")).casefold()]
        if keyword:
            rows = [row for row in rows if keyword.casefold() in str(row.get("模板编号", "")).casefold()]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in LIST_FIELDS})
        entry["字段完整性报告"] = []
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"查询模板 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于OLAP查询可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"查询模板已{action}"

    def validate_template(self, entry_id: int) -> tuple[dict[str, Any] | None, str, bool]:
        """校验查询模板：读取数据集当前可用版本的 schema，不完整时给出明确失败原因。"""
        template = store.find(MODULE, entry_id)
        if template is None:
            return None, f"查询模板 {entry_id} 不存在或已归档", False
        scope = str(template.get("数据集范围") or "").strip()
        statement = str(template.get("查询语句") or "")
        dataset = None
        for row in store.rows("dataset"):
            if scope and scope in {str(row.get("数据集编号") or ""), str(row.get("数据集名称") or "")}:
                dataset = row
                break
        if dataset is None:
            reason = f"引用的数据集「{scope or '未填写'}」不存在，无法读取来源 schema"
            self._record_schema_error(template, ["—"], reason)
            return None, reason, False
        dataset_code = str(dataset.get("数据集编号"))
        source_fields = [str(name) for name in dataset.get("来源字段清单") or []]
        usable = [item for item in dataset.get("数据集版本") or [] if item.get("可用")]
        if not usable:
            reason = f"数据集「{dataset_code}」尚未生成可用数据集版本，请先在数据集管理完成接入处理"
            self._record_schema_error(template, source_fields or ["—"], reason)
            return None, reason, False
        current = usable[-1]
        schema = [str(name) for name in current.get("字段清单") or []]
        referenced = [name for name in source_fields if name in statement]
        missing = [name for name in referenced if name not in schema]
        if missing:
            notes = current.get("字段缺失说明") or {}
            detail = "；".join(f"{name}（{notes.get(name) or '未随版本保存字段缺失说明'}）" for name in missing)
            self._record_schema_error(template, missing, f"数据集 {dataset_code} 当前版本 schema 缺少引用字段")
            return None, (
                f"数据集「{dataset_code}」当前版本 schema 不完整，缺少字段：{detail}；"
                "查询已停止，请先补齐字段或剔除后生成新版本"
            ), False
        result = {
            "模板编号": template.get("模板编号"),
            "数据集编号": dataset_code,
            "版本号": current.get("版本号"),
            "字段数量": len(schema),
            "来源字段数量": len(source_fields),
            "引用字段": referenced,
        }
        return result, (
            f"校验通过：数据集「{dataset_code}」版本 v{current.get('版本号')} "
            f"字段数量 {len(schema)}（来源 schema {len(source_fields)}），引用字段均在 schema 内"
        ), True

    def _record_schema_error(self, template: dict[str, Any], fields: list[str], reason: str) -> None:
        """校验失败写入查询模板自己的字段完整性报告，供字段契约接口跨模块复核。"""
        append_report(template, [
            {"类型": "报错", "字段": name, "丢失原因": reason, "时间": today(), "复核状态": "未复核"}
            for name in fields
        ])
