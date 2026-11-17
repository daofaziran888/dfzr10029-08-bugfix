"""数据集管理接口：维护分析数据集，覆盖完成接入、启用数据集、归档数据集等动作。

字段完整性修复相关接口：
- POST /{entry_id}/ingest 接入处理：解析已有输入、补齐缺失字段、生成可用数据集版本
- GET  /{entry_id}/versions 数据集历史版本（字段缺失说明随版本保存）
- GET  /{entry_id}/integrity-report 字段完整性报告（与列表同一份去重口径）
- GET  /field-contract 字段契约接口（读）：跨业务模块汇总字段完整性报告
- POST /field-contract/review 字段契约接口（复核）：重复提交不多出第二份确认结果
"""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import ActionPayload, ActionResult, EntryPayload, PageResult
from app.services.dataset import DatasetService

router = APIRouter(prefix="/api/dataset", tags=["数据集管理"])

service = DatasetService()

LIST_FIELDS = ["数据集编号", "数据集名称", "来源类型", "字段数量", "行数估算", "刷新周期", "负责人", "数据集状态"]
STATUSES = ["待接入", "已接入", "使用中", "已归档"]


def query_filters(request: Request) -> dict[str, str]:
    """只接受当前模块声明过的字段，忽略分页等保留参数。"""
    return {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in LIST_FIELDS and value.strip()
    }


@router.get("/field-contract")
def get_field_contract() -> dict:
    """字段契约接口（读）：跨业务模块读取字段完整性报告，复核前对照既有数据。"""
    return service.field_contract()


@router.post("/field-contract/review", response_model=ActionResult)
def review_field_contract() -> ActionResult:
    """字段契约接口（复核）：消费字段完整性报告并复核对应记录；重复提交不会生成第二份确认结果。"""
    review, message = service.review_field_contract()
    if review is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=review)


@router.get("", response_model=PageResult[dict])
def list_entries(
    request: Request,
    keyword: str | None = Query(default=None, description="按数据集编号检索"),
    status: str | None = Query(default=None, description="待接入、已接入、使用中、已归档"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    """按页面字段、数据集编号关键字与状态过滤数据集管理列表。"""
    items, total = service.list_entries(
        filters=query_filters(request), keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries(
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> StreamingResponse:
    """下载当前筛选条件下的 UTF-8 CSV；必须声明在 /{entry_id} 之前。"""
    items, _ = service.list_entries(
        filters=query_filters(request), keyword=keyword, status=status, page=1, size=1000000)
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["id", *LIST_FIELDS, "status"])
    for item in items:
        row = []
        for value in [item.get("id"), *(item.get(field, "") for field in LIST_FIELDS), item.get("status", "")]:
            text = "" if value is None else str(value)
            row.append("'" + text if text.startswith(("=", "+", "-", "@")) else text)
        writer.writerow(row)
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="dataset.csv"'})


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条分析数据集明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"分析数据集 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: dict[str, object]) -> ActionResult:
    """登记一条分析数据集，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="分析数据集已登记", entry=entry)


@router.post("/{entry_id}/ingest", response_model=ActionResult)
def ingest_entry(entry_id: int, payload: dict[str, object]) -> ActionResult:
    """接入处理：解析已有输入，按来源字段清单补齐缺失字段并生成可用数据集版本。

    字段缺失说明随数据集版本保存；字段完整性报告的业务输入会被保留；
    相同输入重复提交不会生成第二份版本。
    """
    entry, message, ok = service.ingest(entry_id, payload)
    if not ok:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/{entry_id}/versions")
def list_versions(entry_id: int) -> dict:
    """数据集历史版本列表：字段缺失说明随每个版本保存，可区分真实空列和漏接字段。"""
    versions = service.list_versions(entry_id)
    if versions is None:
        raise HTTPException(status_code=404, detail=f"分析数据集 {entry_id} 不存在或已归档")
    return {"items": versions, "total": len(versions)}


@router.get("/{entry_id}/integrity-report")
def get_integrity_report(entry_id: int) -> dict:
    """字段完整性报告：与列表共用同一份按既有次序去重的口径，不保留旧残留。"""
    report = service.integrity_report(entry_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"分析数据集 {entry_id} 不存在或已归档")
    return {"items": report, "total": len(report)}


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: ActionPayload) -> ActionResult:
    """对单条分析数据集执行完成接入、启用数据集、归档数据集；不允许的动作会被拦下并说明原因。"""
    entry, message = service.run_action(entry_id, payload.action.strip())
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
