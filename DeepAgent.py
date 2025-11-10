import os
from typing import Literal

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from tavily import TavilyClient
from deepagents import create_deep_agent

try:
    from composio_helpers import get_default_composio_tools
except Exception:  # pragma: no cover - optional dependency may not be configured
    get_default_composio_tools = None  # type: ignore

load_dotenv()

tavily_api_key = os.getenv("TAVILY_API_KEY")
if not tavily_api_key:
    raise RuntimeError("TAVILY_API_KEY not set in environment variables or .env file.")

openai_api_key = os.getenv("OPENAI_API_KEY")
if not openai_api_key:
    raise RuntimeError("OPENAI_API_KEY not set in environment variables or .env file.")

tavily_client = TavilyClient(api_key=tavily_api_key)

model = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0,
    api_key=openai_api_key,
)

def internet_search(
    query: str,
    max_results: int = 5,
    topic: Literal["general", "news", "finance"] = "general",
    include_raw_content: bool = False,
):
    """Run a web search"""
    return tavily_client.search(
        query,
        max_results=max_results,
        include_raw_content=include_raw_content,
        topic=topic,
    )
# System prompt to steer the agent to be an expert researcher
research_instructions = """You are an expert researcher. Your job is to conduct thorough research and then write a polished report.

You have access to an internet search tool as your primary means of gathering information.

## `internet_search`

Use this to run an internet search for a given query. You can specify the max number of results to return, the topic, and whether raw content should be included.
"""

def _load_composio_tools():
    if get_default_composio_tools is None:
        return []
    try:
        return get_default_composio_tools()
    except Exception:
        return []


composio_tools = _load_composio_tools()

agent = create_deep_agent(
    model=model,
    tools=[internet_search, *composio_tools],
    system_prompt=research_instructions,
)


def run_agent(query: str) -> str:
    response = agent.invoke({"messages": [{"role": "user", "content": query}]})
    return response["messages"][-1].content


if __name__ == "__main__":
    user_query = input("Enter your research query: ")
    print(run_agent(user_query))