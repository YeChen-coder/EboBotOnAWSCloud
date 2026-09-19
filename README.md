What below is perspective from tech side.

What is going on from human side can be found in https://github.com/YeChen-coder/EBOBotToDigitalPet/blob/main/ShootToTheAWSCloud_en.md.

# EBO Fargate Cloud Experiment

[Chinese](README_zh.md) | [English](README.md)

This cloud copy was prepared from a locally working project at `<source-workspace>`, whose verified source Git revision was `88b97a8e3c38469797d5e2eb89bc819bc6b30d1d`. The business code is under `ha-enabot/ebo` and `realtime-assistant`. The original source tree, data directories, and locally running services were not modified by the migration copy.

The target is one Linux x86_64 Fargate task in Canada Central (`ca-central-1`) with 1 vCPU and 2 GiB of memory after the CPU reduction on September 10 and the memory reduction on September 12. It contains `ebo-engine` and `realtime-assistant`; Home Assistant stays on the local Windows machine. See the full architecture study in [English](EBO_Fargate_Architecture_and_Local_Diagnostic_Agent_Report.en.md) or [Chinese](EBO_Fargate架构与诊断Agent调研报告.md).

## Current status

The memory reduction completed on 2026-09-12. Roughly 65 hours of historical observations showed a task memory peak near 320 MiB, so the authorized configuration change reduced task memory from 4 GiB to 2 GiB. The current task definition was `ebo-cloud-lab:6`, with task ID `<task-id-redacted>`. At 20:31 UTC, CloudFormation was `UPDATE_COMPLETE`, the ECS deployment was `COMPLETED`, both containers were `HEALTHY`, and audio, video, and Realtime remained normal after the startup grace period. The highest sample during the first three minutes was 277 MiB. Estimated Fargate compute is about USD 39.63 per 730-hour month, saving about USD 7.10. See the [memory sizing assessment](Fargate内存降配评估_2026-09-12.md). Earlier release records are retained below by date; task IDs and references to the “current” state describe the acceptance point for that release.

The conversation logging enhancement completed on 2026-09-11. The Assistant now emits final user transcripts, final model output, and key diagnostic events as structured CloudWatch logs. Fifty-five Assistant tests, eight cloud tests, and an offline image-path check passed. Both `20260911-logs` images were deployed by 17:09 UTC. At that point, the task definition was `ebo-cloud-lab:5`, the task ID was `<task-id-redacted>`, the ECS and CloudFormation updates were complete, and both containers and their business health checks were normal. CloudWatch and the local collector received the new `realtime.connected` event. A separate short-lived synthetic cloud task verified that full Chinese transcript and response text reached CloudWatch, after which the test task was stopped. The production service had only one empty transcript observation at that time, so a real spoken exchange still requires human validation. See the [CloudWatch conversation log guide](cloud/CloudWatch对话日志查看指南.md).

The CPU reduction completed on 2026-09-10. CloudFormation deployed 1 vCPU and 4 GiB as task definition `ebo-cloud-lab:3`. The first replacement task encountered an Enabot login connection timeout; an identical replacement recovered. The current task at that acceptance point was `<task-id-redacted>`; both containers were `HEALTHY`, the ECS deployment was `COMPLETED`, and audio input and Realtime connectivity were normal after the startup grace period. See the [AWS cost estimate](AWS成本估算_2026-09-10.md).

The initial cloud deployment and cutover completed on the evening of 2026-09-09 in Toronto time. The account was `<aws-account-id>`, CloudFormation reached `UPDATE_COMPLETE`, the ECS service held one task, both business containers were `HEALTHY`, and real audio, video, and Realtime connectivity worked. The user explicitly approved the required permissions; the [permission record](cloud/权限与部署确认.md) preserves the authorized scope. See the full [deployment acceptance record](cloud/部署验收记录.md).

The old local Engine and Assistant were stopped normally, while local Home Assistant stayed running. The original source code and data were not rewritten, and the local diagnostic watcher retained its existing observation-only behavior. Cloud logs, container performance data, and service metrics were verified as available for on-demand local collection. The human listening and speaking test remained pending because the user could not perform it at that time.

Validation covered 50 Assistant tests, 115 Engine tests with one live-cloud test skipped, and six cloud-boundary tests. The Assistant passed offline startup, separate `/live` and business `/health` checks, JSON logging, and graceful SIGTERM handling using the real configuration shape. The Agora native library loaded and created its service object without network access.

## Migration changes

- Containers in the same task communicate through localhost: Engine API 8098, Engine panel 8101, Assistant 8099, talk WebSocket 8200, and RTSP 8554. This resolves the original port conflict and preserves reverse WAV retrieval.
- The deployment imports the resolved local tuning, Enabot CN account configuration, and existing API token, and verifies that Engine and Assistant use the same token.
- Each business container has its own EFS access point. The initial import preserves microphone and other UI choices, and later starts preserve the persisted microphone-privacy state. Historical family conversations, recordings, and images are not copied.
- Secrets Manager injects runtime configuration. The entrypoint removes large configuration values from its environment after startup and writes Engine settings to EFS. Environment injection is still not an isolation boundary inside a container.
- Standard output is wrapped as JSON with UTC time, service, boot ID, event, and severity fields. A redacted health snapshot and selected CloudWatch EMF metrics are emitted every 15 seconds. Command payloads and raw Realtime error bodies are suppressed. Final transcript and response text have been logged since 2026-09-11 and retained for 14 days.
- ECS health checks use process liveness, while business readiness and source-audio health remain separate observations. The Assistant exits if a required worker terminates so ECS can recover it.
- The ECS service uses minimum healthy percent 0 and maximum percent 100. Deployments briefly interrupt service to avoid two Engine instances connecting to the same robot. ECS Exec is disabled, and the former Home Assistant `/api/restart` route explicitly reports that it is unsupported in the cloud runtime.

