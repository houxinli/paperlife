# 旁听模式实施计划

Issue: https://github.com/houxinli/paperlife/issues/5

**Goal:** iPad单麦克风记录外放的异地通话，AI不出声，多人分离、事后身份/单句归属校订，进入既有素材版本和章节流程。
**Architecture:** 新ASR适配与静默流式代理复用现有16k PCM采集/受权一次票据；说话者、参与者、原始识别、归属修订独立保存。当前人物档案为书的主体，不意味着每句录音都是主体发言。核心、ASR适配、网页按下列固定接口分别实现。
**Spec:** 本轮用户已明确授权实现与部署。ASR新条款/后付费已获得本轮用户明确授权；不购买资源包，不建立永久声纹库。独立开发源仍为paperlife，旧GCP站受控回补部署。

## Global constraints
- 原声和原始识别不可覆盖，身份/文字/句子归属校订追加版本并使旧草稿过期；确认章节不自动改。
- 只有同档案owner/editor可修改归属。guest可录自己绑定的旁听、看历史，不可修改或越权。
- 开始前明确在场/通话双方知情；AI永不播报，不把人的“停止”等自然聊天内容当系统命令，不从旁听抽取AI偏好。
- 同一录音speaker_id仅是声音分组，不等于实名。参与者名单不自动绑定声音。未知/重叠可保留，身份全未知时不允许直接写成主人公自传。
- 初期前台、每次最多20分钟，与现有采集边界一致；后台/断线终止并保留已接收音频，明确呈现不完整识别。
- 真实供应商验证仅用合成语音/虚构素材，不将家庭资料拿去测试。

## Shared contracts

ASR module `listening_asr.py` (provider agent):
- `configured(store)->bool`, `public_config(store,can_configure=False)->{enabled,configured,provider,can_configure}`, `save_config(store,{enabled:bool})` private chmod600 file; default disabled, uses existing store.provider() key/app credentials. No arbitrary URL.
- `async with connect_asr(store) as upstream`: `await upstream.send_audio(pcm:bytes,final:bool=False)`; `async for event in upstream.events()` yields `{segments:[{id,speaker_id,text,start_ms,end_ms}],final:bool}`. Document whether segments cumulative or incremental; normalize to cumulative where possible. IDs stable within current final ASR result, missing speaker=`unknown`. Error sanitized. Verify current official API protocol/2.0 resource, speaker_info, final frame and size limits. No TTS calls.

Core/API modules (core agent): `listening_store.py`, `listening_stream.py`, `listening_api.py` plus store/api/materials/jobs/prompt integration.
- Add sessions.mode default dialogue; listening has original snapshot + annotation revision counter (sessions.version). Original utterances and initial speaker grouping immutable after finalization. Raw mixed WAV saved to existing `audio_path(sid)`.
- `async listen(client,store,session,profile,authorize)` matches realtime.interview entry signature; same /life/live WS dispatcher. Reuse existing issue_ticket/active/auth handling, creation sets mode on persisted session; credentials and explicit enabled required. Proxy emits ready, listening_transcript with normalized segments, saved, ending, ended/error. No audio binary output. Final incoming provider snapshot settles before ending; failures preserve received audio + latest partial result explicitly.
- GET `/life/listening/provider` normal auth; GET `/life/guest/listening/provider` bound guest. PUT normal admin `{enabled}`.
- POST `/life/profiles/{p}/listening` or `/life/guest/listening` `{title,participants:[{id,name,relationship,is_subject}],consent:true}` -> session incl ticket,mode. participants optional; ids simple stable strings. A list is not a confirmed acoustic binding.
- GET `/life/listening/sessions/{sid}` normal member or `/life/guest/listening/{sid}` scoped guest -> `{session:{id,profile,title,status,mode,version,...},participants:[...],speakers:[{id,label,participant_id}],segments:[{id,speaker_id,participant_id,text,start_ms,end_ms,original_text,original_speaker_id}],original_segments:[...],recognition_status,can_edit,versions:[{version,created_at}]}`.
- PUT `/life/listening/sessions/{sid}` owner/editor only `{version,participants:[{id,name,relationship,is_subject}],bindings:{speaker_id:participant_id|null},overrides:{segment_id:{speaker_id?,text?}}}`. Max30 participants, schema limits. Can create manual speaker group by binding key for split/reassignment. Full snapshot semantics, no automatic identity mapping. Reject invalid segment/participant references and stale versions409; explicit merge by binding two speaker groups to same participant.
- Preserve annotations across retries or don't allow re-recognition once annotated; no implicit overwritten human edits. GET `/life/listening/sessions/{sid}/versions/{version}` versioned read; guest no editing/history draft bypass.
- Session material units include speaker `{id,name,relationship,is_subject,confirmed}`, segment/time, annotation revision, original quote vs text correction. Changing binding invalidates item/chapter drafts. Unknown/other speakers clearly attributed; require at least one manually confirmed subject binding before generating a chapter from listening material. Automatic extraction may await identity confirmation, with actionable state; no false self-biography.
- Existing session free-text correction must not erase structured speaker ownership (reject and point to listening editor). Existing raw turns/history remain accessible; mode returned in session lists. Do not apply persona preferences or voice exit heuristics to overheard dialogue.

Web modules (UI agent): `listening.js`, `listening-audio.js`, tests, HTML/CSS; root wires app.js.
- Export `createListening({api,profile,isGuest,note,fetchAudio,onSaved,canConfigure})` -> `{load,clear,openSession,active}` (active getter/function document).
- Recording UI under #listening-workspace in talk view, review UI under #listening-review in read view, model activation #listening-settings admin. Collapsible so normal chat remains simple. Root passes global busy mutual exclusion callback if needed (coordinate before wiring).
- Recording PCM capture can reuse VoiceCall.prepare/capture worklet via subclass/configurable connect endpoint (same /life/live). Must never play incoming audio and show silence mode. Explicit consent, participant optional list; pagehide/visibility ends, no synthetic success. Ready handshake before sending. Live speaker bubbles marked provisional; button stop saves; delayed final result clearly shown.
- Group participant identity and segment reassignment/text editor with version conflict handling, original raw text view and click-to-play timestamp. Do not imply names from speaker_id. Allow participant is_subject mark; one confirmed subject max, unknown speaker remains possible. Guest review readonly.
- Use GET/PUT contracts above, display setup-needed if ASR disabled. Private config only boolean enabled, no new credential UI (existing key reused).

## Tasks / verification
- [ ] ASR protocol/fixture tests: binary frames, final and speaker parsing, truncated/malformed/oversized responses, credentials never leaked; actual synthetic two-voice probe after activation.
- [ ] Core RED→GREEN: source attribution, original immutability, group/single correction, cross-profile/guest/reader guards, stale writes, interrupted recording, correct chapter admission and evidence snapshots.
- [ ] UI tests/preview: silence, stop/pagehide, mode mutual exclusion, identity grouping, stale-profile requests, readonly guest, annotation conflict.
- [ ] Full suite + fresh independent review; source-only PR linked #5 and Project In Progress; no family examples.
- [ ] Private backup/manifest; scoped legacy backport + promote_to_main.sh; verify original data and public assets; software completion vs iPad acoustic acceptance explicitly separated in issue.
