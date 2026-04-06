import asyncio
import json
import logging
import os
import queue
import sys
import threading
import time

import requests
import websocket
from aiortc import RTCIceCandidate, RTCSessionDescription, RTCConfiguration, RTCIceServer
from aiortc import RTCPeerConnection

logger = logging.getLogger("crealitycloud.bridge")

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class SignalingClient:
    def __init__(self, url, token, message_queue, close_queue):
        self.url = url
        self.token = token
        self.message_queue = message_queue
        self.close_queue = close_queue
        self.ws = None
        self.running = False
        self.reconnect_count = 0

    def start(self):
        self.running = True
        self.ws_thread = threading.Thread(target=self._run, daemon=True)
        self.ws_thread.start()
        logger.info(f"Signaling client starting: {self.url}")

    def _run(self):
        while self.running:
            try:
                self.ws = websocket.WebSocketApp(
                    self.url,
                    on_message=self._on_message,
                    on_open=self._on_open,
                    on_close=self._on_close,
                    on_error=self._on_error,
                )
                self.ws.run_forever()
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
            
            if self.running:
                logger.info(f"Reconnecting in 5s... (attempt {self.reconnect_count})")
                time.sleep(5)
                self.reconnect_count += 1

    def _on_message(self, ws, msg):
        try:
            data = json.loads(msg)
            action = data.get("action")
            if action != "join":
                self.message_queue.put(msg)
        except Exception as e:
            logger.error(f"Error parsing message: {e}")

    def _on_open(self, ws):
        logger.info("WebSocket connected")
        self.reconnect_count = 0
        data = {
            "action": "join",
            "to": "server",
            "clientCtx": {
                "device_brand": "raspberry",
                "os_version": "linux",
                "platform_type": 1,
                "app_version": "v1.1.2"
            },
            "token": {
                "jwtToken": self.token
            }
        }
        ws.send(json.dumps(data))

    def _on_close(self, ws, status, msg):
        logger.info(f"WebSocket closed: {status} {msg}")

    def _on_error(self, ws, error):
        logger.error(f"WebSocket error: {error}")
        if isinstance(error, (ConnectionRefusedError, websocket._exceptions.WebSocketConnectionClosedException)):
            pass

    def send(self, data):
        if self.ws and self.ws.sock and self.ws.sock.connected:
            self.ws.send(json.dumps(data))

    def close(self):
        self.running = False
        if self.ws:
            self.ws.close()


