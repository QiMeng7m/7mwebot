import { useState, useEffect, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Trophy, ShoppingBag, Sword, ChartBar, ArrowsClockwise, X } from '@phosphor-icons/react'
import { Select } from './SharedComponents'

const API = 'http://127.0.0.1:7327'

const tabTransition = {
  initial: { opacity: 0, y: 6 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -6 },
}

async function api(path, options = {}) {
  const res = await fetch(`${API}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  return res.json()
}

function Card({ children, className = '' }) {
  return (
    <div className={`bg-bg-main border border-border-main rounded-2xl p-5 ${className}`}>
      {children}
    </div>
  )
}

function SectionTitle({ children, hint }) {
  return (
    <div className="mb-3">
      <h3 className="text-[14px] font-semibold text-text-main">{children}</h3>
      {hint && <p className="text-xs text-text-muted mt-0.5">{hint}</p>}
    </div>
  )
}

function Pill({ active, onClick, children }) {
  return (
    <button
      onClick={onClick}
      className={`px-4 py-1.5 rounded-full text-[13px] font-medium transition-colors cursor-pointer border ${
        active
          ? 'bg-brand-green-light border-brand-green/30 text-brand-green-hover dark:text-brand-green'
          : 'bg-bg-main border-border-main text-text-muted hover:text-text-main'
      }`}
    >
      {children}
    </button>
  )
}

function Bar({ value, max }) {
  const pct = max > 0 ? Math.round((value / max) * 100) : 0
  return (
    <div className="h-2 rounded-full bg-bg-raised overflow-hidden">
      <div
        className="h-full rounded-full bg-brand-green transition-all duration-300"
        style={{ width: `${Math.max(2, pct)}%` }}
      />
    </div>
  )
}

export default function PointsPanel() {
  const [groups, setGroups] = useState([])
  const [chatId, setChatId] = useState('')
  const [view, setView] = useState('board') // board | shop | duels | overview
  const [scope, setScope] = useState('total')
  const [rows, setRows] = useState([])
  const [overview, setOverview] = useState(null)
  const [catalog, setCatalog] = useState({ slots: [], items: [] })
  const [owned, setOwned] = useState([])
  const [duels, setDuels] = useState([])
  const [balance, setBalance] = useState(0)
  const [power, setPower] = useState(null)
  const [shopMember, setShopMember] = useState('')
  const [detail, setDetail] = useState(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [delta, setDelta] = useState('')
  const [reason, setReason] = useState('')

  useEffect(() => {
    async function loadGroups() {
      try {
        const data = await api('/api/nicknames/groups')
        if (data.ok && data.groups?.length) {
          setGroups(data.groups)
          setChatId(data.groups[0].chat_id)
        }
      } catch {
        setMessage('无法加载群列表')
      }
    }
    loadGroups()
  }, [])

  const loadBoard = useCallback(async () => {
    if (!chatId) return
    const data = await api(`/api/points?chat_id=${encodeURIComponent(chatId)}&scope=${scope}&limit=20`)
    if (data.ok) setRows(data.rows || [])
    else setMessage(data.error || '加载榜单失败')
  }, [chatId, scope])

  const loadOverview = useCallback(async () => {
    if (!chatId) return
    const data = await api(`/api/points/overview?chat_id=${encodeURIComponent(chatId)}`)
    if (data.ok) setOverview(data)
  }, [chatId])

  const loadShop = useCallback(async (memberId) => {
    if (!chatId) return
    const query = memberId ? `&sender_id=${encodeURIComponent(memberId)}` : ''
    const data = await api(`/api/equipment?chat_id=${encodeURIComponent(chatId)}${query}`)
    if (data.ok) {
      setCatalog(data.catalog)
      setOwned(data.owned || [])
      setBalance(data.balance || 0)
      setPower(data.power)
    }
  }, [chatId])

  const loadDuels = useCallback(async () => {
    if (!chatId) return
    const data = await api(`/api/duels?chat_id=${encodeURIComponent(chatId)}&limit=50`)
    if (data.ok) setDuels(data.duels || [])
  }, [chatId])

  useEffect(() => {
    if (!chatId) return
    setLoading(true)
    Promise.all([loadBoard(), loadOverview()])
      .catch(() => setMessage('加载失败'))
      .finally(() => setLoading(false))
  }, [chatId, scope, loadBoard, loadOverview])

  useEffect(() => {
    if (!chatId) return
    if (view === 'shop') {
      const member = shopMember || rows[0]?.sender_id || ''
      if (member && member !== shopMember) setShopMember(member)
      loadShop(shopMember || rows[0]?.sender_id || '')
    }
    if (view === 'duels') loadDuels()
  }, [view, chatId, shopMember, rows, loadShop, loadDuels])

  async function openMember(senderId) {
    const data = await api(
      `/api/points/member?chat_id=${encodeURIComponent(chatId)}&sender_id=${encodeURIComponent(senderId)}`
    )
    if (data.ok) setDetail(data.member)
    else setMessage(data.error || '加载成员失败')
  }

  async function refreshAll() {
    await Promise.all([loadBoard(), loadOverview()])
    if (view === 'shop') await loadShop(shopMember)
    if (view === 'duels') await loadDuels()
    if (detail) await openMember(detail.sender_id)
  }

  async function adjustPoints() {
    const value = parseInt(delta, 10)
    if (!detail || !Number.isFinite(value) || value === 0) {
      setMessage('请输入非零的分值')
      return
    }
    setBusy(true)
    try {
      const data = await api('/api/points/adjust', {
        method: 'POST',
        body: JSON.stringify({
          chat_id: chatId,
          sender_id: detail.sender_id,
          sender_name: detail.sender_name,
          delta: value,
          reason,
        }),
      })
      if (data.ok) {
        setMessage(`已调整，当前余额 ${data.balance}`)
        setDelta('')
        setReason('')
        await refreshAll()
      } else {
        setMessage(data.error || '调整失败')
      }
    } finally {
      setBusy(false)
    }
  }

  async function buyItem(item) {
    if (!shopMember) {
      setMessage('请先选择成员')
      return
    }
    setBusy(true)
    try {
      const data = await api('/api/equipment/buy', {
        method: 'POST',
        body: JSON.stringify({
          chat_id: chatId,
          sender_id: shopMember,
          item_id: item.id,
        }),
      })
      if (data.ok) {
        setMessage(`已购买 ${item.name}，余额 ${data.balance}`)
      } else if (data.code === 'insufficient') {
        setMessage(`余额不足：${item.name} 需要 ${item.price} 分`)
      } else if (data.code === 'owned') {
        setMessage(`已经拥有 ${item.name}`)
      } else {
        setMessage(data.error || '购买失败')
      }
      await refreshAll()
      await loadShop(shopMember)
    } finally {
      setBusy(false)
    }
  }

  async function toggleEquip(item, equipped) {
    if (!shopMember) return
    setBusy(true)
    try {
      const data = await api('/api/equipment/equip', {
        method: 'POST',
        body: JSON.stringify({
          chat_id: chatId,
          sender_id: shopMember,
          item_id: item.id,
          equipped,
        }),
      })
      if (!data.ok) setMessage('操作失败')
      await refreshAll()
      await loadShop(shopMember)
    } finally {
      setBusy(false)
    }
  }

  const groupOptions = groups.map(g => ({
    value: g.chat_id,
    label: `${g.group_name}（${g.member_count}人）`,
  }))
  const memberOptions = rows.map(r => ({
    value: r.sender_id,
    label: `${r.sender_name}（${r.points}分）`,
  }))
  const ownedMap = Object.fromEntries(owned.map(o => [o.item_id, o]))
  const maxDaily = overview?.daily?.reduce((m, d) => Math.max(m, d.points), 0) || 0
  const distribution = overview?.level_distribution || {}
  const distributionTotal = Object.values(distribution).reduce((a, b) => a + b, 0)

  return (
    <div className="space-y-6 max-w-6xl">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold text-text-main tracking-tight">群活跃积分</h2>
          <p className="text-xs text-text-muted mt-1">
            发言赚积分、买装备、切磋抢分。累计积分决定排名，余额用于消费。
          </p>
        </div>
        <div className="flex items-center gap-3">
          {groupOptions.length > 0 && (
            <Select value={chatId} onChange={setChatId} options={groupOptions} />
          )}
          <button
            onClick={refreshAll}
            className="p-2 rounded-full border border-border-main text-text-muted hover:text-text-main transition-colors cursor-pointer"
            title="刷新"
          >
            <ArrowsClockwise size={18} />
          </button>
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        <Pill active={view === 'board'} onClick={() => setView('board')}>
          <Trophy size={14} className="inline mr-1" />榜单
        </Pill>
        <Pill active={view === 'shop'} onClick={() => setView('shop')}>
          <ShoppingBag size={14} className="inline mr-1" />装备商店
        </Pill>
        <Pill active={view === 'duels'} onClick={() => setView('duels')}>
          <Sword size={14} className="inline mr-1" />切磋记录
        </Pill>
        <Pill active={view === 'overview'} onClick={() => setView('overview')}>
          <ChartBar size={14} className="inline mr-1" />概览
        </Pill>
      </div>

      {message && (
        <div className="text-xs text-text-muted bg-bg-raised border border-border-main rounded-xl px-4 py-2 flex items-center justify-between">
          <span>{message}</span>
          <button onClick={() => setMessage('')} className="text-text-muted hover:text-text-main cursor-pointer">
            <X size={14} />
          </button>
        </div>
      )}

      <AnimatePresence mode="wait">
        <motion.div key={view} {...tabTransition} transition={{ duration: 0.18 }}>
          {view === 'board' && (
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
              <div className="lg:col-span-2 space-y-4">
                <div className="flex gap-2">
                  <Pill active={scope === 'total'} onClick={() => setScope('total')}>总榜</Pill>
                  <Pill active={scope === 'season'} onClick={() => setScope('season')}>本月</Pill>
                  <Pill active={scope === 'day'} onClick={() => setScope('day')}>今日</Pill>
                </div>
                <Card>
                  {loading ? (
                    <p className="text-sm text-text-muted">加载中…</p>
                  ) : rows.length === 0 ? (
                    <p className="text-sm text-text-muted">这个群还没有积分记录。</p>
                  ) : (
                    <div className="space-y-2">
                      {rows.map(row => (
                        <button
                          key={row.sender_id}
                          onClick={() => openMember(row.sender_id)}
                          className="w-full flex items-center gap-3 px-3 py-2.5 rounded-xl hover:bg-bg-raised transition-colors text-left cursor-pointer"
                        >
                          <span className={`w-7 text-center font-mono text-sm ${
                            row.rank <= 3 ? 'text-brand-green font-semibold' : 'text-text-muted'
                          }`}>
                            {row.rank}
                          </span>
                          <span className="flex-1 min-w-0">
                            <span className="block text-[14px] text-text-main truncate">{row.sender_name}</span>
                            <span className="block text-[11px] text-text-muted font-mono">
                              {row.level}
                            </span>
                          </span>
                          <span className="text-right">
                            <span className="block text-[14px] font-semibold text-text-main font-mono">{row.points}分</span>
                            <span className="block text-[11px] text-text-muted font-mono">余额 {row.balance}</span>
                          </span>
                        </button>
                      ))}
                    </div>
                  )}
                </Card>
              </div>

              <div className="space-y-4">
                {detail ? (
                  <Card>
                    <div className="flex items-start justify-between">
                      <SectionTitle hint={detail.level ? `Lv${detail.level.level} ${detail.level.title}` : ''}>
                        {detail.sender_name}
                      </SectionTitle>
                      <button onClick={() => setDetail(null)} className="text-text-muted hover:text-text-main cursor-pointer">
                        <X size={16} />
                      </button>
                    </div>
                    <div className="grid grid-cols-2 gap-3 mb-4">
                      <Stat label="累计积分" value={detail.earned} />
                      <Stat label="余额" value={detail.balance} />
                      <Stat label="本月" value={detail.season_points} />
                      <Stat label="今日" value={detail.day_points} />
                      <Stat label="武力值" value={detail.power?.total ?? 0} />
                      <Stat label="连续活跃" value={`${detail.streak_days}天`} />
                    </div>
                    {detail.next_level && (
                      <p className="text-xs text-text-muted mb-4">
                        距 Lv{detail.next_level.level} {detail.next_level.title} 还差{' '}
                        {Math.max(0, detail.next_level.min_points - detail.earned)} 分
                      </p>
                    )}
                    <div className="border-t border-border-main pt-4">
                      <p className="text-[13px] font-semibold text-text-main mb-2">管理员调整</p>
                      <div className="flex gap-2 mb-2">
                        <input
                          type="number"
                          value={delta}
                          onChange={e => setDelta(e.target.value)}
                          placeholder="+20 / -10"
                          className="w-24 bg-bg-raised border border-border-main rounded-lg px-3 py-2 text-[13px] text-text-main font-mono focus:outline-none focus:border-brand-green"
                        />
                        <input
                          type="text"
                          value={reason}
                          onChange={e => setReason(e.target.value)}
                          placeholder="理由（可选）"
                          className="flex-1 bg-bg-raised border border-border-main rounded-lg px-3 py-2 text-[13px] text-text-main focus:outline-none focus:border-brand-green"
                        />
                      </div>
                      <button
                        disabled={busy}
                        onClick={adjustPoints}
                        className="w-full py-2 rounded-lg bg-brand-green-light border border-brand-green/20 text-[13px] text-brand-green-hover dark:text-brand-green font-medium disabled:opacity-50 cursor-pointer"
                      >
                        确认调整
                      </button>
                    </div>
                    {detail.events?.length > 0 && (
                      <div className="border-t border-border-main pt-4 mt-4">
                        <p className="text-[13px] font-semibold text-text-main mb-2">最近流水</p>
                        <div className="space-y-1 max-h-48 overflow-auto">
                          {detail.events.slice(0, 12).map((ev, i) => (
                            <div key={i} className="flex justify-between text-[11px] font-mono text-text-muted">
                              <span>{ev.day} {ev.reason}</span>
                              <span className={ev.points >= 0 ? 'text-brand-green' : 'text-[#d45656]'}>
                                {ev.points > 0 ? '+' : ''}{ev.points}
                              </span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </Card>
                ) : (
                  <Card>
                    <p className="text-sm text-text-muted">
                      点击榜单中的成员查看详情、武力值与流水。
                    </p>
                  </Card>
                )}
              </div>
            </div>
          )}

          {view === 'shop' && (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center gap-3">
                <div className="w-64">
                  {memberOptions.length > 0 ? (
                    <Select value={shopMember} onChange={setShopMember} options={memberOptions} />
                  ) : (
                    <p className="text-sm text-text-muted">还没有成员数据</p>
                  )}
                </div>
                <span className="text-xs font-mono text-text-muted">余额 {balance}</span>
                {power && (
                  <span className="text-xs font-mono text-text-muted">武力值 {power.total}</span>
                )}
              </div>

              {catalog.slots.map(slot => {
                const items = catalog.items.filter(i => i.slot === slot.id)
                if (!items.length) return null
                return (
                  <Card key={slot.id}>
                    <SectionTitle>{slot.name}</SectionTitle>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                      {items.map(item => {
                        const mine = ownedMap[item.id]
                        return (
                          <div key={item.id} className="flex items-center gap-3 p-3 rounded-xl border border-border-main bg-bg-raised/40">
                            <div className="flex-1 min-w-0">
                              <p className="text-[14px] text-text-main truncate">{item.name}</p>
                              <p className="text-[11px] text-text-muted font-mono">
                                武力 +{item.power} · {item.price} 分
                                {mine?.equipped ? ' · 已装备' : ''}
                              </p>
                            </div>
                            {mine ? (
                              <button
                                disabled={busy}
                                onClick={() => toggleEquip(item, !mine.equipped)}
                                className="px-3 py-1.5 rounded-lg text-[12px] border border-border-main text-text-muted hover:text-text-main transition-colors cursor-pointer disabled:opacity-50"
                              >
                                {mine.equipped ? '卸下' : '装备'}
                              </button>
                            ) : (
                              <button
                                disabled={busy}
                                onClick={() => buyItem(item)}
                                className="px-3 py-1.5 rounded-lg text-[12px] bg-brand-green-light border border-brand-green/20 text-brand-green-hover dark:text-brand-green font-medium cursor-pointer disabled:opacity-50"
                              >
                                购买
                              </button>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  </Card>
                )
              })}
            </div>
          )}

          {view === 'duels' && (
            <Card>
              <SectionTitle hint="最近的切磋流水，包含被免战挡下的尝试">
                切磋记录
              </SectionTitle>
              {duels.length === 0 ? (
                <p className="text-sm text-text-muted">还没有切磋记录。</p>
              ) : (
                <div className="space-y-1.5">
                  {duels.map((d, i) => {
                    const time = new Date(d.created_at * 1000).toLocaleString()
                    const outcome = d.shield_blocked
                      ? '被免战挡下'
                      : d.attacker_win
                        ? `抢到 ${d.points_moved} 分`
                        : '落败'
                    return (
                      <div key={i} className="flex items-center gap-3 text-[12px] font-mono px-2 py-1.5 rounded-lg hover:bg-bg-raised">
                        <span className="text-text-muted w-36 shrink-0">{time}</span>
                        <span className="text-text-main flex-1 truncate">
                          {d.attacker_name}（{d.attacker_power}） vs {d.defender_name}（{d.defender_power}）
                        </span>
                        <span className="text-text-muted">{Math.round(d.win_prob * 100)}%</span>
                        <span className={`w-24 text-right ${d.attacker_win ? 'text-brand-green' : 'text-text-muted'}`}>
                          {outcome}
                        </span>
                      </div>
                    )
                  })}
                </div>
              )}
            </Card>
          )}

          {view === 'overview' && (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
              <Card>
                <SectionTitle hint="近 14 天全群获得的积分">趋势</SectionTitle>
                {overview ? (
                  <div className="flex items-end gap-1.5 h-40">
                    {overview.daily.map(d => (
                      <div key={d.day} className="flex-1 flex flex-col items-center gap-1" title={`${d.day}：${d.points}分`}>
                        <div className="w-full bg-brand-green/70 rounded-t" style={{
                          height: `${maxDaily ? Math.max(3, (d.points / maxDaily) * 120) : 3}px`,
                        }} />
                        <span className="text-[9px] text-text-muted font-mono">{d.day.slice(5)}</span>
                      </div>
                    ))}
                  </div>
                ) : <p className="text-sm text-text-muted">加载中…</p>}
                {overview && (
                  <div className="flex gap-6 mt-4 text-xs font-mono text-text-muted">
                    <span>参与人数 {overview.participants}</span>
                    <span>今日产生 {overview.day_total} 分</span>
                  </div>
                )}
              </Card>

              <Card>
                <SectionTitle hint="按累计积分计算">等级分布</SectionTitle>
                {Object.keys(distribution).length === 0 ? (
                  <p className="text-sm text-text-muted">暂无数据。</p>
                ) : (
                  <div className="space-y-3">
                    {Object.entries(distribution)
                      .sort((a, b) => b[1] - a[1])
                      .map(([label, count]) => (
                        <div key={label}>
                          <div className="flex justify-between text-xs text-text-muted mb-1">
                            <span>{label}</span>
                            <span className="font-mono">{count}人</span>
                          </div>
                          <Bar value={count} max={distributionTotal} />
                        </div>
                      ))}
                  </div>
                )}
              </Card>
            </div>
          )}
        </motion.div>
      </AnimatePresence>

      <p className="text-[11px] text-text-muted font-mono">
        群内指令：积分 · 我的信息 · 查询 名字 · 积分榜 · 月榜 · 商店 · 购买 · 武力值 · 切磋 @某人 · 买免战券
      </p>
    </div>
  )
}

function Stat({ label, value }) {
  return (
    <div className="bg-bg-raised/60 border border-border-main rounded-xl px-3 py-2">
      <p className="text-[10px] text-text-muted font-mono">{label}</p>
      <p className="text-[15px] text-text-main font-semibold font-mono">{value}</p>
    </div>
  )
}
