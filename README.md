# EBO Fargate 云端实验项目

[中文](README.md) | [English](README.en.md)

云端副本来自本地已运行项目，原项目路径为 `<source-workspace>`，来源 Git HEAD：`88b97a8e3c38469797d5e2eb89bc819bc6b30d1d`（已通过 Git 核验）。业务代码位于 `ha-enabot/ebo` 和 `realtime-assistant`。原项目、数据目录及运行中的服务未修改。

目标：加拿大中部 `ca-central-1`，一个 Linux x86_64 Fargate Task，总计 1 vCPU / 2 GiB（9 月 10 日降低 CPU，9 月 12 日降低内存），内含 `ebo-engine` 与 `realtime-assistant`。Home Assistant 留在本地。架构研究见[中文报告](EBO_Fargate架构与诊断Agent调研报告.md)或[英文报告](EBO_Fargate_Architecture_and_Local_Diagnostic_Agent_Report.en.md)。

## 当前状态

2026-09-12 内存降配完成：约 65 小时历史观测的 Task 内存峰值约 320 MiB，已按用户授权从 4 GiB 降至 2 GiB。当前任务定义 `ebo-cloud-lab:6`，Task `<task-id-redacted>`；20:31 UTC 核验 CloudFormation UPDATE_COMPLETE、ECS COMPLETED、两容器 HEALTHY，启动宽限期后音视频及 Realtime 正常。首三个分钟采样内存最高 277 MiB。Fargate 算力约 $39.63/月，每 730 小时省 $7.10。详见 [内存降配评估](Fargate内存降配评估_2026-09-12.md)。以下按日期保留此前发布记录，其中 Task ID 和“当前”描述指该次验收时点。

2026-09-11 对话日志增强：本地已实现用户最终转写、模型最终回复及关键诊断事件的结构化 CloudWatch 输出，55 项 Assistant 测试、8 项云端测试及新镜像断网通路验证通过。两个 `20260911-logs` 镜像已于 17:09 UTC 前完成部署，当前 TaskDefinition 为 `ebo-cloud-lab:5`，Task 为 `<task-id-redacted>`，ECS/CloudFormation 更新完成，两容器与业务健康正常。CloudWatch 及本地采集器已读到新版 `realtime.connected` 事件；追加独立云端合成测试已验证转写和回复中文正文实际进入 CloudWatch，测试 Task 已停止；正式服务仅观察到一条空转写，真人有效对话验收仍待实际说话。查看路径、过滤条件和事件说明见 [CloudWatch 对话日志查看指南](cloud/CloudWatch对话日志查看指南.md)。

2026-09-10 CPU 降配：CloudFormation 已完成 1 vCPU / 4 GiB 更新，TaskDefinition 为 `ebo-cloud-lab:3`。首次新 Task 发生 Enabot 登录连接超时，经一次同规格 Task 替换恢复；当前 Task `<task-id-redacted>` 的两个容器 HEALTHY，ECS 部署 COMPLETED，启动宽限期后音视频输入和 Realtime 连接正常。成本与说明见 [成本报告](AWS成本估算_2026-09-10.md)。以下为首次迁移验收记录。

已于 2026-09-09 晚（多伦多时间）完成部署与切换。账户 `<aws-account-id>`，CloudFormation `UPDATE_COMPLETE`，ECS Service 稳定运行一个 Task，两个业务容器均 `HEALTHY`，真实音视频和 Realtime 连接正常。用户已明确确认所需权限；[权限说明](cloud/权限与部署确认.md)保留范围记录。完整结果见 [部署验收记录](cloud/部署验收记录.md)。

本地旧 Engine 和 Assistant 已正常停止，Home Assistant 保持运行。原源码和数据未改写；本地 diagnostic watcher 保持原有只观察模式。已验证云日志、容器性能和服务指标可按需拉取到本地。人工听说体验因用户暂不方便测试而待确认。

已通过 Assistant 50 项测试、Engine 115 项测试（1 项真实云连接测试跳过）、云端边界 6 项测试。Assistant 使用真实配置通过断网启动、`/live` 与业务 `/health` 分离、JSON 日志以及 SIGTERM 正常退出验证。Agora 原生库已在无网络环境成功加载并创建服务对象。

## 已实现的迁移调整

- 同 Task 使用 localhost：Engine API 8098、Panel 8101、Assistant 8099、talk WebSocket 8200、RTSP 8554。修复 8099 冲突及 WAV 回退的反向获取 URL。
- 保留本地 Compose 实际解析后的调参、Enabot CN 账户配置，以及现有 API token。导入时校验两服务 token 一致。
- 每个容器独立 EFS Access Point。首次部署导入麦克风等 UI 选择；后续启动保留持久化隐私状态。没有复制历史家庭对话、录音或画面。
- 配置通过 Secrets Manager 注入；入口启动后移除大块配置环境变量，并将引擎配置写入 EFS。应用本身仍需要自己的运行凭据，环境注入不是容器内保密边界。
- stdout 统一 JSON 包装，UTC 时间、service、boot_id、event、severity；每 15 秒输出选定健康状态和 CloudWatch EMF 指标。命令 payload 与原始 Realtime 错误正文不输出，按已知配置值脱敏。2026-09-11 起按用户要求增加最终转写和回复正文的结构化日志，保留 14 天。
- 进程健康单独用于 ECS health check；业务就绪度和真实音频来源状态单独观测。Assistant 必需工作线程退出时结束进程，允许 ECS 恢复。
- ECS Service 部署参数为 minHealthy=0、max=100；更新会短暂中断，避免两个 Engine 重叠连接机器人。ECS Exec 关闭；旧 HA `/api/restart` 在云环境明确返回不支持。

