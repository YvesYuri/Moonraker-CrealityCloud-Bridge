# Moonraker-CrealityCloud Bridge

A bridge that connects **Klipper (via Moonraker)** to **Creality Cloud**, allowing you to use the Creality Cloud app to remotely monitor and control a 3D printer running Klipper.

## How It Works

```
┌─────────────────┐         ┌──────────────────┐         ┌─────────────────────┐
│  Moonraker API  │◄───────►│  Python Bridge   │◄───────►│  ThingsBoard MQTT   │
│  (REST + WS)    │         │  (this project)  │         │  (crealitycloud)    │
└─────────────────┘         └──────────────────┘         └─────────────────────┘
         ▲                           ▲                            ▲
    Klipper                    Python Script              Creality Cloud App
         │                           │
         │                    ┌──────┴──────┐
         │                    │ WebRTC      │
         │                    │ (video)     │
         │                    └─────────────┘
         │                           │
         ▼                           ▼
   /dev/videoX              Creality Cloud App
   (camera)                   (live stream)
```

## Features

### Monitoring
- Nozzle and bed temperature (real-time)
- Print progress (%)
- Elapsed and remaining time
- Toolhead XYZ position
- Current feedrate
- Printer state (printing, paused, idle, error)
- Current layer
- Currently printing filename

### Remote Control
- Pause / Resume print
- Cancel print
- Set nozzle and bed temperature
- Fan on/off
- Home axes (G28)
- Send arbitrary G-code commands
- Adjust feedrate
- Start cloud print (download + print)
- List local files
- Print local file

### Video Streaming (WebRTC)
- Live camera streaming via WebRTC
- Full integration with Creality Cloud app
- Automatically detects camera device
- Fetches ICE servers from Creality API

## Prerequisites

- Python 3.7+
- Moonraker running and accessible
- Creality Cloud account with device token
- FFmpeg and libavcodec (for video): `sudo apt-get install ffmpeg libavcodec-extra`
- Python aiortc and av packages

## Installation

```bash
# Clone or copy to the Moonraker machine
cd moonraker-crealitycloud-bridge

# Install system dependencies
sudo apt-get install ffmpeg libavcodec-extra

# Install Python dependencies
pip install -r requirements.txt
```

## Setup

### 1. Get the Creality Cloud Token

1. Open the **Creality Cloud** app on your phone
2. Add a new device of type **Raspberry Pi**
3. The app will generate a `.tk` file or a JWT token
4. Copy the token content (JWT string)

### 2. First Run (Setup)

```bash
python3 main.py --token YOUR_JWT_TOKEN
```

This registers the device with Creality servers and saves `config.json` locally.

### 3. Normal Run

```bash
python3 main.py
```

### Command Line Options

```
--token TOKEN              JWT token from Creality Cloud (initial setup)
--moonraker URL            Moonraker URL (default: http://localhost:7125)
--moonraker-api-key KEY    Moonraker API key (if required)
--region 0|1               0=China, 1=Overseas (default: 1)
--video-port PORT          Video server port (default: 8080)
--camera-device PATH       Camera device path (default: /dev/video0)
--config-dir DIR           Directory for config files (default: script dir)
--verbose, -v              Enable verbose/debug logging
--version                  Show bridge version
```

### Examples

```bash
# Initial setup
python3 main.py --token eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...

# Custom camera device (e.g., /dev/video5)
python3 main.py --camera-device /dev/video5

# Custom Moonraker URL
python3 main.py --moonraker http://192.168.1.100:7125

# With Moonraker API key
python3 main.py --moonraker-api-key YOUR_API_KEY

# China region + verbose
python3 main.py --region 0 --verbose

# Custom video port
python3 main.py --video-port 9090
```

## Camera Setup

### Prerequisites

1. **Install FFmpeg and codecs**:
   ```bash
   sudo apt-get install ffmpeg libavcodec-extra
   ```

2. **Connect your camera**:
   - USB webcam: Plug in and verify with `ls /dev/video*`
   - Raspberry Pi Camera: Enable in raspi-config and verify

3. **Test camera**:
   ```bash
   # List available cameras
   v4l2-ctl --list-devices
   
   # Test video capture
   ffmpeg -i /dev/video0 -t 5 /tmp/test.mp4
   ```

### How It Works

The bridge implements **WebRTC** for video streaming to be fully compatible with the Creality Cloud app:

1. **Camera Detection**: Checks if the specified camera device exists
2. **WebRTC Setup**: 
   - Fetches ICE servers from Creality API (`/api/cxy/v2/webrtc/iceServersJwt`)
   - Connects to Creality signaling server via WebSocket
   - Handles SDP offer/answer exchange
