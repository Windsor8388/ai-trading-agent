-- Durable, server-only audit persistence for the Trading Intelligence paper lifecycle.
--
-- Tables use an `ati_` namespace in public to avoid coupling to the pre-existing
-- application schema. RLS remains enabled and no browser roles receive a policy;
-- only a trusted backend using Supabase's service_role may invoke the RPC below.

create table if not exists public.ati_order_intents (
    client_order_id text primary key,
    request_fingerprint text not null unique,
    intent jsonb not null,
    first_observed_at timestamptz not null,
    created_at timestamptz not null default timezone('utc', now()),
    constraint ati_order_intents_client_order_id_nonempty
        check (length(btrim(client_order_id)) > 0 and client_order_id = btrim(client_order_id)),
    constraint ati_order_intents_request_fingerprint_sha256
        check (request_fingerprint ~ '^[0-9a-f]{64}$'),
    constraint ati_order_intents_intent_object
        check (jsonb_typeof(intent) = 'object')
);

create table if not exists public.ati_order_states (
    client_order_id text primary key references public.ati_order_intents(client_order_id) on delete restrict,
    last_event_sequence integer not null,
    order_status text not null,
    venue_order_id text,
    filled_quantity numeric not null default 0,
    rejection_reason text,
    updated_at timestamptz not null,
    constraint ati_order_states_sequence_positive check (last_event_sequence >= 1),
    constraint ati_order_states_filled_quantity_nonnegative check (filled_quantity >= 0)
);

create table if not exists public.ati_order_events (
    event_id text primary key,
    client_order_id text not null references public.ati_order_intents(client_order_id) on delete restrict,
    sequence integer not null,
    occurred_at timestamptz not null,
    event_type text not null,
    detail text not null,
    order_status text not null,
    venue_order_id text,
    filled_quantity numeric not null,
    rejection_reason text,
    event_payload jsonb not null,
    recorded_at timestamptz not null default timezone('utc', now()),
    constraint ati_order_events_sequence_positive check (sequence >= 1),
    constraint ati_order_events_filled_quantity_nonnegative check (filled_quantity >= 0),
    constraint ati_order_events_payload_object check (jsonb_typeof(event_payload) = 'object'),
    constraint ati_order_events_client_sequence_unique unique (client_order_id, sequence)
);

create index if not exists ati_order_events_client_occurred_idx
    on public.ati_order_events (client_order_id, occurred_at desc);
create index if not exists ati_order_events_recorded_idx
    on public.ati_order_events (recorded_at desc);

alter table public.ati_order_intents enable row level security;
alter table public.ati_order_states enable row level security;
alter table public.ati_order_events enable row level security;

revoke all on table public.ati_order_intents, public.ati_order_states, public.ati_order_events from anon, authenticated;

create or replace function public.ati_record_order_event(
    p_client_order_id text,
    p_request_fingerprint text,
    p_intent jsonb,
    p_sequence integer,
    p_occurred_at timestamptz,
    p_event_type text,
    p_detail text,
    p_order_status text,
    p_venue_order_id text,
    p_filled_quantity numeric,
    p_rejection_reason text,
    p_event_payload jsonb
)
returns jsonb
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
    v_existing_fingerprint text;
    v_existing_intent jsonb;
    v_existing_payload jsonb;
    v_last_sequence integer;
    v_event_id text;
