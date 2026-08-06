import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { ArtifactArchive } from './ArtifactArchive'
import rendererContract from './finance-artifact-renderers.json'
import {
  FINANCE_ARTIFACT_SCHEMA_FIELDS,
  FINANCE_ARTIFACT_TYPES,
} from './generated/finance-artifact-contracts'
import {
  FINANCE_ARTIFACT_RENDERERS,
  financeArtifactRendererName,
} from './registry'

describe('Finance Artifact renderer coverage', () => {
  afterEach(cleanup)

  it('owns one dedicated renderer for every declared Finance artifact', () => {
    expect(Object.keys(FINANCE_ARTIFACT_RENDERERS)).toEqual([...FINANCE_ARTIFACT_TYPES])
    for (const artifactType of FINANCE_ARTIFACT_TYPES) {
      const renderer = FINANCE_ARTIFACT_RENDERERS[artifactType]
      expect(renderer.name).toBe(financeArtifactRendererName(artifactType))
      expect(renderer.name.toLowerCase()).not.toMatch(/generic|fallback|unknown|rawjson/)
    }
    expect(Object.keys(rendererContract.artifacts)).toEqual([...FINANCE_ARTIFACT_TYPES])
  })

  it('renders all nested business values without exposing sensitive keys or raw JSON', () => {
    render(
      <ArtifactArchive
        artifacts={[
          {
            artifact_id: 'artifact-fund-analysis',
            artifact_type: 'fund_analysis',
            schema_version: '2',
            artifact_version: 1,
            status: 'active',
            confidence: 0.82,
            created_at: '2026-08-05T08:00:00Z',
            content: {
              quality_status: 'available',
              fund_category: '混合型',
              candidate_count: 1,
              recommendations: [{ fund_code: '110011', reason: '长期收益和回撤更均衡' }],
              candidates: [{ fund_code: '110011', fund_name: '测试基金', score: 88.6 }],
              selection_policy: { description: '同类基金横向比较' },
              api_key: 'must-not-render',
            },
            source_refs: [{ type: 'fund_nav', provider: 'eastmoney_via_akshare', id: 'fund:110011' }],
            risk_flags: ['benchmark_definition_missing'],
          },
        ]}
      />,
    )

    expect(screen.getByText('完整分析档案')).toBeInTheDocument()
    expect(screen.getByText('基金分析结果')).toBeInTheDocument()
    expect(screen.getByText('测试基金')).toBeInTheDocument()
    expect(screen.getByText('长期收益和回撤更均衡')).toBeInTheDocument()
    expect(screen.getByText('同类基金横向比较')).toBeInTheDocument()
    expect(screen.queryByText('must-not-render')).not.toBeInTheDocument()
    expect(document.body.textContent).not.toContain('"fund_code"')
  })

  it('renders every schema-declared top-level business field for every artifact type', () => {
    for (const artifactType of FINANCE_ARTIFACT_TYPES) {
      const Renderer = FINANCE_ARTIFACT_RENDERERS[artifactType]
      const topLevelFields = FINANCE_ARTIFACT_SCHEMA_FIELDS[artifactType]
        .filter((field) => !field.includes('.') && !field.includes('[]'))
      const content = Object.fromEntries(
        topLevelFields.map((field) => [field, `字段覆盖:${artifactType}:${field}`]),
      )
      const result = render(
        <Renderer
          artifact={{ artifact_type: artifactType, content }}
          artifactType={artifactType}
          content={content}
        />,
      )
      for (const field of topLevelFields) {
        expect(result.getByText(`字段覆盖:${artifactType}:${field}`)).toBeInTheDocument()
      }
      result.unmount()
    }
  })

  it('keeps superseded and rejected artifact versions available for audit', () => {
    render(<ArtifactArchive artifacts={[
      {
        artifact_id: 'old-report', artifact_type: 'research_report', status: 'superseded',
        content: { conclusion: '历史版本结论必须保留' },
      },
      {
        artifact_id: 'rejected-report', artifact_type: 'research_report', status: 'rejected',
        content: { conclusion: '被拒绝版本也可审计' },
      },
    ]} />)

    expect(screen.getByText('历史与已拒绝版本')).toBeInTheDocument()
    expect(screen.getByText('历史版本结论必须保留')).toBeInTheDocument()
    expect(screen.getByText('被拒绝版本也可审计')).toBeInTheDocument()
  })
})
