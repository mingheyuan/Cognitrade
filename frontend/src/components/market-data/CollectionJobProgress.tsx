import { progressValue } from './status'
import type { CollectionProgress } from './types'

export function CollectionJobProgress({ progress }: { progress: CollectionProgress }) {
  const value = progressValue(progress)
  return (
    <div className="mdc-progress">
      <div className="mdc-progress-label"><span>{progress.completed + progress.skipped} / {progress.total} 已处理</span><strong>{value}%</strong></div>
      <div className="mdc-progress-track" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={value} aria-label="采集任务进度">
        <span style={{ width: `${value}%` }} />
      </div>
      <div className="mdc-progress-breakdown">
        <span>运行 {progress.running}</span><span>排队 {progress.queued}</span><span>失败 {progress.failed}</span><span>跳过 {progress.skipped}</span>
      </div>
    </div>
  )
}
