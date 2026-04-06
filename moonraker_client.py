import json
import logging
import threading
import time

import requests
import websocket

logger = logging.getLogger(__name__)


class MoonrakerClient:
    def __init__(self, base_url, api_key=None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.ws_url = base_url.replace("http", "ws").rstrip("/") + "/websocket"
        self._ws = None
        self._ws_thread = None
        self._connected = False
        self._request_id = 1
        self._pending_requests = {}
        self._callbacks = {
            "status_update": [],
            "history_update": [],
            "filelist_changed": [],
            "gcode_response": [],
        }
        self._running = False
        self._headers = {}
        if api_key:
            self._headers["X-Api-Key"] = api_key

        self._status_cache = {}

    def connect(self):
        self._running = True
        self._ws_thread = threading.Thread(target=self._ws_loop, daemon=True)
        self._ws_thread.start()
        logger.info("Moonraker WebSocket connection started")

    def disconnect(self):
        self._running = False
        if self._ws:
            self._ws.close()
        if self._ws_thread:
            self._ws_thread.join(timeout=5)
        logger.info("Moonraker WebSocket disconnected")

    def _ws_loop(self):
        while self._running:
            try:
                self._ws = websocket.WebSocketApp(
                    self.ws_url,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                    header=self._headers,
                )
                self._ws.run_forever()
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
            if self._running:
                logger.info("Reconnecting in 5s...")
                time.sleep(5)

    def _on_open(self, ws):
        logger.info("Moonraker WebSocket connected")
        self._connected = True
        self._subscribe()

    def _on_close(self, ws, status_code, msg):
        logger.info(f"Moonraker WebSocket closed: {status_code} {msg}")
        self._connected = False

    def _on_error(self, ws, error):
        logger.error(f"Moonraker WebSocket error: {error}")

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            return

        if "id" in data and data["id"] in self._pending_requests:
            callback = self._pending_requests.pop(data["id"])
            callback(data.get("result"))
            return

        if "method" in data:
            method = data["method"]
            raw_params = data.get("params")

            # Normalize params to dict - Moonraker can send different formats
            params = {}
            if isinstance(raw_params, dict):
                params = raw_params
            elif isinstance(raw_params, list) and len(raw_params) > 0:
                # Sometimes params is a list with one dict
                if isinstance(raw_params[0], dict):
                    params = raw_params[0]
                else:
                    logger.debug(f"Unexpected list params format: {raw_params}")
            else:
                logger.debug(f"Unexpected params type: {type(raw_params)}, value: {raw_params}")

            if method == "notify_status_update":
                self._handle_status_update(params)
            elif method == "notify_history_changed":
                self._handle_history_update(params)
            elif method == "notify_filelist_changed":
                self._handle_filelist_changed(params)
            elif method == "notify_gcode_response":
                self._handle_gcode_response(params)

    def _subscribe(self):
        objects = {
            "print_stats": ["state", "filename", "total_duration", "print_duration", "filament_used", "info", "message"],
            "display_status": ["progress", "display_position"],
            "toolhead": ["position"],
            "extruder": ["temperature", "target"],
            "heater_bed": ["temperature", "target"],
            "fan": ["speed"],
            "gcode_move": ["speed_factor"],
            "virtual_sdcard": ["progress", "is_active"],
        }
        self.send_request("printer.objects.subscribe", {"objects": objects})

    def _handle_status_update(self, params):
        # Handle different message formats from Moonraker
        if not isinstance(params, dict):
            logger.debug(f"Unexpected params type in status_update: {type(params)}")
            return

        # Try to get status from params - Moonraker sends it in different ways
        status = None
        if "status" in params:
            status = params.get("status")
        elif "heater_bed" in params or "extruder" in params or "print_stats" in params:
            # Sometimes params IS the status object directly
            status = params
        else:
            logger.debug(f"No status found in params: {params}")
            return

        if not isinstance(status, dict):
            logger.debug(f"Status is not a dict: {type(status)}, value: {status}")
            return

        for key, value in status.items():
            if key in self._status_cache:
                self._status_cache[key].update(value)
            else:
                self._status_cache[key] = value

        for cb in self._callbacks["status_update"]:
            try:
                cb(self._status_cache)
            except Exception as e:
                logger.error(f"Status callback error: {e}")

    def _handle_history_update(self, params):
        if not isinstance(params, dict):
            logger.debug(f"Unexpected params type in history_update: {type(params)}")
            return
        for cb in self._callbacks["history_update"]:
            try:
                cb(params)
            except Exception as e:
                logger.error(f"History callback error: {e}")

    def _handle_filelist_changed(self, params):
        if not isinstance(params, dict):
            logger.debug(f"Unexpected params type in filelist_changed: {type(params)}")
            return
        for cb in self._callbacks["filelist_changed"]:
            try:
                cb(params)
            except Exception as e:
                logger.error(f"Filelist callback error: {e}")

    def _handle_gcode_response(self, params):
        if not isinstance(params, dict):
            logger.debug(f"Unexpected params type in gcode_response: {type(params)}")
            return
        for cb in self._callbacks["gcode_response"]:
            try:
                cb(params)
            except Exception as e:
                logger.error(f"Gcode response callback error: {e}")

    def send_request(self, method, params=None, callback=None):
        if not self._connected or not self._ws:
            logger.warning(f"Cannot send request {method}: not connected")
            return None

        req_id = self._request_id
        self._request_id += 1

        msg = {"jsonrpc": "2.0", "method": method, "id": req_id}
        if params:
            msg["params"] = params

        if callback:
            self._pending_requests[req_id] = callback

        self._ws.send(json.dumps(msg))
        return req_id

    def on_status_update(self, callback):
        self._callbacks["status_update"].append(callback)

    def on_gcode_response(self, callback):
        self._callbacks["gcode_response"].append(callback)

    def get_status(self):
        return dict(self._status_cache)

    def is_connected(self):
        return self._connected

    def _rest_get(self, endpoint):
        url = f"{self.base_url}{endpoint}"
        try:
            resp = requests.get(url, headers=self._headers, timeout=10)
            resp.raise_for_status()
            return resp.json().get("result")
        except Exception as e:
            logger.error(f"REST GET {endpoint} failed: {e}")
            return None

    def _rest_post(self, endpoint, data=None, files=None):
        url = f"{self.base_url}{endpoint}"
        try:
            if files:
                resp = requests.post(url, headers=self._headers, files=files, timeout=60)
            else:
                resp = requests.post(url, headers=self._headers, json=data, timeout=10)
            resp.raise_for_status()
            return resp.json().get("result")
        except Exception as e:
            logger.error(f"REST POST {endpoint} failed: {e}")
            return None

    def get_printer_info(self):
        return self._rest_get("/printer/info")

    def get_temperature_store(self):
        return self._rest_get("/server/temperature_store")

    def get_file_list(self):
        return self._rest_get("/server/files/list")

    def get_file_metadata(self, filename):
        return self._rest_get(f"/server/files/metadata?filename={filename}")

    def get_job_queue(self):
        return self._rest_get("/server/job_queue/status")

    def download_file(self, filename):
        url = f"{self.base_url}/server/files/{filename}"
        try:
            resp = requests.get(url, headers=self._headers, stream=True, timeout=30)
            resp.raise_for_status()
            return resp
        except Exception as e:
            logger.error(f"Download file {filename} failed: {e}")
            return None

    def upload_file(self, file_path, filename):
        with open(file_path, "rb") as f:
            files = {"file": (filename, f, "application/octet-stream")}
            return self._rest_post("/server/files/upload", files=files)

    def delete_file(self, filename):
        return self._rest_post(f"/server/files/{filename}", data={"action": "delete"})

    def start_print(self, filename):
        return self._rest_post("/printer/print/start", {"filename": filename})

    def pause_print(self):
        return self._rest_post("/printer/print/pause")

    def resume_print(self):
        return self._rest_post("/printer/print/resume")

    def cancel_print(self):
        return self._rest_post("/printer/print/cancel")

    def send_gcode(self, script):
        return self._rest_post("/printer/gcode/script", {"script": script})

    def emergency_stop(self):
        return self._rest_post("/printer/emergency_stop")

    def restart_klipper(self):
        return self._rest_post("/printer/restart")

    def restart_firmware(self):
        return self._rest_post("/printer/firmware_restart")

    def get_machine_system_info(self):
        return self._rest_get("/machine/system_info")

    def get_machine_ip(self):
        return self._rest_get("/machine/device_power/devices")
