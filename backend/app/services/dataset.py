"""数据集管理业务规则：状态流转、字段校验与筛选口径都收在这里。

字段完整性修复口径：
- 接入处理只读取来源字段清单与数据集历史版本，解析已有输入后按来源字段清单
  补齐缺失字段，生成字段完整的可用数据集版本；
- 字段缺失说明随数据集版本保存，修复后能区分真实空列和漏接字段；
- 字段完整性报告保留业务输入，列表与详情共用同一份按既有次序去重的口径，
  重复提交不会生成第二份版本或复核确认。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.store import store

MODULE = "dataset"
LIST_FIELDS = ["数据集编号", "数据集名称", "来源类型", "字段数量", "行数估算", "刷新周期", "负责人", "数据集状态"]
REQUIRED_FIELDS = ["数据集编号", "数据集名称", "来源类型"]
STATUS_ORDER = ["待接入", "已接入", "使用中", "已归档"]
ACTION_RULES = {"完成接入": "已接入", "启用数据集": "使用中", "归档数据集": "已归档"}
NEGATIVE_ACTIONS = []

INGEST_ACTION = "完成接入"
STRATEGY_FILL = "补齐空字段"
STRATEGY_DROP = "剔除缺失字段"
INGEST_STRATEGIES = [STRATEGY_FILL, STRATEGY_DROP]
REPORT_TYPES = ["报错", "不生效", "残留", "重复显示"]
FILL_MISSING_NOTE = "来源表暂时缺少该列，按来源字段清单补齐为空列，等待业务侧回填"
DROP_MISSING_NOTE = "来源字段清单缺少该列，已从新版本 schema 中剔除"
CONTRACT_REVIEW_MODULE = "_field_contract_review"


def today() -> str:
    return date.today().isoformat()


def split_fields(value: Any) -> tuple[list[str], list[str]]:
    """把列表或逗号/顿号分隔的字符串解析成字段清单。

    返回 (去重后字段, 重复出现的字段)，次序沿用输入中的既有次序。
    """
    if value is None:
        return [], []
    items = value if isinstance(value, (list, tuple)) else str(value).replace("、", ",").split(",")
    fields: list[str] = []
    duplicates: list[str] = []
    for item in items:
        name = str(item).strip()
        if not name:
            continue
        if name in fields:
            if name not in duplicates:
                duplicates.append(name)
        else:
            fields.append(name)
    return fields, duplicates


def report_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (str(item.get("类型", "")), str(item.get("字段", "")), str(item.get("丢失原因", "")))


def canonical_report(row: dict[str, Any]) -> list[dict[str, Any]]:
    """字段完整性报告的唯一口径：按既有次序去重。

    列表与详情都从这里读，避免列表重复显示、详情残留旧数据导致的错位。
    返回的始终是写回 row 的同一份列表，调用方追加条目不会丢失。
    """
    report = row.setdefault("字段完整性报告", [])
    seen: set[tuple[str, str, str]] = set()
    canonical: list[dict[str, Any]] = []
    for item in report:
        key = report_key(item)
        if key in seen:
            continue
        seen.add(key)
        canonical.append(item)
    row["字段完整性报告"] = canonical
    return canonical


def append_report(row: dict[str, Any], entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把新条目并入字段完整性报告：沿用既有次序，相同条目不重复写入。"""
    report = canonical_report(row)
    existing = {report_key(item) for item in report}
    added: list[dict[str, Any]] = []
    for item in entries:
        key = report_key(item)
        if key in existing:
            continue
        existing.add(key)
        report.append(item)
        added.append(item)
    return added


def version_signature(strategy: str, input_fields: list[str], source_fields: list[str]) -> str:
    return f"{strategy}|{','.join(input_fields)}|{','.join(source_fields)}"