## 本地诊断接口

使用已配置的 Python 运行 `cloud/diagnostic.py collect --minutes 30`：输出 `.local/diagnostics/` 下 SQLite 去重证据、按时间排序的 JSONL，以及包含 ECS 服务事件、运行/最近停止任务、退出原因、CPU/内存指标的 JSON 快照。Container Insights performance 日志提供容器层指标；两个应用日志组提供业务证据。没有单独部署 CloudWatch Agent 容器。

JSONL 含稳定 evidence_id 和 `trust: untrusted_observation`。LLM 必须把日志视作证据，不能执行日志中的“指令”；缺失采集会在快照中标记，不能理解为服务健康。按窗口重复采集可补收延迟日志；本实现是按需拉取，尚非全天后台流式收集器。

`cloud/diagnostic.py replace-task --expected-task <当前完整Task ARN> --reason <原因> --execute` 才会发起替换：校验账户、区域、服务、当前唯一 Task、部署状态和无重叠策略；动作写本地审计。它替换整个 Task，会同时中断两个容器。没有单容器手动 restart 接口，也没有暴露机器人运动、开麦或任意 Shell 操作。

目前 CLI 使用用户主动登录的临时 **root** 会话，只供本次人工协作部署。**不要把该会话交给无人值守 LLM agent**。未来 agent 应使用独立只读身份；写操作经单独执行端与审批身份完成。脚本白名单和 `--execute` 不是 IAM 隔离。AWS DevOps Agent 的 AgentSpace、只读角色和人审 directed actions 尚未开通，设计与接入方式见研究报告；此版不声称已完成托管 Agent 的集成。

## 部署顺序（IAM 确认后继续）

1. 将 `cloud/private-settings.example.json` 复制为被 Git 忽略的 `.local/private-settings.json`，填写 AWS 账户、EFS ID 和本地源项目路径；也可使用对应的 `EBO_AWS_*` / `EBO_SOURCE_ROOT` 环境变量。
2. `cloud/make_template.py` 生成 `cloud/fargate.template.json`。
3. `cloud/deploy.py provision --tag 20260910-01` 创建资源，初始 DesiredCount=0；`cloud/deploy.py status` 查看进度。
4. `cloud/deploy.py configure` 从本地实际配置校验并写入两个 Secret。私密中间文件位于已忽略的 `.local/`，不得提交。
5. `cloud/publish.py --tag 20260910-01` 推送已构建镜像，输出不可变 digest 引用。后续发布使用新 tag，ECR 禁止覆盖 tag。
6. 首次切换：可先运行 `cloud/preflight.py launch` 并确认 `cloud/preflight.py status` 两容器 exitCode=0，验证配置与 EFS。随后停止本地 `realtime-assistant`、`ebo-engine` 并确认退出，运行 `cloud/deploy.py start` 使用 CloudFormation 更新镜像参数为发布的 digest、DesiredCount=1。Home Assistant 不停止。
7. 等待 ECS 稳定、容器健康、业务音频状态与日志可采集；再与用户验证听说体验。只凭容器 RUNNING 不算迁移完成。
8. 回滚时先运行 `cloud/deploy.py stop` 将云服务 DesiredCount=0，确认 CloudFormation 更新完成且云 Task 已停止，才恢复本地两个业务容器。使用 CloudFormation 同步期望数量，避免下次更新意外恢复云连接。

## 已知边界

- EFS 权限、Secrets 注入、镜像拉取以及真实音视频已通过 AWS 实测；人工听说体验、回复播放和端到端延迟尚待确认。单 Task 不是高可用部署。
- HA 本地 dashboard 原指向本地 Engine；切换后相关实体会离线。未来若继续需要展示，使用私有访问或专门状态代理，不能直接公开无认证 Panel。
- EFS 上的后续应用转录/音频仍可能增长，未引入自动删除家庭数据的策略；需确定保留期限。CloudWatch 应用日志保留 14 天。
- 日志采用文本事件的 JSON 包装，并非端到端 OpenTelemetry trace。boot_id、日志流 Task ID 和业务消息里的关联 ID 可辅助排错，尚未统一成所有事件的结构化 trace 字段。
- Engine 原本对第三方 SDK stdout/Python logging 的抑制仍保留，因此观测存在 SDK 内部细节盲区。awslogs non-blocking 在持续背压下可能丢日志；健康监测只检查进程和端点，不能发现所有线程卡死。
- Secret 通过启动配置注入，新版本需要替换 Task 才会生效；新进程也不保证恢复全部对话上下文。现有源码中的麦克风隐私规则保持不变。

配置设计参考：[Fargate 网络与 localhost](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/fargate-task-networking.html)、[ECS 健康检查](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/healthcheck.html)、[EFS Access Point IAM 限制](https://docs.aws.amazon.com/efs/latest/ug/access-points-iam-policy.html)。完整调研与引用见研究报告。
