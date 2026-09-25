"use strict";

// 全局演练状态
let state = null;

const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------- 录入表单
const DEFAULT_LEGS = [
  { x: 0, y: 0, deployed: true },
  { x: 10, y: 0, deployed: true },
  { x: 10, y: 8, deployed: true },
  { x: 0, y: 8, deployed: true },
  { x: 5, y: 12, deployed: false },
];
const DEFAULT_POSES = [
  { x: 5, y: 4, r: 1.5 },
  { x: 2, y: 2, r: 0.8 },
  { x: 8, y: 6, r: 1.0 },
  { x: 5, y: 7, r: 0.6 },
];

function makeRow(tag, cellsHtml) {
  const tr = document.createElement("tr");
  tr.dataset[tag] = "1";
  tr.innerHTML = cellsHtml;
  return tr;
}

function renderSetupForms() {
  const legsBody = $("legs-table").querySelector("tbody");
  legsBody.innerHTML = "";
  DEFAULT_LEGS.forEach((leg, i) => {
    legsBody.appendChild(makeRow("leg", `
      <td>${i + 1}</td>
      <td><input data-f="x" type="number" step="any" value="${leg.x}"></td>
      <td><input data-f="y" type="number" step="any" value="${leg.y}"></td>
      <td><input data-f="d" type="checkbox" ${leg.deployed ? "checked" : ""}></td>
      <td><button type="button" class="danger" data-rm-leg>删除</button></td>`));
  });
  const posesBody = $("poses-table").querySelector("tbody");
  posesBody.innerHTML = "";
  DEFAULT_POSES.forEach((p, i) => {
    posesBody.appendChild(makeRow("pose", `
      <td>${i + 1}</td>
      <td><input data-f="x" type="number" step="any" value="${p.x}"></td>
      <td><input data-f="y" type="number" step="any" value="${p.y}"></td>
      <td><input data-f="r" type="number" step="any" min="0" value="${p.r}"></td>
      <td><button type="button" class="danger" data-rm-pose>删除</button></td>`));
  });
  renumberSetup();
}

function renumberSetup() {
  document.querySelectorAll('#legs-table tbody tr').forEach((tr, i) => {
    tr.firstElementChild.textContent = i + 1;
  });
  document.querySelectorAll('#poses-table tbody tr').forEach((tr, i) => {
    tr.firstElementChild.textContent = i + 1;
  });
  const sel = $("current-pose-select");
  const n = document.querySelectorAll('#poses-table tbody tr').length;
  const prev = sel.value;
  sel.innerHTML = "";
  for (let i = 0; i < n; i++) {
    const opt = document.createElement("option");
    opt.value = String(i);
    opt.textContent = `姿态 ${i + 1}`;
    sel.appendChild(opt);
  }
  if (prev && Number(prev) < n) sel.value = prev;
}

function readSetup() {
  const legs = [...document.querySelectorAll('#legs-table tbody tr')].map((tr) => ({
    x: Number(tr.querySelector('[data-f=x]').value),
    y: Number(tr.querySelector('[data-f=y]').value),
    deployed: tr.querySelector('[data-f=d]').checked,
  }));
  const poses = [...document.querySelectorAll('#poses-table tbody tr')].map((tr) => ({
    x: Number(tr.querySelector('[data-f=x]').value),
    y: Number(tr.querySelector('[data-f=y]').value),
    radius: Number(tr.querySelector('[data-f=r]').value),
  }));
  return { legs, poses, current_pose: Number($("current-pose-select").value) };
}

// ---------------------------------------------------------------- API
async function api(path, body) {
  const resp = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await resp.json();
  // 拒绝响应里附带最新 state（修订号冲突后可立即与服务端对齐）
  if (data.state) state = data.state;
  if (!resp.ok) {
    const err = new Error(data.reason || "请求失败");
    err.kind = data.error;
    throw err;
  }
  state = data;
  return data;
}

async function refresh() {
  const resp = await fetch("/api/state");
  state = await resp.json();
  render();
}

function showRejection(msg, ok = false) {
  const banner = $("reject-banner");
  banner.textContent = msg;
  banner.className = "banner" + (ok ? " ok" : "");
}

