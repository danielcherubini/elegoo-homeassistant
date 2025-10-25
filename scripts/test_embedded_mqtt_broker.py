"""Test script for the embedded MQTT broker.

This script starts the embedded MQTT broker and tests basic functionality.
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path to import from custom_components
sys.path.insert(0, str(Path(__file__).parent.parent))

from custom_components.elegoo_printer.mqtt.server import ElegooMQTTBroker


async def run_broker():
    """Run the embedded MQTT broker."""
    print("Starting embedded MQTT broker...")

    try:
        broker = await ElegooMQTTBroker.get_instance()
        print(f"✅ Broker started successfully on {broker.host}:{broker.port}")
        print(f"\nBroker is running. You can now:")
        print(f"  1. Run test_mqtt_printer.py and connect it to localhost:{broker.port}")
        print(f"  2. Use mosquitto_pub/sub to test:")
        print(f"     mosquitto_sub -h localhost -p {broker.port} -t 'test/#' -v")
        print(f"     mosquitto_pub -h localhost -p {broker.port} -t 'test/topic' -m 'Hello'")
        print(f"\nPress Ctrl+C to stop the broker")

        # Keep broker running
        await broker.serve_forever()

    except KeyboardInterrupt:
        print("\n\nStopping broker...")
        await ElegooMQTTBroker.release_instance()
        print("✅ Broker stopped")
    except Exception as e:
        print(f"❌ Error: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(run_broker())
