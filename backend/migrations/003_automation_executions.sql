create table if not exists public.automation_executions (
  id uuid primary key default gen_random_uuid(),
  proposal_id text not null
    references public.automation_configs (proposal_id),
  source_event_id text not null,
  status text not null default 'claimed',
  event_source_mode text not null default 'simulated',
  execution_flow_id text not null,
  slack_channel_id text not null,
  allowed_repository text not null,
  outcome_summary text,
  evidence text,
  error text,
  claimed_at timestamptz not null default now(),
  finished_at timestamptz,

  constraint automation_executions_proposal_event_key
    unique (proposal_id, source_event_id),
  constraint automation_executions_status_check
    check (status in ('claimed', 'verified', 'unverified', 'unknown', 'failed')),
  constraint automation_executions_event_source_mode_check
    check (event_source_mode in ('simulated'))
);

alter table public.automation_executions enable row level security;
revoke all on table public.automation_executions from anon, authenticated;

notify pgrst, 'reload schema';
