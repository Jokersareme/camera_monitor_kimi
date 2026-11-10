const $ = id => document.getElementById(id);
const ALERT_DURATION_MS = 10000;
const ALERT_MODES = ["sound", "vibrate", "both", "popup"];
let lastStatus = null;
let busy = false;
let cameraOptionsKey = "";
let currentCameraId = null;
let lastMotionEventTime = null;
let statusInitialized = false;
let alertTimer = null;
let audioContext = null;

function reloadVideo() {
  $("camera-feed").src = `/video.mjpeg?t=${Date.now()}`;
}
function updateCameraOptions(camera, cameras) {
  const key = JSON.stringify(cameras);
  if (key !== cameraOptionsKey) {
    cameraOptionsKey = key;
    const select = $("camera-select");
    select.replaceChildren();
    if (!cameras.length) {
      select.add(new Option("未配置摄像头", ""));
    } else {
      cameras.forEach(item => select.add(new Option(item.name, item.id)));
    }
  }
  $("camera-select").value = camera.id || "";
  $("camera-select").disabled = busy || !cameras.length;
  $("camera-hint").textContent = cameras.length ? "选择后立即切换；新视频源成功出帧后才显示在线" : "请先在 config.json 的 cameras 列表中添加真实摄像头";
  if (currentCameraId !== null && currentCameraId !== camera.id) reloadVideo();
  currentCameraId = camera.id;
}

