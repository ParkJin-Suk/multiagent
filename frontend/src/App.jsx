import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, fmt, openStream } from './api.js'
import HeatmapChart from './HeatmapChart.jsx'

const NODES = [
  { id: 'fetcher', label: '영상 확보', desc: 'yt-dlp 로 메타·히트맵·자막' },
  { id: 'highlighter', label: '하이라이트 추출', desc: '가장 많이 다시 본 구간' },
  { id: 'clip_gate', label: '구간 확인', desc: '사람이 구간 확정 후 클립 다운로드' },
  { id: 'transcriber', label: 'STT·화자분리', desc: '화자별 대사 텍스트화' },
  { id: 'translator', label: '번역', desc: '말투 지정 번역' },
  { id: 'scripter', label: '나레이션·드립', desc: '맥락 파악 후 삽입 설계' },
  { id: 'voicer', label: '나레이션 음성', desc: 'TTS + 덕킹/정지 배치' },
  { id: 'subtitler', label: '자막 생성', desc: '대사·나레이션·드립 3종 ASS' },
  { id: 'renderer', label: '최종 합성', desc: '믹싱·리프레임·burn-in' },
]

const STATUS_KO = {
  idle: '대기', running: '진행 중', done: '완료', error: '오류', waiting: '선택 대기',
  skipped: '건너뜀', waiting_clip: '구간 선택 대기', cancelled: '취소됨', pending: '준비 중',
}

const emptyRun = () => ({
  nodes: {}, logs: [], status: 'idle', source: null, candidates: [], curve: [],
  strategy: '', chosen: null, clip: null, transcript: [], translated: [],
  speakerMap: {}, summary: '', gaps: [], script: null, slots: [], timeline: null,
  subtitle: null, render: null, needClip: false,
})

export default function App() {
  const [health, setHealth] = useState(null)
  const [voices, setVoices] = useState([])
  const [form, setForm] = useState({
    url: '',
    translation_style: '',
    narration_persona: '',
    gag_level: 2,
    clip_seconds: 60,
    narration_mode: 'auto',
    vertical_reframe: true,
    review_clip_selection: true,
  })
  const [jobId, setJobId] = useState(null)
  const [run, setRun] = useState(emptyRun())
  const [tab, setTab] = useState('clip')
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState('')
  const esRef = useRef(null)
  const logRef = useRef(null)

  useEffect(() => {
    api.health().then((h) => {
      setHealth(h)
      setForm((f) => ({
        ...f,
        narration_mode: h.narration_mode || 'auto',
        vertical_reframe: h.vertical_reframe ?? true,
        review_clip_selection: h.review_clip_selection ?? true,
      }))
    }).catch(() => setHealth({ ok: false }))
    api.voices().then((d) => setVoices(d.voices || [])).catch(() => {})
  }, [])

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [run.logs.length])

  const handleEvent = useCallback((ev) => {
    setRun((p) => {
      const n = { ...p }
      switch (ev.kind) {
        case 'node':
          n.nodes = { ...p.nodes, [ev.node]: { status: ev.status, ...ev } }; break
        case 'log':
          n.logs = [...p.logs, ev]; break
        case 'status':
          n.status = ev.status
          if (ev.status !== 'waiting_clip') n.needClip = false
          break
        case 'source':
          n.source = ev.source; break
        case 'candidates':
          n.candidates = ev.candidates || []
          n.curve = ev.curve || []
          n.strategy = ev.strategy
          n.chosen = ev.chosen
          break
        case 'clip_selection_required':
          n.needClip = true
          n.candidates = ev.candidates || p.candidates
          n.curve = ev.curve || p.curve
          break
        case 'clip':
          n.clip = ev.clip; n.chosen = ev.chosen; break
        case 'transcript':
          n.transcript = ev.transcript || []
          n.speakerMap = ev.speaker_map || {}
          n.summary = ev.summary || ''
          n.gaps = ev.gaps || []
          break
        case 'translated':
          n.translated = ev.translated || []; break
        case 'script':
          n.script = ev.script; break
        case 'narration':
          n.slots = ev.slots || []; n.timeline = ev.timeline; break
        case 'subtitle':
          n.subtitle = ev.subtitle; break
        case 'render':
          n.render = ev.render; break
        default: break
      }
      return n
    })
    if (ev.kind === 'clip_selection_required' || ev.kind === 'candidates') setTab('clip')
    if (ev.kind === 'translated') setTab('script')
    if (ev.kind === 'narration') setTab('direct')
    if (ev.kind === 'render') setTab('result')
  }, [])

  async function start() {
    setBusy(true)
    try {
      const { job_id } = await api.createJob(form)
      setJobId(job_id)
      setRun(emptyRun())
      setTab('clip')
      esRef.current?.close()
      esRef.current = openStream(job_id, handleEvent)
    } catch (e) { setToast(e.message) } finally { setBusy(false) }
  }

  async function confirmClip(decision) {
    try {
      await api.chooseClip(jobId, decision)
      setRun((r) => ({ ...r, needClip: false }))
    } catch (e) { setToast(e.message) }
  }

  async function stop() {
    try { await api.cancel(jobId); esRef.current?.close() } catch (e) { setToast(e.message) }
  }

  const running = ['running', 'pending', 'waiting_clip'].includes(run.status)
  const activeNode = useMemo(
    () => NODES.find((n) => run.nodes[n.id]?.status === 'running')?.id, [run.nodes]
  )

  return (
    <div className="app">
      <Header health={health} status={run.status} jobId={jobId} source={run.source} />
      <main className="grid">
        <RunForm
          form={form} setForm={setForm} voices={voices} health={health} status={run.status}
          onStart={start} onStop={stop} busy={busy} running={running} hasJob={!!jobId}
        />
        <section className="col center">
          <Pipeline nodes={run.nodes} active={activeNode} />
          <Details tab={tab} setTab={setTab} run={run} onConfirmClip={confirmClip} />
        </section>
        <LogPanel logs={run.logs} innerRef={logRef} />
      </main>
      {toast && <div className="toast" onClick={() => setToast('')}>{toast}</div>}
    </div>
  )
}

