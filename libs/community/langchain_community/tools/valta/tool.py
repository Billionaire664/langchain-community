"""
Valta Budget Guard — LangChain Tool Integration
================================================
Gives any LangChain agent a hard spending ceiling backed by Valta's
policy engine. The agent calls `check_spend` before any paid operation;
Valta evaluates the request against the user's policies and returns
approved/denied before a single token or dollar leaves.

Usage
-----
    from langchain_community.tools.valta.tool import ValtaBudgetTool

    tool = ValtaBudgetTool(
        api_key="vk_live_...",
        agent_id="my-research-agent",
    )

    llm = ChatOpenAI(model="gpt-4o")
    agent = initialize_agent(
        tools=[tool, ...your_other_tools],
        llm=llm,
        agent=AgentType.OPENAI_FUNCTIONS,
    )

How it works
------------
1. Add ValtaBudgetTool to your agent's tool list.
2. The agent calls it before any operation with a cost.
3. Valta checks the spend against your policy and returns approved/denied.
4. If denied, the agent receives the reason and stops.

Policies are managed at valta.co/dashboard/policies.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Optional, Type

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field


class _SpendInput(BaseModel):
    amount: float = Field(
        description="Cost in USD of the operation the agent is about to perform."
    )
    category: Optional[str] = Field(
        default=None,
        description=(
            "Optional category label, e.g. 'llm_inference', 'web_search', "
            "'payment', 'api_call'. Used for policy matching."
        ),
    )
    merchant: Optional[str] = Field(
        default=None,
        description="Service or provider being called, e.g. 'OpenAI', 'Stripe'.",
    )
    purpose: Optional[str] = Field(
        default=None,
        description="Short description of why this spend is needed.",
    )


class ValtaBudgetTool(BaseTool):
    """
    Hard spending control for LangChain agents via Valta.

    Call this tool before any paid operation. Returns 'approved' or
    'denied: <reason>'. If denied, do not proceed with the operation.

    Get an API key at valta.co.
    """

    name: str = "check_spend"
    description: str = (
        "Check whether a planned spend is within budget before executing it. "
        "Call this before any operation that costs money. "
        "Input: the cost in USD and optional category/merchant/purpose. "
        "Returns 'approved' or 'denied: <reason>'. "
        "If the result is 'denied', do NOT proceed with the operation."
    )
    args_schema: Type[BaseModel] = _SpendInput
    return_direct: bool = False

    api_key: str
    agent_id: str
    base_url: str = "https://valta.co"

    def _run(
        self,
        amount: float,
        category: Optional[str] = None,
        merchant: Optional[str] = None,
        purpose: Optional[str] = None,
    ) -> str:
        payload = json.dumps(
            {
                "agent": self.agent_id,
                "amount": amount,
                "currency": "USD",
                "category": category,
                "merchant": merchant,
                "purpose": purpose,
            }
        ).encode()

        req = urllib.request.Request(
            f"{self.base_url}/api/v1/spend",
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            try:
                data = json.loads(exc.read())
            except Exception:
                return f"denied: valta request failed with status {exc.code}"
        except Exception as exc:
            return f"denied: could not reach valta ({exc})"

        if data.get("approved"):
            return "approved"

        if data.get("requires_approval"):
            request_id = data.get("request_id", "")
            return (
                f"denied: requires human approval "
                f"(request_id={request_id}, check valta.co/dashboard)"
            )

        reason = data.get("reason") or data.get("error") or "policy_violation"
        return f"denied: {reason}"

    async def _arun(
        self,
        amount: float,
        category: Optional[str] = None,
        merchant: Optional[str] = None,
        purpose: Optional[str] = None,
    ) -> str:
        try:
            import aiohttp
        except ImportError:
            import asyncio
            return await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._run(amount, category, merchant, purpose)
            )

        payload = {
            "agent": self.agent_id,
            "amount": amount,
            "currency": "USD",
            "category": category,
            "merchant": merchant,
            "purpose": purpose,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/api/v1/spend",
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json()
        except Exception as exc:
            return f"denied: could not reach valta ({exc})"

        if data.get("approved"):
            return "approved"

        if data.get("requires_approval"):
            request_id = data.get("request_id", "")
            return (
                f"denied: requires human approval "
                f"(request_id={request_id}, check valta.co/dashboard)"
            )

        reason = data.get("reason") or data.get("error") or "policy_violation"
        return f"denied: {reason}"
