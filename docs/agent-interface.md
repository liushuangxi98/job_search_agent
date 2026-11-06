# 你实现 Agent 时的接入约定

这份文档定义应用边界，不提供 Agent 业务实现。你负责模型、工具、解析、RAG、评分、面试、记忆和评测。

## 三入口工作台的当前资料流程（2026-11-04）

个人资料统一使用 `GET/PUT /api/candidate`。PUT 提交 `{revision, profile}`，一次事务确认并保存，修订号仅增加一次。GET 返回完整 profile、`revision/confirmed` 及 `missing:[{field,label}]`。

个人核心字段必须完整：`profile.name/education/major/experience_years/skills/experience/projects`，以及 `profile.direction/city/commute_origin/salary_min/salary_max/summary/commute_mode`。工作年限允许 0；`experience` 表示工作经历，`projects` 是独立的项目经历。姓名、学历和期望薪资均不是可选项。旧资料缺项时需要重新完善确认，个人资料不能用信息盲区放行。

简历解析须同时返回画像和求职条件。学历 `education` 与大学专业 `major` 分开提取；缺少专业时请用户补充。个人简介 `summary` 和出行偏好 `commute_mode` 均可编辑且正式确认时必填；出行偏好仅使用“公共交通”或“驾车”，未知为 null。原文依据 `evidence` 由解析程序生成，在草稿和画像页只读展示。字段缺失或含义不清时使用 `needs_input` 提问；最终返回草稿，由页面核对后统一保存。

岗位缺少 `company/title/city/address/jd_text/salary` 时，必须补齐或在 `blind_spots` 中记录招聘方未披露的说明。例如 `{"address":"招聘方尚未提供具体办公地点"}`；`salary` 同时对应薪资上下限。用户须在入库前确认盲区。未知文本可用空字符串，未知薪资用 `null`，不能编造或填零。岗位对象提供 `ready/missing` 供界面在操作入口提示完善。

上传原件及文件元数据可以先保存，画像确认和岗位入池发生在资料核对完成后。真实能力未接入不能伪装成诊断成功；demo 仅用固定结果演示流程。

## 入口

你创建 `career_agent/agent.py`，提供：

`async handle(command: AgentCommand, services: ApplicationServices, emit) -> AgentResponse`

`.env` 改为 `AGENT_MODE=real` 并重启。加载位置只有 `backend/agent_gateway.py`；如果你使用类，可以在那里返回已实例化对象的异步方法。应用不会替你创建这个包。

## 输入

`AgentCommand` 位于 `backend/contracts.py`：

| 字段 | 含义 |
| --- | --- |
| `run_id` | 本次运行 ID，可用于你的 LangGraph 内存线程标识 |
| `action` | `parse_resume`、`extract_jobs`、`index_knowledge`、`evaluate`、`interview`、`chat` |
| `input` | 初始任务参数，如 `document_id`、`job_ids`、`job_id` |
| `context` | 当前页面/岗位/报告标识，以及浏览器会话 `session_id` |
| `snapshot` | 发起时的画像、条件和所选岗位快照；恢复时仍为这份原始快照 |
| `state` | 你在上一次响应中返回的内存状态，不落数据库 |
| `human_input` | 本轮输入始终为字典；首次 decision=start，后续为 answer/confirm/reject/finish |
| `history` | 同一标签页、同一 action 的完整历史副本。每条仅含 `role`（`user` 或 `assistant`）和 `content` |
| `summary` | 当前 action 的上一份摘要，首次为 `None`。Agent 使用摘要和 `history[summary.covered_messages:]` 组织上下文 |
| `previous_usage` | 上一次模型响应的用量，首次或未上报时为 `None`。所有动作均按同一会话、同一 action 传递 |

`human_input` 包含 `input_id`、`decision`、`text` 和 `data`。应用为首次调用填入 decision=start、text（用户文字或空串）、data={}、input_id=None。后续请求的 `decision` 可取 `answer / confirm / reject / finish`。应用将结构化补充附到 text，同时保留 data。一次输入只处理一次，旧输入 ID 返回 409。

