"""
title: Badgr
author: aibadgr
version: 0.1.0
license: Apache-2.0
description: Chat with a model served on Badgr, and deploy, check and stop Badgr GPU endpoints from chat.
requirements: pydantic
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Generator, Iterator, Union

from pydantic import BaseModel, Field

CONTROL_ID = "badgr-control"
HELP = (
    "Badgr control commands:\n"
    "- `deploy <hf-model-id> <minutes> [gpu]` serve a model (stops itself after <minutes>)\n"
    "- `status` list your deployments\n"
    "- `check <deployment-id>` state and recent logs\n"
    "- `stop <deployment-id>` stop it and end billing"
)


class Pipe:
    class Valves(BaseModel):
        BADGR_API_KEY: str = Field(default="", description="Badgr API key")
        BADGR_BASE_URL: str = Field(default="https://aibadgr.com/v1", description="Badgr control API")
        ENDPOINT_URL: str = Field(default="", description="openai_base_url of a served Badgr model, to chat with it")
        ENDPOINT_API_KEY: str = Field(default="", description="Key for the endpoint; defaults to the Badgr key")
        MAX_PRICE_PER_HOUR: float = Field(default=2.0, description="Hard USD/hr cap on every deploy")
        MAX_RUNTIME_MINUTES: int = Field(default=60, description="Longest runtime a deploy may request")

    def __init__(self):
        self.valves = self.Valves()

    # -- HTTP -------------------------------------------------------------

    def _open(self, method: str, url: str, key: str, body=None, timeout: int = 60):
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310

    def _api(self, method: str, path: str, body=None):
        if not self.valves.BADGR_API_KEY:
            raise ValueError("Set BADGR_API_KEY in this function's valves.")
        base = self.valves.BADGR_BASE_URL.rstrip("/")
        with self._open(method, f"{base}{path}", self.valves.BADGR_API_KEY, body) as resp:
            return json.loads(resp.read().decode() or "{}")

    def _endpoint_key(self) -> str:
        return self.valves.ENDPOINT_API_KEY or self.valves.BADGR_API_KEY

    # -- model list --------------------------------------------------------

    def pipes(self) -> list:
        models = [{"id": CONTROL_ID, "name": "Badgr: control"}]
        if self.valves.ENDPOINT_URL:
            try:
                with self._open("GET", f"{self.valves.ENDPOINT_URL.rstrip('/')}/models", self._endpoint_key(), timeout=15) as r:
                    for m in json.loads(r.read().decode()).get("data", []):
                        models.append({"id": m["id"], "name": f"Badgr: {m['id']}"})
            except (urllib.error.URLError, ValueError, KeyError):
                pass  # endpoint stopped or unreachable: the control model still works
        return models

    # -- chat --------------------------------------------------------------

    def pipe(self, body: dict, __user__: dict | None = None) -> Union[str, Generator, Iterator]:
        model = str(body.get("model", ""))
        model = model.split(".", 1)[1] if "." in model and model.startswith(("badgr_pipe.", "badgr.")) else model
        if model == CONTROL_ID:
            return self._control(body)
        return self._chat(model, body)

    def _chat(self, model: str, body: dict):
        if not self.valves.ENDPOINT_URL:
            return "No endpoint configured. Deploy a model with the Badgr: control model, then set ENDPOINT_URL."
        payload = {**body, "model": model}
        stream = bool(body.get("stream"))
        try:
            resp = self._open("POST", f"{self.valves.ENDPOINT_URL.rstrip('/')}/chat/completions", self._endpoint_key(), payload, timeout=300)
        except urllib.error.HTTPError as err:
            return f"Badgr endpoint error {err.code}: {err.read().decode(errors='replace')[:300]}"
        except urllib.error.URLError as err:
            return f"Could not reach the Badgr endpoint (it may have stopped): {err.reason}"
        if not stream:
            with resp:
                data = json.loads(resp.read().decode())
            return data["choices"][0]["message"]["content"]
        return self._stream(resp)

    @staticmethod
    def _stream(resp) -> Generator:
        with resp:
            for raw in resp:
                line = raw.decode().strip()
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    return
                try:
                    delta = json.loads(chunk)["choices"][0].get("delta", {}).get("content")
                except (ValueError, KeyError, IndexError):
                    continue
                if delta:
                    yield delta

    # -- control commands --------------------------------------------------

    def _control(self, body: dict) -> str:
        text = ""
        for m in reversed(body.get("messages", [])):
            if m.get("role") == "user":
                text = m.get("content") if isinstance(m.get("content"), str) else ""
                break
        parts = text.strip().split()
        cmd, args = (parts[0].lower(), parts[1:]) if parts else ("", [])
        try:
            if cmd == "deploy":
                return self._deploy(args)
            if cmd == "status":
                deps = self._api("GET", "/deployments").get("deployments", [])
                if not deps:
                    return "No active deployments."
                return "\n".join(f"- {d.get('deployment_id')}: {d.get('model') or d.get('name')} ({d.get('status')})" for d in deps)
            if cmd == "check" and args:
                dep = self._dep_id(args[0])
                state = self._api("GET", f"/deployments/{dep}")
                lines = (self._api("GET", f"/deployments/{dep}/logs").get("logs") or [])[-20:]
                return f"Status: {state.get('status')}\n\n" + "\n".join(lines)
            if cmd == "stop" and args:
                dep = self._dep_id(args[0])
                self._api("DELETE", f"/deployments/{dep}")
                return f"Stopped {dep}. Billing has ended."
        except urllib.error.HTTPError as err:
            return f"Badgr error {err.code}: {err.read().decode(errors='replace')[:300]}"
        except (urllib.error.URLError, ValueError) as err:
            return f"Error: {err}"
        return HELP

    @staticmethod
    def _dep_id(value: str) -> str:
        if not value or value in (".", "..") or "/" in value or "\\" in value:
            raise ValueError("Invalid deployment id.")
        return urllib.parse.quote(value, safe="")

    def _deploy(self, args: list) -> str:
        if len(args) < 2 or not args[1].isdigit() or int(args[1]) <= 0:
            return "Usage: `deploy <hf-model-id> <minutes> [gpu]`. A runtime in minutes is required."
        minutes = min(int(args[1]), self.valves.MAX_RUNTIME_MINUTES)
        gpu = (args[2] if len(args) > 2 else "L40S").upper().replace("-", "_")
        dep = self._api("POST", "/serve", {
            "model": args[0],
            "gpu": gpu,
            "max_price_per_hour": self.valves.MAX_PRICE_PER_HOUR,
            "max_runtime_minutes": minutes,
        })
        url = dep.get("openai_base_url") or dep.get("endpoint_url") or "(pending)"
        return (f"Deploying `{args[0]}` as {dep.get('deployment_id')} for up to {minutes} minutes.\n"
                f"Endpoint: {url}\nSet ENDPOINT_URL to that value in this function's valves to chat with it. "
                f"Use `stop {dep.get('deployment_id')}` when done.")
