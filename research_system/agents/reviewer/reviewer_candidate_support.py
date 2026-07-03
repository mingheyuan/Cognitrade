from __future__ import annotations


class ReviewerCandidateSupport:
    def extract_review_claims(self, *, source_entries: list[dict]) -> dict:
        candidates = self.extract_candidates(source_entries=source_entries)
        return {
            "candidates": candidates,
            "claim_type_counts": self.claim_type_counts(candidates),
        }

    def extract_candidates(self, *, source_entries: list[dict]) -> list[dict]:
        candidates: list[dict] = []
        for entry in source_entries:
            kind = str(entry.get("kind") or "")
            payload = entry.get("payload") or {}
            entry_id = str(entry.get("entry_id") or kind or "unknown_entry")

            if kind == "report_fragment":
                candidates.extend(self.extract_report_fragment_candidates(entry_id=entry_id, payload=payload))
            elif kind == "source_record" and str(payload.get("source_type") or "") == "distillation_scorecard":
                candidates.extend(self.extract_scorecard_candidates(entry_id=entry_id, payload=payload))
            elif kind == "logic_block":
                candidates.extend(self.extract_logic_block_candidates(entry_id=entry_id, payload=payload))
        return self.dedupe_candidates(candidates)

    def extract_report_fragment_candidates(self, *, entry_id: str, payload: dict) -> list[dict]:
        candidates: list[dict] = []
        risk_text = str(payload.get("risk") or "")
        conclusion_text = str(payload.get("conclusion") or "")
        follow_up_text = str(payload.get("follow_up") or "")
        combined = f"{conclusion_text}\n{risk_text}\n{follow_up_text}"

        expected_direction = self.infer_expected_direction(combined)
        if expected_direction is not None:
            candidates.append(
                {
                    "entry_id": entry_id,
                    "claim_id": entry_id,
                    "claim_type": "direction",
                    "expected_direction": expected_direction,
                    "claim_source": "report_fragment",
                    "claim_text": combined,
                    "window_type": "trading_days",
                    "window_size": 5,
                    "evaluation_start_rule": "next_trading_day_open",
                }
            )

        if self.contains_catalyst_claim(combined):
            candidates.append(
                {
                    "entry_id": entry_id,
                    "claim_id": f"{entry_id}:catalyst",
                    "claim_type": "catalyst",
                    "claim_source": "report_fragment",
                    "claim_text": combined,
                    "window_type": "trading_days",
                    "window_size": 10,
                    "evaluation_start_rule": "next_trading_day_open",
                    "early_invalidation_rule": "catalyst_signal_disappears",
                }
            )

        if self.contains_time_window_claim(combined):
            candidates.append(
                {
                    "entry_id": entry_id,
                    "claim_id": f"{entry_id}:time_window",
                    "claim_type": "time_window",
                    "claim_source": "report_fragment",
                    "claim_text": combined,
                    "window_type": "trading_days",
                    "window_size": self.infer_window_size(combined),
                    "evaluation_start_rule": "next_trading_day_open",
                }
            )
        return candidates

    def extract_scorecard_candidates(self, *, entry_id: str, payload: dict) -> list[dict]:
        candidates: list[dict] = []
        scorecard = payload.get("scorecard") or {}
        setup_label = str(scorecard.get("setup_label") or "")
        expected_direction = self.expected_direction_from_setup(setup_label)
        if expected_direction is not None:
            candidates.append(
                {
                    "entry_id": entry_id,
                    "claim_id": entry_id,
                    "claim_type": "direction",
                    "expected_direction": expected_direction,
                    "claim_source": "distillation_scorecard",
                    "claim_text": str(scorecard),
                    "window_type": "trading_days",
                    "window_size": 5,
                    "evaluation_start_rule": "next_trading_day_open",
                }
            )

        review_strength = str(scorecard.get("review_strength") or "")
        attribution_risk = str(scorecard.get("attribution_risk") or "")
        if review_strength in {"medium", "high"} or attribution_risk in {"mixed", "high"}:
            candidates.append(
                {
                    "entry_id": entry_id,
                    "claim_id": f"{entry_id}:time_window",
                    "claim_type": "time_window",
                    "claim_source": "distillation_scorecard",
                    "claim_text": str(scorecard),
                    "window_type": "trading_days",
                    "window_size": 5,
                    "evaluation_start_rule": "next_trading_day_open",
                }
            )
        return candidates

    def extract_logic_block_candidates(self, *, entry_id: str, payload: dict) -> list[dict]:
        candidates: list[dict] = []
        block_type = str(payload.get("block_type") or "")
        text = str(payload.get("text") or "")
        direction = str(payload.get("direction") or "")
        time_horizon = str(payload.get("time_horizon") or "")

        if block_type == "catalyst" and text:
            candidates.append(
                {
                    "entry_id": entry_id,
                    "claim_id": entry_id,
                    "claim_type": "catalyst",
                    "claim_source": "logic_block",
                    "claim_text": text,
                    "window_type": "trading_days",
                    "window_size": 10,
                    "evaluation_start_rule": "next_trading_day_open",
                    "early_invalidation_rule": "catalyst_signal_disappears",
                }
            )

        if block_type == "claim" and text:
            expected_direction = self.expected_direction_from_logic_block(
                text=text,
                direction=direction,
            )
            if expected_direction is not None:
                candidates.append(
                    {
                        "entry_id": entry_id,
                        "claim_id": f"{entry_id}:direction",
                        "claim_type": "direction",
                        "expected_direction": expected_direction,
                        "claim_source": "logic_block",
                        "claim_text": text,
                        "window_type": "trading_days",
                        "window_size": self.infer_window_size_from_horizon(time_horizon, text),
                        "evaluation_start_rule": "next_trading_day_open",
                    }
                )

            if self.contains_time_window_claim(text) or time_horizon:
                candidates.append(
                    {
                        "entry_id": entry_id,
                        "claim_id": f"{entry_id}:time_window",
                        "claim_type": "time_window",
                        "claim_source": "logic_block",
                        "claim_text": text,
                        "window_type": "trading_days",
                        "window_size": self.infer_window_size_from_horizon(time_horizon, text),
                        "evaluation_start_rule": "next_trading_day_open",
                    }
                )

            if self.contains_catalyst_claim(text):
                candidates.append(
                    {
                        "entry_id": entry_id,
                        "claim_id": f"{entry_id}:catalyst",
                        "claim_type": "catalyst",
                        "claim_source": "logic_block",
                        "claim_text": text,
                        "window_type": "trading_days",
                        "window_size": 10,
                        "evaluation_start_rule": "next_trading_day_open",
                        "early_invalidation_rule": "catalyst_signal_disappears",
                    }
                )
        return candidates

    def dedupe_candidates(self, candidates: list[dict]) -> list[dict]:
        deduped: dict[tuple[str, str], dict] = {}
        for candidate in candidates:
            key = (
                str(candidate.get("entry_id") or ""),
                str(candidate.get("claim_type") or ""),
            )
            existing = deduped.get(key)
            if existing is None:
                deduped[key] = candidate
                continue
            existing_text = str(existing.get("claim_text") or "")
            current_text = str(candidate.get("claim_text") or "")
            if len(current_text) > len(existing_text):
                deduped[key] = candidate
        return list(deduped.values())

    def claim_type_counts(self, candidates: list[dict]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for candidate in candidates:
            claim_type = str(candidate.get("claim_type") or "unknown")
            counts[claim_type] = counts.get(claim_type, 0) + 1
        return counts

    def infer_expected_direction(self, text: str) -> str | None:
        lowered = text.lower()
        if any(token in lowered for token in ("跑赢", "outperform", "偏强", "强于", "relative strength")):
            return "outperform"
        if any(token in lowered for token in ("跑输", "underperform", "偏弱", "弱于")):
            return "underperform"
        return None

    def expected_direction_from_setup(self, setup_label: str) -> str | None:
        if setup_label in {"momentum_expansion", "breadth_confirmed_strength", "index_weak_but_breadth_resilient"}:
            return "outperform"
        if setup_label in {"weak_without_support", "weak_tape_but_catalysts_alive"}:
            return "underperform"
        return None

    def expected_direction_from_logic_block(self, *, text: str, direction: str) -> str | None:
        normalized_direction = direction.strip().lower()
        if normalized_direction == "bullish":
            return "outperform"
        if normalized_direction == "bearish":
            return "underperform"
        inferred = self.infer_expected_direction(text)
        if inferred is not None:
            return inferred
        if any(token in text for token in ("受益", "改善", "增强", "强化")):
            return "outperform"
        if any(token in text for token in ("承压", "走弱", "回落", "减弱")):
            return "underperform"
        return None

    def contains_catalyst_claim(self, text: str) -> bool:
        lowered = text.lower()
        markers = (
            "催化", "催化剂", "政策", "监管", "审批", "注册", "临床", "出海", "医保",
            "catalyst", "policy", "regulatory", "approval", "trial", "licensing",
        )
        return any(marker in lowered for marker in markers)

    def contains_time_window_claim(self, text: str) -> bool:
        lowered = text.lower()
        markers = (
            "短期", "近期", "未来", "几日", "几周", "下周", "本周", "窗口", "trade window",
            "near term", "short term", "next week", "coming days", "window",
        )
        return any(marker in lowered for marker in markers)

    def infer_window_size(self, text: str) -> int:
        lowered = text.lower()
        if any(marker in lowered for marker in ("几周", "下周", "next week")):
            return 10
        return 5

    def infer_window_size_from_horizon(self, time_horizon: str, text: str) -> int:
        if time_horizon in {"中期", "长期"}:
            return 10
        return self.infer_window_size(text)
