import { useState, useEffect, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Trophy, ShoppingBag, Sword, ChartBar, ArrowsClockwise, X, Fish, Sparkle, Broom, Trash, Warning } from '@phosphor-icons/react'
import { Select } from './SharedComponents'

const API = 'http://127.0.0.1:7327'

const tabTransition = {
  initial: { opacity: 0, y: 6 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -6 },
}

const DELAY_LABELS = {
  miss: '空竿',
  junk: '垃圾',
  common: '普通',
  rare: '稀有',
  epic: '史诗',
  legendary: '传说',
  mythic: '神话',
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

// 装备属性：武器纯攻击，护甲纯防御，饰品偏攻击，坐骑偏防御
function equipmentStats(item) {
  if (item.slot === 'weapon') return `攻击 +${item.power}`
  if (item.slot === 'armor') return `防御 +${item.power}`
  const attack = item.slot === 'mount'
    ? Math.floor(item.power / 2)
    : Math.ceil(item.power / 2)
  return `攻 +${attack} · 防 +${item.power - attack}`
}

export default function PointsPanel() {
  const [groups, setGroups] = useState([])
  const [chatId, setChatId] = useState('')
  const [view, setView] = useState('board') // board | shop | duels | fishing | overview
  const [scope, setScope] = useState('total')
  const [rows, setRows] = useState([])
  const [overview, setOverview] = useState(null)
  const [catalog, setCatalog] = useState({ slots: [], items: [] })
  const [owned, setOwned] = useState([])
  const [duels, setDuels] = useState([])
  const [fishing, setFishing] = useState(null)
  const [expansion, setExpansion] = useState(null)
  const [expMember, setExpMember] = useState('')
  const [expBusy, setExpBusy] = useState(false)
  const [fishMember, setFishMember] = useState('')
  const [fishBusy, setFishBusy] = useState(false)
  const [balance, setBalance] = useState(0)
  const [power, setPower] = useState(null)
  const [shopMember, setShopMember] = useState('')
  const [shopSlot, setShopSlot] = useState('')
  const [detail, setDetail] = useState(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [earnedDelta, setEarnedDelta] = useState('')
  const [balanceDelta, setBalanceDelta] = useState('')
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

  const loadFishing = useCallback(async (memberId) => {
    if (!chatId) return
    const query = memberId ? `&sender_id=${encodeURIComponent(memberId)}` : ''
    const data = await api(`/api/fishing?chat_id=${encodeURIComponent(chatId)}${query}`)
    if (data.ok) setFishing(data)
    else setMessage(data.error || '加载钓鱼数据失败')
  }, [chatId])

  const loadExpansion = useCallback(async (memberId) => {
    if (!chatId || !memberId) return
    const data = await api(
      `/api/expansion?chat_id=${encodeURIComponent(chatId)}&sender_id=${encodeURIComponent(memberId)}`
    )
    if (data.ok) setExpansion(data.expansion)
    else setMessage(data.error || '加载扩展玩法失败')
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
    if (view === 'fishing') {
      const member = fishMember || rows[0]?.sender_id || ''
      if (member && member !== fishMember) setFishMember(member)
      loadFishing(member)
    }
    if (view === 'expansion') {
      const member = expMember || rows[0]?.sender_id || ''
      if (member && member !== expMember) setExpMember(member)
      loadExpansion(member)
    }
  }, [view, chatId, shopMember, rows, loadShop, loadDuels, fishMember, loadFishing, expMember, loadExpansion])

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
    if (view === 'fishing') await loadFishing(fishMember)
    if (view === 'expansion') await loadExpansion(expMember)
    if (detail) await openMember(detail.sender_id)
  }

  async function adjustPoints(kind) {
    const raw = kind === 'balance' ? balanceDelta : earnedDelta
    const value = parseInt(raw, 10)
    const label = kind === 'balance' ? '灵石' : '修炼值'
    if (!detail || !Number.isFinite(value) || value === 0) {
      setMessage(`请输入非零的${label}`)
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
          kind,
        }),
      })
      if (data.ok) {
        const sign = value > 0 ? '+' : ''
        setMessage(
          `已调整${label} ${sign}${value}，当前修炼值 ${data.earned} · 灵石 ${data.balance}`,
        )
        if (kind === 'balance') setBalanceDelta('')
        else setEarnedDelta('')
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
        setMessage(`已购买 ${item.name}，灵石 ${data.balance}`)
      } else if (data.code === 'insufficient') {
        setMessage(`灵石不足：${item.name} 需要 ${item.price} 灵石`)
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

  async function buyFishingRod(rod) {
    if (!fishMember) {
      setMessage('请先选择成员')
      return
    }
    setFishBusy(true)
    try {
      const data = await api('/api/fishing/buy_rod', {
        method: 'POST',
        body: JSON.stringify({
          chat_id: chatId,
          sender_id: fishMember,
          rod_id: rod.id,
        }),
      })
      if (data.ok) setMessage(`已购买 ${rod.name}，灵石 ${data.balance}`)
      else if (data.code === 'insufficient') {
        setMessage(`灵石不足：${rod.name} 需要 ${rod.price} 灵石`)
      } else if (data.code === 'owned') {
        setMessage(`已经拥有 ${rod.name}`)
      } else {
        setMessage(data.error || '购买失败')
      }
      await loadFishing(fishMember)
    } finally {
      setFishBusy(false)
    }
  }

  async function equipFishingRod(rod) {
    if (!fishMember) return
    setFishBusy(true)
    try {
      const data = await api('/api/fishing/equip_rod', {
        method: 'POST',
        body: JSON.stringify({
          chat_id: chatId,
          sender_id: fishMember,
          rod_id: rod.id,
        }),
      })
      if (!data.ok) setMessage('切换鱼竿失败')
      await loadFishing(fishMember)
    } finally {
      setFishBusy(false)
    }
  }

  async function buyFishingBait(bait, quantity = 10) {
    if (!fishMember) {
      setMessage('请先选择成员')
      return
    }
    setFishBusy(true)
    try {
      const data = await api('/api/fishing/buy_bait', {
        method: 'POST',
        body: JSON.stringify({
          chat_id: chatId,
          sender_id: fishMember,
          bait_id: bait.id,
          quantity,
        }),
      })
      if (data.ok) setMessage(`已购买 ${bait.name}×${quantity}，灵石 ${data.balance}`)
      else if (data.code === 'insufficient') {
        setMessage(`灵石不足：${bait.name} 需要 ${bait.price * quantity} 灵石`)
      } else {
        setMessage(data.error || '购买失败')
      }
      await loadFishing(fishMember)
    } finally {
      setFishBusy(false)
    }
  }

  const groupOptions = groups.map(g => ({
    value: g.chat_id,
    label: `${g.group_name}（${g.member_count}人）`,
  }))
  const memberOptions = rows.map(r => ({
    value: r.sender_id,
    label: `${r.sender_name}（${r.points}修炼值）`,
  }))
  const ownedMap = Object.fromEntries(owned.map(o => [o.item_id, o]))
  const shopSlotGroups = catalog.slots
    .map(slot => ({
      ...slot,
      items: catalog.items
        .filter(item => item.slot === slot.id)
        .sort((a, b) => a.price - b.price),
    }))
    .filter(slot => slot.items.length > 0)
  const activeShopSlot = shopSlotGroups.find(slot => slot.id === shopSlot) || shopSlotGroups[0]
  const fishInventory = fishing?.inventory || []
  const maxDaily = overview?.daily?.reduce((m, d) => Math.max(m, d.points), 0) || 0
  const distribution = overview?.level_distribution || {}
  const distributionTotal = Object.values(distribution).reduce((a, b) => a + b, 0)

  return (
    <div className="space-y-6 max-w-6xl">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold text-text-main tracking-tight">修炼值</h2>
          <p className="text-xs text-text-muted mt-1">
            发言赚修炼值、买装备、切磋抢灵石。累计修炼值决定排名，灵石用于消费。
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
        <Pill active={view === 'fishing'} onClick={() => setView('fishing')}>
          <Fish size={14} className="inline mr-1" />钓鱼
        </Pill>
        <Pill active={view === 'expansion'} onClick={() => setView('expansion')}>
          <Sparkle size={14} className="inline mr-1" />扩展玩法
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
                    <p className="text-sm text-text-muted">这个群还没有修炼记录。</p>
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
                            <span className="block text-[14px] font-semibold text-text-main font-mono">{row.points}修炼值</span>
                            <span className="block text-[11px] text-text-muted font-mono">灵石 {row.balance}</span>
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
                      <Stat label="累计修炼值" value={detail.earned} />
                      <Stat label="灵石" value={detail.balance} />
                      <Stat label="本月" value={detail.season_points} />
                      <Stat label="今日" value={detail.day_points} />
                      <Stat label="战力" value={detail.power?.total ?? 0} />
                      <Stat label="攻击" value={detail.power?.attack ?? 0} />
                      <Stat label="防御" value={detail.power?.defense ?? 0} />
                      <Stat label="连续活跃" value={`${detail.streak_days}天`} />
                    </div>
                    {detail.next_level && (
                      <p className="text-xs text-text-muted mb-4">
                        距 Lv{detail.next_level.level} {detail.next_level.title} 还差{' '}
                        {Math.max(0, detail.next_level.min_points - detail.earned)} 修炼值
                      </p>
                    )}
                    <div className="border-t border-border-main pt-4">
                      <p className="text-[13px] font-semibold text-text-main mb-1">管理员调整</p>
                      <p className="text-[11px] text-text-muted mb-3">
                        修炼值与灵石分开调整，互不影响。
                      </p>
                      <div className="flex items-center gap-2 mb-2">
                        <span className="w-12 shrink-0 text-[13px] text-text-main">修炼值</span>
                        <input
                          type="number"
                          value={earnedDelta}
                          onChange={e => setEarnedDelta(e.target.value)}
                          placeholder="+20 / -10"
                          className="flex-1 min-w-0 bg-bg-raised border border-border-main rounded-lg px-3 py-2 text-[13px] text-text-main font-mono focus:outline-none focus:border-brand-green"
                        />
                        <button
                          disabled={busy}
                          onClick={() => adjustPoints('earned')}
                          className="shrink-0 px-3 py-2 rounded-lg bg-brand-green-light border border-brand-green/20 text-[13px] text-brand-green-hover dark:text-brand-green font-medium disabled:opacity-50 cursor-pointer"
                        >
                          调整修炼值
                        </button>
                      </div>
                      <div className="flex items-center gap-2 mb-2">
                        <span className="w-12 shrink-0 text-[13px] text-text-main">灵石</span>
                        <input
                          type="number"
                          value={balanceDelta}
                          onChange={e => setBalanceDelta(e.target.value)}
                          placeholder="+100 / -50"
                          className="flex-1 min-w-0 bg-bg-raised border border-border-main rounded-lg px-3 py-2 text-[13px] text-text-main font-mono focus:outline-none focus:border-brand-green"
                        />
                        <button
                          disabled={busy}
                          onClick={() => adjustPoints('balance')}
                          className="shrink-0 px-3 py-2 rounded-lg bg-brand-green-light border border-brand-green/20 text-[13px] text-brand-green-hover dark:text-brand-green font-medium disabled:opacity-50 cursor-pointer"
                        >
                          调整灵石
                        </button>
                      </div>
                      <input
                        type="text"
                        value={reason}
                        onChange={e => setReason(e.target.value)}
                        placeholder="理由（可选，两种调整共用）"
                        className="w-full bg-bg-raised border border-border-main rounded-lg px-3 py-2 text-[13px] text-text-main focus:outline-none focus:border-brand-green"
                      />
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
                <span className="text-xs font-mono text-text-muted">灵石 {balance}</span>
                {power && (
                  <span className="text-xs font-mono text-text-muted">战力 {power.total} · 攻{power.attack} 防{power.defense}</span>
                )}
              </div>

              {shopSlotGroups.length === 0 ? (
                <Card>
                  <p className="text-sm text-text-muted">还没有成员数据</p>
                </Card>
              ) : (
                <>
                  <div className="flex flex-wrap gap-2">
                    {shopSlotGroups.map(slot => {
                      const active = activeShopSlot?.id === slot.id
                      const ownedCount = slot.items.filter(i => ownedMap[i.id]).length
                      return (
                        <button
                          key={slot.id}
                          onClick={() => setShopSlot(slot.id)}
                          className={`flex items-center gap-1.5 px-3.5 py-1.5 rounded-full text-[13px] font-medium border transition-colors cursor-pointer ${
                            active
                              ? 'bg-brand-green-light border-brand-green/30 text-brand-green-hover dark:text-brand-green'
                              : 'bg-bg-main border-border-main text-text-muted hover:text-text-main'
                          }`}
                        >
                          {slot.name}
                          <span className="text-[11px] font-mono opacity-60">{slot.items.length}</span>
                          {ownedCount > 0 && (
                            <span className="text-[10px] opacity-80">已拥有 {ownedCount}</span>
                          )}
                        </button>
                      )
                    })}
                  </div>

                  <div className="bg-bg-main border border-border-main rounded-2xl overflow-hidden">
                    <div className="flex items-baseline justify-between px-5 pt-4 pb-3">
                      <h3 className="text-[14px] font-semibold text-text-main">{activeShopSlot.name}</h3>
                      <p className="text-[11px] text-text-muted">共 {activeShopSlot.items.length} 件 · 按价格从低到高</p>
                    </div>

                    <div className="hidden md:grid grid-cols-[minmax(0,1.6fr)_120px_minmax(0,1fr)_110px_88px] gap-3 px-5 py-2 text-[11px] text-text-muted border-y border-border-main bg-bg-raised/40">
                      <span>装备</span>
                      <span>属性</span>
                      <span>技能</span>
                      <span className="text-right">售价</span>
                      <span className="text-right">操作</span>
                    </div>

                    <div className="divide-y divide-border-main">
                      {activeShopSlot.items.map(item => {
                        const mine = ownedMap[item.id]
                        const skills = item.skill_names || []
                        return (
                          <div
                            key={item.id}
                            className={`grid grid-cols-[minmax(0,1fr)_auto] md:grid-cols-[minmax(0,1.6fr)_120px_minmax(0,1fr)_110px_88px] items-center gap-3 px-5 py-2.5 transition-colors ${
                              mine ? 'bg-brand-green-light/30' : ''
                            } hover:bg-bg-raised`}
                          >
                            <div className="min-w-0">
                              <div className="flex items-center gap-1.5 min-w-0">
                                <span className="text-[13px] text-text-main truncate">{item.name}</span>
                                <span className="shrink-0 rounded px-1 py-0.5 text-[10px] leading-none border border-border-main text-text-muted">
                                  {item.tier_title}
                                </span>
                                {item.set_name && (
                                  <span className="shrink-0 rounded px-1 py-0.5 text-[10px] leading-none border border-border-main text-text-muted">
                                    {item.set_name}
                                  </span>
                                )}
                                {mine?.equipped && (
                                  <span className="shrink-0 text-[10px] text-brand-green">已装备</span>
                                )}
                              </div>
                              {/* 窄屏把属性和价格折到名称下方，避免信息丢失 */}
                              <p className="md:hidden mt-0.5 text-[11px] font-mono text-text-muted truncate">
                                {equipmentStats(item)} · {item.price} 灵石
                                {skills.length > 0 ? ` · ${skills.join('/')}` : ''}
                              </p>
                            </div>

                            <span className="hidden md:block text-[12px] font-mono text-text-main/80 whitespace-nowrap">
                              {equipmentStats(item)}
                            </span>
                            <span className="hidden md:block text-[11px] truncate">
                              {skills.length > 0 ? (
                                <span className="text-brand-green-hover dark:text-brand-green">
                                  {skills.join(' · ')}
                                </span>
                              ) : (
                                <span className="text-text-muted">—</span>
                              )}
                            </span>
                            <span className="hidden md:block text-[12px] font-mono text-right text-text-main whitespace-nowrap">
                              {item.price} 灵石
                            </span>

                            <div className="flex items-center justify-end">
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
                          </div>
                        )
                      })}
                    </div>
                  </div>
                </>
              )}
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
                        ? `抢到 ${d.points_moved} 灵石`
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

          {view === 'fishing' && (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center gap-3">
                <div className="w-64">
                  {memberOptions.length > 0 ? (
                    <Select value={fishMember} onChange={setFishMember} options={memberOptions} />
                  ) : (
                    <p className="text-sm text-text-muted">还没有成员数据</p>
                  )}
                </div>
                {fishing?.member && (
                  <>
                    <span className="text-xs font-mono text-text-muted">灵石 {fishing.member.balance}</span>
                    <span className="text-xs font-mono text-text-muted">
                      本小时 {fishing.member.attempts}/{fishing.member.attempts_limit} 次
                    </span>
                    {fishing.member.daily_cap > 0 && (
                      <span className="text-xs font-mono text-text-muted">
                        今日收益 {fishing.member.earned_today}/{fishing.member.daily_cap}
                      </span>
                    )}
                    <span className="text-xs font-mono text-text-muted">
                      图鉴 {fishing.member.caught_species?.length || 0}/{fishing.member.species_total || 0}
                    </span>
                  </>
                )}
              </div>

              {!fishing ? (
                <Card><p className="text-sm text-text-muted">加载中…</p></Card>
              ) : (
                <>
                  <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
                    <Card>
                      <SectionTitle hint="越高级，稀有鱼概率越高；低级鱼竿也能钓到稀有鱼">
                        鱼竿
                      </SectionTitle>
                      <div className="space-y-2">
                        {(fishing.catalog?.rods || []).map(rod => {
                          const ownedRod = fishInventory.some(
                            row => row.kind === 'rod' && row.item_id === rod.id)
                          const equipped = fishing.member?.rod?.id === rod.id
                          return (
                            <div key={rod.id} className="flex items-center gap-3 p-3 rounded-xl border border-border-main bg-bg-raised/40">
                              <div className="flex-1 min-w-0">
                                <p className="text-[14px] text-text-main truncate">
                                  {rod.name}
                                  {rod.default ? '（默认）' : ''}
                                  {equipped ? ' · 已装备' : ownedRod ? ' · 已拥有' : ''}
                                </p>
                                <p className="text-[11px] text-text-muted font-mono">
                                  稀有 +{Math.round(rod.rare_bonus * 100)}% · 价值 +{Math.round(rod.value_bonus * 100)}%
                                  {rod.price > 0 ? ` · ${rod.price} 灵石` : ''}
                                </p>
                              </div>
                              {equipped ? (
                                <span className="text-[11px] font-mono text-brand-green">使用中</span>
                              ) : ownedRod || rod.default ? (
                                <button
                                  disabled={fishBusy}
                                  onClick={() => equipFishingRod(rod)}
                                  className="px-3 py-1.5 rounded-lg text-[12px] border border-border-main text-text-muted hover:text-text-main transition-colors cursor-pointer disabled:opacity-50"
                                >
                                  切换
                                </button>
                              ) : (
                                <button
                                  disabled={fishBusy}
                                  onClick={() => buyFishingRod(rod)}
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

                    <Card>
                      <SectionTitle hint="每次钓鱼自动消耗 1 个，价格低的优先">
                        鱼饵
                      </SectionTitle>
                      <div className="space-y-2">
                        {(fishing.catalog?.baits || []).map(bait => {
                          const stock = fishInventory.find(
                            row => row.kind === 'bait' && row.item_id === bait.id)
                          return (
                            <div key={bait.id} className="flex items-center gap-3 p-3 rounded-xl border border-border-main bg-bg-raised/40">
                              <div className="flex-1 min-w-0">
                                <p className="text-[14px] text-text-main truncate">
                                  {bait.name} <span className="text-[11px] text-text-muted font-mono">持有 {stock?.quantity || 0}</span>
                                </p>
                                <p className="text-[11px] text-text-muted font-mono">
                                  稀有 +{Math.round(bait.rare_bonus * 100)}% · 价值 +{Math.round(bait.value_bonus * 100)}% · {bait.price} 灵石/个
                                </p>
                              </div>
                              <button
                                disabled={fishBusy}
                                onClick={() => buyFishingBait(bait, 10)}
                                className="px-3 py-1.5 rounded-lg text-[12px] bg-brand-green-light border border-brand-green/20 text-brand-green-hover dark:text-brand-green font-medium cursor-pointer disabled:opacity-50"
                              >
                                买 10 个
                              </button>
                            </div>
                          )
                        })}
                      </div>
                    </Card>
                  </div>

                  <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
                    <Card>
                      <SectionTitle hint="按钓鱼入账灵石排名">钓鱼榜</SectionTitle>
                      {(fishing.board?.value || []).length === 0 ? (
                        <p className="text-sm text-text-muted">本群还没有钓鱼记录。</p>
                      ) : (
                        <div className="space-y-1.5">
                          {fishing.board.value.map((row, index) => (
                            <div key={row.sender_id} className="flex items-center gap-3 text-[12px] font-mono px-2 py-1.5 rounded-lg hover:bg-bg-raised">
                              <span className="text-text-muted w-6">{index + 1}</span>
                              <span className="text-text-main flex-1 truncate">{row.sender_name}</span>
                              <span className="text-text-muted">{row.catches} 条</span>
                              <span className="text-brand-green w-20 text-right">{row.value} 灵石</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </Card>

                    <Card>
                      <SectionTitle hint="单条最重的大鱼">最近上钩 / 大鱼</SectionTitle>
                      {(fishing.board?.biggest || []).length === 0 ? (
                        <p className="text-sm text-text-muted">还没有渔获。</p>
                      ) : (
                        <div className="space-y-1.5">
                          {fishing.board.biggest.slice(0, 8).map((row, index) => (
                            <div key={index} className="flex items-center gap-3 text-[12px] font-mono px-2 py-1.5 rounded-lg hover:bg-bg-raised">
                              <span className="text-text-main flex-1 truncate">
                                {row.sender_name} · {row.species_name}
                              </span>
                              <span className="text-text-muted">
                                {row.weight_g >= 1000 ? `${(row.weight_g / 1000).toFixed(2)}kg` : `${row.weight_g}g`}
                              </span>
                              <span className="text-brand-green w-16 text-right">+{row.amount}</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </Card>
                  </div>

                  <Card>
                    <SectionTitle hint="稀有度越高，机器人越晚回复（最多 60 秒）">
                      反馈延迟
                    </SectionTitle>
                    <div className="flex flex-wrap gap-2 text-[12px] font-mono text-text-muted">
                      {Object.entries(fishing.catalog?.delays || {}).map(([key, seconds]) => (
                        <span key={key} className="px-3 py-1 rounded-full border border-border-main bg-bg-raised/40">
                          {DELAY_LABELS[key] || key} {seconds}s
                        </span>
                      ))}
                    </div>
                  </Card>
                </>
              )}
            </div>
          )}

          {view === 'expansion' && (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center gap-3">
                <div className="w-64">
                  {memberOptions.length > 0 ? (
                    <Select value={expMember} onChange={setExpMember} options={memberOptions} />
                  ) : (
                    <p className="text-sm text-text-muted">还没有成员数据</p>
                  )}
                </div>
                <span className="text-xs text-text-muted">套装 / 任务 / 秘境 / 擂台 / 门派 / 宠物 / 炼丹 / 天象 / 转世</span>
              </div>
              {!expansion ? (
                <Card><p className="text-sm text-text-muted">选择成员后加载扩展玩法数据。</p></Card>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <Card>
                    <SectionTitle hint="每日 / 周常，群内发送「任务」领取">任务</SectionTitle>
                    <div className="space-y-1.5">
                      {expansion.tasks.map(task => (
                        <div key={task.id} className="flex items-center gap-2 text-[12px]">
                          <span className="flex-1 text-text-main truncate">
                            {task.name}
                            <span className="text-text-muted"> · {task.period === 'daily' ? '每日' : '周常'}</span>
                          </span>
                          <span className="font-mono text-text-muted">{task.progress}/{task.target}</span>
                          <span className={task.claimed ? 'text-text-muted' : task.claimable ? 'text-brand-green' : 'text-text-muted'}>
                            {task.claimed ? '已领' : task.claimable ? '可领' : '进行中'}
                          </span>
                        </div>
                      ))}
                    </div>
                  </Card>
                  <Card>
                    <SectionTitle hint="群内发送「秘境」「擂台」">秘境 / 擂台</SectionTitle>
                    <p className="text-[13px] text-text-main">秘境最高第 {expansion.dungeon.best} 层</p>
                    <p className="text-[11px] text-text-muted font-mono mb-2">今日 {expansion.dungeon.attempts}/{expansion.dungeon.limit} 次</p>
                    <p className="text-[13px] text-text-main">擂台 {expansion.arena.entry.points} 分 · 本周第 {expansion.arena.rank || '-'} 名</p>
                    <p className="text-[11px] text-text-muted font-mono">今日 {expansion.arena.used}/{expansion.arena.limit} 次</p>
                    <div className="mt-2 space-y-1">
                      {expansion.arena.board.map((row, i) => (
                        <p key={row.sender_id} className="text-[11px] text-text-muted">
                          {i + 1}. {row.sender_name} {row.points} 分
                        </p>
                      ))}
                    </div>
                  </Card>
                  <Card>
                    <SectionTitle hint="群内发送「门派」「宠物」">门派 / 宠物</SectionTitle>
                    {expansion.guild.guild ? (
                      <p className="text-[13px] text-text-main">
                        【{expansion.guild.guild.name}】Lv{expansion.guild.guild.level}
                        <span className="text-text-muted"> · 帮派攻防 +{Math.round(expansion.guild.bonus * 100)}%</span>
                      </p>
                    ) : (
                      <p className="text-[12px] text-text-muted">本群还没有门派</p>
                    )}
                    <p className="text-[11px] text-text-muted font-mono mt-1">
                      {expansion.guild.member ? `我的身份：${expansion.guild.member.role} · 贡献 ${expansion.guild.member.contribution}` : '未加入门派'}
                    </p>
                    <p className="text-[13px] text-text-main mt-3">
                      {expansion.pet ? `宠物：${expansion.pet.species} Lv${expansion.pet.level}` : '还没有宠物'}
                    </p>
                  </Card>
                  <Card>
                    <SectionTitle hint="群内发送「丹药」「天象」「转世」">炼丹 / 天象 / 转世</SectionTitle>
                    <p className="text-[12px] text-text-main">草药 {expansion.alchemy.herbs} · 技能书 {expansion.alchemy.books}</p>
                    <p className="text-[11px] text-text-muted font-mono mb-2">
                      {expansion.alchemy.pills.map(p => `${p.name}×${p.count}`).join(' / ') || '暂无丹药'}
                    </p>
                    <p className="text-[13px] text-text-main">{expansion.world.weather.name} — {expansion.world.event.name}</p>
                    <p className="text-[11px] text-text-muted">{expansion.world.weather.desc}；{expansion.world.event.desc}</p>
                    <p className="text-[12px] text-text-main mt-2">轮回第 {expansion.rebirth.cycle} 世 · 加成 +{Math.round(expansion.rebirth.bonus * 100)}%</p>
                  </Card>
                  <Card className="md:col-span-2">
                    <SectionTitle hint="穿戴对应套装件数自动激活">套装</SectionTitle>
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                      {expansion.sets.map(set => (
                        <div key={set.id} className="p-3 rounded-xl border border-border-main bg-bg-raised/40">
                          <p className="text-[13px] text-text-main">{set.name} <span className="text-text-muted font-mono">{set.equipped}/{set.pieces}</span></p>
                          {set.bonuses.map((bonus, i) => (
                            <p key={i} className={`text-[11px] ${bonus.active ? 'text-brand-green' : 'text-text-muted'}`}>
                              {bonus.active ? '✓' : '·'} {bonus.desc}
                            </p>
                          ))}
                        </div>
                      ))}
                    </div>
                  </Card>
                </div>
              )}
            </div>
          )}

          {view === 'overview' && (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
              <Card>
                <SectionTitle hint="近 14 天全群获得的修炼值">趋势</SectionTitle>
                {overview ? (
                  <div className="flex items-end gap-1.5 h-40">
                    {overview.daily.map(d => (
                      <div key={d.day} className="flex-1 flex flex-col items-center gap-1" title={`${d.day}：${d.points}修炼值`}>
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
                    <span>今日产生 {overview.day_total} 修炼值</span>
                  </div>
                )}
              </Card>

              <Card>
                <SectionTitle hint="按累计修炼值计算">等级分布</SectionTitle>
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
        群内指令：修炼值 · 我的信息 · 查询 名字 · 修炼榜 · 月榜 · 商店 · 购买 · 战力 · 打坐 · 功法 · 切磋 @某人 · 买免战券 · 钓鱼 · 鱼竿 · 鱼饵 · 鱼篓 · 图鉴 · 钓鱼榜
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
