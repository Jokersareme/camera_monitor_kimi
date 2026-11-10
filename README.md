# 树莓派婴儿睡眠辅助监视

Python + Flask + OpenCV 的局域网实时监视项目。树莓派从手机 HTTP MJPEG/RTSP 或 USB 摄像头取得画面，在婴儿床 ROI 内检测持续明显动作，并通过 ntfy 或 Bark 发送文字提醒。YOLOX-Nano 在本地标注所有识别到的人。

**安全边界：系统可能漏报或误报，人员模型可能漏检、误框或重复框选。它不能判断宝宝一定醒来，不能监测呼吸、窒息或其他医疗状况，不是医疗设备，也不能代替成人看护和婴儿安全睡眠措施。**

## 快速移植与使用（先读这里）

### 运行这个项目需要做什么

首次运行只需要完成下面 6 步；之后的详细解释见对应章节。

1. **准备设备**：一台安装 Raspberry Pi OS 的树莓派、一部能提供 HTTP MJPEG/RTSP 视频流的手机，二者连接同一个家庭 Wi-Fi。
2. **复制项目**：把整个项目复制到树莓派 `/home/pi/camera_monitor`，但不要复制旧电脑上的 `.venv/`、`data/`、`__pycache__/`。虚拟环境必须在树莓派上重新创建；`data/` 中有运行状态和事件记录，也不应作为移植文件。
3. **安装依赖**：在树莓派重新创建 `.venv`，安装 Flask、OpenCV 和 NumPy，具体命令见下文。
4. **填写摄像头列表**：复制 `config.example.json` 为 `config.json`，在 `cameras` 中填入一个或多个真实手机/USB 摄像头。未配置时页面明确显示“未配置摄像头”，不会生成假画面。只测试视频时可暂时不配置通知。
5. **安装人员模型**：运行下载脚本；模型会校验 SHA-256，不使用人员检测时模型加载失败也不会阻止视频和动作检测。
6. **启动并使用**：运行 `app.py`，用手机或电脑浏览器打开 `http://树莓派IP:8000`。

### 第一步：复制哪些文件

可以复制整个目录后排除上述运行产物。必须保留这些内容：

```text
camera_monitor/
├── app.py                       # 总入口：启动所有后台线程和网页服务
├── config.example.json          # 配置模板，不含真实账号或令牌
├── requirements.txt             # Python 依赖清单
├── README.md                    # 本文档
├── baby_monitor/
│   ├── config.py                # 配置读取、环境变量覆盖和参数校验
│   ├── camera.py                # 手机网络流/USB 统一采集和断线重连
│   ├── motion.py                # ROI 持续动作检测
│   ├── detection.py             # YOLOX 多人推理、NMS 和坐标映射
│   ├── notifications.py         # ntfy/Bark、重试和离线提醒
│   ├── state.py                 # 开关、静音持久化和事件记录
│   └── server.py                # Flask 页面、MJPEG 和 API
├── templates/
│   └── index.html               # 手机友好的监视页面
├── static/
│   ├── app.js                   # 状态刷新、页面控制和网页活动提醒
│   └── style.css                # 页面及 10 秒提醒浮层样式
├── tools/
│   ├── download_model.py        # 下载、校验并检查人员模型
│   ├── simulate_stream.py       # 无手机时生成本地模拟 MJPEG 流
│   └── benchmark.py             # FPS、推理耗时、CPU/内存基准
├── deploy/
│   └── baby-monitor.service     # 树莓派开机自启模板
├── models/                      # 自行创建；ONNX 模型放这里，不提交仓库
│   └── yolox_nano.onnx          # 运行下载脚本后生成
└── data/                        # 自行创建；保存开关、静音和事件，不提交仓库
```

`models/yolox_nano.onnx` 可以在机器间复制，但必须重新核对 README 中的 SHA-256。`config.json` 含个人 IP 和通知配置，迁移后应检查，不要把真实凭据提交到代码仓库。

### 第二步：在树莓派安装依赖（推荐命令）

以下命令适用于 Raspberry Pi OS，优先使用系统 OpenCV，减少树莓派 3B/32 位系统找不到 wheel 的问题：

```bash
sudo apt update
sudo apt install -y python3-venv python3-opencv python3-numpy
cd /home/pi/camera_monitor
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install "Flask>=3.0,<4"
mkdir -p data models
cp config.example.json config.json
```

如果是 64 位系统，并且确认 pip 能为树莓派安装 OpenCV，也可以全部通过依赖清单安装：

