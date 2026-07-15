import http from 'node:http'
import process from 'node:process'

function parsePort(argv) {
  const index = argv.indexOf('--port')
  if (index >= 0 && argv[index + 1]) {
    return Number(argv[index + 1])
  }
  return Number(process.env.PLAYWRIGHT_BACKEND_PORT || 15100)
}

function readBody(request) {
  return new Promise((resolve, reject) => {
    let body = ''
    request.setEncoding('utf8')
    request.on('data', (chunk) => {
      body += chunk
    })
    request.on('end', () => resolve(body))
    request.on('error', reject)
  })
}

function writeJson(response, statusCode, payload) {
  response.writeHead(statusCode, {
    'Content-Type': 'application/json; charset=utf-8',
  })
  response.end(JSON.stringify(payload))
}

async function handleChat(request, response) {
  const rawBody = await readBody(request)
  const body = rawBody ? JSON.parse(rawBody) : {}
  const query = typeof body.query === 'string' ? body.query : ''
  const content = `browser smoke reply: ${query}`

  response.writeHead(200, {
    'Content-Type': 'text/event-stream; charset=utf-8',
    'Cache-Control': 'no-cache',
    Connection: 'keep-alive',
  })
  response.write(`data: ${JSON.stringify({ event_type: 'message_delta', content })}\n\n`)
  response.write(`data: ${JSON.stringify({
    event_type: 'done',
    done: true,
    request_id: 'browser_smoke_request',
  })}\n\n`)
  response.end()
}

const port = parsePort(process.argv)
const server = http.createServer((request, response) => {
  if (request.method === 'GET' && request.url === '/readyz') {
    writeJson(response, 200, { status: 'ok' })
    return
  }

  if (request.method === 'OPTIONS') {
    response.writeHead(204, {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Headers': 'content-type,x-user-id,x-tenant-id',
      'Access-Control-Allow-Methods': 'POST,OPTIONS',
    })
    response.end()
    return
  }

  if (request.method === 'POST' && request.url === '/api/chat') {
    handleChat(request, response).catch((error) => {
      writeJson(response, 500, { error: error instanceof Error ? error.message : String(error) })
    })
    return
  }

  writeJson(response, 404, { error: 'not found' })
})

server.listen(port, '127.0.0.1', () => {
  console.log(`[mock-chat-sse] ready on http://127.0.0.1:${port}`)
})
