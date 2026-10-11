create table if not exists public.automation_configs (
  proposal_id text primary key
    references public.automation_candidates (proposal_id) on delete cascade,
  template_type text not null default 'github_pr_merged_to_slack',
  allowed_repository text not null,
  slack_channel_id text not null,
  operational_status text not null default 'setup_required',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  constraint automation_configs_template_type_check
    check (template_type in ('github_pr_merged_to_slack')),
  constraint automation_configs_operational_status_check
    check (operational_status in ('setup_required')),
  constraint automation_configs_repository_format_check
    check (allowed_repository ~ '^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$'),
  constraint automation_configs_channel_id_format_check
    check (slack_channel_id ~ '^[CG][A-Z0-9]{8,}$')
);

alter table public.automation_configs enable row level security;
revoke all on table public.automation_configs from anon, authenticated;

notify pgrst, 'reload schema';