```bash
cd /home/pi/camera_monitor
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
mkdir -p data models
cp config.example.json config.json
```

**两种 OpenCV 安装方式二选一，不要混装。** 安装后检查：

```bash
.venv/bin/python -c "import cv2, flask, numpy; print('依赖正常', cv2.__version__, numpy.__version__)"
```

如果提示 `No module named ...`，先确认命令以 `.venv/bin/python` 开头。使用上面的系统 OpenCV 方案时，缺少 Flask 就执行 `.venv/bin/python -m pip install "Flask>=3.0,<4"`；缺少 OpenCV/NumPy 就重新执行 `sudo apt install -y python3-opencv python3-numpy`，并确认虚拟环境是用 `--system-site-packages` 创建的。只有选择纯 pip 方案时，才用 `.venv/bin/python -m pip install -r requirements.txt` 补齐全部依赖。

如果 pip 提示找不到 OpenCV wheel，改用上面的系统 OpenCV 方案，删除此前创建失败的 `.venv`，再用 `python3 -m venv --system-site-packages .venv` 重建。不要使用系统 `python app.py` 代替 `.venv/bin/python app.py`，否则可能再次遇到缺包。

### 第三步：配置摄像头列表

执行 `nano config.json`，在 `cameras` 中添加真实视频源。例如同时配置一部手机和 USB 摄像头：

```json
{
  "cameras": [
    {
      "id": "phone-1",
      "name": "婴儿房手机",
      "source": "phone",
      "url": "http://手机实际IP:8080/video"
    },
    {
      "id": "usb-1",
      "name": "婴儿房 USB",
      "source": "usb",
      "device": "/dev/video0"
    }
  ],
  "selected_camera": "phone-1"
}
```

- `id` 是列表内不可重复的内部标识；`name` 是网页下拉框显示的名称。
- 手机使用 `source: "phone"` 和实际 HTTP MJPEG/RTSP `url`；USB 使用 `source: "usb"` 和实际 `device`。
- `selected_camera` 只决定首次启动的默认项。网页切换后的选择保存在 `data/state.json`，重启后优先使用网页选择。
- 尚未接入摄像头时使用 `"cameras": []` 和 `"selected_camera": ""`，页面会显示“未配置摄像头”。项目不会自动生成模拟画面。
- 手机摄像头 App 必须先启动视频服务器，地址以 App 显示为准。`nano` 中按 `Ctrl+O`、回车保存，按 `Ctrl+X` 退出。

通知可以稍后配置。需要 ntfy 时，再填写 `notifications.ntfy_server` 和一个至少 16 字符的长随机 `ntfy_topic`；手机 App 订阅同一主题。完整步骤见“手机通知”章节。

### 第四步：下载模型并启动

```bash
cd /home/pi/camera_monitor
.venv/bin/python tools/download_model.py
.venv/bin/python app.py
```

看到 Flask 服务启动后，保持这个终端运行。在**同一局域网**的电脑或手机浏览器打开：

```text
http://树莓派局域网IP:8000
```

停止服务按 `Ctrl+C`。需要树莓派重启后自动运行，再按“开机自启”章节安装 systemd 服务。

### 日常使用

1. 先打开手机摄像头 App，确认手机未锁屏且视频服务器正在运行。
2. 打开监视页面，在顶部“选择摄像头”下拉框中选择设备；页面会清空旧画面、连接新视频源并自动刷新，不需要重启服务。
3. 确认左上角显示“实时”，而不是“离线·非实时画面”或“未配置摄像头”。只有实际收到新视频帧才显示“实时”。
4. 需要动作通知时打开“动作告警”；只想看画面时关闭它即可。关闭后网页弹窗、声音/震动和远程文字通知都会暂停。
5. 在“网页活动提醒”中选择声音、震动、声音和震动或仅弹窗，然后点击一次“测试网页提醒”。该按钮只测试当前浏览器，不产生真实动作事件，也不发送 ntfy/Bark。
6. “人员方框”只控制人员推理和标注，与动作告警互不影响；树莓派 3B 卡顿时可关闭。
7. 进入房间照顾宝宝时可静音 5、15 或 30 分钟；静音不停止画面和检测，可提前取消。
8. 用“显示 ROI 调试框”确认监视区域只覆盖婴儿床，校准后关闭调试框。
9. 第一次使用远程通知时点击“发送文字测试通知”，并以手机实际收到为准；它与“测试网页提醒”是两条独立通知链路。
10. 页面持续离线时检查手机 App、同一 Wi-Fi、IP 地址和树莓派服务；恢复后系统会自动重连。