class DatasetService:
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
        for row in rows:
            canonical_report(row)
        for field, expected in (filters or {}).items():
            rows = [row for row in rows if expected.casefold() in str(row.get(field, "")).casefold()]
        if keyword:
            rows = [row for row in rows if keyword.casefold() in str(row.get("数据集编号", "")).casefold()]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is not None:
            canonical_report(entry)
        return entry

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in LIST_FIELDS})
        entry["来源字段清单"] = split_fields(values.get("来源字段清单"))[0]
        entry["接入字段"] = split_fields(values.get("接入字段"))[0]
        entry["数据集版本"] = []
        entry["字段完整性报告"] = []
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"分析数据集 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于数据集管理可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        note = ""
        if action == INGEST_ACTION:
            # 完成接入时沿着既有数据流做字段核对：解析已有输入、补齐缺失字段、生成可用版本
            note, _ = self._reconcile(entry, {}, strategy=STRATEGY_FILL, trigger=action)
            note = f"；{note}"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"分析数据集已{action}{note}"

    def ingest(self, entry_id: int, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str, bool]:
        """接入处理入口：解析已有输入，按来源字段清单补齐缺失字段并生成可用数据集版本。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"分析数据集 {entry_id} 不存在或已归档", False
        strategy = str(payload.get("策略") or STRATEGY_FILL).strip()
        if strategy not in INGEST_STRATEGIES:
            return None, f"处理策略「{strategy}」不支持，可选：{'、'.join(INGEST_STRATEGIES)}", False
        message, _version = self._reconcile(entry, payload, strategy=strategy, trigger="接入处理")
        return entry, message, True

    def _reconcile(
        self,
        entry: dict[str, Any],
        payload: dict[str, Any],
        *,
        strategy: str,
        trigger: str,
    ) -> tuple[str, dict[str, Any] | None]:
        """核心处理：解析已有输入，对照来源字段清单与历史版本，补齐缺失字段并生成可用版本。"""
        if "接入字段" in payload:
            input_fields, input_duplicates = split_fields(payload.get("接入字段"))
            entry["接入字段"] = input_fields
        else:
            input_fields, input_duplicates = split_fields(entry.get("接入字段"))
            entry["接入字段"] = input_fields
        if "来源字段清单" in payload:
            source_fields, _ = split_fields(payload.get("来源字段清单"))
            entry["来源字段清单"] = source_fields
        else:
            source_fields, _ = split_fields(entry.get("来源字段清单"))
            entry["来源字段清单"] = source_fields

        report_candidates: list[dict[str, Any]] = []
        # 保留字段完整性报告的业务输入：调用方提交的条目校验类型后原样并入
        for item in payload.get("字段完整性报告") or []:
            if not isinstance(item, dict):
                continue
            report_type = str(item.get("类型") or "").strip()
            if report_type not in REPORT_TYPES:
                continue
            report_candidates.append({
                "类型": report_type,
                "字段": str(item.get("字段") or "—"),
                "丢失原因": str(item.get("丢失原因") or "业务侧登记，未填写原因"),
                "时间": today(),
                "复核状态": "未复核",
            })

        for name in input_duplicates:
            report_candidates.append({
                "类型": "重复显示",
                "字段": name,
                "丢失原因": "接入输入中重复出现，已按既有次序只保留第一次",
                "时间": today(),
                "复核状态": "未复核",
            })

        if not source_fields and not input_fields:
            append_report(entry, report_candidates)
            return "没有可解析的接入输入与来源字段清单，本次不生成数据集版本", None
        if not source_fields:
            report_candidates.append({
                "类型": "报错",
                "字段": "—",
                "丢失原因": "缺少来源字段清单，无法核对字段完整性",
                "时间": today(),
                "复核状态": "未复核",
            })
            append_report(entry, report_candidates)
            return "缺少来源字段清单，无法补齐字段，已写入字段完整性报告", None

        missing = [name for name in source_fields if name not in input_fields]
        extra = [name for name in input_fields if name not in source_fields]
        for name in extra:
            report_candidates.append({
                "类型": "残留",
                "字段": name,
                "丢失原因": "接入字段不在来源字段清单内，属于历史版本残留，未写入新版本",
                "时间": today(),
                "复核状态": "未复核",
            })

        note = payload.get("字段缺失说明")
        missing_notes: dict[str, str] = {}
        for name in missing:
            reason = ""
            if isinstance(note, dict):
                reason = str(note.get(name) or "").strip()
            elif note:
                reason = str(note).strip()
            if not reason:
                reason = FILL_MISSING_NOTE if strategy == STRATEGY_FILL else DROP_MISSING_NOTE
            missing_notes[name] = reason
            report_candidates.append({
                "类型": "不生效",
                "字段": name,
                "丢失原因": reason,
                "时间": today(),
                "复核状态": "未复核",
            })

        # 两种有效路径：保留空字段等待补齐，或剔除字段形成新版本，查询可用结果不同
        if strategy == STRATEGY_FILL:
            version_fields = list(source_fields)
        else:
            version_fields = [name for name in source_fields if name not in missing]

        signature = version_signature(strategy, input_fields, source_fields)
        versions = entry.setdefault("数据集版本", [])
        append_report(entry, report_candidates)
        for version in versions:
            if version.get("输入签名") == signature:
                return f"版本 v{version['版本号']} 已覆盖相同输入，重复提交未生成第二份版本", version

        version = {
            "版本号": max((int(item.get("版本号", 0)) for item in versions), default=0) + 1,
            "策略": strategy,
            "字段清单": version_fields,
            "缺失字段": missing,
            "字段缺失说明": missing_notes,
            "可用": True,
            "生成时间": today(),
            "输入签名": signature,
            "触发方式": trigger,
        }
        versions.append(version)
        entry["字段数量"] = len(version_fields)
        if strategy == STRATEGY_FILL:
            message = f"已生成可用数据集版本 v{version['版本号']}，字段数量 {len(version_fields)} 与来源 schema 一致"
        else:
            message = (
                f"已生成可用数据集版本 v{version['版本号']}，"
                f"剔除 {len(missing)} 个缺失字段后字段数量 {len(version_fields)}"
            )
        return message, version

    def list_versions(self, entry_id: int) -> list[dict[str, Any]] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        return entry.setdefault("数据集版本", [])

    def integrity_report(self, entry_id: int) -> list[dict[str, Any]] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        return canonical_report(entry)

    def field_contract(self) -> dict[str, Any]:
        """字段契约视图：跨业务模块读取字段完整性报告，按既有业务数据流次序汇总。"""
        datasets: list[dict[str, Any]] = []
        for row in store.rows(MODULE):
            versions = row.get("数据集版本") or []
            current = versions[-1] if versions else None
            datasets.append({
                "id": row.get("id"),
                "数据集编号": row.get("数据集编号"),
                "来源字段清单": row.get("来源字段清单") or [],
                "当前版本号": current.get("版本号") if current else None,
                "当前版本字段": current.get("字段清单") if current else [],
                "缺失字段": current.get("缺失字段") if current else [],
                "字段完整性报告": canonical_report(row),
            })
        templates: list[dict[str, Any]] = []
        for row in store.rows("query_builder"):
            templates.append({
                "id": row.get("id"),
                "模板编号": row.get("模板编号"),
                "数据集范围": row.get("数据集范围"),
                "字段完整性报告": canonical_report(row),
            })
        return {"数据集": datasets, "查询模板": templates}

    def review_field_contract(self) -> tuple[dict[str, Any] | None, str]:
        """字段契约接口（复核）：消费各模块字段完整性报告，重复提交不多出第二份确认结果。"""
        contract = self.field_contract()
        collected: list[dict[str, Any]] = []
        for block in contract["数据集"]:
            for item in block["字段完整性报告"]:
                collected.append({"模块": MODULE, "记录id": block["id"], "条目": item})
        for block in contract["查询模板"]:
            for item in block["字段完整性报告"]:
                collected.append({"模块": "query_builder", "记录id": block["id"], "条目": item})
        if not collected:
            return None, "各业务模块均没有可复核的字段完整性报告"
        parts: list[str] = []
        for entry in collected:
            report_type, field, reason = report_key(entry["条目"])
            parts.append(f"{entry['模块']}:{entry['记录id']}:{report_type}:{field}:{reason}")
        signature = ";".join(parts)
        reviews = store.rows(CONTRACT_REVIEW_MODULE)
        for review in reviews:
            if review.get("签名") == signature:
                return review, "字段契约复核已存在相同确认，重复提交未生成第二份确认结果"
        for entry in collected:
            entry["条目"]["复核状态"] = "已复核"
        review = {
            "id": max((int(item.get("id", 0)) for item in reviews), default=0) + 1,
            "确认编号": f"FCR-{len(reviews) + 1:04d}",
            "复核条目数": len(collected),
            "复核时间": today(),
            "签名": signature,
        }
        reviews.append(review)
        return review, f"字段契约复核完成，共复核 {len(collected)} 条字段完整性报告"
