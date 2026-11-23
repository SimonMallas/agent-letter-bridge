# Using Agent Letter Bridge

## What it does

You message your AI agent from your phone, in Telegram, Slack or Discord. The
agent is the one already working on your computer. It receives your message as
a letter, does the work, and can answer you in the same chat.

## Setting it up

Most people let the agent do it.

1. Open your agent on your computer.
2. Copy this and give it to that agent:

```text
Set up Agent Letter Bridge from https://github.com/SimonMallas/agent-letter-bridge. Clone that repository if you do not already have this checkout. Do not install a published package instead. Read docs/agent-install.md and follow it. Do not read the setup terminal. I will paste the key.
```

3. The one part only you can do on Telegram is making a bot: message **@BotFather**, send `/newbot`, choose a name, and copy the code it gives you. Paste that code into the terminal the agent opened. Do not send it in a chat, and do not paste it into the agent's conversation. Slack and Discord need a few more steps; they have their own guides.
4. Send your new bot a message, for example “hello”. If the setup terminal asks which chat is yours, answer it there. If it asks the name of the agent, answer that there too. If it asks for a folder, a path, or a helper path, stop and tell the agent. Do not answer that.
5. The agent tells you when the test message has arrived. You are done.

If you would rather do it by hand, follow [the full installation guide](../INSTALL.md).

## Using it

- **Send a message.** Type or dictate it to your bot, as you would to a
  person. When your computer and the agent are running, it usually arrives
  within a few seconds. A busy agent or a network problem can take longer.
- **Get an answer.** The agent can reply in the same chat.
- **Stay in the same thread.** Reply to the message you mean. On Telegram, press
  and hold it, then tap **Reply**. In Slack use **Reply in thread**. In Discord
  use **Reply**. Your message joins that thread.
- **Start a new thread.** Send a message without Reply. It still arrives; a new
  thread is not a mistake.
- **Photos.** Ask your agent whether your setup handles photos. For the agent
  to send one back, you must first approve a folder it may send from.

## Things to know

- Your computer must be on and the agent running. Telegram normally keeps bot
  updates for about a day. Slack and Discord have different recovery limits,
  so turn the computer back on soon rather than relying on an old message.
- Only the people and chats allowed at setup can reach your agent. Everyone
  else is ignored. If you allow a group, everyone in that group can reach it.
- The agent can start a message to you only if you give it permission. The
  default limit is 30 started messages an hour and 300 a day. Answers to your
  messages are not counted.
- Agent Letter Bridge runs no relay server in the middle. Messages travel
  through Telegram, Slack or Discord, and your computer connects directly to
  that platform.
- Every message is kept on your computer as a small file, so the agent can
  look back at what you said.

## If something is wrong

- **No answer?** Check the computer is on, then ask the agent in its own
  window: “check the bridge”.
- **Changed phones?** Sign in with the same chat account and it carries on. A
  different account has to be allowed separately.
- **Want to stop?** Tell the agent: “stop the bridge”.
