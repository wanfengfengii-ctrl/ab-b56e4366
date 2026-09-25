#!/bin/sh
# Compose verify 一次性服务入口：代码静态编译检查 → 单元测试 → 几何/HTTP 冒烟。
# 任一步失败立即以非零退出码退出，由容器退出码报告验收结果。
set -eu

PY="${PYTHON:-python3}"

echo "== [1/3] Python 字节码编译检查 =="
"$PY" -m compileall -q app tests scripts

echo "== [2/3] 单元测试（几何 + 事件轨迹） =="
"$PY" -m unittest discover -s tests -v

echo "== [3/3] 几何业务与 HTTP 端到端冒烟 =="
"$PY" scripts/smoke.py

echo "== verify 全部通过 =="
