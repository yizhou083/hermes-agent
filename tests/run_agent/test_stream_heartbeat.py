"""Tests for streaming inactivity heartbeat (sparse SSE / long prefill)."""

import time

import pytest


class TestStreamHeartbeatScope:
    def test_scope_noop_when_interval_zero(self):
        from run_agent import AIAgent

        agent = AIAgent(
            base_url="http://127.0.0.1:9/v1",
            api_key="sk-test",
            model="gpt-4o-mini",
            quiet_mode=True,
            stream_heartbeat_interval=0.0,
            skip_context_files=True,
            skip_memory=True,
        )
        with agent._stream_heartbeat_scope():
            time.sleep(0.05)
        assert agent._stream_heartbeat_interval == 0.0

    def test_scope_refreshes_activity_while_blocked(self):
        from run_agent import AIAgent

        agent = AIAgent(
            base_url="http://127.0.0.1:9/v1",
            api_key="sk-test",
            model="gpt-4o-mini",
            quiet_mode=True,
            stream_heartbeat_interval=0.12,
            skip_context_files=True,
            skip_memory=True,
        )
        agent._touch_activity("seed")
        with agent._stream_heartbeat_scope():
            time.sleep(0.38)
        summary = agent.get_activity_summary()
        assert summary["seconds_since_activity"] < 0.25
        assert "heartbeat" in (summary.get("last_activity_desc") or "").lower()
