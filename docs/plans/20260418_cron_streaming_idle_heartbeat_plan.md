# Plan: Cron / Gateway 长流式 inactivity 误判与心跳加固 (#8760 方向)

**日期**: 2026-04-18  
**工位**: `Beta_31`（已通过 `git pull E:\MyPROJECT\NousPR\Official_Hermes_Mirror main` 同步）  
**雷达对齐**: `GitHub_Radar.md` 中 [#11691](https://github.com/NousResearch/hermes-agent/pull/11691) 为「重连退避保活」，与本题「长流式无 chunk 窗口下的 inactivity」正交；总账本索引未见 `cron/scheduler.py` 流式 idle 专项 WIP。

> 说明：上游仓库当前 **无** `src/cron/executor.py`；cron 侧 inactivity 轮询与 `AIAgent` 生命周期在 `cron/scheduler.py`；网关侧同类逻辑在 `gateway/run.py`；活动戳在 `run_agent.py` 的 `_touch_activity` / `get_activity_summary`。

---

## Phase 1 — 需求消化（结论）

- **现象**: 长 streaming 生成过程中，若客户端长时间未收到 delta（缓冲、稀疏 chunk、或 provider 侧慢吐字），`seconds_since_activity` 仍增长，cron 默认 `HERMES_CRON_TIMEOUT`（600s）与网关 `HERMES_AGENT_TIMEOUT`（1800s）可能先于「真实仍在生成」触发中断。
- **现状**: `run_agent.py` 在 `for chunk in stream:` 内对每个 chunk 调用 `_touch_activity("receiving stream response")`；**若 iterator 长时间阻塞且无 chunk**，活动戳不会更新。
- **目标**: 以「时间片心跳 +（可选）按 token 计数降频刷新」重置 inactivity；配置化 `idle_timeout` / `heartbeat_interval`；网关路径复用同一语义；可选可观测性（速率/最后 chunk 时间）。

---

## Phase 2 — 原子任务清单（文件级）

**[Step 1]** — 基线与多路径审计  
- **File**: `run_agent.py`  
- **Action**: Modify（仅阅读与标记，首 PR 以文档注释或 issue 链亦可；建议直接列出所有 `chat.completions.create(..., stream=True)` / 异步流式分支）  
- **Details**: 枚举每一处 streaming 消费循环；确认是否均能在「无 chunk 窗口」内更新活动（含 reasoning-only delta、tool-call 流、delegate 路径）。记录与 `last_chunk_time` 相关的已有 stale-stream 检测是否可复用。  
- **Verification**: 本地 `rg "stream=True" run_agent.py` 与人工 checklist 表（可附在本 plan 附录）。

**[Step 2]** — 引入可配置流式心跳（时间驱动为主，token 驱动为辅）  
- **File**: `run_agent.py`  
- **Action**: Modify  
- **Details**:  
  - 在活跃 stream 迭代期间，除 per-chunk `_touch_activity` 外，增加基于 `heartbeat_interval`（秒）的计时：若自上次 `_touch_activity` 起已超过间隔且 stream 仍 open，则 `_touch_activity("stream heartbeat (idle window)")` 或等价描述。  
  - 可选：维护自上次 touch 以来的累计 token 估算（已有 `content_parts` / delta 长度），每 **N** tokens 再 touch 一次，避免极高频 chunk 导致锁竞争（与「每 chunk 必 touch」二选一或降频）。  
  - 实现方式优先：`asyncio`/`threading` 与现有模型一致；不得阻塞 chunk 处理主路径过久。  
- **Verification**: 新增单元测试（见 Step 7）通过；长 sleep mock iterator 下 `seconds_since_activity` 不超过 `heartbeat_interval + ε`。

**[Step 3]** — Cron 侧配置与默认值对齐  
- **File**: `cron/scheduler.py`  
- **Action**: Modify  
- **Details**:  
  - 将当前单一 `HERMES_CRON_TIMEOUT` 与注释扩展为：从 `load_config()` 读取 `cron.idle_timeout`（秒），缺省回退 `HERMES_CRON_TIMEOUT`，再回退默认 600；`0` 表示无限 inactivity 监控（与现语义一致）。  
  - 将 `heartbeat_interval` 传入 `AIAgent` 或通过 `os.environ` / kwargs 仅当 `run_agent` 已支持构造参数时注入（若 `AIAgent.__init__` 无该参数，则用 `hermes_constants` 或模块级读取 config，避免循环导入）。  
- **Verification**: `tests/cron/test_cron_inactivity_timeout.py` 扩展用例覆盖 config 覆盖 env。

**[Step 4]** — 配置模型与示例  
- **File**: `gateway/config.py`（或 `hermes_cli/config` 中实际承载 `cron` 字典的 Pydantic/dataclass 定义处，以代码检索为准）  
- **Action**: Modify  
- **Details**: 增加可选段：  
  ```yaml
  cron:
    idle_timeout: 1800      # 秒；0 = 不限
    heartbeat_interval: 30  # 秒；流式无 chunk 时仍刷新活动
  ```  
  与 `cli-config.yaml.example` / `AGENTS.md` 中相关小节同步一句（英文）。  
- **Verification**: 配置加载单测或最小 `yaml` round-trip 不报错。

**[Step 5]** — 网关 approval / 会话路径复用  
- **File**: `gateway/run.py`  
- **Action**: Modify  
- **Details**: 网关 inactivity 轮询（约 9370–9480 行附近）与 cron 使用同一 `get_activity_summary()` 语义；确保 `agent.gateway_timeout` 与 cron 的 `idle_timeout` 文档不冲突。若引入 `AIAgent` 级 heartbeat，网关路径**无需重复实现**，仅验证 `agent_holder[0]` 在流式长任务中 activity 持续刷新。  
- **Verification**: `tests/gateway/test_gateway_inactivity_timeout.py` 中增加「长间隔无 chunk 仍不超时」的 mock（与 cron 测试共享 FakeAgent 或提取 helper）。

**[Step 6]** — 可观测性（prometheus-like，轻量）  
- **File**: `run_agent.py` 或 `hermes_logging.py`（择一，保持单职责）  
- **Action**: Modify  
- **Details**: 在 stream 循环内以 DEBUG/INFO 结构化日志记录：`stream_chunks_total`、自上次 chunk 的秒数、可选 `estimated_tokens_since_last_touch`；若项目已有统一 metrics 入口则接入，否则**不引入**新 heavy 依赖，仅用 `logger.info(..., extra={...})` 便于 Loki/Promtail。  
- **Verification**: 测试或 caplog 断言关键字段出现频率上限（避免日志风暴）。

**[Step 7]** — 测试矩阵  
- **File**: `tests/cron/test_cron_inactivity_timeout.py`、`tests/gateway/test_gateway_inactivity_timeout.py`；必要时新建 `tests/run_agent/test_stream_activity_heartbeat.py`（若 `run_agent.py` 单测过重，可抽 **<150 行** 的纯函数到 `agent/stream_activity.py` 再测，符合宪法模块化倾向）。  
- **Action**: Modify / Create  
- **Details**: Mock「每 120s 才 yield 一个 chunk」的 iterator，断言在总时长 < `idle_timeout` 内未触发 `TimeoutError`；且 heartbeat 关闭时对照组仍超时（回归）。  
- **Verification**:  
  `python "C:\Users\Administrator\.cursor\skills\test-sentinel\scripts\test_runner.py"`  
  或定向 `pytest tests/cron/test_cron_inactivity_timeout.py tests/gateway/test_gateway_inactivity_timeout.py -q -o addopts=`。

**[Step 8]** — 合规收尾（执行编码时，非本规划文档）  
- **File**: `E:\MyPROJECT\NousPR\Master_Ledger.md`  
- **Action**: Modify  
- **Details**: 在标题 `# 🏆 NousPR 幽灵阵列贡献总账本` **正下方、索引表之上** 插入免战牌：  
  `> 🚧 **WIP LOCK**: [git user.name] 正在强攻 \`cron/scheduler.py\` / \`run_agent.py\` / \`gateway/run.py\`，其他号立即绕行！`  
- **Verification**: 顶部可见 WIP 行；PR 合并后按账本流程撤牌并更新索引。

---

## Phase 3 — 交付确认

> **Plan Master 提示**: 以上是精确到文件级别的原子化任务拆解。是否合理？如果无误，请回复「**按计划执行**」，我将按步骤逐一击破并验证（动工前会先在总账本挂 WIP，且仅 `git add` `src/`/`tests/` 等业务路径下的变更文件，遵守 `.cursorrules`）。
