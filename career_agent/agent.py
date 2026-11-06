"""Agent 唯一入口：分发已实现动作，统一返回后端契约。"""
from backend.contracts import AgentCommand, AgentResponse, ApplicationServices
from .chat import reply
from .parse_resume import get_resume

async def handle(command: AgentCommand, services: ApplicationServices, emit) -> AgentResponse:
    """接收一次运行或人工续接；尚未实现的动作明确失败。"""
    if command.action == 'chat':
        return await reply(command, emit)
    elif command.action == 'parse_resume':
        return await get_resume(command, services.files, emit)
    return AgentResponse(
        status='failed', kind='chat',
        data={'message': f'动作 {command.action} 尚未实现。'},
    )