### 无手机时先试用

在两个终端分别运行：

```bash
# 终端 1：模拟手机视频流
cd /home/pi/camera_monitor
.venv/bin/python tools/simulate_stream.py --port 8080 --scene motion

# 终端 2：让项目读取模拟流并启动
cd /home/pi/camera_monitor
CAMERA_SOURCE=phone CAMERA_URL=http://127.0.0.1:8080/video .venv/bin/python app.py
```

然后打开 `http://树莓派IP:8000`。这只是软件流程模拟，不代表真实手机或婴儿场景已经验收。正式使用时去掉上述环境变量并配置真实 `cameras` 列表。

## 功能

- 可配置的多个手机网络流/USB 摄像头，网页下拉选择后自动重连和刷新；选择结果重启后保持。
- 单例后台采集，网页 MJPEG 实时画面，摄像头未配置/连接中/离线/重连状态；断流后显示离线占位图，不循环旧实时帧。
- 可持久化的动作告警、人员方框独立开关；5/15/30 分钟临时静音，重启不延长。
- ROI 自适应背景动作检测，联合面积、强度、持续时间和窗口占比，抑制光照突变、冻结和严重花屏。
- 持续明显动作触发网页浮层，10 秒自动关闭；每台设备可选声音、震动、两者或仅弹窗，历史事件和重复轮询不重复提醒。
- 单实例 YOLOX-Nano 多人检测、置信度过滤、NMS、原图坐标映射和结果过期提示。
- ntfy/Bark 文字通知，超时与有限重试；动作和摄像头离线分别冷却，恢复有记录。
- 不保存连续录像，不上传画面到云端识别，不默认发送照片。

## 1. 树莓派全新安装

推荐 Raspberry Pi OS，Python 3.9 或更新版本。项目目录以下以 `/home/pi/camera_monitor` 为例。

### 使用系统 OpenCV（优先，适合 3B/32 位系统）

```bash
sudo apt update
sudo apt install -y python3-venv python3-opencv python3-numpy
cd /home/pi/camera_monitor
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install "Flask>=3.0,<4"
mkdir -p data models
cp config.example.json config.json
```

### 使用 pip OpenCV（64 位系统且软件源提供对应架构 wheel）

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
mkdir -p data models
cp config.example.json config.json
```

不要同时安装 apt 和 pip 两套 OpenCV。可用下面的命令确认实际导入版本：

```bash
.venv/bin/python -c "import cv2, flask, numpy; print(cv2.__version__, flask.__version__, numpy.__version__)"
```

## 2. 当前调试：手机作为网络摄像头

### 安卓

1. 安装能在**手机本机**直接提供 HTTP MJPEG 或 RTSP 的摄像头 App（例如 IP Webcam 类应用），不依赖电脑客户端或云端中转。
2. 优先选 HTTP MJPEG，将分辨率设为 640×480 或 1280×720、10～15 FPS。树莓派 3B 先从 640×480、10 FPS 开始。
3. 启动摄像头服务器，在 App 中查看实际流地址。MJPEG 常见为 `http://手机IP:8080/video`，但必须以 App 显示为准。
4. 保持 App 前台，关闭自动锁屏，或将 App 加入后台运行/电池优化白名单。手机与树莓派连接同一局域网。
5. 建议在路由器为手机设置 DHCP 地址保留，避免重启后 IP 改变。

iPhone 或其他手机同样选择能在局域网直接暴露标准 HTTP MJPEG/RTSP 的 App；不要使用只能云端查看的 App。项目不绑定某个可能下架或收费的专有 App。

编辑 `config.json`，把手机加入 `cameras` 列表：

```json
{
  "cameras": [
    {
      "id": "phone-1",
      "name": "婴儿房手机",
      "source": "phone",
      "url": "http://手机实际局域网IP:8080/video",
      "width": 640,
      "height": 480,
      "fps": 10
    }
  ],
  "selected_camera": "phone-1"
}
```

也可用环境变量覆盖，适合不把带凭据的 URL 写入文件：

```bash
export CAMERA_SOURCE=phone
export CAMERA_URL='http://手机实际局域网IP:8080/video'
```

RTSP 只需将 `url` 改为 App 提供的 `rtsp://...` 地址。OpenCV 必须带 FFmpeg 支持。

