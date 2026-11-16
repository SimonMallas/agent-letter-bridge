# Using Agent Letter Bridge

## What it does

You message your AI agent from your phone, in Telegram, Slack or Discord. The
agent is the one already working on your computer. It receives your message as
a letter, does the work, and can answer you in the same chat.

## Setting it up

Most people let the agent do it.

1. Open your agent on your computer.
2. Say: “Set up Agent Letter Bridge so I can message you from my phone on
   Telegram. Follow `docs/agent-install.md` and tell me each thing you need
   from me.”
3. The agent will ask you for the parts only you can do. On Telegram the main
   one is making a bot: message **@BotFather**, send `/newbot`, choose a name,
   and copy the code it gives you. Paste that code where the agent tells you.
   Do not send it in a chat. Slack and Discord need a few more steps; they have
   their own guides.
4. Send your new bot a message, for example “hello”, and answer any questions
   the agent asks.
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
