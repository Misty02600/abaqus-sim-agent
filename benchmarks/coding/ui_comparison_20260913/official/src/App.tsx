import Chat from './Chat.tsx'
import { AppSidebar } from './components/app-sidebar.tsx'
import { ThemeProvider } from './components/theme-provider.tsx'
import { SidebarProvider } from './components/ui/sidebar.tsx'
import { Toaster } from './components/ui/sonner.tsx'
import { cn } from './lib/utils.ts'
import { useHostRun } from './host-api.ts'
import { HostPanel } from './components/host-panel.tsx'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

const queryClient = new QueryClient()

export default function App() {
  const route = /^\/official\/([^/]+)\/?$/.exec(window.location.pathname)
  const runId = route ? decodeURIComponent(route[1]) : null
  const host = useHostRun(runId)

  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider defaultTheme="system" storageKey="pydantic-chat-ui-theme">
        <SidebarProvider defaultOpen>
          <AppSidebar runs={host.runs} runId={runId} />

          <div className="flex flex-col justify-center flex-1 h-screen overflow-hidden">
            <div
              className={cn(
                'flex flex-col max-w-4xl mx-auto relative w-full basis-[100vh] overflow-hidden',
                'has-[.stick-to-bottom:empty]:overflow-visible has-[.stick-to-bottom:empty]:basis-[0px] transition-[flex-basis] duration-200',
              )}
            >
              <HostPanel
                snapshot={host.snapshot}
                runId={runId}
                refresh={host.refresh}
                loadError={host.error}
                connection={host.connection}
              />
              <Chat messages={host.snapshot?.messages.vercel ?? []} />
            </div>
          </div>
        </SidebarProvider>
      </ThemeProvider>
      <Toaster richColors />
    </QueryClientProvider>
  )
}
