import type { ComponentType } from 'react'
import type { FinanceArtifactType } from './generated/finance-artifact-contracts'

export type ArtifactRecordView = {
  artifact_id?: string
  artifact_type?: string
  schema_version?: string
  artifact_version?: number
  content?: Record<string, unknown>
  source_refs?: Array<Record<string, unknown>>
  confidence?: number | null
  risk_flags?: string[]
  status?: string
  metadata?: Record<string, unknown>
  created_at?: string
}

export type ArtifactRendererProps = {
  artifact: ArtifactRecordView
  content: Record<string, unknown>
  artifactType: FinanceArtifactType
}

export type ArtifactRendererComponent = ComponentType<ArtifactRendererProps>
