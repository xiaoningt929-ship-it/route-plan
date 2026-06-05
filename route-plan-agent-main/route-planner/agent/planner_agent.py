# ================================================================
# Agent核心
# ================================================================

import json
import os
import re
from models.schemas import PlanResponse, Route, Stop, Location
from agent.tools import TOOLS_SCHEMA, TOOLS_MAP
from cache_service import get_cached_route, set_cached_route
from core.poi_service import load_pois
from core.ugc_service import get_reviews_by_poi

_client = None

def get_client():
    global _client
    if _client is None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("未安装 openai 依赖，请先运行 pip install -r requirements.txt") from exc
        _client = OpenAI(
            api_key=os.getenv("LONGCAT_API_KEY"),
            base_url="https://api.longcat.chat/openai"
        )
    return _client

MODEL = "LongCat-2.0-Preview"

SYSTEM_PROMPT = """
你是一个专业的本地旅游路线规划Agent。

你的任务是根据用户的出行意图，自主调用工具，规划出3条风格不同的路线方案。

## 工作流程
1. 分析用户意图，提取城市、时长、预算、人群、偏好、出发地等信息
2. 调用 search_poi 搜索合适的景点
3. 对候选景点调用 get_ugc_info 查看真实用户评价和排队情况
4. 根据用户约束（如"不想排队"、"腿脚不便"）筛选或替换景点
5. 如果用户提供了出发地，优先推荐距离出发地近的景点
6. 调用 calculate_route_time 计算时间安排
7. 调用 check_budget 验证预算是否超出，超出则调整
8. 生成3条路线：省时版、省钱版、网红版

## 重要：每个stop必须包含真实坐标
每个停留点的location字段必须填入该POI的真实经纬度坐标。
坐标从search_poi返回的结果里获取，不要编造坐标。

## 输出格式
完成规划后，输出以下JSON格式（不要输出其他内容）：
{
  "routes": [
    {
      "style": "省时版",
      "description": "一句话亮点描述",
      "total_cost": 220,
      "total_duration_minutes": 420,
      "start_time": "09:00",
      "end_time": "16:00",
      "stops": [
        {
          "poi_id": "sh001",
          "name": "外滩",
          "arrive_time": "09:00",
          "duration": 90,
          "leave_time": "10:30",
          "cost": 0,
          "reason": "结合用户约束说明推荐理由",
          "location": {"lat": 31.2397, "lng": 121.4901}
        }
      ]
    },
    {"style": "省钱版", ...},
    {"style": "网红版", ...}
  ]
}

## 注意事项
- stops控制在3-5个，时间安排要合理
- reason要结合用户的具体需求说明，有针对性
- 如果用户说"不想排队"，查UGC后排队长的要换掉
- 如果预算不够，优先选免费或低价景点
- 老人腿脚不便：优先选室内、平路、距离近的景点
- location坐标必须来自search_poi返回的真实数据

## 严格要求
你的最终回复必须且只能是一个合法的JSON对象，从{开始，到}结束。
不要输出任何解释、分析、markdown格式或代码块。
不要输出```json或```。
直接输出JSON，不要有任何前缀或后缀文字。
"""


async def run(user_input: str, start_location: str = None) -> PlanResponse:
    cache_key = f"{user_input}|{start_location or ''}"
    cached = get_cached_route(cache_key)
    if cached:
        return PlanResponse(**cached)

    if not os.getenv("LONGCAT_API_KEY"):
        result = _run_local_demo(user_input)
        set_cached_route(cache_key, _model_dump(result))
        return result

    user_message = user_input
    if start_location:
        user_message += f"\n\n出发地：{start_location}"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message}
    ]

    client = get_client()

    while True:
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS_SCHEMA,
            tool_choice="auto",
            temperature=0.3,
            max_tokens=4096
        )

        message = response.choices[0].message
        messages.append(message)

        if message.tool_calls:
            for tool_call in message.tool_calls:
                func_name = tool_call.function.name
                func_args = json.loads(tool_call.function.arguments)
                func = TOOLS_MAP.get(func_name)
                result = func(**func_args) if func else {"error": f"未知工具: {func_name}"}
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result, ensure_ascii=False)
                })
        else:
            raw = message.content
            # LLM有时会在JSON外面包markdown代码块，清理掉
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                raise ValueError(f"LLM返回的JSON格式错误: {e}\n原始内容: {raw[:200]}")
            routes = _parse_routes(data["routes"])
            result = PlanResponse(
                status="success",
                user_input=user_input,
                routes=routes
            )
            set_cached_route(cache_key, _model_dump(result))
            return result


