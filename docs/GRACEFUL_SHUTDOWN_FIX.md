# Graceful Shutdown Fix for TTS Worker

## Problem

The TTS worker was not responding to `Ctrl+C` (SIGINT) and hanging indefinitely on shutdown. After pressing `Ctrl+C`, the signal was logged but the worker never terminated:

```
^C15:01:00 [INFO    ] 
SIGINT received, initiating graceful shutdown...
```

## Root Cause

The issue was in the RabbitMQ message consumption loop. The worker was blocked in `pika.BlockingConnection.channel.start_consuming()`, which is a **synchronous blocking call** that waits indefinitely for messages.

Even though the signal handler set `_shutdown_requested = True`, the main loop couldn't reach the shutdown logic because it was stuck in `start_consuming()`. The only way to unblock `start_consuming()` is to call `channel.stop_consuming()` from another execution context (like a signal handler).

### Flow Before Fix

```
Signal Handler (SIGINT)
  ↓
Set _shutdown_requested = True
  ↓
(Cannot exit because main thread is blocked in start_consuming())
  ↓
Worker hangs indefinitely
```

## Solution

### 1. Enhanced Signal Handler

Modified `_setup_signal_handlers()` to immediately stop consuming when a signal is received:

```python
def signal_handler(signum, frame):
    """Handle shutdown signals."""
    signal_name = signal.Signals(signum).name
    logger.info(f"\n{signal_name} received, initiating graceful shutdown...")
    self._shutdown_requested = True
    self.rabbitmq_manager.request_shutdown()
    
    # ✅ Immediately stop consuming to unblock start_consuming()
    if (
        self.rabbitmq_manager.channel 
        and not self.rabbitmq_manager.channel.is_closed
    ):
        try:
            self.rabbitmq_manager.channel.stop_consuming()
            logger.info("Message consumption stopped")
        except Exception as e:
            logger.warning_icon(f"Error stopping consumption: {e}")
```

**Key change**: Call `channel.stop_consuming()` to break out of the blocking `start_consuming()` call.

### 2. RabbitMQ Manager: KeyboardInterrupt Handling

Added exception handling in `consume_messages()` to gracefully stop consuming:

```python
try:
    self.channel.start_consuming()
except KeyboardInterrupt:
    # Gracefully stop consuming when interrupted
    logger.info("Stopping message consumption...")
    self.channel.stop_consuming()
    raise
```

### 3. Main Loop: Enhanced Exception Handling

Updated the main loop to explicitly stop consuming on KeyboardInterrupt:

```python
except KeyboardInterrupt:
    logger.info("\nShutting down worker (KeyboardInterrupt)...")
    self._shutdown_requested = True
    # ✅ Stop consuming to unblock start_consuming()
    if self.rabbitmq_manager.channel:
        self.rabbitmq_manager.channel.stop_consuming()
    break
```

## How It Works Now

### Flow After Fix

```
User presses Ctrl+C
  ↓
Signal Handler (SIGINT)
  ↓
1. Set _shutdown_requested = True
2. Call channel.stop_consuming() ← This unblocks start_consuming()
  ↓
Main thread breaks out of start_consuming()
  ↓
Checks while condition (now false)
  ↓
Graceful shutdown cleanup
  ↓
Worker terminates cleanly
```

### Shutdown Sequence

1. **Signal Received**: User presses `Ctrl+C` → `SIGINT` or `SIGTERM` signal
2. **Signal Handler Executes**: 
   - Logs signal received
   - Sets `_shutdown_requested = True`
   - Calls `channel.stop_consuming()` to break blocking call
3. **Main Loop Unblocks**: `start_consuming()` returns
4. **While Loop Exits**: `while not self._shutdown_requested` condition is now false
5. **Cleanup**: 
   - Disconnect from RabbitMQ
   - Log shutdown summary
   - Exit cleanly

## Testing

Test graceful shutdown with:

```bash
# Start the worker
uv run python services/tts_worker.py

# In another terminal or after a moment:
# Press Ctrl+C in the worker terminal

# Expected output:
# ✓ Prompt returns immediately (no hanging)
# ✓ Shutdown logs appear
# ✓ Proper cleanup (circuit breaker stats, etc.)
```

## Files Modified

1. **`services/tts_worker.py`**
   - Enhanced `_setup_signal_handlers()` to call `channel.stop_consuming()`
   - Updated main loop exception handling

2. **`services/rabbitmq_manager.py`**
   - Added KeyboardInterrupt handling in `consume_messages()`

## Behavior Changes

- **Before**: `Ctrl+C` logged but hung indefinitely
- **After**: `Ctrl+C` triggers immediate graceful shutdown
- **No breaking changes**: All existing functionality preserved
- **Backwards compatible**: Existing error handling and retry logic unchanged
