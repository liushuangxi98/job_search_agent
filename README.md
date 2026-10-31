# 求职决策 Agent · 前后端工作台

本机单用户应用：原生 HTML/CSS/JavaScript + FastAPI + MySQL。浏览器地址为 `http://127.0.0.1:8000`。

**当前交付的是应用支撑和固定联调样例。全部 Agent 代码由你亲手实现。** 没有模型调用、OCR、RAG、地图查询、评分算法、面试决策或 LangGraph 实现。

## 启动

环境要求：Windows、Python 3.13、MySQL 8.0。前端无 Node 构建步骤、无 CDN。

1. 首次运行 `setup.cmd`，创建项目虚拟环境并安装依赖。
2. 复制 `.env.example` 为 `.env`，填写 MySQL 用户名和密码。已有 `.env` 不会被启动脚本覆盖。
3. 运行 `init_db.cmd`，创建项目库和缺失表。默认库名为 `job_search_agent`，不清空已有记录。
4. 双击 `start.cmd`，保持窗口打开，在浏览器打开上方地址。按 Ctrl+C 停止。

如果数据库账号没有建库权限，先由数据库管理员创建项目库，再运行：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m backend.init_db
```

接口文档启动后可在 `/docs` 查看。模型、高德 Key 在此前后端阶段都不需要。

## 从哪里开始使用

- 简历页可以手动填写并确认画像。演示模式也可以点击“查看示例画像”，核对后填入表单并保存。
- 条件页填写城市、月薪范围和通勤信息。金额使用元/月，通勤使用分钟。
- 岗位池可以手动创建岗位、粘贴招聘原文或上传截图；演示模式可点击“加入示例岗位”。
- 选中岗位发起评估，查看运行事件、待补充信息及报告。画像必须先确认。
- 岗位详情可进入最多八轮的面试界面，支持提前结束；完成后回到岗位池并可查看报告。
- 题库可上传 MD、查看处理结果、手动编辑题目。真正的解析、索引和索引清理由你的 Agent 完成。
- 右下角聊天入口关联当前岗位、报告或页面。修改提案确认后保存，拒绝则不写入。

## 模拟与真实模式

`.env` 中 `AGENT_MODE=demo` 使用固定联调样例。页面始终显示模拟标识，顶部可以选正常、等待输入、部分结果和失败场景。**样例不会理解上传文件、不会评价你的回答，也没有查询真实通勤。** 导入的演示岗位与题目会明确写出虚构或固定示例说明。

`AGENT_MODE=real` 读取你编写的 `career_agent.agent.handle`。缺少该入口时，真实请求返回 `503 AGENT_NOT_CONNECTED`，不会回退到模拟结果。切换配置后重启服务。

接入说明见 [Agent 接口约定](docs/agent-interface.md)。应用不会自动创建 `career_agent/` 或填充你的 Agent 代码。

## 文件分工

| 位置 | 职责 |
| --- | --- |
| `backend/app.py` | 应用装配、生命周期和静态页面服务 |
| `backend/api/` | 接口层：接收 HTTP 参数、注入服务、返回响应 |
| `backend/services/` | 业务层：资料校验、修订冲突、事务、文件和运行流程 |
| `backend/repositories/` | 数据访问层：在当前 Session 中执行 SQL/ORM 操作 |
| `backend/models.py`、`backend/database.py` | MySQL 表模型、连接池和初始化 |
| `backend/agent_gateway.py` | 唯一的真实 Agent 接入位置 |
| `backend/contracts.py`、`backend/services/run_service.py` | Agent 契约、内存运行、事件和人工确认 |
| `backend/demo.py` | 固定联调数据，非 Agent 实现 |
| `frontend/` | 六个视图、岗位抽屉、聊天、全屏面试 |
| `tests/` | 应用边界与 MySQL 集成测试，不包含 Agent 能力测试 |
| `data/` | 本机上传文件及脱敏事件日志 |

后端采用 `api → services → repositories` 三层架构。推荐从 `api/jobs.py → services/job_service.py → repositories/job_repository.py` 顺着读；业务服务统一管理事务，仓库不自行提交。结构和调用关系见 [后端架构说明](docs/backend-architecture.md)，完整目录见 [项目代码结构与 Agent 开发路线](项目参考代码目录.md)。

MySQL 保存画像、条件、岗位、资料元数据、题目、记忆和已完成报告。报告包括输入快照，之后修改或删除岗位不会重写历史结果。原始文件保存在 `data/uploads/`，不存成数据库大字段。

运行、待确认提案、聊天页面消息和未完成面试仅在内存。刷新可重新发现同一服务进程中仍存在的活动；服务重启后未完成活动丢失，需要重新发起。已保存资料和报告不受影响。

上传图片最大 8 MiB，PDF/DOCX/MD 最大 16 MiB；多图逐张上传。服务端请求体另有 48 MiB 上限，413 不自动重试。文件内容检查只验证基本格式，不替代你实现的解析和 OCR。

## 验证

应用测试使用独立 MySQL 实例，每个用例创建独立的 `job_search_agent_test_...` 数据库，用例完成后仅删除自己的测试库，不清理 `.env` 对应的项目库。

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/test_mysql.py start
.\.venv\Scripts\python.exe -X utf8 -m pytest -q
.\.venv\Scripts\python.exe -X utf8 scripts/test_mysql.py stop
```

