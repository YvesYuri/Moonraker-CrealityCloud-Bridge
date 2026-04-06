import logging
import os
import subprocess
import threading
import time
from io import BytesIO

from flask import Response, stream_with_context

logger = logging.getLogger(__name__)


class VideoServer:
    def __init__(self, host="0.0.0.0", port=8080):
        self.host = host
        self.port = port
        self._running = False
        self._thread = None
        self._flask_app = None
        self._process = None
        self._camera_available = False
        self._check_camera()

    def _check_camera(self):
        self._camera_available = os.path.exists("/dev/video0")
        if self._camera_available:
            logger.info("Camera detected at /dev/video0")
        else:
            logger.warning("No camera found at /dev/video0")

    def is_camera_available(self):
        return self._camera_available

    def start(self):
        if not self._camera_available:
            logger.warning("Cannot start video server: no camera available")
            return False

        if self._running:
            logger.warning("Video server already running")
            return True

        try:
            from flask import Flask, render_template_string, request

            app = Flask(__name__)

            @app.route("/")
            def index():
                return """<!DOCTYPE html>
<html>
<head><title>Moonraker-CrealityCloud Bridge - Video</title></head>
<body>
<h1>Moonraker-CrealityCloud Bridge Video Stream</h1>
<img src="/live" style="width:100%;max-width:640px;">
</body>
</html>"""

            @app.route("/live")
            def live():
                def generate():
                    try:
                        ffmpeg_cmd = [
                            "ffmpeg",
                            "-i", "/dev/video0",
                            "-vcodec", "libx264",
                            "-tune", "zerolatency",
                            "-preset", "ultrafast",
                            "-b:v", "1000k",
                            "-b:a", "64k",
                            "-f", "flv",
                            "-",
                        ]
                        process = subprocess.Popen(
                            ffmpeg_cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                        )
                        self._process = process

                        while self._running:
                            chunk = process.stdout.read(4096)
                            if not chunk:
                                break
                            yield chunk
                    except Exception as e:
                        logger.error(f"Stream error: {e}")
                    finally:
                        if process:
                            process.terminate()
                            process.wait()

                return Response(
                    stream_with_context(generate()),
                    mimetype="video/x-flv",
                )

            @app.route("/mjpeg")
            def mjpeg():
                def generate():
                    try:
                        ffmpeg_cmd = [
                            "ffmpeg",
                            "-i", "/dev/video0",
                            "-vcodec", "mjpeg",
                            "-q:v", "5",
                            "-f", "jpeg",
                            "-",
                        ]
                        process = subprocess.Popen(
                            ffmpeg_cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                        )

                        header = (
                            b"--FRAME\r\n"
                            b"Content-Type: image/jpeg\r\n"
                            b"Content-Length: %s\r\n\r\n"
                        )

                        while self._running:
                            chunk = process.stdout.read(2)
                            if not chunk:
                                break
                            try:
                                process.stdin.write(b"\n")
                            except:
                                pass
                            frame = process.stdout.read(4096)
                            if not frame:
                                break
                            yield (header % str(len(frame)).encode()) + frame + b"\r\n"
                    except Exception as e:
                        logger.error(f"MJPEG stream error: {e}")
                    finally:
                        if process:
                            process.terminate()
                            process.wait()

                return Response(
                    stream_with_context(generate()),
                    mimetype="multipart/x-mixed-replace; boundary=FRAME",
                )

            @app.route("/status")
            def status():
                return {"status": "running" if self._running else "stopped", "camera": self._camera_available}

            self._flask_app = app
            self._running = True

            def run_app():
                try:
                    app.run(host=self.host, port=self.port, threaded=True, debug=False)
                except Exception as e:
                    logger.error(f"Flask error: {e}")

            self._thread = threading.Thread(target=run_app, daemon=True)
            self._thread.start()

            time.sleep(2)
            logger.info(f"Video server started at http://{self.host}:{self.port}")
            return True

        except Exception as e:
            logger.error(f"Failed to start video server: {e}")
            return False

    def stop(self):
        self._running = False
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception as e:
                logger.error(f"Error stopping ffmpeg: {e}")
        logger.info("Video server stopped")

    def get_stream_url(self):
        if not self._camera_available:
            return None
        return f"http://localhost:{self.port}/live"
