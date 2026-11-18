import { useCallback, useEffect, useState } from 'react'
import {
  ArrowClockwise, Coins, Sparkle, Star, TrendUp, Users,
} from '@phosphor-icons/react'

const API = ''

async function api(path, options = {}) {
  const res = await fetch(`${API}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  return res.json()
}

function StatCard({ icon: Icon, label, value, hint }) {
  return (
    <div className="bg-bg-card border border-border-main rounded-2xl p-5">
      <div className="flex items-center gap-2 text-text-muted text-xs mb-2">
        <Icon size={15} />
        {label}
      </div>
      <div className="text-2xl font-semibold text-text-main">{value}</div>
      {hint && <div className="text-xs text-text-muted mt-1">{hint}</div>}
    </div>
  )
}

export default function KoiPanel() {
  const [data, setData] = useState(null)
  const [chatId, setChatId] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [form, setForm] = useState({
    sender_id: '', kind: 'merit', delta: '',
  })

  const load = useCallback(async (cid = '') => {
    setLoading(true)
    setError('')
    try {
      const query = cid ? `?chat_id=${encodeURIComponent(cid)}` : ''
      const result = await api(`/api/koi${query}`)
      if (!result.ok) throw new Error(result.error || '加载失败')
      setData(result)
      setChatId(result.chat_id || '')
    } catch (err) {
      setError(String(err.message || err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  async function submitAdjust(event) {
    event.preventDefault()
    setNotice('')
    setError('')
    const delta = Number(form.delta)
    if (!form.sender_id || !Number.isFinite(delta) || delta === 0) {
      setError('请选择成员并填写非零调整值（可为负数）。')
      return
    }
    const result = await api('/api/koi/adjust', {
      method: 'POST',
      body: JSON.stringify({
        chat_id: chatId,
        sender_id: form.sender_id,
        sender_name: (data?.members || []).find(
          m => m.sender_id === form.sender_id)?.sender_name || form.sender_id,
        kind: form.kind,
        delta,
      }),
    })
    if (!result.ok) {
      setError(result.error || '调整失败')
      return
    }
    setNotice('已调整')
    setForm(prev => ({ ...prev, delta: '' }))
    load(chatId)
  }

  const totals = data?.totals || {}
  const today = data?.today || {}
  const members = data?.members || []
  const chats = data?.chats || []
  const poolLabel = {
    open: '进行中', drawn: '已开奖', refunded: '已退还', none: '未开始',
  }[today.pool_status] || '未开始'

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <div>
          <h2 className="text-lg font-semibold text-text-main">星盘</h2>
          <p className="text-xs text-text-muted mt-0.5">
            求签、观星、放生、点星与锦鲤池
          </p>
        </div>
        <div className="flex-1" />
        <select
          value={chatId}
          onChange={e => { setChatId(e.target.value); load(e.target.value) }}
          className="bg-bg-card border border-border-main rounded-lg px-3 py-2 text-sm text-text-main"
        >
          {chats.length === 0 && <option value="">暂无群数据</option>}
          {chats.map(chat => (
            <option key={chat} value={chat}>{chat}</option>
          ))}
        </select>
        <button
          onClick={() => load(chatId)}
          className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-border-main text-sm text-text-muted hover:text-text-main"
        >
          <ArrowClockwise size={15} className={loading ? 'animate-spin' : ''} />
          刷新
        </button>
      </div>

      {error && (
        <div className="rounded-xl border border-red-300 bg-red-50 text-red-700 text-sm p-3">
          {error}
        </div>
      )}
      {notice && (
        <div className="rounded-xl border border-emerald-300 bg-emerald-50 text-emerald-700 text-sm p-3">
          {notice}
        </div>
      )}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatCard icon={Users} label="成员" value={totals.members || 0}
          hint="有星盘记录的人" />
        <StatCard icon={Coins} label="香火总量" value={totals.merit || 0} />
        <StatCard icon={TrendUp} label="气运总量" value={totals.fortune || 0} />
        <StatCard icon={Sparkle} label="今日求签"
          value={totals.signed_today || 0}
          hint={`锦鲤池 ${poolLabel}`} />
      </div>

      <div className="bg-bg-card border border-border-main rounded-2xl p-5">
        <div className="flex items-center gap-2 mb-3">
          <Star size={16} className="text-brand-green" />
          <h3 className="text-sm font-semibold text-text-main">今日锦鲤池</h3>
        </div>
        <div className="grid grid-cols-3 gap-3 text-sm">
          <div>
            <div className="text-text-muted text-xs">池子</div>
            <div className="text-text-main font-semibold">
              {today.pool_total || 0} 香火
            </div>
          </div>
          <div>
            <div className="text-text-muted text-xs">状态</div>
            <div className="text-text-main font-semibold">{poolLabel}</div>
          </div>
          <div>
            <div className="text-text-muted text-xs">中奖者</div>
            <div className="text-text-main font-semibold">
              {today.pool_winner || '—'}
            </div>
          </div>
        </div>
      </div>

      <div className="bg-bg-card border border-border-main rounded-2xl overflow-hidden">
        <div className="px-5 py-3 border-b border-border-main flex items-center justify-between">
          <h3 className="text-sm font-semibold text-text-main">成员星盘</h3>
          <span className="text-xs text-text-muted">按气运排序</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-text-muted border-b border-border-main">
                <th className="text-left px-5 py-2 font-medium">成员</th>
                <th className="text-left px-3 py-2 font-medium">星座 / MBTI</th>
                <th className="text-right px-3 py-2 font-medium">香火</th>
                <th className="text-right px-3 py-2 font-medium">气运</th>
                <th className="text-left px-3 py-2 font-medium">段位</th>
                <th className="text-right px-5 py-2 font-medium">星盘</th>
              </tr>
            </thead>
            <tbody>
              {members.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-5 py-8 text-center text-text-muted">
                    还没有星盘数据。群里发「求签」开始。
                  </td>
                </tr>
              )}
              {members.map(member => (
                <tr key={member.sender_id}
                  className="border-b border-border-main/60 last:border-0">
                  <td className="px-5 py-2.5 text-text-main">
                    {member.sender_name}
                  </td>
                  <td className="px-3 py-2.5 text-text-muted">
                    {[member.sign, member.mbti].filter(Boolean).join(' · ') || '—'}
                  </td>
                  <td className="px-3 py-2.5 text-right text-text-main">
                    {member.merit}
                  </td>
                  <td className="px-3 py-2.5 text-right text-text-main">
                    {member.fortune}
                  </td>
                  <td className="px-3 py-2.5 text-text-muted">{member.rank}</td>
                  <td className="px-5 py-2.5 text-right text-text-main">
                    {member.stars}/28
                    {member.outer > 0 && (
                      <span className="text-text-muted"> +{member.outer}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <form onSubmit={submitAdjust}
        className="bg-bg-card border border-border-main rounded-2xl p-5">
        <h3 className="text-sm font-semibold text-text-main mb-3">手动调整</h3>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
          <select
            value={form.sender_id}
            onChange={e => setForm(prev => ({ ...prev, sender_id: e.target.value }))}
            className="bg-bg-main border border-border-main rounded-lg px-3 py-2 text-sm text-text-main"
          >
            <option value="">选择成员</option>
            {members.map(member => (
              <option key={member.sender_id} value={member.sender_id}>
                {member.sender_name}
              </option>
            ))}
          </select>
          <select
            value={form.kind}
            onChange={e => setForm(prev => ({ ...prev, kind: e.target.value }))}
            className="bg-bg-main border border-border-main rounded-lg px-3 py-2 text-sm text-text-main"
          >
            <option value="merit">香火</option>
            <option value="fortune">气运</option>
          </select>
          <input
            value={form.delta}
            onChange={e => setForm(prev => ({ ...prev, delta: e.target.value }))}
            placeholder="调整值，可负数"
            className="bg-bg-main border border-border-main rounded-lg px-3 py-2 text-sm text-text-main"
          />
          <button
            type="submit"
            className="rounded-lg bg-brand-green text-white text-sm py-2 hover:opacity-90"
          >
            确认调整
          </button>
        </div>
      </form>
    </div>
  )
}