**摆放安全：手机、充电线和支架必须固定在大人可操作、婴儿不可触及的位置，注意充电发热和通风，不要放在婴儿床内。**

## 3. 安装人员模型

使用官方 YOLOX-Nano ONNX，项目不提交模型文件：

```bash
.venv/bin/python tools/download_model.py
```

脚本会检查 SHA-256 并用 OpenCV 实际加载。固定哈希：

```text
c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d
```

如果 GitHub 连接受限，可在可访问网络的设备从以下**官方地址**下载，核对上述哈希后放入 `models/yolox_nano.onnx`：

```text
https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_nano.onnx
sha256sum models/yolox_nano.onnx
```

模型信息：

- 来源：[Megvii-BaseDetection/YOLOX](https://github.com/Megvii-BaseDetection/YOLOX)，release `0.1.1rc0`，文件 `yolox_nano.onnx`。
- 输入 416×416，COCO 80 类，本项目只保留 `person`；约 0.91M 参数、1.08 GFLOPs。
- 上游代码及该 release 模型按项目 Apache-2.0 许可使用；再分发时保留下游所需的许可与来源说明。
- 推理只使用 OpenCV DNN，不需要 PyTorch、训练框架或云端服务。

模型不存在或加载失败时页面显示“人员检测不可用”，视频和动作检测仍继续工作。

## 4. 启动与局域网观看

```bash
.venv/bin/python app.py
```

在同一局域网电脑或手机浏览器打开：

```text
http://树莓派局域网IP:8000
```

页面包含实时画面、检测人数与结果更新时间、摄像头状态、两个独立开关、静音控制、最近动作/通知/设备异常。视频服务不需要互联网，仅监听局域网；默认没有账号认证，因此只应在可信家庭 Wi-Fi 使用，不要做路由器端口映射或直接暴露公网。

## 5. 手机通知

### ntfy（Android/iPhone，首选）

1. 安装 ntfy App。公共服务可使用 `https://ntfy.sh`；更注重隐私时自行托管服务器。
2. 生成足够长、不可猜测的主题名：

   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(24))"
   ```

3. 在手机 ntfy 中订阅完全相同的主题。
4. 修改本地配置：

   ```json
   {
     "notifications": {
       "provider": "ntfy",
       "ntfy_server": "https://ntfy.sh",
       "ntfy_topic": "替换为刚生成的长随机主题",
       "timeout_seconds": 5,
       "retries": 2
     }
   }
   ```

   也可使用 `NTFY_SERVER`、`NTFY_TOPIC` 环境变量。

5. 重启服务，点击页面“发送文字测试通知”，再到手机确认实际送达。

公共 ntfy 主题不是加密或私密频道，知道主题的人可能订阅或发布消息。不要上传宝宝照片；有条件时优先自托管并启用其访问控制。远程推送需要互联网，和局域网视频的网络条件不同。

### Bark（iPhone）

将 `provider` 改为 `bark`，配置 `bark_server`（默认 `https://api.day.app`）和 `bark_device_key`，或使用 `BARK_SERVER`、`BARK_DEVICE_KEY`。Bark 强制 HTTPS，Key 不会返回网页。默认仍只发送文字。

通知发送失败会在 `data/events.jsonl` 记录错误类型并按配置重试，不阻塞采集、检测或视频。

## 6. 动作检测与调参

默认 ROI 为 `[0.1, 0.1, 0.8, 0.8]`，依次是归一化 `x、y、宽、高`，取值相对于画面宽高。将它缩到婴儿床和宝宝活动区域，尽量排除门口和家长经过的位置。页面“显示 ROI 调试框”仅用于校准，默认关闭。

`motion` 主要参数：

- `sensitivity`：像素差分阈值，默认 25；误报多时增大，漏报多时减小。
- `min_area_ratio`：ROI 内最小运动面积比例，默认 0.025。
- `min_motion_intensity`：有效运动区域平均差分强度，默认 12。
- `sustained_seconds`：连续明显动作至少持续多久，默认 2 秒。
- `window_seconds` / `min_active_ratio`：判断窗口和窗口内有效帧占比，默认 4 秒 / 0.5。
- `cooldown_seconds`：动作提醒冷却，默认 60 秒。
- `background_rate`：背景适应速度，默认 0.03；画面固定时无需频繁调整。
- `lighting_delta_threshold`：大面积均匀亮度变化的重建阈值，默认 18。
- `freeze_seconds`：相同画面持续多久视为冻结，默认 5 秒。

