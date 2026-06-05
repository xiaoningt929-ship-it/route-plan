# AI Route Planner

本项目是一个本地智能路线规划 Demo。用户输入游玩目标后，后端结合本地 POI 和 UGC mock 数据生成路线，前端展示路线方案和地图点位。

## 当前能力

- FastAPI 后端接口：`POST /api/plan`
- React + Vite 前端页面
- 本地 mock POI/UGC 数据
- 有 `LONGCAT_API_KEY` 时调用 LongCat/OpenAI 兼容接口生成路线
- 没有 API key 时自动使用本地 demo 规划逻辑，保证项目可以本地跑通
- 输出 3 类路线：平衡推荐、省钱少排队、经典打卡

## 后端启动

```bash
cd route-planner
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --reload --port 8000
```

如果暂时没有 `LONGCAT_API_KEY`，可以留空，系统会使用本地 mock 规划。

## 前端启动

```bash
cd route-planner/frontend
npm install
npm run dev
```

如果遇到 Rollup 原生依赖报错，通常是因为 `node_modules` 来自其他系统。删除 `frontend/node_modules` 后重新执行 `npm install` 即可。

默认访问：

```text
http://localhost:5173
```

## 示例请求

```bash
curl -X POST http://127.0.0.1:8000/api/plan \
  -H "Content-Type: application/json" \
  -d '{"user_input":"我和朋友在上海玩一天，预算300，不想排队","start_location":"上海人民广场"}'
```

## 目录结构

```text
route-planner/
  main.py                 FastAPI 入口
  api/routes.py           HTTP API
  agent/planner_agent.py  AI Agent 与本地兜底规划
  agent/tools.py          POI/UGC/时间/预算工具
  core/                   数据读取与筛选
  models/schemas.py       Pydantic 数据结构
  mock_data/              本地 POI 和 UGC 数据
  frontend/               React 前端
```
