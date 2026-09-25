"""不可改写的本地事件轨迹与状态重放。

- 轨迹文件为 JSONL，仅以 append 方式写入，进程启动时从头重放重建状态。
- 每条事件落盘即获得自增序号（从 1 开始）；修订号 revision == 已落盘事件数。
- 所有操作都必须携带客户端所见修订号（乐观并发控制），过期则拒绝。
- 被拒绝的操作不会写入轨迹。
- 不变量：任意修订号下，当前部署支腿凸包都完整包含当前载荷圆盘。
"""

from __future__ import annotations

import copy
import json
import math
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .geometry import Point, evaluate_support

EV_SESSION = "session_initialized"
EV_LEG_TOGGLED = "leg_toggled"
EV_POSE_SWITCHED = "pose_switched"

MIN_LEGS, MAX_LEGS = 4, 6
MIN_POSES, MAX_POSES = 4, 8


class ValidationError(ValueError):
    """录入数据或命令格式不合法。"""


class StaleRevisionError(Exception):
    """操作携带的修订号已过期。"""


class RejectedError(Exception):
    """操作合法但会破坏安全不变量，必须拒绝。"""


def _num(obj: Any, label: str) -> float:
    if isinstance(obj, bool) or not isinstance(obj, (int, float)):
        raise ValidationError(f"{label} 必须是数字")
    value = float(obj)
    if not math.isfinite(value):
        raise ValidationError(f"{label} 必须是有限数")
    return value


def parse_setup(payload: Any) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], int]:
    """校验并规范化启动演练的录入数据。"""
    if not isinstance(payload, dict):
        raise ValidationError("请求体必须是 JSON 对象")

    raw_legs = payload.get("legs")
    raw_poses = payload.get("poses")
    if not isinstance(raw_legs, list) or not isinstance(raw_poses, list):
        raise ValidationError("legs 与 poses 必须是数组")
    if not (MIN_LEGS <= len(raw_legs) <= MAX_LEGS):
        raise ValidationError(f"支腿数量必须在 {MIN_LEGS} 至 {MAX_LEGS} 只之间")
    if not (MIN_POSES <= len(raw_poses) <= MAX_POSES):
        raise ValidationError(f"载荷姿态数量必须在 {MIN_POSES} 至 {MAX_POSES} 个之间")

    legs: List[Dict[str, Any]] = []
    seen: set[Point] = set()
    for i, item in enumerate(raw_legs):
        if not isinstance(item, dict):
            raise ValidationError(f"支腿 {i + 1} 必须是对象")
        x = _num(item.get("x"), f"支腿 {i + 1} 的 x 坐标")
        y = _num(item.get("y"), f"支腿 {i + 1} 的 y 坐标")
        deployed = item.get("deployed", True)
        if not isinstance(deployed, bool):
            raise ValidationError(f"支腿 {i + 1} 的 deployed 必须是布尔值")
        key = (round(x, 9), round(y, 9))
        if key in seen:
            raise ValidationError(f"支腿 {i + 1} 与其他支腿坐标重合：({x}, {y})")
        seen.add(key)
        legs.append({"x": x, "y": y, "deployed": deployed})

    poses: List[Dict[str, Any]] = []
    for i, item in enumerate(raw_poses):
        if not isinstance(item, dict):
            raise ValidationError(f"姿态 {i + 1} 必须是对象")
        x = _num(item.get("x"), f"姿态 {i + 1} 的投影中心 x")
        y = _num(item.get("y"), f"姿态 {i + 1} 的投影中心 y")
        r = _num(item.get("radius", item.get("r")), f"姿态 {i + 1} 的不确定半径")
        if r < 0:
            raise ValidationError(f"姿态 {i + 1} 的不确定半径不能为负")
        poses.append({"x": x, "y": y, "r": r})

    current_pose = int(payload.get("current_pose", 0))
    if not 0 <= current_pose < len(poses):
        raise ValidationError("current_pose 不是有效的姿态下标")

    return legs, poses, current_pose


