# Network Resilience - Automatic Reconnection

## Problem

When the network changes (e.g., switching WiFi networks, network interruption, VPN reconnection), the RabbitMQ worker would crash with:

```
ConnectionResetError: [WinError 10054] 远程主机强迫关闭了一个现有的连接。
```

The worker would then exit completely, requiring manual restart.

## Solution

The TTS worker now implements automatic reconnection with the following features:

### 1. Connection Health Monitoring

- `_is_connection_open()`: Continuously checks if RabbitMQ connection and channel are healthy
- Detects connection drops before attempting operations

### 2. Automatic Reconnection with Exponential Backoff

- `_reconnect_with_backoff()`: Automatically attempts to reconnect when connection is lost
- **Initial delay**: 5 seconds
- **Maximum delay**: 300 seconds (5 minutes)
- **Backoff strategy**: Exponential (5s → 10s → 20s → 40s → 80s → 160s → 300s)
- Resets delay to 5s after successful reconnection

### 3. Connection Error Handling

The worker now catches and handles these specific exceptions:
- `pika.exceptions.ConnectionClosedByBroker`
- `pika.exceptions.AMQPConnectionError`
- `pika.exceptions.StreamLostError`

### 4. Main Consumption Loop

The `start()` method now uses a while loop that:
1. Checks connection health before consuming
2. Automatically reconnects if connection is lost
3. Resumes message processing after reconnection
4. Only exits on explicit shutdown request

### 5. Result Publishing Resilience

The `publish_result()` method now:
- Checks connection health before publishing
- Attempts reconnection if connection is closed
- Handles connection errors separately from other errors
- Maintains partial failure recovery for completed jobs

## Behavior

### Normal Operation
```
✓ Connected to RabbitMQ
Worker ready and listening for jobs...
```

### Network Change Detected
```
ERROR: Connection lost: StreamLostError(...)
WARNING: Attempting to reconnect to RabbitMQ (attempt 1, waiting 5s)...
✓ Successfully reconnected to RabbitMQ after 1 attempts
INFO: Starting message consumption...
Worker ready and listening for jobs...
```

### Multiple Reconnection Attempts
```
ERROR: Connection lost: ConnectionResetError(...)
WARNING: Attempting to reconnect (attempt 1, waiting 5s)...
ERROR: Reconnection attempt 1 failed: [Error details]
WARNING: Attempting to reconnect (attempt 2, waiting 10s)...
ERROR: Reconnection attempt 2 failed: [Error details]
WARNING: Attempting to reconnect (attempt 3, waiting 20s)...
✓ Successfully reconnected to RabbitMQ after 3 attempts
```

### Graceful Shutdown
```
^C
SIGINT received, initiating graceful shutdown...
Stopped consuming new messages
Worker stopped successfully
```

## Configuration

No configuration changes required. The reconnection behavior is automatic.

### Customizable Parameters

If you need to adjust reconnection behavior, modify these instance variables in `__init__`:

```python
self._reconnect_delay = 5          # Initial delay (seconds)
self._max_reconnect_delay = 300    # Maximum delay (seconds)
```

## Benefits

1. **Zero Downtime**: Worker continues running through network changes
2. **No Manual Intervention**: Automatically recovers from connection issues
3. **Message Safety**: Messages are not lost during reconnection
4. **Resource Efficient**: Exponential backoff prevents excessive reconnection attempts
5. **Production Ready**: Handles both temporary and long-term network issues

## Testing

To test the reconnection feature:

1. Start the worker: `python -m services.tts_worker`
2. Wait for "Worker ready and listening for jobs..."
3. Change your network (switch WiFi, toggle VPN, etc.)
4. Observe automatic reconnection in logs
5. Worker continues processing jobs after reconnection

## Limitations

- Messages being processed during disconnection may be requeued (depends on timing)
- Reconnection attempts continue indefinitely until successful or manual shutdown
- If RabbitMQ server is permanently down, worker will keep retrying (this is intentional for resilience)

## Related Files

- `services/tts_worker.py`: Main worker implementation
- `services/circuit_breaker.py`: Circuit breaker for failure detection
- `services/idempotent_upload.py`: Prevents duplicate uploads during retries
