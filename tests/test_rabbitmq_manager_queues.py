"""
Unit tests for RabbitMQManager queue declaration and DLX setup.
Verifies that tts_jobs, tts_results, vox_jobs, and vox_results are declared correctly.
"""

from unittest.mock import MagicMock
from services.rabbitmq_manager import RabbitMQManager


def test_setup_dlx_queues_declares_vox_results():
    """Verify that _setup_dlx_queues declares vox_results with appropriate DLX and DLQ."""
    manager = RabbitMQManager(rabbitmq_url="amqp://guest:guest@localhost:5672/")
    mock_channel = MagicMock()
    manager.channel = mock_channel

    manager._setup_dlx_queues()

    # Collect all declared exchanges
    exchanges = [
        call.kwargs.get("exchange") or call.args[0]
        if call.args
        else call.kwargs.get("exchange")
        for call in mock_channel.exchange_declare.call_args_list
    ]
    assert "tts_jobs.dlx" in exchanges
    assert "tts_results.dlx" in exchanges
    assert "vox_jobs.dlx" in exchanges
    assert "vox_results.dlx" in exchanges

    # Collect all declared queues and their arguments
    declared_queues = {}
    for call in mock_channel.queue_declare.call_args_list:
        q_name = call.kwargs.get("queue")
        if not q_name and call.args:
            q_name = call.args[0]
        q_args = call.kwargs.get("arguments", {})
        declared_queues[q_name] = q_args

    # Check that vox_results and vox_jobs are among the declared queues
    assert "tts_jobs" in declared_queues
    assert "tts_results" in declared_queues
    assert "vox_jobs" in declared_queues
    assert "vox_results" in declared_queues
    assert "vox_jobs_failed" in declared_queues
    assert "vox_results_failed" in declared_queues

    # Check vox_results configuration matches DLX standard
    vox_results_args = declared_queues["vox_results"]
    assert vox_results_args["x-dead-letter-exchange"] == "vox_results.dlx"
    assert vox_results_args["x-dead-letter-routing-key"] == "vox_results_failed"
    assert vox_results_args["x-message-ttl"] == 604800000

    # Check queue bindings
    bound_queues = [
        (call.kwargs.get("queue"), call.kwargs.get("exchange"))
        for call in mock_channel.queue_bind.call_args_list
    ]
    assert ("vox_results_failed", "vox_results.dlx") in bound_queues
    assert ("vox_jobs_failed", "vox_jobs.dlx") in bound_queues
