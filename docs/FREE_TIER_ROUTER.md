# Free tier router (FreeLLMAPI)

[FreeLLMAPI](https://github.com/tashfeenahmed/freellmapi) (MIT license) is a small server you
run on your own computer. You give it free API keys from many providers (Groq, Cerebras,
Google, Mistral, OpenRouter and about thirty more), and it gives you back ONE address and ONE
key. Each request goes to whichever free model has quota left. When one runs dry, the next
one answers.

GawdBot does not install or bundle it. FreeLLMAPI speaks the OpenAI format, so it plugs in
as an ordinary brain in `brains.toml`. Turn it off and nothing else changes.

## The deal with free tiers

Free is paid for some other way, usually with your data. Several free tiers say the provider
may use your prompts to train models, and some let people read them. Google's terms for its
unpaid API say it plainly: "Do not submit sensitive, confidential, or personal information to
the Unpaid Services." FreeLLMAPI itself describes the project as for personal experimentation.

So GawdBot has a switch for any brain like this: `shares_data = true`.

| What | Paid or local brain | Brain with `shares_data = true` |
|---|---|---|
| Your message and the conversation | Sent | Sent |
| Tool results in this run (file text, search results, command output) | Sent | Sent |
| Your saved memories (facts, preferences, decisions, deadlines) | Sent | **Never sent** |
| Embedding (turning memories into vectors for search) | Allowed | **Never used** |

The first two rows have to go, because the brain cannot answer without seeing the question.
So the rule of thumb is: **anything you type to a free brain, assume a stranger may read.**
Use it for low stakes work and as a backup, not for anything private.

If a paid brain fails and the chain falls back to a free one, the memories are still
withheld, automatically, on that one call. `/trace` shows the line
`memory withheld: free may share data with its provider` whenever it happens.

If you truly accept the trade, `MEMORY_TO_SHARED_BRAINS=true` in `.env` lets shared brains see
and embed memories. It is off by default and should stay off.

## Set it up on Windows

1. Download the Windows `.exe` installer from the
   [FreeLLMAPI releases page](https://github.com/tashfeenahmed/freellmapi/releases) and run it.
   It sits in the system tray and serves on `http://localhost:3001`.
2. Open its dashboard and add free keys for the providers you want. Rules for this step:
   * **One account per provider.** Making extra accounts to stack free quota breaks most
     providers' terms and can get you banned.
   * **Skip Cohere.** Cohere calls its free keys "evaluation keys" and caps them at 1,000
     calls a month, which an agent burns through fast.
   * **Think twice about Google, NVIDIA and GitHub Models.** They are generous, but read each
     one's free terms first. Google's unpaid tier is the one quoted above.
3. On the dashboard's **Keys** page, copy the unified key (it starts with `freellmapi-`).
   Store it in your vault, never in a file:
   ```powershell
   python main.py secrets set FREELLMAPI_KEY
   ```
4. In `brains.toml`, add the brain (the commented block at the bottom of
   `brains.example.toml` is the same thing):
   ```toml
   [brains.free]
   kind        = "openai_compat"
   base_url    = "http://localhost:3001/v1"
   model       = "auto"          # or "auto:fast" / "auto:smart"
   api_key     = "FREELLMAPI_KEY"
   shares_data = true
   ```
5. Put it LAST in your main chain, so it only answers when your real brains cannot:
   ```toml
   [chains]
   main = ["qwen", "free"]
   ```
   Leave it out of the `embedding` chain. GawdBot skips it there anyway.
6. Check it:
   ```powershell
   python main.py brain
   python main.py brain test
   ```
   `brain` shows `shares your data: memories withheld` next to it.

## Seeing which model really answered

With `model = "auto"`, the router picks a different model per request. GawdBot records the
model the response says answered (`served_model`) and uses it everywhere:

* `/trace` shows `free via groq/llama-3.3-70b` instead of just `free`.
* `/usage` groups tokens by the real model.
* Paperclip cost reports name the real model. Free brains have no price, so they are reported
  as unpriced, never as a made up number.
* If you ever allow embedding, vectors are tagged with the real embedding model, so vectors
  from two different models are never compared.

## Limits worth knowing

* Free models vary a lot at tool calling. If the agent fumbles tools on `auto`, try
  `auto:smart` or put one specific model id in `model`.
* Free tiers have rate limits and no promise of uptime. That is why it belongs at the end of
  a chain.
* FreeLLMAPI keeps your provider keys encrypted in its own local database. GawdBot never sees
  them; it only holds the one unified key, in the vault.
