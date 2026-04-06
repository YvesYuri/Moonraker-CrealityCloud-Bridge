import json
import os
import logging

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "deviceName": None,
    "deviceSecret": None,
    "iotType": 2,
    "region": 1,
    "moonraker_url": "http://localhost:7125",
    "moonraker_api_key": None,
}

CONFIG_FILENAME = "config.json"
P2P_CONFIG_FILENAME = "p2pcfg.json"


class BridgeConfig:
    def __init__(self, config_dir=None):
        if config_dir is None:
            config_dir = os.path.dirname(os.path.abspath(__file__))
        self.config_dir = config_dir
        self.config_path = os.path.join(config_dir, CONFIG_FILENAME)
        self.p2p_path = os.path.join(config_dir, P2P_CONFIG_FILENAME)
        self._data = dict(DEFAULT_CONFIG)
        self._p2p_data = {}
        self._load()

    def _load(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r") as f:
                    loaded = json.load(f)
                    self._data.update(loaded)
                    logger.info(f"Config loaded from {self.config_path}")
            except Exception as e:
                logger.error(f"Failed to load config: {e}")
        else:
            logger.info("No config file found, using defaults")

        if os.path.exists(self.p2p_path):
            try:
                with open(self.p2p_path, "r") as f:
                    self._p2p_data = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load p2p config: {e}")

    def save(self):
        try:
            with open(self.config_path, "w") as f:
                json.dump(self._data, f, indent=2)
            logger.info(f"Config saved to {self.config_path}")
        except Exception as e:
            logger.error(f"Failed to save config: {e}")

    def save_p2p(self):
        try:
            with open(self.p2p_path, "w") as f:
                json.dump(self._p2p_data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save p2p config: {e}")

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value
        self.save()

    def data(self):
        return dict(self._data)

    def p2p_get(self, key, default=None):
        return self._p2p_data.get(key, default)

    def p2p_set(self, key, value):
        self._p2p_data[key] = value
        self.save_p2p()

    def is_configured(self):
        return (
            self._data.get("deviceName") is not None
            and self._data.get("deviceSecret") is not None
        )
