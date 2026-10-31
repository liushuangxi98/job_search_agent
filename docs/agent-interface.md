# 你实现 Agent 时的接入约定

这份文档定义应用边界，不提供 Agent 业务实现。你负责模型、工具、解析、RAG、评分、面试、记忆和评测。

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
| `input` | 初始输入，如 `document_id`、`text`、`job_ids`、`job_id` |
| `context` | 当前页面/岗位/报告标识，以及浏览器会话 `session_id` |
| `snapshot` | 发起时的画像、条件和所选岗位快照；恢复时仍为这份原始快照 |
| `state` | 你在上一次响应中返回的内存状态，不落数据库 |
| `human_input` | 首次为 `None`；恢复时为回答、确认、拒绝或结束决定 |

`human_input` 包含 `input_id`、`decision`、`text` 和 `data`。`decision` 可取 `answer / confirm / reject / finish`。一次输入只处理一次，旧输入 ID 返回 409。

面试的 `finish` 交给你的入口生成结束报告；其他活动在澄清阶段选择 `finish` 时，由应用直接结束，不再调用入口或生成新报告。

聊天中的 `session_id` 供你实现短期记忆和上下文选择；应用不会自动把全部聊天历史塞入模型。模型配置由你读取，聊天/Embedding 使用 URL、Key、Model 和 OpenAI 兼容接口；百炼专用重排使用独立 HTTP 适配。

## 可用的普通存取

三层重构后，`services.repository` 仍保留下面的方法名，实际指向 `backend/services/agent_data_service.py`，并由它调用各业务服务；不向 Agent 暴露 SQLAlchemy Session。`services.files` 指向 `DocumentService`。运行管理位于 `backend/services/run_service.py`，HTTP 入口位于 `backend/api/runs.py`。你仍只需按本页契约实现 Agent，无须自己管理数据库事务。

兼容接口沿用原有普通存取语义：列表分页参数由 HTTP 路由检查，直接调用时需传合法的 `offset/limit`；`add_document/delete_document` 只处理元数据，完整上传和删除文件使用资料服务。评估和面试报告应通过 `AgentResponse` 返回，由运行服务校验后保存；直接调用 `save_report` 不会替你执行运行结果校验。

- `services.files.resolve(document_id)`：根据已登记资料 ID 返回受控文件路径；你自行读取、解析或交给本地 MCP。不要把模型任意给出的本机路径当作资料。
- `services.repository.candidate('profile' / 'preferences')`：读取当前资料，返回 `revision / confirmed / data`。
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

| kind | data 的主要字段 |
| --- | --- |
| `profile_draft` | `profile`（符合 ProfileData）、可选 `notice` |
| `job_drafts` | `jobs`（JobData 列表）、可选 `notice` |
| `knowledge_result` | `questions`、`coverage`、`notice`；真实入库/索引由你的处理代码完成 |
| `evaluation_report` | `title`、`summary`、`jobs`；每岗包含 `job_id/company/title`、四项分数、`gaps/recommendation/missing/evidence/commutes` |
| `interview_question` | `round`、`question`、`question_id`、`source` |
| `interview_report` | `title/summary/dimensions/answers/weaknesses/practice` |
| `chat` | `message`、可选 `notice` |

分数为 `0—100` 或 `null`；后端检查字段和范围，不计算你的匹配与加权算法。`commutes` 使用 `public_transit / driving`，各项包含 `duration_minutes / distance_km`，失败用 `null`。模型缺失、接口失败不能伪装成零分或零分钟。

评估/面试报告在 `completed` 或 `partial` 时由运行容器自动保存一次，包含输入快照。不要为同一运行重复生成不同报告。面试问答证据需要你在最终 `answers` 中返回，未完成状态不保存。

资料抽取结果只作为草稿展示，不会自动覆盖用户画像或新增岗位；需用户预览确认。演示适配器在 `backend/demo.py`，它只是界面数据样例，不能作为 Agent 已完成的证明。

## 人工补充和修改确认

普通问题：

```json
{"status":"needs_input","kind":"chat","data":{"message":"请确认地址"},"pending_input":{"kind":"question","prompt":"公司工作地点是哪一个？"}}
```

修改提案：

```json
{"status":"needs_input","kind":"chat","data":{"message":"是否更新期望城市？"},"pending_input":{"kind":"proposal","prompt":"期望城市改为杭州","proposal":{"target":"preferences","revision":3,"changes":{"city":"杭州"}}}}
```

`target` 支持 `profile / preferences / job`，岗位还要提供 `target_id`。`revision` 必须来自读取时的记录。应用在确认后校验字段和修订号，再保存修改，然后将确认决定交回入口；拒绝不修改。不要在输出提案前自行写入这些待确认内容。修改资料后需重新读取当前值，`command.snapshot` 仍是原始快照。

## 事件

调用 `await emit(kind, message, data=None)` 发送状态或工具摘要。建议 `kind` 为 `status / tool / observation / message / error`。应用为事件添加序号和时间，前端每秒获取新增事件，本机保存 JSONL。

只发送必要摘要。应用仅对事件文本、事件数据和错误诊断进行脱敏，不改写运行 ID、事件时间、待确认提案或业务快照。你仍须避免在普通业务结果或事件中返回密钥、完整 HTTP 认证头、整张图片和原始大响应。单条事件过大时只保留摘要。

## 输出校验与界面恢复

应用按 `kind` 校验界面会读取的字段和数组，结构错误不会保存为完成报告。岗位草稿允许公司或职位暂缺，用户可在预览中补齐或跳过；正式入库仍要求公司和职位有效。分项分数缺失时总分必须为空，但加权计算仍由你的代码实现。

运行查询额外返回 `context` 和 `subject_job_id`，用于刷新后回到同一个面试岗位。历史运行的展示不替换正在进行的活动。轮询失败仅重试状态 GET，不重发模型操作 POST；413 不自动重试。

## 应用层边界

一次只有一个活动；`needs_input` 仍占用当前活动。新的运行会返回 409，避免并行覆盖状态。等待期间可以编辑业务资料；旧提案的版本校验会阻止覆盖新数据。

服务重启后内存运行消失，旧 ID 返回 `404 RUN_EXPIRED`。报告和已确认资料保留。任务恢复、模型预算、未知工具拒绝、自检修复以及检索策略属于你实现的 Agent 逻辑。

`GET /api/runs/{id}?after=N` 返回增量 `events`、`next_seq`、当前 `result/pending_input/report_id`。提交补充使用 `POST /api/runs/{id}/input`，携带当前 `pending_input.id`。具体接口形状可启动应用后查看 `/docs`。
