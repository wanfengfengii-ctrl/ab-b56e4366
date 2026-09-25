"""几何业务 + HTTP 端到端冒烟。

流程：
1. 启动真实 HTTP 服务（子进程，临时事件轨迹、临时端口）；
2. 健康检查、初始状态查询；
3. 启动演练 → 校验凸包包含圆盘；
4. 越界收腿被拒（422）且轨迹不增加；
5. 过期修订号被拒（409）；
6. 合法姿态切换（相切）被接受；
7. 杀掉进程后重新启动，校验事件轨迹重放得到同一修订号；
8. 检查事件轨迹文件仅含被接受的事件。

任一断言失败即以非零退出码退出，供 compose verify 服务报告验收结果。
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_health(base: str, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/healthz", timeout=1) as resp:
                if resp.status == 200:
                    return
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.25)
    raise RuntimeError(f"服务健康检查超时：{last}")


def request(method: str, url: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def start_server(port: int, events_path: str) -> subprocess.Popen:
    env = dict(os.environ, PORT=str(port), EVENTS_PATH=events_path)
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.server"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        wait_health(f"http://127.0.0.1:{port}")
    except Exception:
        out = proc.stdout.read() if proc.stdout else ""
        proc.kill()
        raise RuntimeError(f"服务未能启动：\n{out}")
    return proc


def main() -> int:
    tmpdir = tempfile.mkdtemp(prefix="dome-smoke-")
    events_path = os.path.join(tmpdir, "events.jsonl")
    port = free_port()
    base = f"http://127.0.0.1:{port}"

    proc = start_server(port, events_path)
    try:
        # 1. 健康检查
        status, body = request("GET", base + "/healthz")
        assert status == 200 and body["status"] == "ok", body

        # 2. 未启动状态
        status, state = request("GET", base + "/api/state")
        assert status == 200 and state["started"] is False, state

        # 3. 启动演练：四角 + 一条中心腿，圆盘半径 1.5
        setup = {
            "legs": [
                {"x": 0, "y": 0, "deployed": True},
                {"x": 10, "y": 0, "deployed": True},
                {"x": 10, "y": 10, "deployed": True},
                {"x": 0, "y": 10, "deployed": True},
                {"x": 5, "y": 5, "deployed": True},
            ],
            "poses": [
                {"x": 5, "y": 5, "radius": 1.5},
                {"x": 9.2, "y": 9.2, "radius": 1.0},   # 越界姿态
                {"x": 5, "y": 5, "radius": 5.0},       # 与四边相切
                {"x": 1, "y": 1, "radius": 0.2},
            ],
            "current_pose": 0,
        }
        status, state = request("POST", base + "/api/session", setup)
        assert status == 200, f"启动演练失败：{state}"
        assert state["revision"] == 1 and state["safe"] is True, state
        assert len(state["hull"]) == 4, "凸包应由四角构成"

        # 4. 收起角腿 → 三角形无法包含圆盘 → 422 拒绝，轨迹不变
        status, body = request("POST", base + "/api/legs/toggle",
                               {"revision": 1, "leg_index": 0})
        assert status == 422, body
        assert body["error"] == "operation_rejected", body
        assert "越出支承面" in body["reason"] or "至少需要 3" in body["reason"], body["reason"]
        assert body["state"]["revision"] == 1, "拒绝操作不得推进修订号"
        assert body["state"]["legs"][0]["deployed"] is True

        # 5. 过期修订号 → 409
        status, body = request("POST", base + "/api/poses/switch",
                               {"revision": 99, "pose_index": 1})
        assert status == 409 and body["error"] == "stale_revision", body
        assert body["state"]["revision"] == 1

        # 6. 切换到越界姿态 → 422
        status, body = request("POST", base + "/api/poses/switch",
                               {"revision": 1, "pose_index": 1})
        assert status == 422, body
        assert body["state"]["current_pose"] == 0

        # 7. 合法切换到相切姿态 → 接受，修订号推进
        status, state = request("POST", base + "/api/poses/switch",
                                {"revision": 1, "pose_index": 2})
        assert status == 200 and state["revision"] == 2, state
        assert state["current_pose"] == 2 and state["safe"] is True
        assert abs(state["min_margin"]) < 1e-9, "相切余量应为 0"

        # 8. 在中心腿辅助下收起角腿：四角缺一时内切大圆盘必然越界 → 拒绝
        status, body = request("POST", base + "/api/legs/toggle",
                               {"revision": 2, "leg_index": 0})
        assert status == 422, body
    finally:
        proc.terminate()
        proc.wait(timeout=10)

    # 9. 轨迹文件只含 2 条已接受事件
    with open(events_path, encoding="utf-8") as fh:
        events = [json.loads(line) for line in fh if line.strip()]
    assert [e["seq"] for e in events] == [1, 2], events
    assert events[0]["type"] == "session_initialized"
    assert events[1]["type"] == "pose_switched"

    # 10. 重新启动服务：从不可改写轨迹重放，修订号保持 2
    port2 = free_port()
    proc2 = start_server(port2, events_path)
    try:
        status, state = request("GET", f"http://127.0.0.1:{port2}/api/state")
        assert status == 200, state
        assert state["started"] is True, "重放后应处于已启动状态"
        assert state["revision"] == 2, f"重放修订号错误：{state['revision']}"
        assert state["current_pose"] == 2
        assert state["safe"] is True
        # 重放后拒绝重复录入（轨迹不可改写）
        status, body = request("POST", f"http://127.0.0.1:{port2}/api/session", setup)
        assert status == 400, body
    finally:
        proc2.terminate()
        proc2.wait(timeout=10)

    print("冒烟通过：健康检查 / 凸包圆盘包含 / 相切安全 / 越界拒绝 / "
          "修订号冲突 / 事件轨迹重放 全部验证成功")
    return 0


if __name__ == "__main__":
    sys.exit(main())