面试的 `finish` 交给你的入口生成结束报告；其他活动在澄清阶段选择 `finish` 时，由应用直接结束，不再调用入口或生成新报告。

应用按 `(session_id, action)` 保存历史，缺少会话标识时用 `run_id` 替代。相同 action 再次启动沿用历史，暂不自动清空。所有动作均收到 `command.history`，本轮用户文字统一从 `human_input["text"]` 读取，`input` 仅保留文件 ID、岗位 ID 等任务参数。文件 ID 和岗位选择等启动参数只用于执行请求；历史记录实际用户输入、追问和助手结果。模型配置由你读取，聊天/Embedding 使用 URL、Key、Model 和 OpenAI 兼容接口；百炼专用重排使用独立 HTTP 适配。

## 可用的普通存取

三层重构后，`services.repository` 仍保留下面的方法名，实际指向 `backend/services/agent_data_service.py`，并由它调用各业务服务；不向 Agent 暴露 SQLAlchemy Session。`services.files` 指向 `DocumentService`。运行管理位于 `backend/services/run_service.py`，HTTP 入口位于 `backend/api/runs.py`。你仍只需按本页契约实现 Agent，无须自己管理数据库事务。

兼容接口沿用原有普通存取语义：列表分页参数由 HTTP 路由检查，直接调用时需传合法的 `offset/limit`；`add_document/delete_document` 只处理元数据，完整上传和删除文件使用资料服务。评估和面试报告应通过 `AgentResponse` 返回，由运行服务校验后保存；直接调用 `save_report` 不会替你执行运行结果校验。

- `services.files.resolve(document_id)`：根据已登记资料 ID 返回受控文件路径；你自行读取、解析或交给本地 MCP。不要把模型任意给出的本机路径当作资料。
- `services.repository.candidate()`：读取当前资料，返回 `revision / confirmed / data`。
- `job(id)`、`jobs(q='', offset=0, limit=100)`：岗位读取。
- `documents(kind=None)`、`document(id)`：资料元数据。
- `records('questions' / 'memories', q='', offset=0, limit=100)`：题目或记忆列表。
- `save_record('questions' / 'memories', data, key=None)`：题目或记忆存取，输入结构见 `backend/schemas.py`。
- `delete_record(...)`：移除对应记录。Chroma 同步、索引版本和记忆规则由你实现。

索引动作需由你的代码保存题目元数据和向量；仅返回处理摘要不会替你写索引。资料删除会移除关联题目元数据，检索应排除失效的题目/资料 ID，并由你清理向量。

## 输出

返回 `AgentResponse` 或同形字典：

- `status`：`completed / partial / needs_input / failed`。调用期间应用显示 `running`。
- `kind`：`profile_draft / job_drafts / knowledge_result / evaluation_report / interview_question / interview_report / chat`。
- `data`：对应界面数据。
- `pending_input`：等待用户输入时提供。
- `state`：你需要在本次运行下一轮取回的 JSON 状态，仅内存保存。
- `summary`：Agent 可写回 `{text, covered_messages}`，覆盖范围从第一条消息数起，仅包括本轮之前的历史。省略表示沿用旧摘要。应用独立追加原文。
- `usage`：本次模型响应的用量字典，包含 `input_tokens / output_tokens / total_tokens`，可带服务商的分项。模型未上报时传 `None`，没有调用模型时省略。运行查询分别返回 `usage` 和 `previous_usage`。确认、拒绝等固定回执沿用已有记录。

| kind | data 的主要字段 |
| --- | --- |
| `profile_draft` | `profile`（ProfileData，包含求职条件）必填；可选 `diagnosis` 字符串列表、`notice` |
| `job_drafts` | `jobs`（JobDraft 列表，含 `blind_spots`）、可选 `diagnosis`、`notice` |
| `knowledge_result` | `questions`、`coverage`、`notice`；真实入库/索引由你的处理代码完成 |
| `evaluation_report` | `title/summary/jobs`；每岗包含 `job_id/company/title/match_score/matches/strengths/gaps/recommendation/missing/evidence/commutes` |
| `interview_question` | `round`、`question`、`question_id`、`source` |
| `interview_report` | `title/summary/dimensions/answers/weaknesses/practice` |
| `chat` | `message`、可选 `notice` |

