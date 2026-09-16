# CloudWatch 转写、回复与诊断日志

本次改动已于 **2026-09-11 17:09 UTC 前部署完成**。当前 TaskDefinition `ebo-cloud-lab:5`，Task `<task-id-redacted>`，两容器 HEALTHY，ECS 部署 COMPLETED，CloudFormation UPDATE_COMPLETE。启动宽限期后机器人音视频与 Realtime 连接正常。

## 在哪里看

进入 AWS 控制台，区域选择 **加拿大中部 ca-central-1**，打开 CloudWatch → Logs（日志）→ Log groups（日志组）→ **`/ebo-cloud/assistant`**。打开最新 Task 对应的日志流。每条记录是 JSON，展开即可看到 `event` 和 `transcript`。

[直接打开 Assistant 日志组](https://ca-central-1.console.aws.amazon.com/cloudwatch/home?region=ca-central-1#logsV2:log-groups/log-group/$252Febo-cloud$252Fassistant)

日志流中的筛选框输入下面的过滤条件，可以只看双方对话：

```text
{ $.event = "conversation.user.transcript" || $.event = "conversation.assistant.output" }
```

也可以进入 CloudWatch → Logs Insights，选择 `/ebo-cloud/assistant`，把时间范围设为最近一小时，再运行：

```sql
fields @timestamp, event, transcript, item_id, response_id, output_id, persisted, chunk_index, chunk_count, @logStream
| filter event in ["conversation.user.transcript", "conversation.assistant.output"]
| sort @timestamp asc
| limit 1000
```

没有结果时，先确认时间范围包含实际说话的时刻，而且新版已经部署。运行健康但没有新对话，不会生成虚构转写。日志上传也可能有短暂延迟。

## 事件含义

| event | 内容 |
|---|---|
| `conversation.user.transcript` | 用户语音的最终转写正文、item_id、会话开始时间、文件保存状态 |
| `conversation.assistant.output` | 模型最终回复正文、response_id/item_id/output_id、关联 WAV/TXT 文件、生成和播放信息快照 |
| `conversation.transcription.failed` | 转写失败的 item_id 和错误代码 |
| `realtime.response.done` | 回复完成状态、错误代码、输入/输出/总 token 数（接口有返回时） |
| `speaker.stream.failed` | 音频流发送失败、对应输出与 stream_id、异常类型 |
| `speaker.playback.result` | 流播放结束、中断、WAV 回退请求或回退失败，附生成/已播放毫秒数 |
| `speaker.interrupted` | 对话打断时对应输出、生成时长、截断所用播放时长 |
| `storage.write_failed` | 回复音频持久化失败 |
| `realtime.connected` / `realtime.disconnected` | Realtime 连接、关闭代码、是否为计划内轮换 |
| `realtime.server_error` | 服务端错误代码与类型 |

所有事件带 UTC `timestamp`、`service`、`boot_id`、独立 `event_id`；日志流名称还包含 Task ID。`source_timestamp` 是应用发出事件的 Unix 秒时间。不同事件用 response_id/output_id/item_id 关联；不能假定所有用户 item 与模型 response 都存在完整的一对一映射。

`persisted=true` 表示应用成功写入 EFS 文件；`false` 表示写入失败，但本次收到的文本仍输出到了日志。`speaker.playback.result` 的 `wav_fallback_requested` 只表示已发起回退请求，不能当成用户实际听见声音。回复正文事件里的播放信息是当时的快照，随后应查看同 output_id 的播放结果或打断事件。

只记录最终转写/回复及关键状态，不逐 token 或逐音频帧写日志。超过 4,000 字符的正文按字符分段：相同 event_id、chunk_count，按从 0 开始的 chunk_index 拼接 transcript。凭据在分段前脱敏，已知配置凭据也由外层日志包装器再次脱敏。正文中的换行保留为 JSON 转义，避免被误认成额外事件。

这些是应用整理后的 JSON 记录，不是完整 OpenAI 原始 WebSocket 事件包；请求头、密钥、系统提示词、原始音频与图片数据不作为新增事件输出。现有本地诊断采集器依然把日志当作不可信证据，不执行正文中的指令。

## 原来的 JSONL 文件

| 用途 | Assistant 容器路径 | EFS 文件系统根目录中的路径 |
|---|---|---|
| 用户转写 | `/data/transcripts.jsonl` | `/assistant/transcripts.jsonl` |
| 模型回复 | `/data/assistant_outputs.jsonl` | `/assistant/assistant_outputs.jsonl` |
| 回复 WAV/TXT | `/data/replies/` | `/assistant/replies/` |
| 会话记忆审计 | `/data/logs/session-memory.jsonl` | `/assistant/logs/session-memory.jsonl` |

它们位于本项目 EFS `<efs-id-redacted>`，通过 Assistant Access Point 挂载。当前没有把 EFS 做成本地 Windows 文件夹，也没有启用 ECS Exec 或文件浏览页面。新增 CloudWatch 日志不改变这些持久化文件；历史内容不会自动回填到 CloudWatch。

需要拉取新版对话日志到本地时，现有 `cloud/diagnostic.py collect --minutes 30` 会把这些事件一并写入 `.local/diagnostics/*-evidence.jsonl`。它包含其他诊断事件，是证据汇总文件，并不是 EFS 原文件的逐字节副本。

## 保留时间与成本

沿用 `/ebo-cloud/assistant` 的 **14 天**保留期，仍使用原有 awslogs 通道。本次不新增 Container Insights 指标、容器、NAT 网关或其他 AWS 服务。新增费用随实际日志字节数和查询扫描量增长；之前约 $0.20/月的应用日志写入估算是没有正文时的基线，不能直接代表新增后的用量。

EFS 文件没有因此设置自动清理；CloudWatch 14 天过期不等于删除 EFS 上的原始 JSONL/WAV。新增正文日志从新版上线之后开始产生，CloudWatch 查看权限能够读取这些对话正文。

过滤语法参考：[CloudWatch JSON 过滤条件](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/FilterAndPatternSyntax.html)、[Logs Insights filter](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CWL_QuerySyntax-Filter.html)。

## 本地验收记录

2026-09-11：55 项 Assistant 单元/回归测试、8 项云端边界测试全部通过；新 Assistant 镜像在 `--network none` 下验证应用 → 带前缀事件 → 实际 runtime 包装器 → JSON 输出，全程使用合成测试文本。验证包括长中文/换行回复分段完整性、跨分段边界的凭据脱敏、EFS 写入异常仍保留文本事件、服务身份不可被子进程字段覆盖，以及不输出完整 API payload。

构建镜像：`ebo-cloud/assistant:20260911-logs`、`ebo-cloud/engine:20260911-logs`。发布后需再检查最新 Task 的真实业务健康、`realtime.connected` 新事件，以及用户实际说话后生成的转写/回复事件。尚未把本地合成测试当作云端真实对话验收。


云端验收：已在 CloudWatch 读取到新格式 `realtime.connected`，既有本地采集器也读取成功，`collection_errors=[]`。AWS `test-metric-filter` 以合成样例验证双方对话过滤条件匹配正确，未创建新指标或向业务日志写入合成对话。截至验收，新 Task 尚未收到新的语音转写；原有 6 条 EFS 回复不是本次新生成的记录。真实转写与回复正文在下一次对话后可做现场确认。页面已设置最近一小时的对话过滤，点击 Refresh 刷新。


## 部署测试记录与真实对话的区分

额外的云端通路测试使用独立、自动退出的临时 Task。它不启动业务 Engine、不调用模型、不连接机器人，只使用容器 `/tmp` 中的测试文件，输出两条正文明确标注为“部署验证”的记录。其 `service` 为 `logging-smoke`，`reason` 为 `synthetic_deployment_check`。测试记录不是用户说话或模型真实回复；测试中的 `persisted=true` 仅表示写入临时测试文件。

只看真实 Assistant 对话时，将日志事件过滤条件改为：

```text
{ ($.event = "conversation.user.transcript" || $.event = "conversation.assistant.output") && $.service = "realtime-assistant" }
```

Logs Insights 中同样可以追加：

```sql
| filter service = "realtime-assistant"
```

云端临时测试已通过：2026-09-11 22:12:57 UTC，CloudWatch 收到两条包含完整中文正文的合成转写/回复记录，以及 `logging.smoke.completed`。测试 Task `<task-id-redacted>` 已 STOPPED；Assistant 测试程序 exitCode=0，配套等待进程由 ECS 正常终止（exitCode=143），没有遗留常驻测试 Task。浏览器已实际显示两条测试正文。正式 Task 仍 HEALTHY，音视频与 Realtime 正常。最近六小时另外收到 1 条真实来源的空转写，没有有效正文或新回复；尚未以真人有效对话验证正文。
