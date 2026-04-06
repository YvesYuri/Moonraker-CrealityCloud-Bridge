import json
import logging
import time

from tb_device_mqtt import TBDeviceMqttClient

logger = logging.getLogger(__name__)


class CrealityMQTT:
    def __init__(self, device_name, token, region=1):
        self.device_name = device_name
        self.token = token
        self.region = region
        self._host = "mqtt.crealitycloud.cn" if region == 0 else "mqtt.crealitycloud.com"
        self._client = None
        self._connected = False
        self._on_rpc_handler = None

        self.telemetry = {
            "nozzleTemp": 0,
            "bedTemp": 0,
            "curFeedratePct": 0,
            "dProgress": 0,
            "printProgress": 0,
            "printJobTime": 0,
            "printLeftTime": 0,
        }

        self.attributes = {
            "printStartTime": " ",
            "layer": 0,
            "printedTimes": 0,
            "timesLeftToPrint": 0,
            "err": 0,
            "curPosition": " ",
            "printId": " ",
            "filename": " ",
            "video": 0,
            "netIP": " ",
            "state": 0,
            "tfCard": 0,
            "model": " ",
            "mcu_is_print": 0,
            "boxVersion": "moonraker_bridge_v1.0.0",
            "stop": 0,
            "print": " ",
            "nozzleTemp2": 0,
            "bedTemp2": 0,
            "pause": 0,
            "fan": 0,
            "autohome": 0,
            "opGcodeFile": " ",
            "gcodeCmd": " ",
            "setPosition": " ",
            "tag": "1.0.0",
            "led_state": 0,
            "retGcodeFileInfo": " ",
            "InitString": " ",
            "APILicense": " ",
            "DIDString": " ",
        }

    def connect(self):
        try:
            self._client = TBDeviceMqttClient(self._host, self.token)
            self._client.set_server_side_rpc_request_handler(self._on_rpc)
            self._client.connect(timeout=90, keepalive=30)
            self._connected = True
            logger.info(f"Connected to ThingsBoard MQTT at {self._host}")
            self._send_init_shadow()
            return True
        except Exception as e:
            logger.error(f"Failed to connect to ThingsBoard: {e}")
            return False

    def disconnect(self):
        if self._client:
            try:
                self._client.disconnect()
            except Exception as e:
                logger.error(f"Error disconnecting: {e}")
        self._connected = False

    def is_connected(self):
        if self._client:
            return self._client.is_connected
        return False

    def _send_init_shadow(self):
        self.send_telemetry(self.telemetry)
        self.send_attributes(self.attributes)

    def send_telemetry(self, payload):
        if not self._connected or not payload:
            return
        try:
            self._client.send_telemetry(payload)
        except Exception as e:
            logger.error(f"Failed to send telemetry: {e}")

    def send_attributes(self, payload):
        if not self._connected or not payload:
            return
        try:
            self._client.send_attributes(payload)
            logger.debug(f"Sent attributes: {payload}")
        except Exception as e:
            logger.error(f"Failed to send attributes: {e}")

    def reply_rpc(self, request_id, payload):
        if not self._connected or not self._client:
            return
        try:
            self._client.send_rpc_reply(request_id, json.dumps(payload))
            logger.debug(f"RPC reply: {payload}")
        except Exception as e:
            logger.error(f"Failed to send RPC reply: {e}")

    def set_rpc_handler(self, handler):
        self._on_rpc_handler = handler

    def _on_rpc(self, client, request_id, request_body):
        if self._on_rpc_handler:
            try:
                self._on_rpc_handler(client, request_id, request_body)
            except Exception as e:
                logger.error(f"RPC handler error: {e}")
                self.reply_rpc(request_id, {"code": -1, "error": str(e)})
