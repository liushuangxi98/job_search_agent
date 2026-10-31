# 后端三层架构 Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 用可读、常见的三层结构组织后端，并保持前端与 Agent 接口行为兼容。

**Architecture:** 接口层 `api/` 调用业务层 `services/`，业务层在事务内调用 `repositories/`。表结构和连接拆到 `models.py` / `database.py`，现有 Agent 接口通过业务服务适配器保留。

**Tech Stack:** Python 3.13、FastAPI、同步 SQLAlchemy 2、MySQL；不新增依赖。

**Spec:** [后端三层架构](../../backend-architecture.md)；本次用户要求三层架构、通用命名与易懂注释。

## Global Constraints

- 在当前仓库完成；用户自行开发全部 Agent 算法，不创建 `career_agent/`。
- API、数据库表及配置兼容；48 MiB 请求限制、413 不重试、文件限制均保持。
- 使用隔离 MySQL；不改动用户 `.env`、数据库、上传文件或现有运行进程。
- 浏览器验收交给子代理，仅返回文字结果，完成后关闭。
- 修改完成后先留在工作区供查看；按用户后续指令提交到本地 Git，不推送远程。

## Review Focus

- 服务可以被 Agent 直接调用，校验与 HTTP 路径必须一致。
- 修订检查与更新处于同一事务，失败不能覆盖资料或部分保存来源。
- 后台运行不能持有已关闭或共享的 Session。
- 文件元数据写入失败清理新文件，删除资料不改写历史报告。
- 服务关闭和真实模式未接入行为保持，前端继续使用原 API。

## Tasks

- [x] 1. 运行原有非浏览器测试，保存 OpenAPI 基线；增加直接调用业务服务的回归测试，先看到未提供服务入口的失败。
- [x] 2. 拆出模型和连接，建立按业务划分的仓库与服务；验证服务层规则、事务和文件失败清理。
- [x] 3. 使用 APIRouter 和 Depends 拆分接口，迁移运行服务与错误处理；对比 OpenAPI、运行原有 API/MySQL 回归。
- [x] 4. 更新入口引用、README、Agent 接口、目录文档和流程图，使文件名与代码一致。
- [x] 5. 委派完整回归及浏览器检查、独立代码评审；修复发现的问题，记录验证结果并关闭测试实例。

## 验证命令

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/test_mysql.py start
.\.venv\Scripts\python.exe -X utf8 -m pytest -q -m "not browser"
# 完整 pytest（含浏览器）由子代理执行。
.\.venv\Scripts\python.exe -X utf8 scripts/test_mysql.py stop
```

## 执行记录

- 已保存重构前 OpenAPI 到忽略目录 `.runtime/api-before-refactor.json`。
- 基线：34 passed, 8 deselected。新增 5 项测试先因业务服务入口缺失失败，迁移后通过；另补充初始化保留已有资料的回归。
- 重构后：40 passed, 8 deselected。OpenAPI 除文档标签分组外完全一致，7 张 MySQL 表结构和约束不变。
- 新服务测试发现 MySQL DATETIME 精度差异：岗位创建后刷新数据库默认值，沿用原先读回记录的行为；题目回滚测试以已持久化状态为比较基准。
- 独立只读审查通过：未发现新增 Critical / Important / Minor 问题；确认三层实质分离、事务边界和 Session 生命周期合理。
- Ruling: 保留直接列表调用的既有分页语义，HTTP 继续限制参数；Agent 直接调用需传合法值，已在接入文档说明，不把本次结构重构扩展为参数行为修改。
- Ruling: 保留元数据和报告直接存取的既有语义，完整上传、文件清理和运行结果校验仍走对应服务路径；已明确写入接入文档，避免 Agent 调用方误以为直接存取包含完整流程。
- 浏览器结果由独立验收代理核查，代码审查员不重复执行。
- 最终完整回归48/48通过（47.03秒），父线程核对原始日志；流程图两处新文本视觉验收通过，PNG1560×2015、475571字节。
- 两个子代理和隔离MySQL均已关闭；所有任务完成。用户随后要求将重构提交到本地 Git。