// ---------------------------------------------------------------- 渲染
function render() {
  if (!state) return;
  $("rev").textContent = state.revision;

  if (state.started) {
    $("setup-panel").classList.add("hidden");
    $("drill-panel").classList.remove("hidden");
    $("cur-pose").textContent = `姿态 ${state.current_pose + 1}`;
    const safety = $("safety");
    if (state.safe) {
      safety.textContent = `安全（最小余量 ${state.min_margin?.toFixed(3)}）`;
      safety.style.color = "#7fffb8";
    } else {
      safety.textContent = "不安全";
      safety.style.color = "#ff8a8a";
    }

    // 支腿按钮
    const legBox = $("leg-buttons");
    legBox.innerHTML = "";
    state.legs.forEach((leg, i) => {
      const b = document.createElement("button");
      b.textContent = `支腿 ${i + 1}：${leg.deployed ? "已部署 ▼（点击收起）" : "已收起 ▲（点击放下）"}`;
      if (!leg.deployed) b.classList.add("danger");
      b.onclick = () => actToggleLeg(i);
      legBox.appendChild(b);
    });

    // 姿态按钮
    const poseBox = $("pose-buttons");
    poseBox.innerHTML = "";
    state.poses.forEach((p, i) => {
      const b = document.createElement("button");
      b.textContent = `姿态 ${i + 1}（r=${p.r}）${i === state.current_pose ? " · 当前" : ""}`;
      b.disabled = i === state.current_pose;
      b.onclick = () => actSwitchPose(i);
      poseBox.appendChild(b);
    });
  } else {
    $("setup-panel").classList.remove("hidden");
    $("drill-panel").classList.add("hidden");
    $("cur-pose").textContent = "—";
    const safety = $("safety");
    safety.textContent = "未启动";
    safety.style.color = "";
  }

  if (state.last_rejection) {
    showRejection(`最近一次拒绝：${state.last_rejection.reason}`);
  }
  drawView();
}

async function actToggleLeg(i) {
  const rev = state.revision;
  try {
    await api("/api/legs/toggle", { revision: rev, leg_index: i });
    showRejection(`操作已接受（修订号 ${rev} → ${state.revision}）`, true);
    render();
  } catch (e) {
    showRejection(`操作被拒绝（携带修订号 ${rev}）：${e.message}`);
    render();
  }
}

async function actSwitchPose(i) {
  const rev = state.revision;
  try {
    await api("/api/poses/switch", { revision: rev, pose_index: i });
    showRejection(`操作已接受（修订号 ${rev} → ${state.revision}）`, true);
    render();
  } catch (e) {
    showRejection(`操作被拒绝（携带修订号 ${rev}）：${e.message}`);
    render();
  }
}