begin
    if p_client_order_id is null or length(btrim(p_client_order_id)) = 0 or p_client_order_id <> btrim(p_client_order_id) then
        raise exception 'client_order_id must be a non-empty trimmed string';
    end if;
    if p_request_fingerprint is null or p_request_fingerprint !~ '^[0-9a-f]{64}$' then
        raise exception 'request_fingerprint must be a lowercase SHA-256 hex digest';
    end if;
    if p_intent is null or jsonb_typeof(p_intent) <> 'object' or p_event_payload is null or jsonb_typeof(p_event_payload) <> 'object' then
        raise exception 'intent and event_payload must be JSON objects';
    end if;
    if p_sequence is null or p_sequence < 1 or p_occurred_at is null then
        raise exception 'sequence and occurred_at are required';
    end if;
    if p_event_type is null or length(btrim(p_event_type)) = 0 or p_detail is null or p_order_status is null or length(btrim(p_order_status)) = 0 then
        raise exception 'event type, detail, and order status are required';
    end if;
    if p_filled_quantity is null or p_filled_quantity < 0 then
        raise exception 'filled quantity must be non-negative';
    end if;

    insert into public.ati_order_intents (
        client_order_id, request_fingerprint, intent, first_observed_at
    ) values (
        p_client_order_id, p_request_fingerprint, p_intent, p_occurred_at
    ) on conflict (client_order_id) do nothing;

    -- The intent row is the per-order serialization lock. It prevents races from
    -- creating duplicate events or skipping an event sequence.
    select request_fingerprint, intent
      into v_existing_fingerprint, v_existing_intent
      from public.ati_order_intents
     where client_order_id = p_client_order_id
       for update;

    if v_existing_fingerprint is distinct from p_request_fingerprint or v_existing_intent is distinct from p_intent then
        raise exception 'client_order_id is already bound to a different immutable order intent';
    end if;

    v_event_id := p_client_order_id || ':' || p_sequence::text;
    select event_payload
      into v_existing_payload
      from public.ati_order_events
     where event_id = v_event_id;

    if found then
        if v_existing_payload is distinct from p_event_payload then
            raise exception 'event retry payload differs from durable event';
        end if;
        return jsonb_build_object('outcome', 'duplicate', 'event_id', v_event_id, 'sequence', p_sequence);
    end if;

    select last_event_sequence
      into v_last_sequence
      from public.ati_order_states
     where client_order_id = p_client_order_id;

    if v_last_sequence is null and p_sequence <> 1 then
        raise exception 'first durable event must have sequence 1';
    end if;
    if v_last_sequence is not null and p_sequence <> v_last_sequence + 1 then
        raise exception 'event sequence is not contiguous';
    end if;

    insert into public.ati_order_events (
        event_id, client_order_id, sequence, occurred_at, event_type, detail,
        order_status, venue_order_id, filled_quantity, rejection_reason, event_payload
    ) values (
        v_event_id, p_client_order_id, p_sequence, p_occurred_at, p_event_type, p_detail,
        p_order_status, p_venue_order_id, p_filled_quantity, p_rejection_reason, p_event_payload
    );

    insert into public.ati_order_states (
        client_order_id, last_event_sequence, order_status, venue_order_id,
        filled_quantity, rejection_reason, updated_at
    ) values (
        p_client_order_id, p_sequence, p_order_status, p_venue_order_id,
        p_filled_quantity, p_rejection_reason, p_occurred_at
    ) on conflict (client_order_id) do update set
        last_event_sequence = excluded.last_event_sequence,
        order_status = excluded.order_status,
        venue_order_id = excluded.venue_order_id,
        filled_quantity = excluded.filled_quantity,
        rejection_reason = excluded.rejection_reason,
        updated_at = excluded.updated_at;

    return jsonb_build_object('outcome', 'recorded', 'event_id', v_event_id, 'sequence', p_sequence);
end;
$$;

revoke all on function public.ati_record_order_event(
    text, text, jsonb, integer, timestamptz, text, text, text, text, numeric, text, jsonb
) from public, anon, authenticated;
grant execute on function public.ati_record_order_event(
    text, text, jsonb, integer, timestamptz, text, text, text, text, numeric, text, jsonb
) to service_role;

comment on function public.ati_record_order_event(
    text, text, jsonb, integer, timestamptz, text, text, text, text, numeric, text, jsonb
) is 'Atomic idempotent recorder for Trading Intelligence paper-order lifecycle events. Service role only.';
