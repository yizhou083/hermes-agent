# Plan: Stream heartbeat for cron / gateway inactivity (sparse chunks)

**Status**: Implemented (see PR to `hermes-agent`).

## Problem

Long streaming responses can stall without SSE chunks for extended periods (buffering, slow prefill). Inactivity watchdogs use `AIAgent.get_activity_summary()`; without chunk-driven `_touch_activity` updates, healthy runs may be killed.

## Approach

- Add `_stream_heartbeat_scope()` on `AIAgent`: background thread calls `_touch_activity()` on a fixed interval while a stream iterator is active.
- Config via `HERMES_STREAM_HEARTBEAT_INTERVAL` (default 30s, `0` off) and optional `cron.*` / `agent.stream_heartbeat_interval` in `config.yaml` bridged to env in `cron/scheduler.py` and `gateway/run.py`.
- Wrap chat completions, Anthropic, Codex Responses (and fallback), and Bedrock converse wait paths.

## Verification

```bash
pytest tests/run_agent/test_stream_heartbeat.py tests/cron/test_cron_inactivity_timeout.py -q -o addopts=
```

## Related upstream

- [#11691](https://github.com/NousResearch/hermes-agent/pull/11691) (cron reconnect backoff) is orthogonal to this idle-stream issue.
- Issue [#8760](https://github.com/NousResearch/hermes-agent/issues/8760) class: sparse stream vs inactivity timeout.