def _parse_routes(raw_routes: list) -> list[Route]:
    routes = []
    for r in raw_routes:
        stops = []
        for s in r.get("stops", []):
            loc = s.get("location", {})
            stops.append(Stop(
                poi_id=s["poi_id"],
                name=s["name"],
                arrive_time=s["arrive_time"],
                duration=s["duration"],
                leave_time=s["leave_time"],
                cost=s["cost"],
                reason=s["reason"],
                location=Location(
                    lat=loc.get("lat", 0),
                    lng=loc.get("lng", 0)
                )
            ))
        routes.append(Route(
            style=r["style"],
            description=r["description"],
            total_cost=r["total_cost"],
            total_duration_minutes=r["total_duration_minutes"],
            start_time=r["start_time"],
            end_time=r["end_time"],
            stops=stops
        ))
    return routes


def _run_local_demo(user_input: str) -> PlanResponse:
    """No-key fallback: build usable routes from local mock data."""
    city = _extract_city(user_input)
    budget = _extract_budget(user_input)
    group = _extract_group(user_input)
    avoid_queue = any(word in user_input for word in ["不排队", "少排队", "不想排队", "避开排队"])

    pois = [p for p in load_pois() if p.get("city") == city]
    if group:
        group_matches = [p for p in pois if group in p.get("suitable_for", [])]
        if len(group_matches) >= 3:
            pois = group_matches

    scored = sorted(
        pois,
        key=lambda p: _poi_score(p, budget=budget, avoid_queue=avoid_queue),
        reverse=True
    )

    budget_candidates = sorted(scored, key=lambda p: (p.get("cost", 0), _wait_minutes(p), -p.get("score", 0)))
    if budget is not None:
        budget_candidates = [p for p in budget_candidates if p.get("cost", 0) <= budget]
    if len(budget_candidates) < 3:
        budget_candidates = sorted(scored, key=lambda p: (p.get("cost", 0), _wait_minutes(p), -p.get("score", 0)))

    route_specs = [
        ("平衡推荐", "综合评分、预算和排队情况，适合作为默认游玩路线。", scored[:5]),
        ("省钱少排队", "优先选择低费用、等待时间较短的地点。", budget_candidates[:5]),
        ("经典打卡", "优先覆盖城市代表性景点和热门拍照点。", sorted(pois, key=lambda p: ("网红" not in p.get("category", []), -p.get("score", 0)))[:5]),
    ]

    routes = []
    used_signatures = set()
    for style, description, candidates in route_specs:
        selected = _dedupe(candidates)[:4]
        if len(selected) < 3:
            selected = _dedupe(scored)[:4]
        selected = _ensure_unique_selection(selected, scored, used_signatures)
        selected = _order_no_backtracking(selected)
        signature = tuple(p["id"] for p in selected)
        used_signatures.add(signature)
        routes.append(_build_route(style, description, selected))

    return PlanResponse(
        status="success",
        user_input=user_input,
        routes=routes
    )


def _extract_city(user_input: str) -> str:
    for city in ["上海", "北京", "成都"]:
        if city in user_input:
            return city
    return "上海"


