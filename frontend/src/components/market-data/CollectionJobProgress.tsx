import { progressValue } from './status'
import type { CollectionProgress } from './types'

export function CollectionJobProgress({ progress }: { progress: CollectionProgress }) {
  const value = progressValue(progress)
  const settled = progress.completed + progress.failed + progress.skipped
  const provider = progress.provider
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
      </div>
      {provider && provider.total > 0 && settled < progress.total ? (
        <small className="mdc-provider-progress">
          上游阶段：{providerPhase} · {provider.completed}/{provider.total}
          {provider.snapshot_type ? ` · ${provider.snapshot_type}` : ''}
        </small>
      ) : null}
    </div>
  )
}