每次只调整一个参数。用宝宝实际躺卧、翻身、被子遮挡和昼夜光线校准；不要把“检测到人员框”当作醒来，也不要因某帧没有人员框而屏蔽动作检测。

## 7. 人员检测性能

默认每秒最多推理 1 次，视频仍持续显示最新帧。方框只短暂保留，`stale_seconds` 到期后隐藏，不用旧结果冒充实时检测。

树莓派 3B 卡顿时依次调整：

1. 将 `detection.interval_seconds` 从 1 调到 2～5。
2. 将 `detection.input_size` 从 416 降到 320（必须为 32 的倍数），可能进一步漏检。
3. 降低手机流至 640×480、10 FPS，降低 `camera.jpeg_quality`。
4. 仍无法兼顾时关闭“人员方框”，保留实时视频与动作告警。

实测命令：

```bash
.venv/bin/python tools/benchmark.py --seconds 60
```

输出采集 FPS、平均/最大推理耗时、进程 CPU（单核 100% 为满一个核）、Linux 内存和末尾跳过帧数。推理线程没有历史帧队列，只读取最新帧。

## 8. 网页活动提醒、静音与摄像头离线提醒

### 网页活动提醒

1. 在“网页活动提醒”中选择声音、震动、声音和震动或仅弹窗。选择保存在当前浏览器的 `localStorage`，不同手机互不影响；浏览器禁用存储时，选择在本次页面会话仍有效。
2. 每次打开页面后，点击一次“测试网页提醒”。声音使用 Web Audio，浏览器通常要求先有点击等用户操作才允许播放；测试会同时检查当前选择，不写入动作事件，也不调用 ntfy/Bark。
3. 检测到持续明显动作的新事件时，页面顶部出现不可选中、不阻挡操作的浮层，10 秒后自动关闭。另一个新事件会更新检测时间、重新计时并按所选方式提醒；每秒状态刷新不会重复提醒，首次打开页面也不会回放历史事件。
4. 动作告警关闭或临时静音时不弹窗、不发声、不震动，已显示的浮层也会关闭；恢复后只响应之后的新事件。摄像头离线时不触发活动提醒。
5. 网页必须保持打开。锁屏、切到后台或浏览器冻结页面后，不保证声音、震动和计时准确；需要锁屏后的远程通知时，另行配置 ntfy/Bark。
6. 震动使用浏览器的 Vibration API，需要浏览器、系统设置和设备共同支持。iPhone Safari 不支持网页震动，请选择声音；不支持或调用被浏览器拒绝时，页面会显示原因，不会假装提醒成功。

### 静音与离线

- 静音 5/15/30 分钟只暂停动作通知（包括网页活动提醒），视频、动作状态和人员结果继续更新；可提前取消，到期自动恢复。
- `notifications.offline_enabled` 控制独立离线提醒。
- 持续离线超过 `offline_threshold_seconds`（默认 30 秒）才提醒，按 `offline_cooldown_seconds`（默认 300 秒）限制频率。
- 离线时不发送动作通知；恢复后记录 `recovered` 事件。
- 人数变化不触发通知。

## 9. USB 摄像头到货后切换

本次已经实现 USB 采集路径，但当前开发环境没有 USB 硬件，**尚未实机验证**。

```bash
sudo apt install -y v4l-utils
v4l2-ctl --list-devices
ls -l /dev/video*
v4l2-ctl --device=/dev/video0 --list-formats-ext
```

把 USB 摄像头追加到 `config.json` 的 `cameras` 列表中：

```json
{
  "id": "usb-1",
  "name": "婴儿房 USB",
  "source": "usb",
  "device": "/dev/video0",
  "width": 640,
  "height": 480,
  "fps": 10
}
```

`device` 也可为序号 `0`。修改列表后重启服务，再从网页下拉框选择它，无需修改其他模块。检查页面实时画面、帧率、拔插后的重连和动作告警。CSI 摄像头不在本版范围内。

## 10. 开机自启

先手动运行并完成验收，再安装服务：

```bash
mkdir -p /home/pi/camera_monitor/data
cp deploy/baby-monitor.service /tmp/baby-monitor.service
# 如用户或目录不是 pi / /home/pi/camera_monitor，先编辑服务文件中的路径和 User/Group。
sudo cp deploy/baby-monitor.service /etc/systemd/system/baby-monitor.service
sudo systemctl daemon-reload
sudo systemctl enable --now baby-monitor
systemctl status baby-monitor
journalctl -u baby-monitor -n 100 --no-pager
```