class EventStore:
    """append-only JSONL 事件轨迹及其重放状态。"""

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._events: List[Dict[str, Any]] = []
        self._legs: List[Dict[str, Any]] = []
        self._poses: List[Dict[str, Any]] = []
        self._current_pose: int = -1
        self._started = False
        self._last_rejection: Optional[Dict[str, Any]] = None
        self._replay()

    # ------------------------------------------------------------------ 重放
    def _replay(self) -> None:
        if not os.path.exists(self._path):
            return
        with open(self._path, "r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    self._apply(event, trusted=True)
                except (json.JSONDecodeError, ValidationError, KeyError) as exc:
                    raise RuntimeError(
                        f"事件轨迹 {self._path} 第 {lineno} 行损坏，无法重放：{exc}"
                    ) from exc
        self._started = bool(self._events)

    def _apply(self, event: Dict[str, Any], trusted: bool) -> None:
        etype = event.get("type") if trusted else event["type"]
        seq = event.get("seq") if trusted else event["seq"]
        if seq != len(self._events) + 1:
            raise ValidationError(f"事件序号不连续：期望 {len(self._events) + 1}，实际 {seq}")

        if etype == EV_SESSION:
            self._legs = copy.deepcopy(event["legs"])
            self._poses = copy.deepcopy(event["poses"])
            self._current_pose = event["current_pose"]
        elif etype == EV_LEG_TOGGLED:
            idx = event["leg_index"]
            if not 0 <= idx < len(self._legs):
                raise ValidationError(f"非法支腿下标：{idx}")
            self._legs[idx]["deployed"] = event["deployed"]
        elif etype == EV_POSE_SWITCHED:
            idx = event["pose_index"]
            if not 0 <= idx < len(self._poses):
                raise ValidationError(f"非法姿态下标：{idx}")
            self._current_pose = idx
        else:
            raise ValidationError(f"未知事件类型：{etype}")

        self._events.append(event)

    def _append(self, event: Dict[str, Any]) -> None:
        """加锁校验并以 append 方式落盘（轨迹永不改写）。"""
        event["seq"] = len(self._events) + 1
        event["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        # 先在内存中校验可应用性
        self._apply(event, trusted=False)
        os.makedirs(os.path.dirname(os.path.abspath(self._path)), exist_ok=True)
        with open(self._path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    # ------------------------------------------------------------------ 查询
    @property
    def started(self) -> bool:
        return self._started

    @property
    def revision(self) -> int:
        return len(self._events)

    def _evaluation(self):
        pose = self._poses[self._current_pose]
        deployed = [(l["x"], l["y"]) for l in self._legs if l["deployed"]]
        return deployed, evaluate_support(deployed, (pose["x"], pose["y"]), pose["r"])

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            hull = None
            safe = False
            reason: Optional[str] = None
            margin: Optional[float] = None
            if self._started:
                _, ev = self._evaluation()
                hull = ev.hull
                safe = ev.safe
                reason = ev.reason
                margin = ev.min_margin
            return {
                "started": self._started,
                "revision": self.revision,
                "legs": copy.deepcopy(self._legs),
                "poses": copy.deepcopy(self._poses),
                "current_pose": self._current_pose if self._started else None,
                "hull": hull,
                "safe": safe,
                "unsafe_reason": reason,
                "min_margin": margin,
                "last_rejection": copy.deepcopy(self._last_rejection),
            }

    # ------------------------------------------------------------------ 命令
    def start_session(self, payload: Any) -> Dict[str, Any]:
        legs, poses, current_pose = parse_setup(payload)
        with self._lock:
            if self._started:
                raise ValidationError("演练已存在；事件轨迹不可改写，不能重新录入")
            # 初始状态本身必须安全，否则拒绝启动且不写入任何事件
            deployed = [(l["x"], l["y"]) for l in legs if l["deployed"]]
            pose = poses[current_pose]
            result = evaluate_support(deployed, (pose["x"], pose["y"]), pose["r"])
            if not result.safe:
                self._record_rejection("start_session", result.reason, None)
                raise RejectedError(result.reason)
            self._append(
                {
                    "type": EV_SESSION,
                    "legs": legs,
                    "poses": poses,
                    "current_pose": current_pose,
                }
            )
            self._started = True
            self._last_rejection = None
            return self.snapshot()

    def _check_revision(self, expected: Any, operation: str = "操作") -> None:
        if isinstance(expected, bool) or not isinstance(expected, int):
            raise ValidationError("revision 必须是整数")
        if expected != self.revision:
            err = StaleRevisionError(
                f"{operation}的修订号已过期：所携带修订号 {expected}，当前修订号 {self.revision}"
            )
            self._record_rejection(operation, str(err), expected)
            raise err

    def toggle_leg(self, payload: Any) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValidationError("请求体必须是 JSON 对象")
        with self._lock:
            self._require_started()
            self._check_revision(payload.get("revision"), f"收放 {payload.get('leg_index', '?')} 号支腿")
            idx = payload.get("leg_index")
            if not isinstance(idx, int) or isinstance(idx, bool) or not (0 <= idx < len(self._legs)):
                raise ValidationError("leg_index 不是有效的支腿下标")

            next_deployed = not self._legs[idx]["deployed"]
            candidate = [
                (l["x"], l["y"])
                for i, l in enumerate(self._legs)
                if (i == idx and next_deployed) or (i != idx and l["deployed"])
            ]
            pose = self._poses[self._current_pose]
            result = evaluate_support(candidate, (pose["x"], pose["y"]), pose["r"])
            if not result.safe:
                reason = (
                    f"{'放下' if next_deployed else '收起'} {idx + 1} 号支腿被拒绝：{result.reason}"
                )
                self._record_rejection("toggle_leg", reason, payload.get("revision"))
                raise RejectedError(reason)

            self._append(
                {"type": EV_LEG_TOGGLED, "leg_index": idx, "deployed": next_deployed}
            )
            return self.snapshot()

    def switch_pose(self, payload: Any) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValidationError("请求体必须是 JSON 对象")
        with self._lock:
            self._require_started()
            self._check_revision(payload.get("revision"), f"切换姿态")
            idx = payload.get("pose_index")
            if not isinstance(idx, int) or isinstance(idx, bool) or not (0 <= idx < len(self._poses)):
                raise ValidationError("pose_index 不是有效的姿态下标")
            if idx == self._current_pose:
                raise ValidationError(f"{idx + 1} 号姿态即当前姿态，未发生切换")

            candidate_pose = self._poses[idx]
            deployed = [(l["x"], l["y"]) for l in self._legs if l["deployed"]]
            result = evaluate_support(
                deployed, (candidate_pose["x"], candidate_pose["y"]), candidate_pose["r"]
            )
            if not result.safe:
                reason = f"切换至 {idx + 1} 号姿态被拒绝：{result.reason}"
                self._record_rejection("switch_pose", reason, payload.get("revision"))
                raise RejectedError(reason)

            self._append({"type": EV_POSE_SWITCHED, "pose_index": idx})
            return self.snapshot()

    def _require_started(self) -> None:
        if not self._started:
            raise ValidationError("演练尚未启动，请先录入支腿与姿态数据")

    def _record_rejection(self, operation: Optional[str], reason: str, expected: Any) -> None:
        self._last_rejection = {
            "operation": operation,
            "reason": reason,
            "expected_revision": expected,
            "current_revision": self.revision,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
