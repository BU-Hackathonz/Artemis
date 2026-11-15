// Dev-server-only route: POST /api/claude runs one Claude turn for the liquid prototype.
//
// The API key stays in this Node process (read from client/.env.local, which is gitignored);
// the browser only ever talks to this route. Prototype use only: Artemis's real model calls
// belong in the Python core (server/artemis/agent/).
//
// Claude can answer in text or propose one action per turn through a tool:
//   propose_file_changes  write files in the user's project (in-browser, after approval)
//   propose_run           run a Python file (in a browser sandbox, after approval)
// The browser shows the approval, does what was approved, and sends the result back as a
// tool_result in the next request. Nothing happens on this machine.
//
// Request:  { messages: BetaMessageParam[] }  (the whole conversation, assistant turns verbatim)
// Response: server-sent events, each `data: <json>`:
//   {type: "text", text}                                  a piece of the reply, as it streams
//   {type: "turn", content, stopReason, model, invalid?}   the finished assistant turn; append
//        `content` to the conversation unchanged. `invalid` = a tool call whose input didn't
//        validate: don't act on it, answer it with an is_error tool_result.
//   {type: "error", message, retry?}                       something went wrong

import Anthropic from '@anthropic-ai/sdk'
import type { Plugin } from 'vite'
import type { IncomingMessage, ServerResponse } from 'node:http'

const MODEL = 'claude-opus-5-5'

const SYSTEM = `You are Artemis, a voice-first coding assistant for blind and low-vision developers.
Your replies are read aloud by a screen reader or speech engine, so:
- Answer in plain sentences. No markdown tables, no headings, no decorative symbols.
- Be brief: two to five sentences unless the user asks for more.
- Say what code does before showing it.

You work on the user's small Python project, which lives in their browser. To change it, call
propose_file_changes; to run a file, call propose_run. The user reviews and approves or rejects
every proposal, and you get the result back. One proposal at a time. If a proposal is rejected,
don't repeat it: ask what they'd like instead. Programs run in Pyodide (Python in the browser):
the standard library works, there is no network, no input() and no third-party packages. After a
run, tell the user the outcome in a sentence.`

const TOOLS: Anthropic.Beta.BetaTool[] = [
  {
    name: 'propose_file_changes',
    description: 'Propose creating or replacing files in the user\'s project. Call this whenever the user asks you to write, create, change or fix code. The user must approve before anything is written.',
    eager_input_streaming: true,
    strict: true,
    input_schema: {
      type: 'object',
      additionalProperties: false,
      required: ['summary', 'files'],
      properties: {
        summary: { type: 'string', description: 'One sentence the user hears in the approval: what will change and why.' },
        files: {
          type: 'array',
          description: 'Every file to create or replace, with its complete new contents.',
          items: {
            type: 'object',
            additionalProperties: false,
            required: ['path', 'content'],
            properties: {
              path: { type: 'string', description: 'Relative path, e.g. hello.py' },
              content: { type: 'string', description: 'The full file contents' },
            },
          },
        },
      },
    },
  },
  {
    name: 'propose_run',
    description: 'Propose running a Python file from the project. Call this when the user asks to run or test their program, or after an approved change when running it is the obvious next step. The user must approve first.',
    eager_input_streaming: true,
    strict: true,
    input_schema: {
      type: 'object',
      additionalProperties: false,
      required: ['entry', 'summary'],
      properties: {
        entry: { type: 'string', description: 'The Python file to run, e.g. hello.py' },
        summary: { type: 'string', description: 'One sentence the user hears in the approval.' },
      },
    },
  },
]

// eager_input_streaming means the API doesn't validate tool input: check it here before the
// browser acts on it.
function validToolInput(block: Anthropic.Beta.BetaToolUseBlock): boolean {
  const input = block.input as Record<string, unknown> | null
  if (!input || typeof input !== 'object' || typeof input.summary !== 'string') return false
  if (block.name === 'propose_run') return typeof input.entry === 'string' && input.entry.trim() !== ''
  if (block.name === 'propose_file_changes') {
    return Array.isArray(input.files) && input.files.length > 0 && input.files.every((f) =>
      f && typeof f === 'object' && typeof f.path === 'string' && f.path.trim() !== '' && !f.path.includes('..') && typeof f.content === 'string')
  }
  return false
}

function readBody(req: IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    let data = ''
    req.on('data', (chunk) => {
      data += chunk
      if (data.length > 2_000_000) reject(new Error('Request too large'))
    })
    req.on('end', () => resolve(data))
    req.on('error', reject)
  })
}

