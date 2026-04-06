#!/usr/bin/env python3
"""
Moonraker-CrealityCloud Bridge
Connects Klipper (via Moonraker) to Creality Cloud for remote monitoring and control.
"""

import argparse
import logging
import os
import signal
import sys
import time

from bridge import MoonrakerCrealityBridge

VERSION = "1.0.0"

logger = logging.getLogger(__name__)


def setup_logging(verbose=False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main():
    parser = argparse.ArgumentParser(
        description="Moonraker-CrealityCloud Bridge - Use Creality Cloud app with Klipper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # First-time setup with token from Creality Cloud app
  python main.py --token YOUR_JWT_TOKEN

  # Run with existing configuration
  python main.py

  # Custom Moonraker URL and region
  python main.py --moonraker http://192.168.1.100:7125 --region 0

  # With Moonraker API key
  python main.py --moonraker-api-key YOUR_API_KEY
        """,
    )

    parser.add_argument(
        "--token",
        type=str,
        help="JWT token from Creality Cloud app (.tk file content)",
    )
    parser.add_argument(
        "--moonraker",
        type=str,
        default=None,
        help="Moonraker URL (default: http://localhost:7125)",
    )
    parser.add_argument(
        "--moonraker-api-key",
        type=str,
        default=None,
        help="Moonraker API key (if required)",
    )
    parser.add_argument(
        "--region",
        type=int,
        choices=[0, 1],
        default=None,
        help="Region: 0=China, 1=Overseas (default: 1)",
    )
    parser.add_argument(
        "--config-dir",
        type=str,
        default=None,
        help="Directory for config files (default: script directory)",
    )
    parser.add_argument(
        "--video-port",
        type=int,
        default=8080,
        help="Video server port (default: 8080)",
    )
    parser.add_argument(
        "--camera-device",
        type=str,
        default="/dev/video0",
        help="Camera device path (default: /dev/video0)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose/debug logging",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"moonraker-crealitycloud-bridge {VERSION}",
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    logger.info(f"Moonraker-CrealityCloud Bridge v{VERSION}")

    config_dir = args.config_dir or os.path.dirname(os.path.abspath(__file__))
    bridge = MoonrakerCrealityBridge(config_dir=config_dir)

    if args.moonraker:
        bridge.config.set("moonraker_url", args.moonraker)
    if args.moonraker_api_key:
        bridge.config.set("moonraker_api_key", args.moonraker_api_key)
    if args.region is not None:
        bridge.config.set("region", args.region)
    if args.video_port:
        bridge.config.set("video_port", args.video_port)
    if args.camera_device:
        bridge.config.set("camera_device", args.camera_device)

    if args.token:
        logger.info("Setting up device with token...")
        if not bridge.setup_token(args.token):
            logger.error("Failed to set up device. Check your token and try again.")
            sys.exit(1)
        logger.info("Device setup complete. Starting bridge...")

    if not bridge.config.is_configured():
        logger.error("Bridge not configured!")
        logger.info("Run with --token <JWT_TOKEN> to set up.")
        logger.info("Get the token from the Creality Cloud app by creating a Raspberry Pi device.")
        sys.exit(1)

    cfg = bridge.config.data()
    logger.info(f"Device: {cfg['deviceName']}")
    logger.info(f"Region: {'China' if cfg.get('region', 1) == 0 else 'Overseas'}")
    logger.info(f"Moonraker: {cfg.get('moonraker_url', 'http://localhost:7125')}")

    if not bridge.connect():
        logger.error("Failed to connect to Creality Cloud")
        sys.exit(1)

    def shutdown(signum, frame):
        logger.info("Shutting down...")
        bridge.disconnect()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    logger.info("Bridge is running. Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        bridge.disconnect()


if __name__ == "__main__":
    main()
