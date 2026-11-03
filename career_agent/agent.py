"""
模块名称:  agent.py
所属项目:  job_search_agent
创建时间:  2026/11/2 16:45
作者:      刘双喜 <1604030114@qq.com>
功能描述:
"""
from backend.contracts import AgentResponse
from career_agent.chat import chat


async def handle(command, services, emit):
    if command.action == "chat":
        reply = await chat(command.input['text'])
        ret = AgentResponse(status='completed', kind='chat', data={'message': reply.content}, pending_input={},
                            state={'last_message': command.input})
        return ret
    else:
        return AgentResponse(status='failed', kind='chat', data={'message': f'{command.action}暂未实现'})
