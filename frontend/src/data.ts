export type ViewId = 'workbench' | 'overview' | 'market' | 'paper' | 'screening' | 'methodology' | 'runs'

export const INTENT_LABELS: Record<string, string> = {
  'core.conversation.answer': '普通对话',
  stock_check: '个股体检',
  stock_compare: '多股比较',
  stock_screen: '股票筛选',
  sector_research: '行业研究',
  distill_kol_logic: '逻辑蒸馏',
  review_thesis: '判断复核',
  methodology_evolution: '方法论演进',
}

export const ACTIVE_RUN_STATUSES = new Set([
  'received',
  'identifying_intent',
  'ready',
  'running',
  'waiting_external',
  'waiting_approval',
])
