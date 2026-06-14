"""
LLM setup, agent executor, and the public run_netalytics_agent() entry point.
"""
import os
from langchain_core.messages import HumanMessage

try:
    from langchain_litellm import ChatLiteLLM
except ImportError:
    from langchain_community.chat_models import ChatLiteLLM

from langgraph.prebuilt import create_react_agent

from app.config import LITELLM_API_KEY, LITELLM_API_BASE, LITELLM_MODEL
from agent.tools import ALL_TOOLS
from agent.memory import load_history, save_history

# ── LLM ───────────────────────────────────────────────────────────────────────
llm = ChatLiteLLM(
    model=LITELLM_MODEL,
    api_key=LITELLM_API_KEY,
    api_base=LITELLM_API_BASE,
    temperature=0.6,
    model_kwargs={"caching": False},
)

# ── Agent ──────────────────────────────────────────────────────────────────────
agent_executor = create_react_agent(llm, ALL_TOOLS)


def run_netalytics_agent(
    user_message: str,
    week: str,
    region: str,
    operator: str,
    cluster: str,
    thread_id: str,
) -> str:
    """Invoke the LangGraph ReAct agent with per-thread PostgreSQL memory."""
    try:
        contextual_prompt = (
            f"{user_message}\n\n"
            f"[Hidden Context - Active UI Filters: Week: {week}, Region: {region}, "
            f"Operator: {operator}, Cluster: {cluster}]"
        )

        chat_history = load_history(thread_id)
        chat_history.append(HumanMessage(content=contextual_prompt))

        response         = agent_executor.invoke({"messages": chat_history})
        updated_history  = response["messages"]

        save_history(thread_id, updated_history)

        return updated_history[-1].content

    except Exception as e:
        print(f"[Agent Error] {e}")
        return "My analytical engine is currently offline. Please try again."
