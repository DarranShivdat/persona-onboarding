# Requirements audit — Sun Sep 27, 2026, 11:30pm PT

Brief (CLAUDE.md "What this is" / trial email): hosted conversational onboarding for Persona
that collects (1) agent name — by text, before the call; (2) the user's name; (3) a connected
Gmail; (4) one thing they need help with — via an adaptive text chat AND a browser "phone call"
that collects everything except the agent name. Must survive stress (hangups, refusals,
nonsense), feel like a conversation (not a form), steer back gently, and allow early graduation.

Status key: PASS · PARTIAL · FAIL. "Before" = Darran's live test 10:56pm; "Now" = agent
56ed4f5 deployed 11:24pm PT.

| # | Requirement | Before | Now | Evidence | Gap → owner |
|---|---|---|---|---|---|
| 1 | Agent name collected by text, before the call | PASS | PASS | flow.yaml `agent_name` node is text-only; voice nodes never ask it (`voice_tools`, test_voice_flows); chips Juno/Atlas/Surprise me (dq-04 e2e) | — |
| 2 | User's name | PARTIAL | PARTIAL | Collected on text + voice, but voice STT turned "Darran" into "Darren" with no read-back | Voice read-back + spell-it fallback, text confirm when unusual → **NAME-001** (running) |
| 3 | Connected Gmail (real OAuth) | PARTIAL | PARTIAL | smoke: OAuth start → accounts.google.com with the prod redirect_uri; callback + token storage covered by e2e/gmail-oauth mocks; Gmail filled only via OAuth (invariant 3). Not yet exercised end-to-end by a human on prod; home-screen Connect Gmail unverified | Live consent run in AUDIT-001 stops at Google (by design); home Connect Gmail → **GRAD-001**; needs a human consent pass Mon AM |
| 4 | One thing they need help with | PARTIAL | PASS | Before: "text my mom" overwrote "automating emails". Now: additions append ("Automating emails and texting my mom"), live probe 11:25pm | — |
| 5 | Text chat that adapts | **FAIL** | PASS | Before: prod text used FakeLlm (whole utterance = slot, fixed "Got it: X."). Now: Claude extraction (any order, corrections, questions) + reaction-only phrasing; live API test 11:16pm ("lets go with Nova" → agent_name Nova; "is my data safe?" answered, then steered back) | Latency ~2–3 s per text turn → LAT-001 |
| 6 | Browser phone call collecting everything but the agent name | PASS | PASS | Darran's call connected and captured name/need; hosted Playwright real-agent call PASS 11:26pm (desktop + mobile) | Connect ~6 s, turn latency 2.3–3.0 s → **LAT-001** |
| 7 | Survives user errors incl. hangups | PARTIAL | PASS* | Hangup → lease released, chat resumes with what's left (real-agent-call e2e; test_voice_lease). Nonsense absorbed (noise_or_fragment). Before: "I want to do more than Gmail" misread as refusal → instant graduation + hangup; fixed in extraction rules (live probe: no refuse_slot) | *Full stress sweep in AUDIT-001 error/hangup screens |
| 8 | Conversational, not a form | FAIL | PARTIAL | Before: "Got it: X." echoes, repeated 13–15 s Gmail pitch. Now: natural acks, why-pitch once, reaction-only LLM lines | Voice still speaks template-derived lines through a free "in your own words" prompt → **GUARD-001** tightens voice phrasing (tokens/temperature/guard) |
| 9 | Early graduation into the product | PARTIAL | PARTIAL | Graduation legal from any node once need is filled / user insists (engine `_graduate`, invariant 4). But the graduation screen's composer was dead (23:00:40 "my name is Darrran not darren" → canned "You're all set") and tiles weren't editable | **GRAD-001** (running): scoped replies, tap-to-edit, Connect Gmail, dismiss |
| 10 | Gentle steering back when info is missing | PASS | PASS | reask → explain why (once) → skip-for-later budget per node; off-topic answered in one line then the brain's ask is appended by code | — |
| 11 | Hosted, reviewable | PASS | PASS | Vercel + Fly live, smoke 7/7, /about + /privacy (Google-verification ready) | — |