// ---------------------------------------------------------------- Canvas 视图
function drawView() {
  const canvas = $("view");
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (!state || !state.started) {
    ctx.fillStyle = "#5a6c80";
    ctx.font = "15px sans-serif";
    ctx.fillText("尚未启动演练", 24, 32);
    return;
  }

  const pts = [];
  state.legs.forEach((l) => pts.push([l.x, l.y]));
  state.poses.forEach((p) => pts.push([p.x, p.y]));
  const pose = state.poses[state.current_pose];
  const r = pose.r || 1;
  let minX = Math.min(...pts.map((p) => p[0])) - r - 1;
  let maxX = Math.max(...pts.map((p) => p[0])) + r + 1;
  let minY = Math.min(...pts.map((p) => p[1])) - r - 1;
  let maxY = Math.max(...pts.map((p) => p[1])) + r + 1;
  const pad = 40;
  const sx = (canvas.width - 2 * pad) / (maxX - minX);
  const sy = (canvas.height - 2 * pad) / (maxY - minY);
  const s = Math.min(sx, sy);
  const X = (x) => pad + (x - minX) * s;
  // y 轴翻转（屏幕坐标向下）
  const Y = (y) => canvas.height - pad - (y - minY) * s;

  // 凸包填充
  if (state.hull && state.hull.length >= 3) {
    ctx.beginPath();
    state.hull.forEach((p, i) => (i ? ctx.lineTo(X(p[0]), Y(p[1])) : ctx.moveTo(X(p[0]), Y(p[1]))));
    ctx.closePath();
    ctx.fillStyle = "rgba(80,200,140,0.14)";
    ctx.strokeStyle = "#46c98c";
    ctx.lineWidth = 2;
    ctx.fill();
    ctx.stroke();
  }

  // 其他姿态中心
  state.poses.forEach((p, i) => {
    if (i === state.current_pose) return;
    ctx.beginPath();
    ctx.arc(X(p.x), Y(p.y), 4, 0, Math.PI * 2);
    ctx.fillStyle = "#c98aff";
    ctx.fill();
    ctx.fillStyle = "#8f7aa8";
    ctx.font = "11px sans-serif";
    ctx.fillText(`P${i + 1}`, X(p.x) + 6, Y(p.y) - 6);
  });

  // 当前载荷圆盘
  ctx.beginPath();
  ctx.arc(X(pose.x), Y(pose.y), Math.max(r * s, 2), 0, Math.PI * 2);
  ctx.fillStyle = "rgba(255,170,60,0.28)";
  ctx.strokeStyle = "#ffaa3c";
  ctx.lineWidth = 2;
  ctx.fill();
  ctx.stroke();
  ctx.beginPath();
  ctx.arc(X(pose.x), Y(pose.y), 3, 0, Math.PI * 2);
  ctx.fillStyle = "#ffaa3c";
  ctx.fill();

  // 支腿
  state.legs.forEach((l, i) => {
    ctx.beginPath();
    ctx.arc(X(l.x), Y(l.y), 6, 0, Math.PI * 2);
    if (l.deployed) {
      ctx.fillStyle = "#5aa9ff";
      ctx.fill();
    } else {
      ctx.strokeStyle = "#9fb3c8";
      ctx.setLineDash([4, 3]);
      ctx.stroke();
      ctx.setLineDash([]);
    }
    ctx.fillStyle = "#cfe0f2";
    ctx.font = "12px sans-serif";
    ctx.fillText(`L${i + 1}`, X(l.x) + 8, Y(l.y) - 8);
  });
}

// ---------------------------------------------------------------- 事件绑定
$("add-leg").onclick = () => {
  const tbody = $("legs-table").querySelector("tbody");
  const n = tbody.children.length;
  if (n >= 6) return;
  tbody.appendChild(makeRow("leg", `
    <td></td>
    <td><input data-f="x" type="number" step="any" value="0"></td>
    <td><input data-f="y" type="number" step="any" value="0"></td>
    <td><input data-f="d" type="checkbox" checked></td>
    <td><button type="button" class="danger" data-rm-leg>删除</button></td>`));
  renumberSetup();
};
$("add-pose").onclick = () => {
  const tbody = $("poses-table").querySelector("tbody");
  const n = tbody.children.length;
  if (n >= 8) return;
  tbody.appendChild(makeRow("pose", `
    <td></td>
    <td><input data-f="x" type="number" step="any" value="0"></td>
    <td><input data-f="y" type="number" step="any" value="0"></td>
    <td><input data-f="r" type="number" step="any" min="0" value="1"></td>
    <td><button type="button" class="danger" data-rm-pose>删除</button></td>`));
  renumberSetup();
};
document.addEventListener("click", (e) => {
  if (e.target.matches("[data-rm-leg]")) {
    e.target.closest("tr").remove();
    renumberSetup();
  }
  if (e.target.matches("[data-rm-pose]")) {
    e.target.closest("tr").remove();
    renumberSetup();
  }
});

$("start-btn").onclick = async () => {
  let payload;
  try {
    payload = readSetup();
  } catch {
    showRejection("录入数据包含非法数值");
    return;
  }
  try {
    await api("/api/session", payload);
    showRejection("演练已启动，事件轨迹已建立", true);
    render();
  } catch (e) {
    showRejection(`启动被拒绝：${e.message}`);
    if (state) render();
  }
};

renderSetupForms();
refresh();
