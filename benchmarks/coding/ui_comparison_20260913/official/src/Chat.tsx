import { Conversation, ConversationContent, ConversationScrollButton } from '@/components/ai-elements/conversation'
import { Source, Sources, SourcesContent, SourcesTrigger } from '@/components/ai-elements/sources'
import { HiddenToolsGroup } from '@/components/hidden-tools-group'
import { ToolCallGroup } from '@/components/tool-call-group'
import { ToolFiltersProvider } from '@/contexts/tool-filters'
import type { UIDataTypes, UIMessage, UIMessagePart, UITools } from 'ai'
import type { ReactNode } from 'react'
import { Part } from './Part'
import { toolNameOfPart } from '@/lib/tool-filters'
import { groupParts } from '@/lib/tool-grouping'

const ChatInner = ({ messages }: { messages: UIMessage[] }) => {
  return (
    <Conversation className="h-full min-h-0">
      <ConversationContent>
        {messages.map((message) => (
          <div key={message.id} className={message.role === 'user' ? 'group/user-message' : undefined}>
            {message.role === 'assistant' && message.parts.filter((part) => part.type === 'source-url').length > 0 && (
              <Sources>
                <SourcesTrigger count={message.parts.filter((part) => part.type === 'source-url').length} />
                {message.parts
                  .filter((part) => part.type === 'source-url')
                  .map((part, i) => (
                    <SourcesContent key={`${message.id}-${i}`}>
                      <Source href={part.url} title={part.url} />
                    </SourcesContent>
                  ))}
              </Sources>
            )}
            {renderMessageParts(
              message,
              (part, i) => (
                <Part
                  key={`${message.id}-${i}`}
                  part={part}
                  message={message}
                  status="ready"
                  index={i}
                  regen={() => {
                    /* Read-only rendering: retry controls are not mounted. */
                  }}
                  lastMessage={message.id === messages.at(-1)?.id}
                  onApprovalResponse={() => {
                    /* All user input belongs to the host question panel. */
                  }}
                  readOnly
                />
              ),
              () => false,
            )}
          </div>
        ))}
        {messages.length === 0 && (
          <p className="text-sm text-muted-foreground">Host conversation history will appear here.</p>
        )}
      </ConversationContent>
      <ConversationScrollButton />
    </Conversation>
  )
}

const Chat = ({ messages }: { messages: UIMessage[] }) => (
  <ToolFiltersProvider defaults={[]}>
    <ChatInner messages={messages} />
  </ToolFiltersProvider>
)

export default Chat
// Walk a message's parts and render them, collapsing two kinds of consecutive
// runs into a single element: filtered tool parts into a `HiddenToolsGroup`, and
// runs of >=2 calls to the same (non-filtered) tool into a `ToolCallGroup`.
// `renderPart` is the per-part renderer (returns a `<Part>` element); grouping
// is message-level so it lives here rather than in `Part`. Lone calls and
// non-tool parts render unchanged.
function renderMessageParts(
  message: UIMessage,
  renderPart: (part: UIMessagePart<UIDataTypes, UITools>, index: number) => ReactNode,
  isFiltered: (toolName: string) => boolean,
): ReactNode[] {
  const descriptors = message.parts.map((part) => {
    const toolName = toolNameOfPart(part)
    // Questions keep their individual waiting/answered badges instead of a generic running group.
    if (toolName === 'ask_user') return { toolName: null, filtered: false }
    return { toolName, filtered: toolName !== null && isFiltered(toolName) }
  })

  return groupParts(descriptors).map((run) => {
    if (run.kind === 'single') {
      return renderPart(message.parts[run.index], run.index)
    }
    if (run.kind === 'hidden') {
      return (
        <HiddenToolsGroup
          key={`hidden-${message.id}-${run.indices[0]}`}
          toolNames={run.indices.map((i) => toolNameOfPart(message.parts[i]) ?? '')}
        >
          {run.indices.map((i) => renderPart(message.parts[i], i))}
        </HiddenToolsGroup>
      )
    }
    return (
      <ToolCallGroup
        key={`tool-group-${message.id}-${run.indices[0]}`}
        toolName={run.toolName}
        states={run.indices.map((i) => partState(message.parts[i]))}
      >
        {run.indices.map((i) => renderPart(message.parts[i], i))}
      </ToolCallGroup>
    )
  })
}

// A tool part's lifecycle state (e.g. `output-available`). Non-tool parts have
// no state; the grouping pass never asks for theirs.
function partState(part: UIMessagePart<UIDataTypes, UITools>): string {
  return 'state' in part && typeof part.state === 'string' ? part.state : ''
}