## LLM-constraint audit (where could the model steer?)
| Surface | Finding | Status |
|---|---|---|
| Node transitions | Brain (`brain/engine.py` over `flow.yaml`) picks every next node; the Pipecat Flows handler returns the brain's node, never an LLM choice. Tool args that "ask" for a node/graduation/unknown intent are ignored | PASS — `test_guard.py::test_llm_asking_for_another_node_is_ignored`, `::test_voice_task_never_offers_a_node_choice`, `test_voice_flows.py::test_llm_args_cannot_move_node_or_fill_gmail` |
| Tools per node | Voice nodes expose only the tools declared in flow.yaml; every Flows handler is wrapped (`VoiceFlow.guarded`): a function not exposed on the brain's current node, or unknown (Pipecat catch-all `handle_unknown_function`), is rejected — no brain turn, state unchanged, `rejected_tool_call` logged, node line re-spoken. Text path forces the tool | PASS — `test_guard.py::test_out_of_node_tool_call_is_rejected_and_state_unchanged`, `::test_stale_record_slots_after_graduation_is_rejected`, `::test_unknown_function_is_rejected_via_the_catch_all`, `::test_call_session_registers_the_catch_all` |
| Extraction schema | One `record_slots` schema for all slots — deliberate (invariant 2). Code-side per-node acceptance from flow.yaml (`acceptance.intents`, slot `channels`, gmail `extraction: candidate_only`): agent_name never taken on a call, gmail never filled from extraction (only OAuth), intents not meaningful on a node dropped; each drop is a `rejected_extraction` event | PASS — `test_guard.py::test_agent_name_extraction_on_voice_is_ignored_and_logged`, `::test_agent_name_on_voice_ignored_through_the_voice_handler`, `::test_gmail_never_filled_from_extraction_even_if_a_validator_says_ok`, `::test_intents_not_meaningful_here_are_dropped_with_an_event`, `::test_acceptance_table_is_spec_data_and_validated` |
| Text phrasing | Was: model wrote the whole reply (asked "what's your name?" at the call offer). Now: model writes ≤ 1 reaction sentence; code appends the ask; questions/self-intros/pitches filtered; policy answers templated | FIXED 11:14pm |
| Temperature / tokens | Extraction temperature 0; text reaction 80 tokens, temperature 0.4. Voice LLM: 120 tokens, temperature 0.3 | PASS — `test_guard.py::test_voice_llm_settings` |
| Voice speech guard | Task instruction is now "say this line; you may shorten it, never add facts, questions or offers". `voice/speech_guard.py` sits between LLM and TTS on Flows calls and runs `llm/guard.check` per sentence against the brain's line + approved facts; violations are dropped + logged; if nothing survives the templated line is spoken (no dead air) | PASS — `test_guard.py::test_voice_task_says_the_line_without_adding`, `::test_speech_filter_drops_violations_and_never_leaves_silence`, `::test_speech_context_comes_from_the_brain_line`, `::test_speech_guard_processor_in_a_pipeline` |

## Modularity
Nodes are data (flow.yaml) executed by one pure engine; per-node behaviour lives in validators
and templates keyed by slot, not in per-node modules. Leaks found: none that let one node
act for another; the voice task prompt embeds the brief for the current node only.
GUARD-001 proves it (`services/agent/tests/test_guard.py`): (a) a tool call not declared on
the current node is rejected with state unchanged; (b) transitions only come from the brain;
(c) a new optional slot + node added to a copy of flow.yaml runs through the engine and the
call with no code change (`test_new_optional_slot_and_node_run_without_engine_change`,
`test_new_node_builds_on_the_call_without_voice_change`). Leak found and fixed while writing
(c): ask lines were keyed by slot in `llm/templates.py` (KeyError for a new slot); a slot with
no hand-written line now asks with its spec `ask` (or description).
