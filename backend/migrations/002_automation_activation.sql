begin;

alter table public.automation_configs
  add column if not exists event_source_mode text not null default 'simulated',
  add column if not exists activated_at timestamptz,
  add column if not exists disabled_at timestamptz,
  add column if not exists activated_slack_account_id text,
  add column if not exists activated_execution_flow_id text;

alter table public.automation_configs
  drop constraint if exists automation_configs_operational_status_check;
alter table public.automation_configs
  add constraint automation_configs_operational_status_check
  check (operational_status in ('setup_required', 'active', 'disabled'));

alter table public.automation_configs
  drop constraint if exists automation_configs_event_source_mode_check;
alter table public.automation_configs
  add constraint automation_configs_event_source_mode_check
  check (event_source_mode in ('simulated'));

alter table public.automation_configs
  drop constraint if exists automation_configs_active_snapshot_check;
alter table public.automation_configs
  add constraint automation_configs_active_snapshot_check
  check (
    operational_status <> 'active'
    or (
      activated_at is not null
      and activated_slack_account_id is not null
      and activated_execution_flow_id is not null
    )
  );

commit;

notify pgrst, 'reload schema';
