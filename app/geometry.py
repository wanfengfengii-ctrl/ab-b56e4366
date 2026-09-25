"""平面几何：凸包与载荷圆盘（带非负不确定半径）的包含判定。

约定：
- 支承多边形由已部署支腿平面坐标的凸包给出，凸包顶点按逆时针排列。
- 圆盘完整位于凸多边形内，当且仅当圆心到每一条支承边所在直线的
  有符号距离（内侧为正）都不小于半径；距离恰好等于半径（边界相切）
  视为安全。
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

EPS = 1e-9

Point = Tuple[float, float]


def convex_hull(points: Sequence[Point]) -> List[Point]:
    """返回点集的凸包（Andrew 单调链，逆时针，剔除共线中点）。

    点数不足或全部共线时返回长度小于 3 的列表。
    """
    pts = sorted({(float(x), float(y)) for x, y in points})
    if len(pts) <= 2:
        return pts

    def cross(o: Point, a: Point, b: Point) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: List[Point] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= EPS:
            lower.pop()
        lower.append(p)

    upper: List[Point] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= EPS:
            upper.pop()
        upper.append(p)

    return lower[:-1] + upper[:-1]


def min_signed_edge_distance(center: Point, hull: Sequence[Point]) -> float:
    """圆心到凸包各边所在直线的最小有符号距离（逆时针多边形内侧为正）。"""
    cx, cy = center
    distances: List[float] = []
    n = len(hull)
    for i in range(n):
        ax, ay = hull[i]
        bx, by = hull[(i + 1) % n]
        ex, ey = bx - ax, by - ay
        length = math.hypot(ex, ey)
        if length < EPS:
            continue
        # 叉积 ex*(cy-ay) - ey*(cx-ax)，逆时针内侧为正
        signed = (ex * (cy - ay) - ey * (cx - ax)) / length
        distances.append(signed)
    return min(distances)


class SupportEvaluation:
    """当前部署支腿对载荷圆盘的支承评估结果。"""

    __slots__ = ("hull", "safe", "reason", "min_margin")

    def __init__(
        self,
        hull: Optional[List[Point]],
        safe: bool,
        reason: Optional[str] = None,
        min_margin: Optional[float] = None,
    ) -> None:
        self.hull = hull
        self.safe = safe
        self.reason = reason
        # min_margin = 最小边距 - 半径；>=0 即圆盘完整包含
        self.min_margin = min_margin


def evaluate_support(
    deployed_points: Sequence[Point],
    center: Point,
    radius: float,
) -> SupportEvaluation:
    """评估部署支腿凸包是否完整包含载荷圆盘。"""
    n_deployed = len(deployed_points)
    if n_deployed < 3:
        return SupportEvaluation(
            hull=None,
            safe=False,
            reason=f"仅有 {n_deployed} 只部署支腿，至少需要 3 只才能形成支承多边形",
        )

    hull = convex_hull(deployed_points)
    if len(hull) < 3:
        return SupportEvaluation(
            hull=hull if hull else None,
            safe=False,
            reason="部署支腿位置共线（或重合），无法形成有效的支承多边形",
        )

    min_distance = min_signed_edge_distance(center, hull)
    margin = min_distance - radius

    if min_distance < -EPS:
        return SupportEvaluation(
            hull=hull,
            safe=False,
            reason=(
                f"载荷圆心已越出支承面（圆心至支承边最小有符号距离 "
                f"{min_distance:.6f} < 0）"
            ),
            min_margin=margin,
        )

    if margin < -EPS:
        return SupportEvaluation(
            hull=hull,
            safe=False,
            reason=(
                f"载荷圆盘越出支承面：半径 {radius:.6f} 大于圆心至支承边的"
                f"最小距离 {min_distance:.6f}（余量 {margin:.6f}）"
            ),
            min_margin=margin,
        )

    return SupportEvaluation(hull=hull, safe=True, min_margin=margin)
