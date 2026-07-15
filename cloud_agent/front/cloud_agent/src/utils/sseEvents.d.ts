export interface AssistantStreamState {
  content: string
  status: string
  done: boolean
}

export interface SsePayload {
  event_type?: string
  stream_mode?: string
  step?: unknown
  content?: unknown
  done?: unknown
}

export function createAssistantStreamState(): AssistantStreamState
export function formatAgentStep(step: unknown): string
export function applySsePayload(
  state: AssistantStreamState,
  payload: SsePayload | unknown,
): AssistantStreamState
