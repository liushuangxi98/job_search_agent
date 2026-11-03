"""
模块名称:  chat.py
所属项目:  job_search_agent
创建时间:  2026/11/3 17:48
作者:      刘双喜 <1604030114@qq.com>
功能描述:
"""
from career_agent.prompts import CHAT_PROMPTS
from career_agent.llm import build_chat
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage


def chat(user_msg):
    llm_chat = build_chat()
    reply = llm_chat.invoke([
        SystemMessage(CHAT_PROMPTS),
        HumanMessage(user_msg)
    ])
    return reply