import { CirclePlus, MessageCircle } from 'lucide-react'
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarTrigger,
} from '@/components/ui/sidebar'
import { TooltipProvider } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'
import { runPath, type RunEntry } from '@/host-api'
import { ModeToggle } from './mode-toggle'
import logoSvg from '../assets/logo.svg'

export function AppSidebar({ runs, runId }: { runs: RunEntry[]; runId: string | null }) {
  return (
    <TooltipProvider>
      <Sidebar collapsible="icon">
        <SidebarHeader>
          <SidebarTrigger className="ml-auto" />
          <div className="ml-2 flex items-center">
            <h1 className="text-l font-medium text-balance truncate whitespace-nowrap">
              <img src={logoSvg} alt="" className="inline h-4 mr-2 mb-1" />
              <span className="group-data-[state=collapsed]:invisible">Pydantic AI</span>
            </h1>
          </div>
        </SidebarHeader>
        <SidebarContent>
          <SidebarGroup>
            <SidebarMenu className="mb-2">
              <SidebarMenuItem>
                <SidebarMenuButton asChild tooltip="New task">
                  <a href="/official/">
                    <CirclePlus />
                    <span>New task</span>
                  </a>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
            <SidebarGroupContent>
              <SidebarMenu>
                {runs.map((run) => (
                  <SidebarMenuItem key={run.run_id} className="group/sidebar-menu-item">
                    <div className="flex items-center gap-1 h-auto">
                      <SidebarMenuButton asChild tooltip={run.goal} className="flex-1">
                        <a
                          href={runPath(run.run_id)}
                          aria-current={run.run_id === runId ? 'page' : undefined}
                          className={cn('h-auto flex items-start gap-2', { 'bg-accent': run.run_id === runId })}
                        >
                          <MessageCircle className="size-3 mt-1" />
                          <span className="flex flex-col items-start min-w-0">
                            <span className="truncate max-w-44">{run.goal}</span>
                            <span className="text-xs text-muted-foreground truncate max-w-44">
                              {run.run_id} · {run.status}
                            </span>
                          </span>
                        </a>
                      </SidebarMenuButton>
                    </div>
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        </SidebarContent>
        <SidebarFooter>
          <ModeToggle />
        </SidebarFooter>
      </Sidebar>
    </TooltipProvider>
  )
}