function formatTime(value) {
  return value ? new Date(value * 1000).toLocaleString() : "暂无";
}
function showError(message) {
  $("error").textContent = message || "";
  $("error").classList.toggle("visible", Boolean(message));
}
async function api(path, options) {
  const response = await fetch(path, {cache: "no-store", ...options});
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "请求失败");
  return body;
}
function usesSound() {
  return ["sound", "both"].includes($("motion-alert-mode").value);
}
function usesVibration() {
  return ["vibrate", "both"].includes($("motion-alert-mode").value);
}
function updateAlertCapability(message = "") {
  if (message) {
    $("alert-capability").textContent = message;
    return;
  }
  const notes = [];
  if (usesSound()) {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) notes.push("当前浏览器不支持网页提示音");
    else if (!audioContext || audioContext.state !== "running") notes.push("声音需先点击“测试网页提醒”授权");
    else notes.push("网页提示音已启用");
  }
  if (usesVibration()) {
    notes.push(typeof navigator.vibrate === "function" ? "震动是否生效取决于手机浏览器和系统设置" : "当前浏览器不支持震动；iPhone Safari 请改用声音");
  }
  notes.push("请保持网页打开；锁屏或后台时不保证提醒");
  $("alert-capability").textContent = notes.join("；");
}
let soundTimer = null;
let soundEndTimer = null;
function stopSound() {
  clearInterval(soundTimer);
  clearTimeout(soundEndTimer);
  soundTimer = null;
  soundEndTimer = null;
}
function playBeep() {
  const start = audioContext.currentTime;
  const oscillator = audioContext.createOscillator();
  const gain = audioContext.createGain();
  oscillator.type = "sine";
  oscillator.frequency.setValueAtTime(880, start);
  oscillator.frequency.setValueAtTime(660, start + 0.22);
  oscillator.frequency.setValueAtTime(880, start + 0.44);
  gain.gain.setValueAtTime(0.0001, start);
  gain.gain.exponentialRampToValueAtTime(0.22, start + 0.03);
  gain.gain.setValueAtTime(0.22, start + 0.58);
  gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.7);
  oscillator.connect(gain);
  gain.connect(audioContext.destination);
  oscillator.start(start);
  oscillator.stop(start + 0.7);
  oscillator.onended = () => { oscillator.disconnect(); gain.disconnect(); };
}
function playSound(durationSeconds) {
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextClass) {
    updateAlertCapability("当前浏览器不支持网页提示音，请改用震动或仅弹窗");
    return;
  }
  try {
    if (!audioContext) audioContext = new AudioContextClass();
    const startSound = () => {
      if (audioContext.state !== "running") {
        updateAlertCapability("浏览器尚未允许声音，请点击“测试网页提醒”后再试");
        return;
      }
      stopSound();
      playBeep();
      soundTimer = setInterval(playBeep, 800);
      soundEndTimer = setTimeout(stopSound, Math.max(0.8, durationSeconds) * 1000);
      updateAlertCapability();
    };
    if (audioContext.state === "suspended") {
      audioContext.resume().then(startSound).catch(() => updateAlertCapability("浏览器阻止了自动声音，请点击“测试网页提醒”授权"));
    } else {
      startSound();
    }
  } catch (_) {
    updateAlertCapability("提示音启用失败，请检查浏览器声音权限或改用其他提醒方式");
  }
}
let vibrateTimer = null;
let vibrateEndTimer = null;
function stopVibration() {
  clearInterval(vibrateTimer);
  clearTimeout(vibrateEndTimer);
  vibrateTimer = null;
  vibrateEndTimer = null;
  if (typeof navigator.vibrate === "function") { try { navigator.vibrate(0); } catch (_) {} }
}
function vibrateOnce() {
  if (!navigator.vibrate([400, 150, 400])) updateAlertCapability("浏览器未接受震动请求，请检查系统设置或改用声音");
}
function vibrate(durationSeconds) {
  if (typeof navigator.vibrate !== "function") {
    updateAlertCapability("当前浏览器不支持震动；iPhone Safari 请改用声音或仅弹窗");
    return;
  }
  try {
    stopVibration();
    vibrateOnce();
    vibrateTimer = setInterval(vibrateOnce, 700);
    vibrateEndTimer = setTimeout(stopVibration, Math.max(1, durationSeconds) * 1000);
  } catch (_) {
    updateAlertCapability("震动调用失败，请检查浏览器权限或改用声音");
  }
}
function hideMotionAlert() {
  clearTimeout(alertTimer);
  alertTimer = null;
  stopSound();
  stopVibration();
  $("motion-alert").hidden = true;
}
function alertDurationSeconds(id) {
  const value = Number($(id).value);
  return Number.isFinite(value) && value > 0 ? value : 10;
}
function showMotionAlert(eventTime, isTest = false) {
  clearTimeout(alertTimer);
  $("motion-alert").querySelector("strong").textContent = isTest ? "网页活动提醒测试" : "检测到持续明显活动";
  $("motion-alert-time").textContent = `${formatTime(eventTime)} · 弹窗将在 10 秒后关闭，声音和震动按设定时长`;
  $("motion-alert").hidden = false;
  if (usesSound()) playSound(alertDurationSeconds("motion-alert-sound-seconds"));
  if (usesVibration()) vibrate(alertDurationSeconds("motion-alert-vibrate-seconds"));
  alertTimer = setTimeout(hideMotionAlert, ALERT_DURATION_MS);
}
function processMotionEvent(status) {
  const motionEvent = (status.events || {}).motion;
  const eventTime = motionEvent && Number(motionEvent.time);
  const validEventTime = Number.isFinite(eventTime) && eventTime > 0 ? eventTime : null;
  if (!statusInitialized) {
    lastMotionEventTime = validEventTime;
    statusInitialized = true;
    return;
  }
  if (!validEventTime || validEventTime <= (lastMotionEventTime || 0)) return;
  lastMotionEventTime = validEventTime;
  const prefs = status.preferences;
  if (status.camera.online && prefs.motion_enabled && !prefs.mute.active) showMotionAlert(validEventTime);
}
function update(status) {
  lastStatus = status;
  const camera = status.camera;
  updateCameraOptions(camera, status.cameras || []);
  const cameraText = camera.online ? "在线" : ({unconfigured:"未配置摄像头", connecting:"连接中，尚未收到画面", reconnecting:"离线，重连中", online:"已连接，等待画面"}[camera.state] || "离线");
  $("camera-status").textContent = camera.name ? `${camera.name}：${cameraText}` : cameraText;
  $("camera-badge").textContent = camera.online ? "实时" : (camera.state === "unconfigured" ? "未配置摄像头" : "离线 · 非实时画面");
  $("camera-badge").className = "badge " + (camera.online ? "online" : "offline");
  $("fps").textContent = camera.online ? `${camera.fps} FPS` : "—";
  const prefs = status.preferences;
  $("motion-toggle").checked = prefs.motion_enabled;
  $("person-toggle").checked = prefs.person_enabled;
  $("debug-toggle").checked = prefs.debug_enabled;
  $("mute-status").textContent = prefs.mute.active ? `静音中，剩余 ${Math.ceil(prefs.mute.remaining_seconds / 60)} 分钟；到期自动恢复` : "未静音";
  if (!prefs.motion_enabled || prefs.mute.active) hideMotionAlert();
  processMotionEvent(status);
  const motion = status.motion || {};
  $("motion-status").textContent = motion.active ? "检测到持续明显动作" : (motion.state === "frozen" ? "画面冻结，已暂停判断" : "未触发");
  const detection = status.detection || {};
  $("person-count").textContent = prefs.person_enabled && detection.fresh ? detection.count : "—";
  $("detection-status").textContent = !prefs.person_enabled ? "已关闭" : ({loading:"模型加载中", running: detection.fresh ? "运行中" : "等待新结果", unavailable:"不可用", disabled:"已关闭"}[detection.state] || detection.state || "未知");
  $("detection-updated").textContent = formatTime(detection.updated_at);
  const events = status.events || {};
  $("last-motion").textContent = formatTime(events.motion && events.motion.time);
  $("last-notification").textContent = events.notification ? `${formatTime(events.notification.time)}（${events.notification.result || "已记录"}）` : "暂无";
  $("last-device").textContent = formatTime((events.offline || events.recovered || {}).time);
}
async function refresh() {
  try { update(await api("/api/status")); showError(""); }
  catch (_) { showError("状态连接失败，请检查树莓派服务；视频可能仍在重连。"); }
}
async function change(path, body) {
  if (busy) return;
  busy = true;
  try { await api(path, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)}); await refresh(); showError(""); }
  catch (error) { await refresh(); showError(error.message); }
  finally {
    busy = false;
    if (lastStatus) updateCameraOptions(lastStatus.camera, lastStatus.cameras || []);
  }
}
try {
  const savedMode = localStorage.getItem("motionAlertMode");
  if (ALERT_MODES.includes(savedMode)) $("motion-alert-mode").value = savedMode;
} catch (_) {}
$("motion-alert-mode").addEventListener("change", event => {
  try { localStorage.setItem("motionAlertMode", event.target.value); } catch (_) {}
  updateAlertCapability();
});
function bindAlertDuration(id, storageKey) {
  try {
    const saved = localStorage.getItem(storageKey);
    if (saved && [...$(id).options].some(option => option.value === saved)) $(id).value = saved;
  } catch (_) {}
  $(id).addEventListener("change", event => {
    try { localStorage.setItem(storageKey, event.target.value); } catch (_) {}
  });
}
bindAlertDuration("motion-alert-sound-seconds", "motionAlertSoundSeconds");
bindAlertDuration("motion-alert-vibrate-seconds", "motionAlertVibrateSeconds");

