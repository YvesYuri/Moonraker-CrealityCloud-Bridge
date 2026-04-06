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

## Prerequisites

- Python 3.7+
- Moonraker running and accessible
- Creality Cloud account with device token

## Installation

```bash
# Clone or copy to the Moonraker machine
cd moonraker-crealitycloud-bridge

# Install dependencies
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
--config-dir DIR           Directory for config files (default: script dir)
--verbose, -v              Enable verbose/debug logging
--version                  Show bridge version
```

### Examples

```bash
# Initial setup
python3 main.py --token eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...

# Custom Moonraker URL
python3 main.py --moonraker http://192.168.1.100:7125

# With Moonraker API key
python3 main.py --moonraker-api-key YOUR_API_KEY

# China region + verbose
python3 main.py --region 0 --verbose
```

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
ExecStart=/usr/bin/python3 /home/pi/moonraker-crealitycloud-bridge/main.py
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
  "moonraker_api_key": null
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

## Limitations

- **P2P Camera**: P2P string configuration is supported, but video streaming requires additional setup
- **LED**: LED control depends on printer firmware (G-code M224/M225/M936)
- **Large files**: Downloading .gcode.gz files from the cloud may take time

## License

Same license as the original OctoPrint-CrealityCloud project.