## Local diagnostic interface

Run `cloud/diagnostic.py collect --minutes 30` with the configured Python interpreter. It writes a deduplicated SQLite evidence store, ordered JSONL, and a JSON snapshot under ignored `.local/diagnostics/`. The snapshot includes ECS service events, running and recently stopped tasks, exit reasons, and CPU and memory metrics. Container Insights performance logs provide container metrics; the two application log groups provide business evidence. No CloudWatch Agent sidecar is deployed.

Each JSONL record has a stable `evidence_id` and `trust: untrusted_observation`. An LLM must treat log content as evidence rather than executable instructions. Missing collection is marked in the snapshot and must not be interpreted as a healthy service. Repeated overlapping windows recover late events. This is an on-demand collector, not an always-on streaming daemon.

`cloud/diagnostic.py replace-task --expected-task <full-current-task-arn> --reason <reason> --execute` is the only implemented replacement command. It verifies the account, region, service, single-current-task state, deployment state, and non-overlap policy before recording the action locally. Replacing a task interrupts both containers. There is no single-container manual restart operation and no infrastructure repair tool that exposes robot movement, microphone, arbitrary shell, or similar device commands.

The current CLI uses an interactive temporary root session only for the supervised deployment work. Do not give that session to an unattended LLM agent. A future agent should use a separate read-only identity, with write operations delegated to a separate approved executor. Script allowlists and an `--execute` flag are not IAM isolation. AWS DevOps Agent integration remains a proposed design rather than a completed managed-agent deployment.

## Deployment sequence (after IAM confirmation)

1. Copy `cloud/private-settings.example.json` to the Git-ignored `.local/private-settings.json`, then fill in the AWS account ID, EFS ID, and local source path. The equivalent `EBO_AWS_*` and `EBO_SOURCE_ROOT` environment variables can be used instead.
2. Run `cloud/make_template.py` to generate `cloud/fargate.template.json`.
3. Run `cloud/deploy.py provision --tag 20260910-01`. It creates resources with `DesiredCount=0`; use `cloud/deploy.py status` to monitor progress.
4. Run `cloud/deploy.py configure`. It validates the resolved local configuration and writes two secrets. Sensitive intermediate files stay under ignored `.local/` and must never be committed.
5. Run `cloud/publish.py --tag 20260910-01` to publish both images and record immutable digest references. Use a new tag for every later release; ECR tag replacement is disabled.
6. For the first cutover, optionally run `cloud/preflight.py launch`, then confirm with `cloud/preflight.py status` that both containers exit with code 0 after reading secrets and EFS. Stop the local `realtime-assistant` and `ebo-engine` and confirm they exited before running `cloud/deploy.py start`; that command uses CloudFormation to update the image parameters to the published digests and set `DesiredCount=1`. Home Assistant remains running.
7. Wait for ECS stability, container health, business-audio evidence, and log collection, then perform the human speaking and listening test. A `RUNNING` task alone is not acceptance.
8. To roll back, run `cloud/deploy.py stop`, wait for CloudFormation and the cloud task to stop, and only then restore the two local business containers. Keeping desired count in CloudFormation prevents a later update from unexpectedly restoring the cloud connection.

## Known boundaries

- AWS testing verified EFS permissions, secret injection, image pulling, and real audio/video. Human listening quality, response playback, and end-to-end latency still need validation. A single task is not highly available.
- The local Home Assistant dashboard still targets the local Engine, so relevant entities go offline after the cutover. A future dashboard should use private access or an authenticated status proxy; the unauthenticated Engine panel must not be exposed publicly.
- Application transcripts and audio on EFS can continue growing. No automatic family-data deletion policy has been introduced. CloudWatch application logs retain 14 days.
- JSON-wrapped text events do not form a full OpenTelemetry trace. Boot IDs, task IDs in log streams, and business correlation IDs help investigation, but the schema does not yet provide one trace context across every event.
- Engine still suppresses substantial third-party SDK stdout and Python logging, leaving a blind spot. Non-blocking `awslogs` can lose records under sustained backpressure. Health checks cover processes and endpoints but cannot detect every stalled thread.
- Secret changes require a task replacement. A new process also does not guarantee complete restoration of prior conversation context. Existing microphone-privacy behavior remains unchanged.

The design uses AWS guidance for [Fargate task networking and localhost](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/fargate-task-networking.html), [ECS container health checks](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/healthcheck.html), and [EFS access-point IAM controls](https://docs.aws.amazon.com/efs/latest/ug/access-points-iam-policy.html). The architecture report contains the complete rationale and source index.
