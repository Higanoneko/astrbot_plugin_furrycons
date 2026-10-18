from dataclasses import asdict, dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from .errors import SourceError
from .regions import display_region


def text(value, default=""):
    return value.strip() if isinstance(value, str) else default


def display_date(value, timezone):
    if not value:
        return "日期未明确"
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return "日期未明确（源数据无时区）"
        return parsed.astimezone(ZoneInfo(timezone)).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return "日期未明确（源数据无效）"


@dataclass(frozen=True)
class Convention:
    id: str
    name: str
    status: str
    start: str
    end: str
    region: str
    address: str
    description: str
    source: str

    @classmethod
    def from_api(cls, data):
        if not isinstance(data, dict) or not text(data.get("id")):
            raise SourceError("数据源响应缺少展会身份，请稍后重试或联系机器人管理者。")
        return cls(
            id=text(data["id"]),
            name=text(data.get("name"), "名称未明确") or "名称未明确",
            status=text(data.get("status")),
            start=text(data.get("startDate")),
            end=text(data.get("endDate")),
            region=text(data.get("region")) or "地区未明确",
            address=text(data.get("address")) or "地址未明确",
            description=text(data.get("detail")),
            source=text(data.get("url")),
        )

    def detail_view(self, timezone):
        result = asdict(self)
        result["timezone"] = timezone
        result["region"] = display_region(self.region)
        result["status_label"] = {
            "scheduled": "计划举办",
            "cancelled": "已取消",
            "postponed": "延期 · 日期待定",
            "rescheduled": "已改期",
        }.get(self.status, f"未知状态（{self.status or '未提供'}）")
        result["start_label"] = display_date(self.start, timezone)
        result["end_label"] = display_date(self.end, timezone)
        if self.status == "postponed":
            result["start_label"] = result["end_label"] = "日期待定（旧日期不作日程）"
        excerpt = self.description[:1800]
        if len(self.description) > len(excerpt):
            excerpt += "\n……（更多内容请查看来源）"
        result["introduction"] = excerpt or "暂无介绍"
        result["introduction_type"] = "原文节选"
        result["source"] = self.source or "来源链接未提供"
        return result
