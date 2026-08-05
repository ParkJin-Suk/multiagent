import { useMemo, useRef, useState } from 'react'
import { fmt } from './api.js'

const W = 1000
const H = 150
const PAD = 6

/**
 * 유튜브 '가장 많이 다시 본 장면' 곡선(또는 오디오 에너지 곡선) 위에
 * 후보 구간을 겹쳐 그린다. 클릭으로 후보 선택, 드래그로 구간 직접 지정.
 */
export default function HeatmapChart({
  curve = [], candidates = [], duration = 0, selected, onSelect, onRange,
}) {
  const svgRef = useRef(null)
  const [drag, setDrag] = useState(null)   // {from, to}
  const [hover, setHover] = useState(null)

  const dur = duration || (curve.length ? curve[curve.length - 1].t : 0) ||
    (candidates.length ? Math.max(...candidates.map((c) => c.end)) : 1)

  const path = useMemo(() => {
    if (!curve.length) return ''
    const max = Math.max(...curve.map((p) => p.v)) || 1
    const pts = curve.map((p) => {
      const x = (p.t / dur) * W
      const y = H - PAD - (p.v / max) * (H - PAD * 2)
      return [x, y]
    })
    let d = `M 0 ${H} L ${pts[0][0].toFixed(1)} ${pts[0][1].toFixed(1)}`
    for (let i = 1; i < pts.length; i++) {
      const [x0, y0] = pts[i - 1]
      const [x1, y1] = pts[i]
      const mx = (x0 + x1) / 2
      d += ` C ${mx.toFixed(1)} ${y0.toFixed(1)}, ${mx.toFixed(1)} ${y1.toFixed(1)}, ${x1.toFixed(1)} ${y1.toFixed(1)}`
    }
    d += ` L ${W} ${H} Z`
    return d
  }, [curve, dur])

  const toTime = (evt) => {
    const rect = svgRef.current.getBoundingClientRect()
    const ratio = Math.min(1, Math.max(0, (evt.clientX - rect.left) / rect.width))
    return ratio * dur
  }

  const onDown = (e) => {
    const t = toTime(e)
    setDrag({ from: t, to: t })
  }
  const onMove = (e) => {
    const t = toTime(e)
    setHover(t)
    if (drag) setDrag((d) => ({ ...d, to: t }))
  }
  const onUp = () => {
    if (drag) {
      const start = Math.min(drag.from, drag.to)
      const end = Math.max(drag.from, drag.to)
      if (end - start >= 3) onRange?.({ start: +start.toFixed(2), end: +end.toFixed(2) })
      setDrag(null)
    }
  }

  const ticks = Array.from({ length: 6 }, (_, i) => (dur / 5) * i)

  return (
    <div className="heatmap">
      <div className="heatmap-head">
        <span>{curve.length ? '가장 많이 다시 본 구간' : '구간 후보'}</span>
        <span className="hint">후보를 클릭하거나, 그래프를 드래그해 직접 지정</span>
      </div>
      <svg
        ref={svgRef} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none"
        className="heatmap-svg"
        onMouseDown={onDown} onMouseMove={onMove} onMouseUp={onUp}
        onMouseLeave={() => { setHover(null); setDrag(null) }}
      >
        <defs>
          <linearGradient id="heatFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#4d9dff" stopOpacity="0.55" />
            <stop offset="100%" stopColor="#4d9dff" stopOpacity="0.04" />
          </linearGradient>
        </defs>

        {path
          ? <path d={path} fill="url(#heatFill)" stroke="#7cb8ff" strokeWidth="1.6" />
          : <line x1="0" y1={H - 10} x2={W} y2={H - 10} stroke="#2b3244" strokeWidth="2" />}

        {candidates.map((c) => {
          const x = (c.start / dur) * W
          const w = Math.max(3, ((c.end - c.start) / dur) * W)
          const on = selected === c.index
          return (
            <g key={c.index} onClick={(e) => { e.stopPropagation(); onSelect?.(c.index) }}
               style={{ cursor: 'pointer' }}>
              <rect x={x} y="0" width={w} height={H}
                    fill={on ? 'rgba(255,77,77,0.28)' : 'rgba(255,255,255,0.07)'}
                    stroke={on ? '#ff6b6b' : '#3a4256'} strokeWidth={on ? 2 : 1} />
              <text x={x + 5} y="15" fontSize="12" fill={on ? '#ffb4b4' : '#8d97ad'}>
                #{c.index + 1}
              </text>
            </g>
          )
        })}

        {drag && (
          <rect
            x={(Math.min(drag.from, drag.to) / dur) * W} y="0"
            width={Math.max(2, (Math.abs(drag.to - drag.from) / dur) * W)} height={H}
            fill="rgba(53,208,127,0.25)" stroke="#35d07f" strokeWidth="2"
          />
        )}

        {hover !== null && (
          <line x1={(hover / dur) * W} y1="0" x2={(hover / dur) * W} y2={H}
                stroke="#8d97ad" strokeWidth="1" strokeDasharray="3 3" />
        )}
      </svg>

      <div className="heatmap-axis">
        {ticks.map((t, i) => <span key={i}>{fmt(t)}</span>)}
      </div>
      {hover !== null && (
        <div className="heatmap-hover">
          {drag
            ? `${fmt(Math.min(drag.from, drag.to))} → ${fmt(Math.max(drag.from, drag.to))} (${Math.abs(drag.to - drag.from).toFixed(1)}초)`
            : fmt(hover)}
        </div>
      )}
    </div>
  )
}
