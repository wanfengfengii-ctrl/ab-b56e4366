# 移动式穹顶测绘平台 · 安全操作演练

浏览器端演练"收车/调整仪器姿态"时的安全操作次序：录入 4–6 只支腿平面坐标与
初始部署状态、4–8 个载荷姿态（投影中心 + 非负不确定半径），在支承多边形视图上
逐次切换载荷姿态或收放支腿。任一可能导致载荷误差圆盘越出支承面的操作、部署
支腿不足以形成安全支承的操作，或携带过期修订号的操作，都会被服务端明确拒绝，
且拒绝操作不会写入事件轨迹。

## 业务规则

- 支承多边形 = 全部**已部署**支腿平面坐标的凸包（少于 3 只或共线即无有效支承）。
- 安全条件：载荷圆盘完整位于凸包内——圆心到每条支承边的有符号距离 ≥ 不确定半径；
  **距离恰好相等（边界相切）视为安全**。
- 每次只能执行一个操作：切换一个载荷姿态，或收/放一只支腿。
- 每个操作必须携带客户端所见修订号（乐观并发控制）；修订号 = 事件轨迹中已落盘
  事件的自增序号，过期返回 `409`。
- 事件轨迹为本地 append-only JSONL（默认 `/data/events.jsonl`），只追加、永不改写；
  服务启动时从头重放重建状态。初始录入（`session_initialized`）也是一条事件。
- 页面持续显示：当前修订号、各支腿部署状态、当前载荷姿态、最近一次拒绝理由。

## 运行

```bash
# 宿主机端口可配置（默认 8080）
HOST_PORT=9000 docker compose up --build web
# 打开 http://localhost:9000
```

健康检查：`GET /healthz` → `200 {"status":"ok","revision":N}`（Dockerfile 内置
容器 HEALTHCHECK，compose 中亦有声明）。

事件轨迹持久化在命名卷 `event-data`（容器内 `/data`）。

## 验收（一次性 verify 服务）

`verify` 服务完成 **代码编译检查 → 单元测试 → 几何/HTTP 业务冒烟** 后自行退出，
以退出码报告验收结果：

```bash
docker compose up --build verify      # 随 compose up 执行，Exited(0) 即验收通过
# 或单独运行：
docker compose run --rm verify
```

冒烟覆盖：健康检查、初始安全校验启动、凸包包含圆盘、相切安全、越界收腿/姿态
切换返回 422 且轨迹不增长、过期修订号返回 409、合法操作推进修订号、杀掉进程后
重启从轨迹重放出同一修订号、拒绝重复录入。

## 本地开发（无需 Docker，仅用 Python 3.11 标准库）

```bash
python3 -m app.server                 # 启动服务，PORT / EVENTS_PATH 可覆盖
sh scripts/verify.sh                  # 本地执行完整验收
```

## 目录结构

```
app/
  geometry.py      凸包（单调链）与圆盘包含判定（有符号边距 ≥ 半径）
  store.py         append-only JSONL 事件轨迹、重放、修订号与操作校验
  server.py        标准库 HTTP 服务：/healthz、/api/*、静态前端
  static/          浏览器页面（Canvas 绘制支承多边形与误差圆盘）
tests/test_all.py  几何与事件轨迹单元测试（25 项）
scripts/
  verify.sh        verify 服务入口（编译→测试→冒烟，失败即非零退出）
  smoke.py         真实 HTTP 端到端冒烟（含轨迹重放校验）
Dockerfile         python:3.11-slim，零第三方依赖，内置 HEALTHCHECK
docker-compose.yml web 服务（HOST_PORT 可配置）+ 一次性 verify 服务
```

## HTTP API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/healthz` | 健康检查 |
| GET | `/api/state` | 当前完整状态（修订号、支腿、姿态、凸包、最近拒绝理由） |
| POST | `/api/session` | 录入数据并启动演练（初始状态必须安全，否则拒绝，不写轨迹） |
| POST | `/api/legs/toggle` | `{revision, leg_index}` 收/放一只支腿 |
| POST | `/api/poses/switch` | `{revision, pose_index}` 切换一个载荷姿态 |

拒绝响应：`409` 修订号过期 / `422` 安全规则拒绝（均附带最新 `state` 供页面
立即对齐）/ `400` 录入或命令格式错误。
