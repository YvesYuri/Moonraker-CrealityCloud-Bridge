import json
import logging
import random
import time
import uuid

import requests


class CrealityAPI:
    def __init__(self):
        self.__homeurl = "https://api.crealitycloud.cn"
        self.__overseaurl = "https://api.crealitycloud.com"
        self.__headers = {
            "__CXY_OS_VER_": "v0.0.1",
            "_CXY_OS_LANG_": "1",
            "__CXY_PLATFORM_": "5",
            "__CXY_DUID_": "234",
            "__CXY_APP_ID_": "creality_model",
            "__CXY_REQUESTID_": self._get_request_id(),
        }

    def _get_request_id(self):
        t = time.localtime(time.time())
        r = random.randint(10000, 99999)
        return f"Raspberry{t.tm_sec}10{r}"

    def getconfig(self, token):
        home_url = self.__homeurl + "/api/cxy/v2/device/user/importDevice"
        oversea_url = self.__overseaurl + "/api/cxy/v2/device/user/importDevice"
        headers = {
            "Content-Type": "application/json",
            "__CXY_JWTOKEN_": token,
        }
        mac = uuid.UUID(int=uuid.getnode()).hex[-12:].upper()
        data = json.dumps({"mac": mac, "iotType": 2})

        try:
            response = requests.post(home_url, data=data, headers=headers, timeout=5).text
            if "result" not in response:
                response = requests.post(oversea_url, data=data, headers=headers, timeout=5).text
            return json.loads(response)
        except Exception as e:
            return {"error": str(e)}

    def exchangeTb(self, deviceName, productKey, deviceSecret, region):
        homeurl = self.__homeurl + "/api/cxy/v2/device/user/exchangeTb"
        overseaurl = self.__overseaurl + "/api/cxy/v2/device/user/exchangeTb"
        data = json.dumps({
            "deviceName": str(deviceName),
            "productKey": str(productKey),
            "deviceSecret": str(deviceSecret),
        })
        headers = {"Content-Type": "application/json"}

        url = homeurl if region == 0 else overseaurl
        try:
            response = requests.post(url, data=data, headers=headers, timeout=5).text
            return json.loads(response)
        except Exception as e:
            return {"error": str(e)}
