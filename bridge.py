import json
import logging
import os
import socket
import tempfile
import threading
import time
import uuid
from contextlib import closing
from enum import Enum

import requests

from config import BridgeConfig
from creality_mqtt import CrealityMQTT
from moonraker_client import MoonrakerClient
from video_server import VideoServer

logger = logging.getLogger(__name__)


class ErrorCode(Enum):
    UNKNOW = 0
    STOP = 1
    DOWNLOAD_FAIL = 2
    PRINT_DISCONNECT = 3
    BREAK_SERIAL = 4
    NO_PRINTABLE = 5
    HEAT_FAIL = 6
    SYSTEM_HALT = 7
    SYSTEM_TIMOUT = 8
    NO_TFCARD = 9
    NO_SPLACE = 10


STATE_IDLE = 0
STATE_PRINTING = 1
STATE_DONE = 2
STATE_ERROR = 3
STATE_STOPPED = 4
STATE_PAUSED = 5


class MoonrakerCrealityBridge:
    def __init__(self, config_dir=None):
        self.config = BridgeConfig(config_dir)
        self.moonraker = None
        self.mqtt = None
        self.video_server = None
        self.webrtc_manager = None

        self._state = STATE_IDLE
        self._pause = 0
        self._stop = 0
        self._fan = 0
        self._nozzle_temp_target = 0
        self._bed_temp_target = 0
        self._print_id = ""
        self._filename = ""
        self._model = ""
        self._layer = 0
        self._position = " "
        self._feedrate_pct = 100
        self._dprogress = 0
        self._is_cloud_print = False
        self._print_start_time = 0
        self._print_estimated_time = 0
        self._error_code = 0
        self._led_state = 0
        self._autohome = 0
        self._connect_state = 0
        self._video = 0
        self._camera_device = self.config.get("camera_device", "/dev/video0")

        self._telemetry_buffer = {}
        self._attributes_buffer = {}

        self._upload_timer = None
        self._iot_timer = None
        self._running = False

        self._file_list_cache = []

    def setup_token(self, jwt_token):
        logger.info("Setting up device token...")
        from cxhttp import CrealityAPI

        api = CrealityAPI()
        result = api.getconfig(jwt_token)

        if "result" not in result:
            logger.error(f"Failed to register device: {result}")
            return False

        res = result["result"]
        self.config.set("deviceName", res["deviceName"])
        self.config.set("deviceSecret", res["tbToken"])
        self.config.set("iotType", res.get("iotType", 2))
        self.config.set("region", res.get("regionId", 1))

        logger.info(f"Device registered: {res['deviceName']}")
        return True

    def connect(self):
        if not self.config.is_configured():
            logger.error("Bridge not configured. Run with --token first.")
            return False

        cfg = self.config.data()
        region = cfg.get("region", 1)

        moonraker_url = cfg.get("moonraker_url", "http://localhost:7125")
        moonraker_api_key = cfg.get("moonraker_api_key")

        self.moonraker = MoonrakerClient(moonraker_url, moonraker_api_key)
        self.moonraker.connect()

        time.sleep(2)

        self.mqtt = CrealityMQTT(
            device_name=cfg["deviceName"],
            token=cfg["deviceSecret"],
            region=region,
        )

        if not self.mqtt.connect():
            logger.error("Failed to connect to Creality Cloud MQTT")
            return False

        self.mqtt.set_rpc_handler(self._on_rpc_request)

        self.moonraker.on_status_update(self._on_status_update)

        self._connect_state = 1
        self._set_attribute("state", STATE_IDLE)
        self._set_attribute("tfCard", 1)
        self._set_attribute("connect", 1)
        self._send_buffers()

        self._fetch_printer_model()
        self._fetch_file_list()

        self._init_video_server()

        self._running = True
        self._upload_timer = threading.Thread(target=self._upload_loop, daemon=True)
        self._upload_timer.start()

        self._iot_timer = threading.Thread(target=self._iot_loop, daemon=True)
        self._iot_timer.start()

        logger.info("Bridge connected and running")
        return True

    def disconnect(self):
        self._running = False
        if self.webrtc_manager:
            self.webrtc_manager.stop()
        if self.video_server:
            self.video_server.stop()
        if self.mqtt:
            self.mqtt.disconnect()
        if self.moonraker:
            self.moonraker.disconnect()
        logger.info("Bridge disconnected")

    def _init_video_server(self):
        video_port = self.config.get("video_port", 8080)
        self.video_server = VideoServer(port=video_port, camera_device=self._camera_device)

        if self.video_server.is_camera_available():
            self._video = 1
            self._set_attribute("video", 1)
            self.video_server.start()
            self._init_webrtc()
            logger.info("Video server initialized")
        else:
            self._video = 0
            self._set_attribute("video", 0)
            logger.info("No camera available, video server not started")

    def _init_webrtc(self):
        try:
            from webrtc_manager import WebRTCManager

            cfg = self.config.data()
            self.webrtc_manager = WebRTCManager(
                device_name=cfg["deviceName"],
                token=cfg["deviceSecret"],
                region=cfg.get("region", 1),
                camera_device=self._camera_device,
                verbose=False
            )
            self.webrtc_manager.start()
            logger.info("WebRTC manager started")
        except Exception as e:
            logger.error(f"Failed to start WebRTC manager: {e}")

    def _fetch_printer_model(self):
        try:
            info = self.moonraker.get_printer_info()
            if info and "machine" in info:
                self._model = info["machine"]
            elif info and "software_version" in info:
                self._model = "Klipper"
        except Exception:
            self._model = "Klipper"
        self._set_attribute("model", self._model)

    def _fetch_file_list(self):
        try:
            files = self.moonraker.get_file_list()
            if files:
                self._file_list_cache = files
        except Exception as e:
            logger.error(f"Failed to fetch file list: {e}")

    def _on_status_update(self, status):
        try:
            self._process_status(status)
        except Exception as e:
            logger.error(f"Error processing status update: {e}")

    def _process_status(self, status):
        # Ensure status is a dict before processing
        if not isinstance(status, dict):
            logger.debug(f"Status is not a dict: {type(status)}")
            return

        print_stats = status.get("print_stats", {}) or {}
        display_status = status.get("display_status", {}) or {}
        toolhead = status.get("toolhead", {}) or {}
        extruder = status.get("extruder", {}) or {}
        heater_bed = status.get("heater_bed", {}) or {}
        fan = status.get("fan", {}) or {}
        gcode_move = status.get("gcode_move", {}) or {}

        mr_state = print_stats.get("state", "standby")
        mr_filename = print_stats.get("filename", "")
        total_duration = print_stats.get("total_duration", 0)
        print_duration = print_stats.get("print_duration", 0)

        progress = display_status.get("progress", 0)
        if progress is not None:
            progress_pct = int(progress * 100)
        else:
            progress_pct = 0

        nozzle_temp = extruder.get("temperature", 0)
        nozzle_target = extruder.get("target", 0)
        bed_temp = heater_bed.get("temperature", 0)
        bed_target = heater_bed.get("target", 0)

        position = toolhead.get("position", [0, 0, 0, 0])
        if isinstance(position, list) and len(position) >= 3:
            self._position = f"X:{position[0]:.1f} Y:{position[1]:.1f} Z:{position[2]:.1f}"

        speed_factor = gcode_move.get("speed_factor", 1.0)
        self._feedrate_pct = int(speed_factor * 100)

        fan_speed = fan.get("speed", 0)
        self._fan = 1 if fan_speed > 0 else 0

        if mr_filename and mr_filename != self._filename:
            self._filename = mr_filename
            self._set_attribute("print", mr_filename)

        state_map = {
            "printing": STATE_PRINTING,
            "paused": STATE_PAUSED,
            "complete": STATE_DONE,
            "standby": STATE_IDLE,
            "error": STATE_ERROR,
            "cancelled": STATE_STOPPED,
        }
        new_state = state_map.get(mr_state, STATE_IDLE)

        if mr_state == "printing" and self._state != STATE_PRINTING:
            self._state = STATE_PRINTING
            self._print_start_time = int(time.time())
            self._set_attribute("printStartTime", str(self._print_start_time))
            self._set_attribute("state", STATE_PRINTING)
            self._set_telemetry("printJobTime", 0)
            self._set_telemetry("printLeftTime", 0)
            self._set_telemetry("dProgress", 0)

            if self._is_cloud_print:
                self._set_attribute("mcu_is_print", 0)
            else:
                ts = int(time.time())
                self._print_id = f"local_{ts}"
                self._set_attribute("printId", self._print_id)
                self._set_attribute("mcu_is_print", 1)

            try:
                self._set_temp_from_gcode(mr_filename)
            except Exception:
                pass

        elif mr_state == "paused" and self._state != STATE_PAUSED:
            self._state = STATE_PAUSED
            self._pause = 1
            self._set_attribute("state", STATE_PAUSED)
            self._set_attribute("pause", 1)

        elif mr_state == "complete" and self._state != STATE_DONE:
            self._state = STATE_DONE
            self._pause = 0
            self._stop = 0
            self._set_attribute("state", STATE_DONE)
            self._set_attribute("pause", 0)
            self._set_attribute("stop", 0)
            self._set_telemetry("printProgress", 0)
            self._set_telemetry("printLeftTime", 0)
            self._set_telemetry("printJobTime", 0)
            if self._print_id.startswith("local_"):
                self._set_attribute("printId", " ")
                self._set_attribute("mcu_is_print", 0)
            self._is_cloud_print = False
            self._filename = ""

        elif mr_state == "error" and self._state != STATE_ERROR:
            self._state = STATE_ERROR
            self._error_code = ErrorCode.PRINT_DISCONNECT.value
            self._set_attribute("state", STATE_ERROR)
            self._set_attribute("err", self._error_code)
            self._set_telemetry("printProgress", 0)
            if self._is_cloud_print:
                self._is_cloud_print = False

        elif mr_state == "cancelled" and self._state != STATE_STOPPED:
            self._state = STATE_STOPPED
            self._stop = 2
            self._error_code = ErrorCode.STOP.value
            self._set_attribute("state", STATE_STOPPED)
            self._set_attribute("stop", 2)
            self._set_attribute("err", self._error_code)
            self._set_telemetry("printProgress", 0)
            if self._print_id.startswith("local_"):
                self._set_attribute("printId", " ")
                self._set_attribute("mcu_is_print", 0)
            if self._is_cloud_print:
                self._is_cloud_print = False

        elif mr_state == "standby" and self._state not in (STATE_PRINTING, STATE_PAUSED):
            if self._state != STATE_IDLE:
                self._state = STATE_IDLE
                self._set_attribute("state", STATE_IDLE)

        if self._state == STATE_PRINTING:
            elapsed = int(total_duration) if total_duration else 0
            self._set_telemetry("printJobTime", elapsed)

            if progress_pct > 0 and progress_pct < 100:
                remaining = int((elapsed / progress_pct) * (100 - progress_pct))
            else:
                remaining = 0
            self._set_telemetry("printLeftTime", remaining)

            self._set_telemetry("dProgress", progress_pct)
            self._set_telemetry("printProgress", progress_pct)

        self._set_telemetry("nozzleTemp", int(nozzle_temp or 0))
        self._set_telemetry("bedTemp", int(bed_temp or 0))
        self._set_telemetry("curFeedratePct", self._feedrate_pct)

        self._set_attribute("curPosition", self._position)
        self._set_attribute("nozzleTemp2", int(nozzle_target or 0))
        self._set_attribute("bedTemp2", int(bed_target or 0))
        self._set_attribute("fan", self._fan)

    def _set_temp_from_gcode(self, filename):
        try:
            resp = self.moonraker.get_file_metadata(filename)
            if resp:
                info = resp.get("metadata", {})
                if "first_layer_bed" in info:
                    self._bed_temp_target = int(info["first_layer_bed"])
                    self._set_attribute("bedTemp2", self._bed_temp_target)
                if "first_layer_extr_temp" in info:
                    self._nozzle_temp_target = int(info["first_layer_extr_temp"])
                    self._set_attribute("nozzleTemp2", self._nozzle_temp_target)
        except Exception as e:
            logger.error(f"Failed to get temp from metadata: {e}")

    def _set_telemetry(self, key, value):
        self._telemetry_buffer[key] = value

    def _set_attribute(self, key, value):
        self._attributes_buffer[key] = value

    def _send_buffers(self):
        if self._telemetry_buffer:
            self.mqtt.send_telemetry(dict(self._telemetry_buffer))
            self._telemetry_buffer.clear()
        if self._attributes_buffer:
            self.mqtt.send_attributes(dict(self._attributes_buffer))
            self._attributes_buffer.clear()

    def _upload_loop(self):
        while self._running:
            try:
                pass
            except Exception as e:
                logger.error(f"Upload timer error: {e}")
            time.sleep(2)

    def _iot_loop(self):
        while self._running:
            try:
                self._send_buffers()
            except Exception as e:
                logger.error(f"IoT timer error: {e}")
            time.sleep(3)

    def _on_rpc_request(self, client, request_id, request_body):
        method = request_body.get("method", "")
        params = request_body.get("params", {})

        if "set" in method:
            self._handle_rpc_set(client, request_id, params)
        elif "get" in method:
            self._handle_rpc_get(client, request_id, params)
        else:
            self.mqtt.reply_rpc(request_id, {"code": -1, "error": "Unknown method"})

    def _handle_rpc_set(self, client, request_id, params):
        try:
            for prop_name, prop_value in params.items():
                self._apply_property(prop_name, prop_value)
            self.mqtt.reply_rpc(request_id, {"code": 0})
        except Exception as e:
            logger.error(f"RPC set error: {e}")
            self.mqtt.reply_rpc(request_id, {"code": -1, "error": str(e)})

    def _handle_rpc_get(self, client, request_id, params):
        try:
            result = {"code": 0}
            for prop_name in params.keys():
                val = self._get_property(prop_name)
                if val is not None:
                    result[prop_name] = val
            self.mqtt.reply_rpc(request_id, result)
        except Exception as e:
            logger.error(f"RPC get error: {e}")
            self.mqtt.reply_rpc(request_id, {"code": -1, "error": str(e)})

    def _apply_property(self, name, value):
        value_str = str(value)

        if name == "pause":
            v = int(value_str)
            if v == 0:
                self.moonraker.resume_print()
                self._pause = 0
                self._set_attribute("pause", 0)
            elif v == 1:
                self.moonraker.pause_print()
                self._pause = 1
                self._set_attribute("pause", 1)

        elif name == "stop":
            v = int(value_str)
            if v == 1:
                self.moonraker.cancel_print()
                self._stop = 1
                self._state = STATE_STOPPED
                self._set_attribute("stop", 1)
                self._set_attribute("state", STATE_STOPPED)
            elif v == 2:
                self._stop = 2
                self._state = STATE_STOPPED
                self._set_attribute("stop", 2)
                self._set_attribute("state", STATE_STOPPED)

        elif name == "nozzleTemp2":
            v = int(value_str)
            self._nozzle_temp_target = v
            self.moonraker.send_gcode(f"M104 S{v}")
            self._set_attribute("nozzleTemp2", v)

        elif name == "bedTemp2":
            v = int(value_str)
            self._bed_temp_target = v
            self.moonraker.send_gcode(f"M140 S{v}")
            self._set_attribute("bedTemp2", v)

        elif name == "gcodeCmd":
            self.moonraker.send_gcode(value_str)
            self._set_attribute("gcodeCmd", value_str)
            if "G0" in value_str:
                idx = value_str.find("G0") + 2
                self._set_attribute("setPosition", value_str[idx:])
            elif "G28" in value_str:
                if "X" in value_str and "Y" in value_str:
                    self._set_attribute("setPosition", "X0 Y0")
                elif "Z" in value_str:
                    self._set_attribute("setPosition", "Z0")

        elif name == "fan":
            v = int(value_str)
            if v == 1:
                self.moonraker.send_gcode("M106")
            else:
                self.moonraker.send_gcode("M107")
            self._fan = v
            self._set_attribute("fan", v)

        elif name == "autohome":
            if value_str == "0" or value_str == "":
                self.moonraker.send_gcode("G28")
            self._autohome = 1
            self._set_attribute("autohome", 1)

        elif name == "curFeedratePct":
            v = int(value_str)
            self._feedrate_pct = v
            self.moonraker.send_gcode(f"M220 S{v}")
            self._set_telemetry("curFeedratePct", v)

        elif name == "print":
            self._handle_cloud_print(value_str)

        elif name == "opGcodeFile":
            self._handle_file_operation(value_str)

        elif name == "reqGcodeFile":
            v = int(value_str)
            page = v & 0x0000FFFF
            self._handle_file_list_request(page)

        elif name == "led":
            v = int(value_str)
            self._led_state = v
            self._set_attribute("led_state", v)

        elif name == "InitString":
            self.config.p2p_set("InitString", value_str)
            self._set_attribute("InitString", value_str)

        elif name == "APILicense":
            self.config.p2p_set("APILicense", value_str)
            self._set_attribute("APILicense", value_str)

        elif name == "DIDString":
            self.config.p2p_set("DIDString", value_str)
            self._set_attribute("DIDString", value_str)

        else:
            logger.debug(f"Unhandled property: {name}={value_str}")

    def _get_property(self, name):
        props = {
            "state": self._state,
            "pause": self._pause,
            "stop": self._stop,
            "fan": self._fan,
            "nozzleTemp2": self._nozzle_temp_target,
            "bedTemp2": self._bed_temp_target,
            "curFeedratePct": self._feedrate_pct,
            "curPosition": self._position,
            "model": self._model,
            "filename": self._filename,
            "printId": self._print_id,
            "layer": self._layer,
            "led_state": self._led_state,
            "autohome": self._autohome,
            "connect": self._connect_state,
        }
        return props.get(name)

    def _handle_cloud_print(self, download_url):
        self._is_cloud_print = True
        self._dprogress = 0
        self._print_id = str(uuid.uuid1()).replace("-", "")
        self._set_attribute("printId", self._print_id)
        self._set_attribute("print", download_url)

        threading.Thread(target=self._download_and_print, args=(download_url,), daemon=True).start()

    def _download_and_print(self, url):
        try:
            filename = os.path.basename(url)
            temp_dir = tempfile.gettempdir()
            temp_path = os.path.join(temp_dir, f"crealitycloud-{filename}")

            self._download_file(url, temp_path)

            if temp_path.endswith(".gz"):
                import gzip

                gcode_path = temp_path.replace(".gz", "")
                with gzip.open(temp_path, "rb") as f_in:
                    with open(gcode_path, "wb") as f_out:
                        f_out.write(f_in.read())
                os.remove(temp_path)
                final_filename = os.path.basename(gcode_path)
                final_path = gcode_path
            else:
                final_filename = filename
                final_path = temp_path

            self._dprogress = 100

            self.moonraker.upload_file(final_path, final_filename)

            os.remove(final_path)

            self.moonraker.start_print(final_filename)

            self._state = STATE_PRINTING
            self._print_start_time = int(time.time())
            self._set_attribute("state", STATE_PRINTING)
            self._set_attribute("printStartTime", str(self._print_start_time))

        except Exception as e:
            logger.error(f"Cloud print failed: {e}")
            self._error_code = ErrorCode.DOWNLOAD_FAIL.value
            self._set_attribute("err", self._error_code)
            self._is_cloud_print = False

    def _download_file(self, url, dest_path):
        with closing(requests.get(url, stream=True, timeout=120)) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            last_update = time.time()

            with open(dest_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        pct = int((downloaded / total) * 100)
                        if time.time() - last_update > 2:
                            self._dprogress = pct
                            last_update = time.time()

        self._dprogress = 100

    def _handle_file_operation(self, value):
        value_str = str(value)
        if value_str.startswith("print"):
            parts = value_str.split("/local/", 1)
            if len(parts) > 1:
                filename = parts[1]
                self.moonraker.start_print(filename)
        elif value_str.startswith("delete"):
            parts = value_str.split(":", 1)
            if len(parts) > 1:
                filename = parts[1]
                self.moonraker.delete_file(filename)
        elif value_str.startswith("rename"):
            pass
        self._set_attribute("opGcodeFile", value_str)

    def _handle_file_list_request(self, page):
        page_size = 50
        files = self._file_list_cache
        total = len(files)
        start = page * page_size
        end = start + page_size
        page_files = files[start:end]

        file_list = []
        for f in page_files:
            file_list.append({
                "filename": f.get("path", f.get("filename", "")),
                "size": f.get("size", 0),
                "modified": f.get("modified", 0),
            })

        result = {
            "retGcodeFileInfo": {
                "page": page,
                "total": total,
                "files": file_list,
            }
        }
        self._set_attribute("retGcodeFileInfo", json.dumps(result))