const MOTION_PARAM_DEFS = {
  sensitivity: {label: "灵敏度阈值（像素差异，越小越灵敏）", min: 5, max: 80, step: 1},
  min_area_ratio: {label: "最小动作面积（占画面比例）", min: 0.001, max: 0.3, step: 0.001},
  min_motion_intensity: {label: "动作强度阈值", min: 1, max: 100, step: 1},
  sustained_seconds: {label: "需持续秒数", min: 0.2, max: 10, step: 0.1},
  window_seconds: {label: "判断窗口秒数", min: 1, max: 30, step: 0.5},
  min_active_ratio: {label: "窗口内活跃比例", min: 0.1, max: 1, step: 0.05},
  cooldown_seconds: {label: "告警冷却秒数", min: 5, max: 600, step: 5},
  freeze_seconds: {label: "画面冻结判定秒数", min: 1, max: 60, step: 1},
};
let motionParamSaveTimer = null;
function formatParamValue(value, step) {
  return step >= 1 ? String(Math.round(value)) : String(Number(value.toFixed(step >= 0.05 ? 2 : 3)));
}
function buildMotionParams(current) {
  const container = $("motion-params");
  container.replaceChildren();
  Object.entries(MOTION_PARAM_DEFS).forEach(([key, def]) => {
    const row = document.createElement("div");
    row.className = "param-row";
    const label = document.createElement("label");
    label.htmlFor = `param-${key}`;
    label.textContent = def.label;
    const slider = document.createElement("input");
    slider.type = "range";
    slider.id = `param-${key}`;
    slider.min = def.min;
    slider.max = def.max;
    slider.step = def.step;
    slider.value = current && Number.isFinite(Number(current[key])) ? current[key] : def.min;
    const value = document.createElement("output");
    value.textContent = formatParamValue(Number(slider.value), def.step);
    slider.addEventListener("input", () => {
      value.textContent = formatParamValue(Number(slider.value), def.step);
      clearTimeout(motionParamSaveTimer);
      motionParamSaveTimer = setTimeout(() => saveMotionParam(key, Number(slider.value)), 400);
    });
    row.append(label, slider, value);
    container.append(row);
  });
}
async function loadMotionParams() {
  try {
    const body = await api("/api/settings/motion-params");
    buildMotionParams(body.current);
    $("motion-params-status").textContent = "";
  } catch (error) {
    $("motion-params-status").textContent = "检测设置不可用：" + error.message;
  }
}
async function saveMotionParam(key, value) {
  try {
    await api("/api/settings/motion-params", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({[key]: value})});
    $("motion-params-status").textContent = `已保存：${MOTION_PARAM_DEFS[key] ? MOTION_PARAM_DEFS[key].label : key} = ${value}`;
    showError("");
  } catch (error) {
    $("motion-params-status").textContent = "保存失败：" + error.message;
  }
}
$("detection-settings-toggle").addEventListener("click", () => {
  const panel = $("detection-settings");
  panel.hidden = !panel.hidden;
  $("detection-settings-toggle").textContent = panel.hidden ? "展开 ▾" : "收起 ▴";
  if (!panel.hidden) loadMotionParams();
});
$("test-motion-alert").addEventListener("click", () => showMotionAlert(Date.now() / 1000, true));
$("camera-select").addEventListener("change", event => change("/api/cameras/select", {id:event.target.value}));
$("motion-toggle").addEventListener("change", event => change("/api/settings/motion", {enabled:event.target.checked}));
$("person-toggle").addEventListener("change", event => change("/api/settings/person", {enabled:event.target.checked}));
$("debug-toggle").addEventListener("change", event => change("/api/settings/debug", {enabled:event.target.checked}));
document.querySelectorAll("[data-minutes]").forEach(button => button.addEventListener("click", () => change("/api/mute", {minutes:Number(button.dataset.minutes)})));
$("test-notify").addEventListener("click", () => change("/api/notify/test", {}));
updateAlertCapability();
refresh();
setInterval(refresh, 1000);