def _extract_budget(user_input: str) -> int | None:
    match = re.search(r"预算\s*(\d+)|(\d+)\s*元", user_input)
    if not match:
        return None
    value = match.group(1) or match.group(2)
    return int(value)


def _extract_group(user_input: str) -> str | None:
    for group in ["亲子", "老人", "情侣", "朋友", "独行"]:
        if group in user_input:
            return group
    if "孩子" in user_input or "小朋友" in user_input:
        return "亲子"
    return None


def _poi_score(poi: dict, budget: int | None, avoid_queue: bool) -> float:
    score = poi.get("score", 0) * 10
    wait = _wait_minutes(poi)
    cost = poi.get("cost", 0)
    if budget is not None and cost > budget:
        score -= 20
    if avoid_queue:
        score -= wait * 0.4
        if _crowd_level(poi) == "高":
            score -= 10
    if "免费" in poi.get("tags", []):
        score += 3
    return score


def _wait_minutes(poi: dict) -> int:
    return get_reviews_by_poi(poi["id"]).get("avg_wait_minutes", 0)


def _crowd_level(poi: dict) -> str:
    return get_reviews_by_poi(poi["id"]).get("crowd_level", "未知")


def _dedupe(pois: list[dict]) -> list[dict]:
    seen = set()
    result = []
    for poi in pois:
        if poi["id"] in seen:
            continue
        seen.add(poi["id"])
        result.append(poi)
    return result


def _ensure_unique_selection(
    selected: list[dict],
    fallback: list[dict],
    used_signatures: set[tuple[str, ...]]
) -> list[dict]:
    signature = tuple(p["id"] for p in _order_no_backtracking(selected))
    if signature not in used_signatures:
        return selected
    for size in [3, 4]:
        for offset in range(0, max(1, len(fallback) - size + 1)):
            candidate = _dedupe(fallback[offset:offset + size])
            if len(candidate) < 3:
                continue
            candidate_signature = tuple(p["id"] for p in _order_no_backtracking(candidate))
            if candidate_signature not in used_signatures:
                return candidate
    return selected


def _order_no_backtracking(pois: list[dict]) -> list[dict]:
    """Greedy nearest-neighbor ordering to avoid obvious zigzags."""
    if len(pois) <= 2:
        return pois
    remaining = pois[:]
    current = min(remaining, key=lambda p: (p["location"]["lng"], p["location"]["lat"]))
    ordered = [current]
    remaining.remove(current)
    while remaining:
        current = min(remaining, key=lambda p: _distance(ordered[-1], p))
        ordered.append(current)
        remaining.remove(current)
    return ordered


def _distance(a: dict, b: dict) -> float:
    return (
        (a["location"]["lat"] - b["location"]["lat"]) ** 2
        + (a["location"]["lng"] - b["location"]["lng"]) ** 2
    )


def _build_route(style: str, description: str, pois: list[dict]) -> Route:
    schedule = TOOLS_MAP["calculate_route_time"]([p["id"] for p in pois], "09:00")
    poi_map = {p["id"]: p for p in pois}
    stops = []
    for item in schedule["schedule"]:
        poi = poi_map[item["poi_id"]]
        ugc = get_reviews_by_poi(poi["id"])
        reason = f"{poi['score']}分，{ugc.get('crowd_level', '未知')}拥挤，适合{','.join(poi.get('suitable_for', [])[:2])}。"
        stops.append(Stop(
            poi_id=poi["id"],
            name=poi["name"],
            arrive_time=item["arrive_time"],
            duration=item["duration"],
            leave_time=item["leave_time"],
            cost=item["cost"],
            reason=reason,
            location=Location(**poi["location"])
        ))
    return Route(
        style=style,
        description=description,
        total_cost=schedule["total_cost"],
        total_duration_minutes=schedule["total_duration_minutes"],
        start_time="09:00",
        end_time=schedule["end_time"],
        stops=stops
    )


def _model_dump(model):
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()
