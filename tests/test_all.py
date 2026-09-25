"""几何与事件轨迹单元测试（标准库 unittest）。"""

import json
import math
import os
import tempfile
import unittest

from app.geometry import convex_hull, evaluate_support, min_signed_edge_distance
from app.store import (
    EventStore,
    RejectedError,
    StaleRevisionError,
    ValidationError,
)


def square_setup(radius=1.0, cx=5.0, cy=5.0, deployed=(True,) * 4):
    return {
        "legs": [
            {"x": 0, "y": 0, "deployed": deployed[0]},
            {"x": 10, "y": 0, "deployed": deployed[1]},
            {"x": 10, "y": 10, "deployed": deployed[2]},
            {"x": 0, "y": 10, "deployed": deployed[3]},
        ],
        "poses": [
            {"x": cx, "y": cy, "radius": radius},
            {"x": 9.5, "y": 9.5, "radius": 1.0},   # 越界
            {"x": 5, "y": 5, "radius": 5.0},       # 恰好内切（半径=中心至边距离）
            {"x": 5, "y": 5, "radius": 0.0},       # 零半径
        ],
        "current_pose": 0,
    }


class GeometryTests(unittest.TestCase):
    def test_convex_hull_square_ccw(self):
        hull = convex_hull([(0, 0), (10, 0), (10, 10), (0, 10), (5, 5)])
        self.assertEqual(len(hull), 4)
        self.assertEqual(hull[0], (0.0, 0.0))
        # 逆时针：首边叉积为正
        (x0, y0), (x1, y1), (x2, y2) = hull[:3]
        self.assertGreater((x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0), 0)

    def test_collinear_points(self):
        hull = convex_hull([(0, 0), (1, 1), (2, 2), (3, 3)])
        self.assertLess(len(hull), 3)

    def test_disk_safely_inside(self):
        legs = [(0, 0), (10, 0), (10, 10), (0, 10)]
        ev = evaluate_support(legs, (5, 5), 1.0)
        self.assertTrue(ev.safe)
        self.assertAlmostEqual(ev.min_margin, 4.0)

    def test_tangent_is_safe(self):
        # 圆心 (5,5)，半径 5：圆盘与四条边全部相切
        legs = [(0, 0), (10, 0), (10, 10), (0, 10)]
        ev = evaluate_support(legs, (5, 5), 5.0)
        self.assertTrue(ev.safe)
        self.assertAlmostEqual(ev.min_margin, 0.0)

    def test_disk_outside_rejected(self):
        legs = [(0, 0), (10, 0), (10, 10), (0, 10)]
        ev = evaluate_support(legs, (9.5, 9.5), 1.0)
        self.assertFalse(ev.safe)
        self.assertIn("越出支承面", ev.reason)

    def test_center_inside_but_disk_crosses_edge(self):
        legs = [(0, 0), (10, 0), (10, 10), (0, 10)]
        ev = evaluate_support(legs, (5, 5), 5.001)
        self.assertFalse(ev.safe)
        self.assertLess(ev.min_margin, 0)

    def test_fewer_than_three_legs(self):
        ev = evaluate_support([(0, 0), (10, 0)], (5, 0), 0.1)
        self.assertFalse(ev.safe)
        self.assertIn("3", ev.reason)

    def test_triangle_offset_center(self):
        legs = [(0, 0), (10, 0), (0, 10)]
        # 内切圆中心 (2.93, 2.93)，半径约 2.93
        r = 10 * (2 - math.sqrt(2)) / 2
        ev = evaluate_support(legs, (r, r), r)
        self.assertTrue(ev.safe)
        self.assertAlmostEqual(ev.min_margin, 0.0, places=9)
        ev2 = evaluate_support(legs, (r, r), r + 0.001)
        self.assertFalse(ev2.safe)

    def test_signed_distance_signs(self):
        hull = convex_hull([(0, 0), (10, 0), (10, 10), (0, 10)])
        self.assertAlmostEqual(min_signed_edge_distance((5, 5), hull), 5.0)
        self.assertLess(min_signed_edge_distance((5, -1), hull), 0)


class SetupValidationTests(unittest.TestCase):
    def test_counts_out_of_range(self):
        bad = square_setup()
        bad["legs"].pop()
        with self.assertRaises(ValidationError):
            EventStore(self.tmp()).start_session(bad)

    def test_negative_radius(self):
        bad = square_setup()
        bad["poses"][0]["radius"] = -0.1
        with self.assertRaises(ValidationError):
            EventStore(self.tmp()).start_session(bad)

    def test_duplicate_leg_coords(self):
        bad = square_setup()
        bad["legs"][1] = {"x": 0, "y": 0, "deployed": True}
        with self.assertRaises(ValidationError):
            EventStore(self.tmp()).start_session(bad)

    def test_unsafe_initial_state_rejected(self):
        store = EventStore(self.tmp())
        with self.assertRaises(RejectedError):
            store.start_session(square_setup(radius=9.0))
        # 拒绝不写入轨迹
        self.assertFalse(store.started)
        self.assertEqual(store.revision, 0)

    @staticmethod
    def tmp():
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        os.unlink(path)
        return path


class StoreOperationTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        os.unlink(self.path)
        self.store = EventStore(self.path)
        self.store.start_session(square_setup())

    def test_initial_revision_is_one(self):
        self.assertEqual(self.store.revision, 1)

    def test_stow_leg_that_breaks_support_is_rejected(self):
        # 收起任意一条腿都只剩 3 条；正方形去掉一角后的三角形不再包含圆盘
        with self.assertRaises(RejectedError):
            self.store.toggle_leg({"revision": 1, "leg_index": 0})
        self.assertEqual(self.store.revision, 1)
        snap = self.store.snapshot()
        self.assertIn("收起", snap["last_rejection"]["reason"])
        self.assertTrue(snap["legs"][0]["deployed"])

    def test_stow_leg_with_extra_support_accepted(self):
        # 五腿场景：中心支腿收起后四角仍安全
        setup = square_setup()
        setup["legs"].append({"x": 5, "y": 5, "deployed": True})
        store = EventStore(self.path + "2")
        store.start_session(setup)
        rev = store.revision
        snap = store.toggle_leg({"revision": rev, "leg_index": 4})
        self.assertEqual(snap["revision"], rev + 1)
        self.assertFalse(snap["legs"][4]["deployed"])

    def test_pose_switch_outside_rejected(self):
        with self.assertRaises(RejectedError):
            self.store.switch_pose({"revision": 1, "pose_index": 1})
        self.assertEqual(self.store.revision, 1)
        self.assertEqual(self.store.snapshot()["current_pose"], 0)

    def test_pose_switch_to_tangent_accepted(self):
        snap = self.store.switch_pose({"revision": 1, "pose_index": 2})
        self.assertEqual(snap["revision"], 2)
        self.assertEqual(snap["current_pose"], 2)
        self.assertTrue(snap["safe"])

    def test_zero_radius_pose(self):
        # 零半径点位于凸包内即安全
        snap = self.store.switch_pose({"revision": 1, "pose_index": 3})
        self.assertTrue(snap["safe"])

    def test_stale_revision_rejected(self):
        self.store.switch_pose({"revision": 1, "pose_index": 3})  # rev -> 2
        with self.assertRaises(StaleRevisionError):
            self.store.switch_pose({"revision": 1, "pose_index": 2})
        self.assertEqual(self.store.revision, 2)

    def test_missing_revision_rejected(self):
        with self.assertRaises(ValidationError):
            self.store.toggle_leg({"leg_index": 0})

    def test_switch_to_same_pose_rejected(self):
        with self.assertRaises(ValidationError):
            self.store.switch_pose({"revision": 1, "pose_index": 0})
        self.assertEqual(self.store.revision, 1)

    def test_event_log_is_append_only_and_replayable(self):
        self.store.switch_pose({"revision": 1, "pose_index": 3})
        with open(self.path, encoding="utf-8") as fh:
            lines = [json.loads(l) for l in fh if l.strip()]
        self.assertEqual([e["seq"] for e in lines], [1, 2])
        # 轨迹不可改写：重新录入必须失败
        with self.assertRaises(ValidationError):
            self.store.start_session(square_setup())
        # 重放得到同样状态
        replayed = EventStore(self.path)
        snap1, snap2 = self.store.snapshot(), replayed.snapshot()
        self.assertEqual(snap1["revision"], snap2["revision"])
        self.assertEqual(snap1["current_pose"], snap2["current_pose"])
        self.assertEqual(snap1["legs"], snap2["legs"])

    def test_rejected_ops_not_in_log(self):
        for bad in (
            lambda: self.store.toggle_leg({"revision": 1, "leg_index": 0}),
            lambda: self.store.switch_pose({"revision": 1, "pose_index": 1}),
            lambda: self.store.switch_pose({"revision": 99, "pose_index": 2}),
        ):
            with self.assertRaises(Exception):
                bad()
        with open(self.path, encoding="utf-8") as fh:
            lines = [l for l in fh if l.strip()]
        self.assertEqual(len(lines), 1)

    def test_deploy_insufficient_legs(self):
        # 初始只部署 3 条腿且不能包含圆盘 → 启动被拒绝
        setup = square_setup(deployed=(True, True, True, False))
        store = EventStore(self.path + "3")
        with self.assertRaises(RejectedError):
            store.start_session(setup)


if __name__ == "__main__":
    unittest.main(verbosity=2)
