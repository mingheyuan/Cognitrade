export type ViewId = 'workbench' | 'overview' | 'screening' | 'methodology' | 'runs'

export type ThreadItem = {
  id: string
  title: string
  subtitle: string
  intent: string
  time: string
  status: 'completed' | 'running' | 'needs_review'
}

export type EvidenceItem = {
  id: string
  source: string
  title: string
  meta: string
  quality: number
  type: 'market' | 'filing' | 'methodology' | 'news'
}

export const threads: ThreadItem[] = [
  {
    id: 'thread-001',
    title: '宁德时代基本面与估值体检',
    subtitle: '现金流改善能否支撑估值修复？',
    intent: 'stock_check',
    time: '11:42',
    status: 'completed',
  },
  {
    id: 'thread-002',
    title: '人形机器人产业链梳理',
    subtitle: '催化、核心环节与候选公司',
    intent: 'sector_research',
    time: '昨天',
    status: 'running',
  },
  {
    id: 'thread-003',
    title: '银行股横向估值比较',
    subtitle: '招商、宁波、江苏银行同口径比较',
    intent: 'stock_compare',
    time: '周三',
    status: 'completed',
  },
  {
    id: 'thread-004',
    title: '高股息低波动候选池',
    subtitle: '排除 ST，关注现金流质量',
    intent: 'stock_screen',
    time: '周二',
    status: 'needs_review',
  },
]

export const priceSeries = [
  { day: '06/02', stock: 100, benchmark: 100 },
  { day: '06/05', stock: 99.2, benchmark: 100.4 },
  { day: '06/10', stock: 101.5, benchmark: 100.8 },
  { day: '06/13', stock: 103.8, benchmark: 99.9 },
  { day: '06/18', stock: 102.9, benchmark: 100.7 },
  { day: '06/23', stock: 106.4, benchmark: 101.1 },
  { day: '06/26', stock: 108.1, benchmark: 101.9 },
  { day: '07/01', stock: 107.2, benchmark: 102.2 },
  { day: '07/04', stock: 111.6, benchmark: 103.1 },
  { day: '07/07', stock: 113.2, benchmark: 102.7 },
  { day: '07/10', stock: 115.7, benchmark: 103.4 },
]

export const evidenceItems: EvidenceItem[] = [
  {
    id: 'E01',
    source: 'AkShare · 行情快照',
    title: '近 30 日相对沪深 300 超额收益 +12.3pct',
    meta: '2026-07-10 15:00 · 已缓存',
    quality: 96,
    type: 'market',
  },
  {
    id: 'E02',
    source: '公司公告 · 财务数据',
    title: '经营性现金流增速高于净利润增速',
    meta: '2026-04-25 · 一手来源',
    quality: 100,
    type: 'filing',
  },
  {
    id: 'E03',
    source: 'DCF · 方法论快照',
    title: '基准情景价值区间 238–286 元',
    meta: 'v1.8 · 关键假设 4 项',
    quality: 82,
    type: 'methodology',
  },
  {
    id: 'E04',
    source: '公告与行业新闻',
    title: '海外扩产仍面临政策与执行不确定性',
    meta: '近 30 日 · 3 个交叉来源',
    quality: 78,
    type: 'news',
  },
]

export const methodologyWeights = [
  { factor: '现金流', score: 86, previous: 72 },
  { factor: '盈利质量', score: 78, previous: 75 },
  { factor: '估值', score: 72, previous: 66 },
  { factor: '资金面', score: 64, previous: 70 },
  { factor: '趋势', score: 81, previous: 68 },
  { factor: '催化', score: 69, previous: 62 },
]

export const screeningCandidates = [
  {
    rank: 1,
    code: '600036',
    name: '招商银行',
    sector: '银行',
    score: 86,
    change: 1.82,
    valuation: '偏低',
    cashflow: 92,
    momentum: 76,
    tier: '核心池',
    reason: '估值与盈利质量同口径占优',
  },
  {
    rank: 2,
    code: '600900',
    name: '长江电力',
    sector: '公用事业',
    score: 82,
    change: -0.34,
    valuation: '合理',
    cashflow: 95,
    momentum: 68,
    tier: '核心池',
    reason: '现金流确定性高，波动可控',
  },
  {
    rank: 3,
    code: '300750',
    name: '宁德时代',
    sector: '电池',
    score: 79,
    change: 2.84,
    valuation: '合理',
    cashflow: 84,
    momentum: 88,
    tier: '核心池',
    reason: '趋势与现金流共振，估值仍需跟踪',
  },
  {
    rank: 4,
    code: '601318',
    name: '中国平安',
    sector: '保险',
    score: 74,
    change: 0.62,
    valuation: '偏低',
    cashflow: 71,
    momentum: 65,
    tier: '观察池',
    reason: '估值具吸引力，盈利拐点待验证',
  },
  {
    rank: 5,
    code: '000333',
    name: '美的集团',
    sector: '家电',
    score: 72,
    change: -0.18,
    valuation: '合理',
    cashflow: 88,
    momentum: 58,
    tier: '观察池',
    reason: '出海与现金流较稳，动量偏弱',
  },
]

export const runHistory = [
  {
    id: 'run_7f82a1',
    title: '宁德时代基本面与估值体检',
    intent: 'stock_check',
    status: '已完成',
    duration: '48.2s',
    evidence: 27,
    time: '今天 11:42',
  },
  {
    id: 'run_c913e4',
    title: '人形机器人产业链梳理',
    intent: 'sector_research',
    status: '运行中',
    duration: '26.8s',
    evidence: 18,
    time: '今天 10:18',
  },
  {
    id: 'run_a08d61',
    title: '招商、宁波、江苏银行比较',
    intent: 'stock_compare',
    status: '已完成',
    duration: '1m 12s',
    evidence: 46,
    time: '昨天 16:03',
  },
  {
    id: 'run_901bcc',
    title: '高股息低波动候选池',
    intent: 'stock_screen',
    status: '待复核',
    duration: '58.4s',
    evidence: 35,
    time: '周二 14:20',
  },
  {
    id: 'run_442be9',
    title: '复核创新药板块判断',
    intent: 'review_thesis',
    status: '已完成',
    duration: '32.6s',
    evidence: 21,
    time: '周一 09:37',
  },
]

export const marketBreadth = [
  { name: '银行', score: 84, change: 1.2 },
  { name: '电池', score: 78, change: 2.8 },
  { name: '机器人', score: 73, change: 1.6 },
  { name: '创新药', score: 66, change: -0.7 },
  { name: '半导体', score: 61, change: 0.4 },
  { name: '白酒', score: 48, change: -1.1 },
]