/* ───────────────────────── 헤더 ───────────────────────── */
function Header({ health, status, jobId, source }) {
  return (
    <header className="header">
      <div className="brand">
        <span className="logo">✂</span>
        <div>
          <h1>Reaction Factory</h1>
          <p>{source?.title
            ? `${source.title} · ${fmt(source.duration)}`
            : '번역 · 나레이션 클립 자동 생산 라인'}</p>
        </div>
      </div>
      <div className="chips">
        {health && (
          <>
            <Chip ok={health.llm_key} label={`LLM ${health.llm_model || ''}`} offLabel="LLM 키 없음" />
            <Chip ok={health.stt_provider !== 'subtitles'} label={`STT ${health.stt_provider}`}
                  offLabel="STT 자막모드" />
            <Chip ok={health.hf_token} label="화자분리 ON" offLabel="화자분리 OFF" />
            <Chip ok={health.typecast_key} label="Typecast" offLabel="TTS edge" />
          </>
        )}
        <span className={`status-pill s-${status}`}>{STATUS_KO[status] || status}</span>
        {jobId && <span className="jobid">#{jobId}</span>}
      </div>
    </header>
  )
}

function Chip({ ok, label, offLabel }) {
  return <span className={`chip ${ok ? 'on' : 'off'}`}>{ok ? label : (offLabel || label)}</span>
}

/* ───────────────────────── 실행 폼 ───────────────────────── */
function RunForm({ form, setForm, voices, health, status, onStart, onStop, busy, running, hasJob }) {
  const up = (k) => (e) => {
    const v = e.target.type === 'checkbox' ? e.target.checked : e.target.value
    setForm((f) => ({ ...f, [k]: v }))
  }
  const gagLabels = ['없음', '조금', '보통', '많이']
  return (
    <aside className="col left">
      <div className="card">
        <h2>실행 설정</h2>

        <label>영상 URL <small>또는 로컬 파일 경로</small></label>
        <input value={form.url} onChange={up('url')}
               placeholder="https://www.youtube.com/watch?v=…" />

        <label>번역 말투</label>
        <textarea rows="3" value={form.translation_style} onChange={up('translation_style')}
                  placeholder="예: 반말 위주, 인터넷 방송 자막 톤. 욕설은 순화." />

        <label>나레이터 캐릭터</label>
        <textarea rows="2" value={form.narration_persona} onChange={up('narration_persona')}
                  placeholder="예: 무심한 다큐 나레이터. 한 발 떨어져서 설명." />

        <div className="row">
          <div>
            <label>드립 <b>{gagLabels[form.gag_level]}</b></label>
            <input type="range" min="0" max="3" value={form.gag_level} onChange={up('gag_level')} />
          </div>
          <div>
            <label>클립 길이 <b>{form.clip_seconds}초</b></label>
            <input type="range" min="20" max="180" step="5"
                   value={form.clip_seconds} onChange={up('clip_seconds')} />
          </div>
        </div>

        <label>나레이션 삽입</label>
        <select value={form.narration_mode} onChange={up('narration_mode')}>
          <option value="auto">auto — 갭이 되면 덕킹, 아니면 정지 삽입</option>
          <option value="duck">duck — 원본 볼륨만 낮춤</option>
          <option value="freeze">freeze — 항상 화면 정지</option>
        </select>

        {voices.length > 0 && (
          <p className="hint">TTS 보이스 {voices.length}개 사용 가능 · .env 에서 지정</p>
        )}

        <div className="switches">
          <label className="switch">
            <input type="checkbox" checked={form.vertical_reframe} onChange={up('vertical_reframe')} />
            <span>세로 쇼츠로 리프레임 (1080×1920)</span>
          </label>
          <label className="switch">
            <input type="checkbox" checked={form.review_clip_selection}
                   onChange={up('review_clip_selection')} />
            <span>구간을 내가 직접 고르기</span>
          </label>
        </div>

        {health && !health.llm_key && <p className="warn-box">.env 에 LLM API 키가 없습니다.</p>}
        {health?.font_warning && <p className="warn-box">{health.font_warning}</p>}
        {health?.stt_provider === 'subtitles' && (
          <p className="warn-box">STT 가 자막 재활용 모드입니다. 화자분리를 쓰려면
            requirements-stt.txt 설치 후 STT_PROVIDER=whisperx 로 바꾸세요.</p>
        )}

        <div className="actions">
          <button className="primary" onClick={onStart}
                  disabled={busy || running || !form.url.trim() || (health && !health.llm_key)}>
            {status === 'waiting_clip' ? '구간 선택 대기…' : running ? '작업 중…' : '클립 생산 시작'}
          </button>
          {hasJob && running && <button className="ghost" onClick={onStop}>중단</button>}
        </div>
      </div>
    </aside>
  )
}

/* ───────────────────────── 파이프라인 ───────────────────────── */
function Pipeline({ nodes, active }) {
  return (
    <div className="card pipeline">
      <h2>에이전트 파이프라인</h2>
      <div className="nodes">
        {NODES.map((n, i) => {
          const st = nodes[n.id]?.status || 'idle'
          return (
            <div key={n.id} className={`node ${st} ${active === n.id ? 'active' : ''}`}>
              <div className="node-rail">
                <span className="dot" />
                {i < NODES.length - 1 && <span className="line" />}
              </div>
              <div className="node-body">
                <div className="node-head">
                  <strong>{n.label}</strong>
                  <span className={`badge b-${st}`}>{STATUS_KO[st] || st}</span>
                </div>
                <p>{n.desc}</p>
                <NodeExtra id={n.id} info={nodes[n.id]} />
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function NodeExtra({ id, info }) {
  if (!info) return null
  const b = []
  if (id === 'fetcher' && info.duration) {
    b.push(fmt(info.duration))
    b.push(info.has_heatmap ? 'heatmap 있음' : 'heatmap 없음 → 오디오 분석')
  }
  if (id === 'highlighter' && info.count) b.push(`${info.strategy} · 후보 ${info.count}개`, info.chosen)
  if (id === 'clip_gate' && info.duration) b.push(`${info.range} · ${info.duration}초`)
  if (id === 'transcriber' && info.lines) b.push(`발화 ${info.lines}`, `화자 ${info.speakers}명`, info.language)
  if (id === 'translator' && info.lines) b.push(`${info.lines}줄 번역`)
  if (id === 'scripter' && info.narrations != null) b.push(`나레이션 ${info.narrations}`, `드립 ${info.gags}`)
  if (id === 'voicer' && info.count != null) b.push(`음성 ${info.count}개`, info.freeze ? `정지 ${info.freeze}` : '전부 덕킹')
  if (id === 'subtitler' && info.dialogue != null) b.push(`대사 ${info.dialogue}`, `나레 ${info.narration}`, `드립 ${info.gag}`)
  if (id === 'renderer' && info.duration) b.push(`${info.duration}초`, `${info.size_mb}MB`)
  if (info.status === 'error') b.push(info.message)
  if (!b.filter(Boolean).length) return null
  return <div className="node-extra">{b.filter(Boolean).map((x, i) => <span key={i}>{x}</span>)}</div>
}

/* ───────────────────────── 상세 ───────────────────────── */
function Details({ tab, setTab, run, onConfirmClip }) {
  const tabs = [['clip', '구간'], ['script', '대사·번역'], ['direct', '연출'], ['result', '결과']]
  return (
    <div className="card details">
      <div className="tabs">
        {tabs.map(([k, l]) => (
          <button key={k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>
            {l}{k === 'clip' && run.needClip ? ' •' : ''}
          </button>
        ))}
      </div>
      <div className="tab-body">
        {tab === 'clip' && <ClipView run={run} onConfirm={onConfirmClip} />}
        {tab === 'script' && <ScriptView run={run} />}
        {tab === 'direct' && <DirectView run={run} />}
        {tab === 'result' && <ResultView run={run} />}
      </div>
    </div>
  )
}

function ClipView({ run, onConfirm }) {
  const [sel, setSel] = useState(null)
  const [manual, setManual] = useState(null)

  useEffect(() => {
    if (run.chosen && sel === null) setSel(run.chosen.index ?? 0)
  }, [run.chosen])

  if (!run.candidates.length) return <Empty text="하이라이트 후보가 나오면 여기에 표시됩니다." />

  const cand = run.candidates[sel] || run.candidates[0]
  const range = manual || { start: cand?.start, end: cand?.end }

  return (
    <div className="stack">
      <HeatmapChart
        curve={run.curve}
        candidates={run.candidates}
        duration={run.source?.duration || 0}
        selected={sel}
        onSelect={(i) => { setSel(i); setManual(null) }}
        onRange={(r) => setManual(r)}
      />

      <div className="cand-list">
        {run.candidates.map((c) => (
          <button key={c.index} className={`cand ${sel === c.index ? 'on' : ''}`}
                  onClick={() => { setSel(c.index); setManual(null) }}>
            <span className="cand-time">{fmt(c.start)} → {fmt(c.end)}</span>
            <span className="cand-score">{c.source} {c.score?.toFixed?.(2)}</span>
            <span className="cand-reason">{c.reason}</span>
            {c.preview && <span className="cand-prev">{c.preview.slice(0, 120)}</span>}
          </button>
        ))}
      </div>

      {run.needClip && (
        <div className="approval">
          <div>
            <strong>이 구간으로 진행할까요?</strong>
            <p>{fmt(range.start)} → {fmt(range.end)} ({(range.end - range.start).toFixed(1)}초)
              {manual ? ' · 그래프에서 직접 지정' : ''}</p>
          </div>
          <div className="approval-actions">
            <button className="primary" onClick={() => onConfirm(
              manual ? { start: manual.start, end: manual.end } : { candidate_index: sel }
            )}>이 구간으로 확정</button>
            <button className="danger" onClick={() => onConfirm({ cancel: true })}>취소</button>
          </div>
        </div>
      )}

      {run.clip && (
        <div className="player-row">
          <video src={run.clip.url} controls playsInline />
          <div className="player-side">
            <Field k="구간" v={`${fmt(run.clip.start)} → ${fmt(run.clip.end)}`} />
            <Field k="길이" v={`${run.clip.duration}초`} />
            <Field k="해상도" v={`${run.clip.width}×${run.clip.height} @${run.clip.fps}fps`} />
          </div>
        </div>
      )}
    </div>
  )
}

function ScriptView({ run }) {
  const lines = run.translated.length ? run.translated : run.transcript
  if (!lines.length) return <Empty text="STT 결과가 나오면 여기에 표시됩니다." />
  return (
    <div className="stack">
      {run.summary && <p className="summary">{run.summary}</p>}
      <div className="speakers">
        {Object.entries(run.speakerMap).map(([id, name]) => (
          <span key={id} className="sp"><b>{name}</b> {id}</span>
        ))}
      </div>
      <table className="lines">
        <tbody>
          {lines.map((l, i) => (
            <tr key={i}>
              <td className="t">{fmt(l.start)}</td>
              <td className="sp-cell">{run.speakerMap[l.speaker] || l.speaker.replace('SPEAKER_', 'S')}</td>
              <td>
                <div className="ko">{l.ko || l.text}</div>
                {l.ko && l.ko !== l.text && <div className="orig">{l.text}</div>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function DirectView({ run }) {
  if (!run.script) return <Empty text="나레이션·드립이 설계되면 여기에 표시됩니다." />
  const dur = (run.clip?.duration || 1) + (run.timeline?.added || 0)
  return (
    <div className="stack">
      <p className="summary">{run.script.context}</p>

      <h4>타임라인</h4>
      <div className="track-wrap">
        <Track label="대사" color="#6f8fbf" items={(run.translated.length ? run.translated : run.transcript)
          .map((l) => ({ start: l.start, end: l.end, text: l.ko || l.text }))} dur={dur} />
        <Track label="나레이션" color="#ffa500" items={run.slots.map((s) => ({
          start: s.final_at, end: s.final_at + s.duration, text: s.text, tag: s.mode }))} dur={dur} />
        <Track label="드립" color="#ffe14d" items={(run.script.gags || []).map((g) => ({
          start: g.start, end: g.start + (g.duration || 1.8), text: g.text }))} dur={dur} />
      </div>

      <h4>나레이션 {run.slots.length}개</h4>
      <div className="slot-list">
        {run.slots.map((s) => (
          <div key={s.index} className="slot">
            <span className={`mode ${s.mode}`}>{s.mode === 'freeze' ? '정지 삽입' : '덕킹'}</span>
            <span className="t">{fmt(s.final_at)} · {s.duration}s</span>
            <span className="txt">{s.text}</span>
          </div>
        ))}
        {!run.slots.length && <div className="empty">나레이션 없음</div>}
      </div>

      <h4>드립 {(run.script.gags || []).length}개</h4>
      <div className="slot-list">
        {(run.script.gags || []).map((g, i) => (
          <div key={i} className="slot">
            <span className="mode gag">자막만</span>
            <span className="t">{fmt(g.start)}</span>
            <span className="txt">{g.text}</span>
          </div>
        ))}
        {!(run.script.gags || []).length && <div className="empty">드립 없음</div>}
      </div>
    </div>
  )
}

function Track({ label, color, items, dur }) {
  return (
    <div className="track">
      <span className="track-label">{label}</span>
      <div className="track-bar">
        {items.map((it, i) => {
          const left = (it.start / dur) * 100
          const w = Math.max(0.6, ((it.end - it.start) / dur) * 100)
          return (
            <div key={i} className="track-item" title={`${fmt(it.start)} ${it.text}`}
                 style={{ left: `${left}%`, width: `${w}%`, background: color }}>
              {it.tag === 'freeze' && <em>❚</em>}
            </div>
          )
        })}
      </div>
    </div>
  )
}

function ResultView({ run }) {
  if (!run.render) return <Empty text="최종 영상이 만들어지면 여기에 표시됩니다." />
  const r = run.render
  const s = run.script || {}
  return (
    <div className="stack result">
      <div className="player-row">
        <video src={r.url} controls poster={r.thumbnail_url} playsInline />
        <div className="player-side">
          <Field k="길이" v={`${r.duration}초`} />
          <Field k="용량" v={`${r.size_mb} MB`} />
          <Field k="해상도" v={`${r.width}×${r.height}`} />
          {run.timeline?.added > 0 && <Field k="정지삽입" v={`+${run.timeline.added}초`} />}
          <div className="dl">
            <a href={r.url} download>영상</a>
            {r.srt_url && <a href={r.srt_url} download>SRT</a>}
            {r.ass_url && <a href={r.ass_url} download>ASS</a>}
            {r.thumbnail_url && <a href={r.thumbnail_url} download>썸네일</a>}
          </div>
        </div>
      </div>
      <h4>업로드 메타</h4>
      <Field k="제목" v={s.title} />
      <Field k="설명" v={s.description} />
      <div className="tags">{(s.tags || []).map((t, i) => <span key={i}>#{t}</span>)}</div>
    </div>
  )
}

function Field({ k, v }) {
  if (!v) return null
  return <div className="field"><span className="k">{k}</span><span className="v">{v}</span></div>
}

function Empty({ text }) { return <div className="empty">{text}</div> }

/* ───────────────────────── 로그 ───────────────────────── */
function LogPanel({ logs, innerRef }) {
  return (
    <aside className="col right">
      <div className="card log-card">
        <h2>실시간 로그</h2>
        <div className="logs" ref={innerRef}>
          {logs.length === 0 && <div className="empty">아직 로그가 없습니다.</div>}
          {logs.map((l, i) => (
            <div key={i} className={`log ${l.level}`}>
              <span className="t">{new Date(l.ts * 1000).toLocaleTimeString('ko-KR', { hour12: false })}</span>
              {l.node && <span className="n">{l.node}</span>}
              <span className="m">{l.message}</span>
            </div>
          ))}
        </div>
      </div>
    </aside>
  )
}