class WebRTCManager:
    def __init__(self, device_name, token, region, camera_device="/dev/video0", verbose=False):
        self.device_name = device_name
        self.token = token
        self.region = region
        self.camera_device = camera_device
        self.verbose = verbose
        
        self.message_queue = queue.Queue()
        self.close_queue = queue.Queue()
        
        self.signaling = None
        self.pc = None
        self.running = False
        self.peer_id = None
        
        self.ice_urls = ""
        self.ice_username = ""
        self.ice_credential = ""
        
        self._fetch_ice_servers()

    def _fetch_ice_servers(self):
        try:
            if self.region == 0:
                url = "https://api.crealitycloud.cn/api/cxy/v2/webrtc/iceServersJwt"
            else:
                url = "https://api.crealitycloud.com/api/cxy/v2/webrtc/iceServersJwt"
            
            data = f'{{"deviceName": "{self.device_name}"}}'
            headers = {
                "Content-Type": "application/json",
                "__CXY_JWTOKEN_": self.token
            }
            
            response = requests.post(url, data=data, headers=headers, timeout=10).text
            result = json.loads(response)
            
            if "result" in result and result["result"]:
                ice_server = result["result"].get("iceServers", [{}])[0]
                if ice_server:
                    self.ice_urls = ice_server.get("urls", "")
                    self.ice_username = ice_server.get("username", "")
                    self.ice_credential = ice_server.get("credential", "")
                    logger.info(f"ICE servers fetched: {self.ice_urls}")
        except Exception as e:
            logger.error(f"Failed to fetch ICE servers: {e}")

    def _get_signaling_url(self):
        if self.region == 0:
            return "wss:// Signaling.crealitycloud.cn/ws"
        else:
            return "wss:// Signaling.crealitycloud.com/ws"

    async def start(self):
        logger.info("Starting WebRTC manager...")
        
        signaling_url = self._get_signaling_url()
        self.signaling = SignalingClient(signaling_url, self.token, self.message_queue, self.close_queue)
        self.signaling.start()
        
        self.running = True
        self._webrtc_loop()

    def _webrtc_loop(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            while self.running:
                try:
                    message = self.message_queue.get(timeout=1)
                    loop.run_until_complete(self._handle_message(message))
                except queue.Empty:
                    continue
                except Exception as e:
                    logger.error(f"Error in WebRTC loop: {e}")
        finally:
            loop.close()

    async def _handle_message(self, message):
        try:
            data = json.loads(message)
            action = data.get("action")
            
            if action == "ice_msg":
                sdp_message = data.get("sdpMessage", {})
                msg_type = sdp_message.get("type")
                
                if msg_type == "offer":
                    await self._handle_offer(data)
                elif msg_type == "candidate":
                    await self._handle_candidate(data)
                    
        except Exception as e:
            logger.error(f"Error handling message: {e}")

    async def _handle_offer(self, data):
        peer_id = data.get("from")
        sdp_message = data.get("sdpMessage", {})
        sdp_data = sdp_message.get("data", {})
        
        ice_servers = data.get("iceServers", [])
        if ice_servers:
            self.ice_urls = ice_servers[0].get("urls", "")
            self.ice_username = ice_servers[0].get("username", "")
            self.ice_credential = ice_servers[0].get("credential", "")
        
        logger.info(f"Received offer from {peer_id}")
        
        ice_server = RTCIceServer(
            urls=self.ice_urls,
            username=self.ice_username if self.ice_username else None,
            credential=self.ice_credential if self.ice_credential else None,
        )
        
        self.pc = RTCPeerConnection(configuration=RTCConfiguration([ice_server]))
        
        from media_handlers import MediaPlayer
        options = {"video_size": "640x480", "rtbufsize": "160M"}
        self.webcam = MediaPlayer(self.camera_device, format="v4l2", options=options)
        
        @self.pc.on("iceconnectionstatechange")
        def on_ice_connection_state_change():
            logger.info(f"ICE connection state: {self.pc.iceConnectionState}")
        
        @self.pc.on("connectionstatechange")
        def on_connection_state_change():
            logger.info(f"Connection state: {self.pc.connectionState}")
        
        if self.webcam and self.webcam.video:
            self.pc.addTrack(self.webcam.video)
        
        rtc_description = RTCSessionDescription(sdp=sdp_data.get("sdp"), type=sdp_data.get("type"))
        await self.pc.setRemoteDescription(rtc_description)
        
        answer = await self.pc.createAnswer()
        await self.pc.setLocalDescription(answer)
        
        response_data = {
            "action": "ice_msg",
            "sdpMessage": {
                "data": {
                    "sdp": self.pc.localDescription.sdp,
                    "type": self.pc.localDescription.type
                },
                "type": "answer"
            },
            "to": peer_id
        }
        
        self.signaling.send(response_data)
        
        sdp_list = self.pc.localDescription.sdp.split("\r\n")
        for line in sdp_list:
            if "candidate" in line:
                candidate_data = {
                    "action": "ice_msg",
                    "sdpMessage": {
                        "type": "candidate",
                        "data": line[2:]
                    },
                    "to": peer_id
                }
                self.signaling.send(candidate_data)

    async def _handle_candidate(self, data):
        if not self.pc:
            return
            
        candidate_data = data.get("sdpMessage", {}).get("data", {})
        if not candidate_data:
            return
            
        try:
            parts = candidate_data.split()
            if len(parts) >= 8:
                candidate = RTCIceCandidate(
                    component=int(parts[1]),
                    foundation=parts[0].replace("candidate:", ""),
                    ip=parts[4],
                    port=int(parts[5]),
                    priority=int(parts[3]),
                    protocol=parts[2],
                    type=parts[7],
                    sdpMid="0",
                    sdpMLineIndex=0,
                )
                await self.pc.addIceCandidate(candidate)
        except Exception as e:
            logger.error(f"Error adding ICE candidate: {e}")

    def stop(self):
        logger.info("Stopping WebRTC manager...")
        self.running = False
        
        if self.pc:
            try:
                self.pc.close()
            except Exception:
                pass
        
        if self.signaling:
            self.signaling.close()
        
        logger.info("WebRTC manager stopped")