function isMessageList(value: unknown): value is Anthropic.Beta.BetaMessageParam[] {
  return Array.isArray(value) && value.length > 0 && value.every((m) =>
    m && typeof m === 'object' && (m.role === 'user' || m.role === 'assistant') && (typeof m.content === 'string' || Array.isArray(m.content)))
}

export function claudeDevRoute(env: Record<string, string>): Plugin {
  return {
    name: 'artemis-claude-dev-route',
    apply: 'serve', // never part of a build
    configureServer(server) {
      server.middlewares.use('/api/claude', async (req: IncomingMessage, res: ServerResponse) => {
        if (req.method !== 'POST') { res.statusCode = 405; res.end(); return }
        const send = (event: object) => res.write(`data: ${JSON.stringify(event)}\n\n`)
        res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', Connection: 'keep-alive' })

        if (!env.ANTHROPIC_API_KEY) {
          send({ type: 'error', message: 'No API key: add ANTHROPIC_API_KEY to client/.env.local and restart the dev server.' })
          res.end(); return
        }
        let messages: Anthropic.Beta.BetaMessageParam[]
        try {
          const body = JSON.parse(await readBody(req)) as { messages?: unknown }
          if (!isMessageList(body.messages)) throw new Error('Expected {messages: [{role, content}]}')
          messages = body.messages
        } catch (e) {
          send({ type: 'error', message: (e as Error).message }); res.end(); return
        }

        const client = new Anthropic({
          apiKey: env.ANTHROPIC_API_KEY,
          // Needed for a key that isn't scoped to one workspace.
          defaultHeaders: env.ANTHROPIC_WORKSPACE_ID ? { 'anthropic-workspace-id': env.ANTHROPIC_WORKSPACE_ID } : undefined,
        })
        const stream = client.beta.messages.stream({
          model: MODEL,
          max_tokens: 32000,
          system: SYSTEM,
          messages,
          tools: TOOLS,
          tool_choice: { type: 'auto', disable_parallel_tool_use: true }, // one proposal at a time
          output_config: { effort: 'low' }, // short spoken answers: keep it quick
          // If Claude Opus 5.5 declines, the API retries on a fallback model inside the same call.
          betas: ['server-side-fallback-2026-07-01'],
          fallbacks: 'default',
        })
        req.on('close', () => { if (!res.writableEnded) stream.abort() })

        try {
          // Consume the stream and read the final message together: with eager tool input, a
          // tool input the SDK can't parse at all rejects here.
          for await (const event of stream) {
            if (event.type === 'content_block_delta' && event.delta.type === 'text_delta') send({ type: 'text', text: event.delta.text })
          }
          const final = await stream.finalMessage()
          if (final.stop_reason === 'refusal') {
            send({ type: 'error', message: 'Claude declined to answer that request.' })
          } else if (final.stop_reason === 'max_tokens' && final.content.some((b) => b.type === 'tool_use')) {
            send({ type: 'error', message: 'Claude\'s proposal was too long and got cut off. Try asking for a smaller change.' })
          } else {
            const bad = final.content.find((b): b is Anthropic.Beta.BetaToolUseBlock => b.type === 'tool_use' && !validToolInput(b))
            send({ type: 'turn', content: final.content, stopReason: final.stop_reason, model: final.model,
              invalid: bad ? { id: bad.id, raw: JSON.stringify(bad.input) } : undefined })
          }
        } catch (e) {
          if (stream.aborted) return
          let message = 'Request failed. Check the terminal running the dev server.'
          if (e instanceof Anthropic.AuthenticationError) message = 'The API key was rejected (401). Check ANTHROPIC_API_KEY in client/.env.local.'
          else if (e instanceof Anthropic.PermissionDeniedError) message = 'This key may not use that model or workspace (403).'
          else if (e instanceof Anthropic.RateLimitError) message = 'Rate limited. Try again in a moment.'
          else if (e instanceof Anthropic.BadRequestError) message = `Bad request: ${e.message}`
          else if (e instanceof Anthropic.APIError) message = `Claude API error ${e.status}: ${e.message}`
          else message = 'Claude\'s proposal came through garbled. Please send that again.' // unparseable tool input
          server.config.logger.error(`[api/claude] ${String(e)}`)
          send({ type: 'error', message })
        }
        res.end()
      })
    },
  }
}
