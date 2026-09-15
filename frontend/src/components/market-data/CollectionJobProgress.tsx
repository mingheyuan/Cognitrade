import { collectionSettledCount, progressValue } from './status'
import type { CollectionProgress } from './types'

export function CollectionJobProgress({ progress }: { progress: CollectionProgress }) {
  const value = progressValue(progress)
  const settled = collectionSettledCount(progress)
  const provider = progress.provider
  const postfetch = progress.native_postfetch
  const postfetchStatus = postfetch ? ({
    running: '进行中', completed: '已执行', partial: '仍有缺口', failed: '执行失败',
    not_run: '未执行', cancelled: '已取消',
  }[postfetch.status] || postfetch.status) : null
  const providerPhase = provider ? ({
    starting: '准备数据源',
    collecting: '查询数据源',
    completed: '数据源返回',
    cache_hit: '读取缓存',
    persisting: '校验并落库',
    failed: '数据源调用失败',
  }[provider.phase] || provider.phase) : null
  return (
    <div className="mdc-progress">
      <div className="mdc-progress-label"><span>{settled} / {progress.total} 已结算</span><strong>{value}%</strong></div>
      <div className="mdc-progress-track" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={value} aria-label="采集任务进度">
        <span style={{ width: `${value}%` }} />
      </div>
      <div className="mdc-progress-breakdown">
        <span>成功 {progress.completed}</span><span>运行 {progress.running}</span><span>排队 {progress.queued}</span><span>失败 {progress.failed}</span><span>跳过 {progress.skipped}</span>
        {Boolean(progress.partial) && <span>部分完成 {progress.partial}</span>}
        {Boolean(progress.cancelling) && <span>取消中 {progress.cancelling}</span>}
      </div>
      {provider && provider.total > 0 && settled < progress.total ? (
        <small className="mdc-provider-progress">
          上游阶段：{providerPhase} · 上游返回 {provider.completed}/{provider.total}（不代表已落库）
          {provider.snapshot_type ? ` · ${provider.snapshot_type}` : ''}
        </small>
      ) : null}
      {postfetch ? (
        <small className="mdc-provider-progress">
          原生补搜：{postfetchStatus} · 尝试 {postfetch.attempted} · 成功 {postfetch.succeeded} · 缺口 {postfetch.failed} · 新增快照 {postfetch.persisted_snapshot_count}
          {postfetch.reason_code === 'no_current_day_postfetch_scope' ? ' · 历史日期不使用当前网页回填' : ''}
          {postfetch.reason_code === 'research_deadline_exceeded' ? ' · 共享截止时间已到' : ''}
          {postfetch.reason_code === 'injected_transport_not_configured' ? ' · 补搜来源未配置' : ''}
          {' · 不代表报告自审通过'}
        </small>
      ) : null}
    </div>
  )
}
