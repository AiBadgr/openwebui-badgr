# Badgr Pipe for Open WebUI

One Open WebUI Function that adds two kinds of models:

- **Badgr: control**: type `deploy <hf-model-id> <minutes> [gpu]`, `status`, `check <id>` or `stop <id>` in a chat.
- **Badgr: \<model\>**: chat with a model you served, streamed through the endpoint's OpenAI-compatible API.

## Install

Admin Panel, Functions, New Function, paste `badgr_pipe.py`, save, then set the valves: `BADGR_API_KEY`, and after a deploy `ENDPOINT_URL`.

## Spending limits

Every deploy needs a runtime in minutes, is clamped to the `MAX_RUNTIME_MINUTES` valve, and uses the `MAX_PRICE_PER_HOUR` valve as its price cap. A served endpoint stops itself when its runtime ends; chats then report that it may have stopped.

## Limits

Tested with Open WebUI's Function contract (`Valves`, `pipes()`, `pipe()`) via unit tests; installed and run in a live Open WebUI 0.12.0: the Function loads, "Badgr: control" appears, `help` returns the command list and `status` without a key returns the valve error. Calls against the live Badgr API are not tested. Posting it to the Open WebUI community directory is a manual step from an openwebui.com account. Valves are stored by Open WebUI, so scope the Badgr key accordingly.

## Develop

```bash
pip install pydantic pytest && python -m pytest
```