匹配分为独立的 `0—100` 或 `null`；后端检查范围，不计算匹配算法，也不计算薪资分、通勤分、总分或排名。`matches` 每项包含 `requirement`（岗位要求）、`evidence`（经历依据文字）和 `assessment`（matched/partial/gap/unknown）。未知证据不能等同于用户不具备能力。

`commutes` 使用 `public_transit / driving`，各项包含 `duration_minutes / distance_km / route_summary`；分别为单程分钟、公里和主要路线说明。耗时/距离未知用 `null`，路线未知用空字符串。模型缺失、接口失败不能伪装成零分、零分钟或虚构路线。

评估/面试报告在 `completed` 或 `partial` 时由运行容器自动保存一次，包含输入快照。不要为同一运行重复生成不同报告。面试问答证据需要你在最终 `answers` 中返回，未完成状态不保存。

资料抽取结果只作为草稿展示，不会自动覆盖用户画像或新增岗位；需用户预览确认。演示适配器在 `backend/demo.py`，它只是界面数据样例，不能作为 Agent 已完成的证明。

## 人工补充和修改确认

普通问题：

```json
{"status":"needs_input","kind":"chat","data":{"message":"请确认地址"},"pending_input":{"kind":"question","prompt":"公司工作地点是哪一个？"}}
```

修改提案：

```json
{"status":"needs_input","kind":"chat","data":{"message":"是否更新期望城市？"},"pending_input":{"kind":"proposal","prompt":"期望城市改为杭州","proposal":{"target":"profile","revision":3,"changes":{"city":"杭州"}}}}
```

`target` 支持 `profile / job`，岗位还要提供 `target_id`。`revision` 必须来自读取时的记录。确认时应用核对字段和修订号并保存，拒绝时保留原资料。聊天提案由应用直接记录决定、回复用户并结束运行，其他动作仍可续接 Agent。不要在提出建议时写入待确认的内容。

## 事件

调用 `await emit(kind, message, data=None)` 发送状态或工具摘要。建议 `kind` 为 `status / tool / observation / message / error`。应用为事件添加序号和时间，前端每秒获取新增事件，本机保存 JSONL。

只发送必要摘要。应用仅对事件文本、事件数据和错误诊断进行脱敏，不改写运行 ID、事件时间、待确认提案或业务快照。你仍须避免在普通业务结果或事件中返回密钥、完整 HTTP 认证头、整张图片和原始大响应。单条事件过大时只保留摘要。

## 输出校验与界面恢复

应用按 `kind` 校验字段和数组，结构错误不会保存为完成报告。岗位草稿允许字段暂缺，用户在预览中补齐、确认盲区或跳过。新评估结果只保留当前 ReportJob 的字段，忽略旧薪资分、通勤分和总分；数据库中的历史报告不被改写，仍可按原快照查看和导出。

运行查询额外返回 `context` 和 `subject_job_id`，用于刷新后回到同一个面试岗位。历史运行的展示不替换正在进行的活动。轮询失败仅重试状态 GET，不重发模型操作 POST；413 不自动重试。

## 应用层边界

一次只有一个活动；`needs_input` 仍占用当前活动。新的运行会返回 409，避免并行覆盖状态。等待期间可以编辑业务资料；旧提案的版本校验会阻止覆盖新数据。

服务重启后内存运行消失，旧 ID 返回 `404 RUN_EXPIRED`。报告和已确认资料保留。任务恢复、模型预算、未知工具拒绝、自检修复以及检索策略属于你实现的 Agent 逻辑。

`GET /api/runs/{id}?after=N` 返回增量 `events`、`next_seq`、当前 `result/pending_input/report_id`。提交补充使用 `POST /api/runs/{id}/input`，携带当前 `pending_input.id`。具体接口形状可启动应用后查看 `/docs`。