服务只写入项目的 `data/`，失败时 5 秒后重启。修改配置后执行 `sudo systemctl restart baby-monitor`。

## 11. 模拟视频源与故障排查

没有手机时可在另一终端启动可复现的 MJPEG 源：

```bash
.venv/bin/python tools/simulate_stream.py --port 8080 --scene motion
```

将 `CAMERA_URL=http://127.0.0.1:8080/video` 指向它。场景可通过 `/control?scene=static|motion|brief|outside|noise|light` 切换。

常见问题：

- **无法打开手机流**：先在树莓派浏览器或另一台电脑访问流地址；检查同网段、App 前台、IP、防火墙和地址是否确实为 MJPEG/RTSP。
- **一直重连**：查看 `journalctl`；网页不会显示完整摄像头 URL 或凭据。
- **没有人员框**：先确认模型哈希和 OpenCV 加载，再检查页面开关、结果是否过期。躺卧、侧躺、遮挡和暗光可能漏检；无框不代表房间无人。
- **动作太敏感/不敏感**：缩小 ROI，再逐项调整面积、强度和持续阈值；检查自动曝光与摄像头是否移动。
- **通知失败**：检查互联网、DNS、服务器、主题/Key 和事件日志；视频不受通知故障影响。
- **USB 离线**：确认设备路径、当前用户访问权限及其他程序是否独占设备。

## 12. 验收清单与本次验证状态

在真实设备上逐项验收：

1. 手机持续取流；电脑和另一台手机通过树莓派 IP 同时观看。
2. 断流、重连、恢复均有正确状态，断流时不展示旧实时帧。
3. 动作开关关闭时只更新状态不通知；重启后开关保持。
4. ROI 内持续动作只按冷却提醒，短暂小动作、噪声和开关灯不轻易告警。
5. 通知不可用时视频继续；静音、取消、到期和冷却行为正确。
6. 无人、单人、双人和多人画面的框位置、置信度、人数和 NMS 正确；换分辨率后仍准确。
7. 用**获得授权的婴儿实际画面**检查躺卧、翻身、遮挡和暗光漏检，不能拿站立人物测试替代。
8. 持续离线按独立频率提醒，恢复有记录。
9. 分别在树莓派 3B/4B 记录 60 秒以上 FPS、推理耗时和 CPU/内存；没有 3B 时至少测 4B，并注明 3B 未测。
10. 按本文从全新安装跑到网页、多人方框和手机实际通知，再完成 USB 切换。

本次开发环境实际完成：Windows Python 3.12 下的真实 HTTP MJPEG 取流约 9.8 FPS、断线清帧与自动重连、两个 MJPEG 客户端、多场景动作判定、模型哈希与 OpenCV 加载、公开无人/双人/多人图片推理（0/2/3 人）、640×360 坐标映射、开关与静音持久化、本地 ntfy HTTP POST/重试/冷却/离线/恢复链路。公开图本机推理约 46～78 ms。

随后已在目标树莓派（Raspbian 12、32 位 armv7l）完成部署和 20 秒模拟流基准：平均 8.54 FPS、人员推理平均 398.9 ms、内存约 136.5 MB；已验证 PC 局域网网页/MJPEG、动作与离线通知模拟链路、断流恢复、持久化及 systemd 自启。详见 `部署记录.md`。

2026-11-09 新增摄像头列表/即时切换并接入真实 vivo 手机 MJPEG：PC 和树莓派均实际收到 640×480 视频帧，短时采集约 30 FPS，树莓派人员检测正常。该结果只证明当前手机取流可用，不替代长期稳定性或婴儿实际场景验收。

2026-11-09 新增网页活动提醒：本地通过模拟流和真实 Edge 浏览器验证浮层出现及 10 秒自动关闭，并通过脚本验证事件去重、计时重置、开关/静音和震动不支持时的降级。真实手机的扬声器、震动、后台行为仍需在目标手机上点击“测试网页提醒”验收。

**尚未完整实机验证：树莓派 3B、真实手机长期取流、真实手机网页声音/震动和另一台真实手机浏览器、USB 摄像头、真实 ntfy/Bark 手机送达，以及授权婴儿躺卧/遮挡画面。短时测试不替代长时间稳定性测试，以上项目不得视为已经通过。**
