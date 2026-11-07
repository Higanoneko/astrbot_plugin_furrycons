import re
from urllib.parse import quote

import httpx

from .errors import SourceError, UserError
from .models import Convention


class FurryConsSource:
    def __init__(self, settings, transport=None):
        self.settings = settings
        self.client = httpx.AsyncClient(
            timeout=30,
            follow_redirects=False,
            transport=transport,
            headers={"Authorization": settings.api_key},
        )

    async def close(self):
        await self.client.aclose()

    async def detail(self, event_id):
        if not isinstance(event_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", event_id):
            raise UserError(
                f"展会 ID 无效。请使用 /{self.settings.command_entry} 详情 <ID>，可从列表或来源链接获取 ID。"
            )
        data = await self._get(f"event/{quote(event_id, safe='')}", detail=True)
        convention = Convention.from_api(data)
        if convention.id != event_id:
            raise SourceError("数据源返回的展会身份不匹配，未生成详情；请稍后重试。")
        return convention

    async def _get(self, path, params=None, *, detail=False):
        if not self.settings.api_key:
            raise SourceError("尚未配置 FurryCons API Key，请机器人管理者在插件配置中填写。")
        try:
            response = await self.client.get(
                f"{self.settings.api_base.rstrip('/')}/{path}", params=params
            )
            if response.status_code == 404 and detail:
                raise UserError("未找到该展会，请核对 ID 或重新查询。")
            if response.status_code in (401, 403):
                raise SourceError("数据源鉴权失败，请机器人管理者检查 API Key 与访问权限。")
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError):
            # Never include request/response text or headers in a public diagnostic.
            raise SourceError(
                "展会数据源暂不可用，请稍后重试；管理者可检查数据源配置与网络。"
            ) from None
        return data

    async def list_all(self):
        # Date predicates upstream are deliberately not used: fetching every page
        # preserves ongoing events and postponed records carrying old dates.
        for attempt in range(2):
            rows, seen, expected, current = [], set(), None, 1
            while True:
                payload = await self._get("event/", {"current": current, "pageSize": 100})
                if not isinstance(payload, dict):
                    raise SourceError("展会列表响应格式无效，请稍后重试。")
                total, size = payload.get("total"), payload.get("pageSize")
                data = payload.get("data")
                if (
                    type(total) is not int
                    or total < 0
                    or type(size) is not int
                    or size < 1
                    or payload.get("current") != current
                    or not isinstance(data, list)
                ):
                    raise SourceError("展会列表分页契约无效，无法确认结果完整，请稍后重试。")
                if expected is None:
                    expected = total
                if total != expected or len(data) > size:
                    break
                conventions = [Convention.from_api(row) for row in data]
                ids = {row.id for row in conventions}
                if seen.intersection(ids) or len(ids) != len(conventions):
                    break
                seen.update(ids)
                rows.extend(conventions)
                if len(rows) == total:
                    return rows
                if not data or len(rows) > total or current * size >= total:
                    break
                current += 1
        raise SourceError("上游列表在分页期间变化或分页不完整，未提供不完整结果，请重新查询。")
