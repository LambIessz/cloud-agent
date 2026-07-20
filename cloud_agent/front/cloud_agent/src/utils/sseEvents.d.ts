export interface AssistantStreamState {
  content: string
  status: string
  done: boolean
  schemaVersion: string
}

export interface SsePayload {
  schema_version?: string
  event_type?: string
  stream_mode?: string
  step?: unknown
  route_to?: unknown
  tool_name?: unknown
  content?: unknown
  final?: unknown
  done?: unknown
}

export const SSE_SCHEMA_VERSION: string
export function createAssistantStreamState(): AssistantStreamState
export function formatAgentStep(step: unknown): string
export function formatToolName(toolName: unknown): string
export function applySsePayload(
  state: AssistantStreamState,
  payload: SsePayload | unknown,
): AssistantStreamState