测试实例仅绑定 `127.0.0.1`，使用随机密码和临时端口，凭据只写入被忽略的 `.runtime/`。启动测试需要已安装 `mysqld`，可以使用现有 MySQL 安装中的可执行文件，不会改动原实例。

测试还包含使用本机 Microsoft Edge 的 Playwright DOM 回归，无需下载浏览器，不截图。只运行后端测试可加 `-m "not browser"`。由 AI 执行浏览器/视觉验收时，仍必须委派子代理并仅回报文字结果。

隔离视觉检查可在测试实例启动后执行 `.\.venv\Scripts\python.exe -X utf8 -m scripts.visual_preview`，访问 `http://127.0.0.1:8001`；该进程使用独立测试库。视觉验收遵守项目约束：由子代理执行，父线程仅接收文字结论。

未保存的画像/条件表单在同一页面会话中保留，普通聊天不会覆盖它们；如果服务器资料已更新，旧表单保存会提示修订冲突，可选择重新读取已保存资料。导入预览可补齐字段或跳过条目；取消会停止尚未发送的请求，已经保存的条目不会回滚，会明确提示数量。

## 常见问题

- **数据库不可用 / 503**：检查 MySQL 是否启动、`.env` 是否正确、项目库是否已初始化。程序不会切换 SQLite。
- **资料已发生变化 / 409**：已有编辑使旧修订失效。刷新后重新操作；聊天提案可拒绝后重新生成，避免覆盖新资料。
- **已有活动 / 409**：先回答当前问题、确认/拒绝提案或结束面试。一次仅有一个活动。
- **真实 Agent 尚未接入 / 503**：按接口说明提供你的入口，或切回演示模式验证页面。
- **端口被占用**：先检查是否已打开启动窗口。不要为了启动本项目强行关闭其他程序。
- **上传失败 / 413**：缩小文件后由你重新选择上传，应用不会自动重复同一请求。

依赖仅安装在 `.venv/`；`.env`、上传、日志、测试实例与运行数据已列入 `.gitignore`。

## 本次交付验证

2026-10-31：首次交付通过42项应用/API/真实MySQL/Edge DOM测试，两个Windows初始化/启动入口已实际执行。同日完成三层架构重构后，完整 **48项测试通过**（含8项Edge交互测试），独立代码审查和流程图局部视觉检查通过。

API与现有MySQL表结构保持兼容，启动方式不变，重启服务后加载新代码。详细过程和边界见 [实施与验收记录](docs/implementation-progress.md)。这些结果只证明前后端支撑，不表示真实Agent能力已实现。
