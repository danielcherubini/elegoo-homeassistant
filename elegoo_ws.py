import logging
import websockets
import asyncio

_LOGGER = logging.getLogger(__name__)
printer_ip = "10.0.0.212"

# --- WebSocket Connection and Communication ---


async def send_command_and_get_response(command):
    try:
        # Replace with your WebSocket URL
        async with websockets.connect(f"ws://{printer_ip}:3030/websocket") as websocket:
            await websocket.send(command)
            _LOGGER.debug(f"Sent command: {command}")

            response = await websocket.recv()
            _LOGGER.debug(f"Received response: {response}")

            return response
    except (websockets.exceptions.ConnectionClosedError, ConnectionRefusedError) as e:
        _LOGGER.error(f"WebSocket error: {e}")
        return None


async def get_printer_data():
    """Fetch data from your Elegoo printer using WebSockets."""
    # Example: Sending a command to get printer status and parsing the response
    response = await send_command_and_get_response("get_status")
    if response:
        # Parse the response (replace with your actual parsing logic)
        # Example: Assuming the response is a JSON string
        import json
        try:
            data = json.loads(response)
            return {
                "uv_temperature": data.get("uv_temp"),
                "total_time": data.get("total_time"),  # in milliseconds
                "time_spent": data.get("time_spent"),  # in milliseconds
                # in milliseconds
                "time_remaining": data.get("time_remaining"),
                "filename": data.get("filename"),
                "current_layer": data.get("current_layer"),
                "total_layers": data.get("total_layers"),
                "remaining_layers": data.get("remaining_layers"),
            }
        except json.JSONDecodeError as e:
            _LOGGER.error(f"Error decoding JSON response: {e}")
            return None
    else:
        _LOGGER.warning("No response received from the printer.")
        return None


def main():
    """This is your main function."""
    asyncio.run(get_printer_data())  # Run the async function


if __name__ == "__main__":
    main()