3. **Video Stream**: Uses `aiortc` library to capture from V4L2 device and stream via WebRTC

### Configuration

- Default camera: `/dev/video0`
- Custom camera: Use `--camera-device /dev/video5` (or your device)
- The app will show camera icon when `video=1` is sent via MQTT

## Running as a Service (systemd)

```bash
sudo nano /etc/systemd/system/creality-bridge.service
```

```ini
[Unit]
Description=Moonraker-CrealityCloud Bridge
After=network.target moonraker.service

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/moonraker-crealitycloud-bridge
ExecStart=/usr/bin/python3 /home/pi/moonraker-crealitycloud-bridge/main.py --camera-device /dev/video5
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable creality-bridge
sudo systemctl start creality-bridge
sudo systemctl status creality-bridge
```

## Configuration Files

### `config.json` (auto-generated)
```json
{
  "deviceName": "your_device",
  "deviceSecret": "tb_token_here",
  "iotType": 2,
  "region": 1,
  "moonraker_url": "http://localhost:7125",
  "moonraker_api_key": null,
  "video_port": 8080,
  "camera_device": "/dev/video0"
}
```

### `p2pcfg.json` (P2P video configuration)
```json
{
  "InitString": "...",
  "APILicense": "...",
  "DIDString": "..."
}
```

## Command Mapping

| Creality Cloud | → Moonraker |
|---|---|
| `pause=1` | `POST /printer/print/pause` |
| `pause=0` | `POST /printer/print/resume` |
| `stop=1` | `POST /printer/print/cancel` |
| `nozzleTemp2=200` | `M104 S200` |
| `bedTemp2=60` | `M140 S60` |
| `fan=1` | `M106` |
| `fan=0` | `M107` |
| `autohome=0` | `G28` |
| `curFeedratePct=80` | `M220 S80` |
| `gcodeCmd=G1 X10 Y10` | `POST /printer/gcode/script` |
| `print=url` | Download + Upload + `/printer/print/start` |
| `opGcodeFile=print:/local/x.gcode` | `/printer/print/start` |

## Printer States

| State | Value | Description |
|---|---|---|
| Idle | 0 | Printer idle/ready |
| Printing | 1 | Currently printing |
| Done | 2 | Print complete |
| Error | 3 | Print error |
| Stopped | 4 | Cancelled |
| Paused | 5 | Paused |

## Logging

Logs are displayed in the console. Use `--verbose` for debug details:

```bash
python3 main.py --verbose
```

## Troubleshooting

### "Bridge not configured"
Run with `--token` to set up the device.

### "Failed to connect to ThingsBoard"
- Check your internet connection
- Confirm the region is correct (0=China, 1=Overseas)
- Verify the token hasn't expired

### "Moonraker connection failed"
- Check if Moonraker is running: `systemctl status moonraker`
- Test the URL: `curl http://localhost:7125/printer/info`
- If Moonraker requires an API key, use `--moonraker-api-key`

### Temperature not showing
- Verify sensors are correctly configured in Klipper
- The bridge uses `extruder.temperature` and `heater_bed.temperature` from Moonraker

### Camera not working
- Verify camera exists: `ls -la /dev/video*`
- Test FFmpeg: `ffmpeg -i /dev/video5 -t 5 /tmp/test.mp4`
- Install FFmpeg: `sudo apt-get install ffmpeg libavcodec-extra`
- Check if av library works: `python3 -c "import av; print(av.__version__)"`

### WebRTC issues
- Check ICE servers are being fetched (use `--verbose`)
- Verify WebSocket connection to Creality signaling server
- Check firewall allows outbound WebSocket connections

### Video loads but doesn't play in app
- Ensure `video=1` attribute is being sent to Creality Cloud
- Check that WebRTC manager started without errors
- Verify ICE servers were obtained from Creality API

## Architecture Details

### WebRTC Flow

```
1. Bridge starts → Fetches ICE servers from Creality API
2. Bridge connects to Creality signaling server (WebSocket)
3. App opens → Requests video stream from server
4. Server sends "offer" via signaling channel
5. Bridge receives offer → Creates answer with camera stream
6. ICE negotiation → Direct P2P video stream established
7. Video flows directly from camera to app
```

### Dependencies

- `aiortc`: WebRTC implementation
- `av` (PyAV): Video capture from V4L2 devices
- `websocket-client`: WebSocket connection to Creality signaling
- `tb-mqtt-client`: ThingsBoard MQTT for telemetry/attributes

## License

Same license as the original OctoPrint-CrealityCloud project.
