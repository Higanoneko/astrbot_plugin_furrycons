"""Clearly labelled illustrative data for deployment and theme previews."""

from .models import Convention


def preview_data(theme_key="builtin:material", command_entry="FEC"):
    event = Convention.from_api(
        {
            "id": "preview-event",
            "name": "山海相逢 · 主题预览示例",
            "status": "scheduled",
            "startDate": "2026-11-07T02:00:00+00:00",
            "endDate": "2026-11-08T10:00:00+00:00",
            "region": "深圳市",
            "address": "示例会场（非真实活动）",
            "url": "https://www.furrycons.cn",
            "detail": "这是用于检查主题排版的示例资料，不代表真实展会安排。\n\n"
            "山海之间，与朋友相逢。请检查长名称、中文字体、日期、状态、地址和来源是否清晰可读。\n\n"
            "正式展会信息请以查询结果和来源页面为准。",
        }
    ).detail_view("Asia/Shanghai")
    event.update(theme_key=theme_key, number=1, schedule_note="")
    undated = Convention.from_api(
        {"id": "preview-undated", "name": "北境之约 · 日期待定示例", "status": "postponed"}
    ).detail_view("Asia/Shanghai")
    undated.update(number=2, schedule_note="日期待定（不归入上述日程范围）")
    return {
        "event": event,
        "query": {
            "command_entry": command_entry,
            "layout": "compact",
            "theme_key": theme_key,
            "range": "主题预览 · 示例数据（非真实展会）",
            "code": "PREVIEW",
            "page": 1,
            "pages": 1,
            "total": 2,
            "events": [event, undated],
        },
    }
