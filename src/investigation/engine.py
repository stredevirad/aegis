import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any

from src.domain import (
    DiagnosisClassification,
    FinishReason,
    Hypothesis,
    HypothesisStatus,
    Incident,
    InvestigationStatus,
    LLMMessage,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    MessageRole,
    RemediationRecommendation,
    ToolCall,
    ToolResult,
)
from src.investigation.tools import ToolDispatcher
from src.persistence.events import InvestigationEvent
from src.persistence.repository import SQLiteRepository


class InvestigationEngine:
    def __init__(
        self,
        incident: Incident,
        llm_provider: LLMProvider,
        tool_dispatcher: ToolDispatcher,
        repository: SQLiteRepository,
        max_turns: int = int(os.environ.get("MAXIMUM_LLM_TURNS", "7")),
        max_tool_calls: int = int(os.environ.get("MAXIMUM_TOOL_CALLS", "12")),
        max_duration_seconds: int = int(os.environ.get("MAXIMUM_INVESTIGATION_DURATION_SECONDS", "900")),
        investigation_mode: str = os.environ.get("AEGIS_INVESTIGATION_MODE", "fast"),
    ):
        self.incident = incident
        self.llm_provider = llm_provider
        self.tool_dispatcher = tool_dispatcher
        self.repository = repository

        self.max_turns = max_turns
        self.max_tool_calls = max_tool_calls
        self.max_duration_seconds = max_duration_seconds
        self.investigation_mode = investigation_mode.lower()

        self.messages: list[LLMMessage] = []
        self.turn_count = 0
        self.tool_call_count = 0
        self.start_time = time.time()
        self.tool_call_history: set[str] = set()
        self.duplicate_call_count = 0
        self.useful_tool_calls_in_stage = 0
        self.provider_errors: list[str] = []

        # System prompt setting the constraints
        mode_desc = "FAST MODE: Output precisely what is requested. Be concise." if self.investigation_mode == "fast" else "ITERATIVE MODE: Use tools to gather evidence."
        self.messages.append(
            LLMMessage(
                role=MessageRole.SYSTEM,
                content=(
                    "You are Aegis, an incident investigation AI. "
                    "You must follow a strict process: Collect Evidence -> Form Hypotheses -> Verify Hypotheses -> Conclude -> Recommend. "
                    "Only base your conclusions on evidence gathered. "
                    "If the evidence shows no significant anomaly, you MUST conclude NO_ANOMALY rather than inventing a fault. "
                    "Correlation is not causation — do not infer root causes from normal performance variation. "
                    f"Follow the explicit instructions given to you at each step. {mode_desc}"
                ),
            )
        )

    def _record_event(self, action: str, summary: str, tool_name: str | None = None) -> None:
        event = InvestigationEvent(
            id=f"evt_{int(time.time() * 1000)}",
            incident_id=self.incident.id,
            timestamp=datetime.now(timezone.utc),
            state=self.incident.status,
            action=action,
            summary=summary,
            tool_name=tool_name,
        )
        self.repository.save_event(event)

    def _transition_to(self, new_status: InvestigationStatus, reason: str = "") -> None:
        if self.incident.status != new_status:
            self.incident.status = new_status
            self.useful_tool_calls_in_stage = 0
            self.repository.save_incident(self.incident)
            self._record_event("STATE_TRANSITION", f"Transitioned to {new_status.value}. {reason}")

    def run(self) -> None:
        self._record_event("INVESTIGATION_STARTED", f"Engine started in {self.investigation_mode} mode.")
        self._transition_to(InvestigationStatus.COLLECTING_EVIDENCE)

        if self.investigation_mode == "fast":
            self._fast_mode_collect_evidence()
        else:
            self.messages.append(
                LLMMessage(
                    role=MessageRole.USER,
                    content=f"Incident: {self.incident.title}\nDescription: {self.incident.description}\n"
                            f"Please begin by using tools to collect initial evidence about this incident. "
                            f"When you have gathered enough preliminary evidence, you MUST stop calling tools and provide a summary of findings to proceed.",
                )
            )

        try:
            while self.incident.status not in (
                InvestigationStatus.RECORDED,
                InvestigationStatus.FAILED,
            ):
                self._check_budgets()
                self._step()
        except Exception as e:
            self.messages.append(LLMMessage(role=MessageRole.SYSTEM, content=f"Error: {e}"))
            self._transition_to(InvestigationStatus.FAILED, reason=str(e))
        finally:
            if not self.incident.end_time:
                self.incident.end_time = datetime.now(timezone.utc)
            self.repository.save_incident(self.incident)
            self._record_event("INVESTIGATION_ENDED", f"Final state: {self.incident.status.value}")

    def _check_budgets(self) -> None:
        if self.turn_count >= self.max_turns:
            raise RuntimeError(f"Exceeded MAXIMUM_LLM_TURNS ({self.max_turns})")
        if self.tool_call_count >= self.max_tool_calls:
            raise RuntimeError(f"Exceeded MAXIMUM_TOOL_CALLS ({self.max_tool_calls})")
        if (time.time() - self.start_time) > self.max_duration_seconds:
            raise RuntimeError(
                f"Exceeded MAXIMUM_INVESTIGATION_DURATION ({self.max_duration_seconds}s)"
            )

    def _classify_diagnosis(self, diagnosis_text: str, content: str) -> DiagnosisClassification:
        """Infer diagnosis classification from LLM output."""
        # Check for explicit CLASSIFICATION: tag first (case-insensitive)
        for line in content.split("\n"):
            if "CLASSIFICATION" in line.upper():
                cls_text = line.upper()
                for cls in DiagnosisClassification:
                    if cls.value in cls_text:
                        return cls

        # Infer from diagnosis text
        lower = diagnosis_text.lower()
        no_anomaly_indicators = [
            "no significant anomaly", "no anomaly", "system is healthy",
            "healthy", "no issues", "no fault", "operating normally",
            "within expected", "no degradation",
        ]
        insufficient_indicators = [
            "insufficient", "unable to determine", "cannot confirm",
            "missing evidence", "inconclusive",
        ]

        if any(ind in lower for ind in no_anomaly_indicators):
            return DiagnosisClassification.NO_ANOMALY
        if any(ind in lower for ind in insufficient_indicators):
            return DiagnosisClassification.INSUFFICIENT

        return DiagnosisClassification.PLAUSIBLE

    def _extract_supporting_evidence_ids(self, text: str) -> list[str]:
        """Extract evidence IDs explicitly cited or identify genuinely supporting evidence."""
        available_ids = {e.id: e for e in self.incident.evidence}
        cited_ids = []

        # Look for explicit evidence ID mentions (e.g. ev_m_..., ev_l_..., ev_t_...)
        for token in re.findall(r"\b(?:ev_[a-zA-Z0-9_\-]+|initial_[mlt])\b", text):
            if token in available_ids and token not in cited_ids:
                cited_ids.append(token)

        if cited_ids:
            return cited_ids

        # If no explicit IDs cited, find evidence matching the diagnosed/affected service or verification results
        target_service = (self.incident.affected_service or "").lower()
        diag_lower = (self.incident.diagnosis or "").lower()

        relevant_ids = []
        for e in reversed(self.incident.evidence):
            svc = (e.service_resource or "").lower()
            summary = (e.summary or "").lower()
            data_str = json.dumps(e.data).lower() if isinstance(e.data, (dict, list)) else str(e.data).lower()

            is_relevant = (
                (svc and svc in diag_lower) or
                (target_service and target_service in svc) or
                ("error" in summary or "500" in summary or "fail" in summary) or
                ("error" in data_str or "fail" in data_str)
            )
            if is_relevant and e.id not in relevant_ids:
                relevant_ids.append(e.id)
                if len(relevant_ids) >= 5:
                    break

        return relevant_ids if relevant_ids else [e.id for e in self.incident.evidence[:2]]

    def _parse_recommendation_and_record(self, content: str) -> None:
        rec_str = None
        for line in content.split("\n"):
            clean = line.strip().lstrip("*-# ")
            if "RECOMMENDATION" in clean.upper():
                rec_str = clean
                break
        if not rec_str:
            rec_str = content.strip().split("\n")[-1] if content.strip() else "Investigate upstream services"
            
        parts = [p.strip() for p in rec_str.split("|")]
        action = re.sub(r"(?i)^.*?RECOMMENDATION\s*(\d+)?\s*[:\-\.]*\s*", "", parts[0]).strip(" *`_#") or "Investigate upstream services"
        
        reason = "Unknown"
        impact = "Unknown"
        risk = "Unknown"
        
        for p in parts[1:]:
            p_clean = p.strip()
            p_upper = p_clean.upper()
            if "REASON" in p_upper:
                reason = re.sub(r"(?i)^.*?REASON\s*[:\-\.]*\s*", "", p_clean).strip(" *`_#")
            elif "IMPACT" in p_upper:
                impact = re.sub(r"(?i)^.*?IMPACT\s*[:\-\.]*\s*", "", p_clean).strip(" *`_#")
            elif "RISK" in p_upper:
                risk = re.sub(r"(?i)^.*?RISK\s*[:\-\.]*\s*", "", p_clean).strip(" *`_#")
        
        # Link genuinely supporting evidence rather than all collected evidence
        combined_text = f"{content}\n{self.incident.diagnosis or ''}"
        supporting_ids = self._extract_supporting_evidence_ids(combined_text)

        rec = RemediationRecommendation(
            action=action,
            reason=reason,
            expected_impact=impact,
            risk_uncertainty=risk,
            supporting_evidence_ids=supporting_ids
        )
        self.repository.save_recommendation(self.incident.id, rec)
        self._record_event("RECOMMENDATION_PRODUCED", f"Action: {action}")
        self._transition_to(InvestigationStatus.RECORDED)

    def _store_diagnosis(self, diagnosis: str, content: str) -> None:
        """Store diagnosis text, classification, and record event."""
        classification = self._classify_diagnosis(diagnosis, content)
        self.incident.diagnosis = diagnosis
        self.incident.diagnosis_classification = classification
        self.repository.save_incident(self.incident)
        self._record_event("DIAGNOSIS_PRODUCED", f"[{classification.value}] {diagnosis}")

    def _safe_dispatch(self, tool_call: ToolCall) -> "ToolResult | str":
        """Dispatch a tool call with error handling for provider failures."""
        try:
            res = self.tool_dispatcher.dispatch(tool_call)
            if isinstance(res, str) and res.startswith("Error"):
                self.provider_errors.append(res)
                self._record_event("PROVIDER_ERROR", res, tool_name=tool_call.name)
            return res
        except Exception as e:
            error_msg = f"Provider error during {tool_call.name}: {e!s}"
            self.provider_errors.append(error_msg)
            self._record_event("PROVIDER_ERROR", error_msg, tool_name=tool_call.name)
            return f"Error: {error_msg}"

    def _fast_mode_collect_evidence(self) -> None:
        evidence_bundle = []
        target = self.incident.affected_service or "unknown-service"
        
        from src.domain.models import ToolResult

        # Dispatch all three modalities with error handling
        modality_results = {}
        for label, tc in [
            ("metrics", ToolCall(id="initial_m", name="query_metrics", arguments={"query": target})),
            ("logs", ToolCall(id="initial_l", name="search_logs", arguments={"query": target})),
            ("traces", ToolCall(id="initial_t", name="search_traces", arguments={"service_name": target})),
        ]:
            res = self._safe_dispatch(tc)
            modality_results[label] = res
            self.tool_call_count += 1

        # Extract real evidence objects and append without duplication
        existing_ids = {e.id for e in self.incident.evidence}
        for res in modality_results.values():
            if isinstance(res, ToolResult):
                for e in res.evidence:
                    if e.id not in existing_ids:
                        self.incident.evidence.append(e)
                        existing_ids.add(e.id)
                        
        self.repository.save_incident(self.incident)

        def _bound_summary(summary: Any) -> Any:
            if isinstance(summary, list):
                return summary[:2]  # Keep only 2 items for the initial context
            return summary

        for label in ("metrics", "logs", "traces"):
            res = modality_results[label]
            if isinstance(res, ToolResult):
                summary = _bound_summary(res.summary)
            else:
                summary = res
            evidence_bundle.append(f"--- Initial {label.title()} ---\n{json.dumps(summary, default=str)}")

        # Note any provider errors to the LLM
        provider_note = ""
        if self.provider_errors:
            provider_note = "\n\nNOTE: Some telemetry sources encountered errors:\n" + "\n".join(f"- {e}" for e in self.provider_errors)
        
        self.messages.append(
            LLMMessage(
                role=MessageRole.USER,
                content=f"Incident: {self.incident.title}\nDescription: {self.incident.description}\n"
                        f"Affected Service: {self.incident.affected_service or 'Unknown'}\n\n"
                        "INITIAL EVIDENCE BUNDLE:\n" + "\n".join(evidence_bundle) + provider_note + "\n\n"
                        "Based ONLY on this initial evidence, provide at least two competing hypotheses using the format 'HYPOTHESIS: <description>'.\n"
                        "If the evidence shows no significant anomaly, one hypothesis MUST be 'HYPOTHESIS: No significant anomaly detected.'\n"
                        "You MUST also return a structured tool call for verification. Identify which missing evidence would distinguish the hypotheses, and make up to 2 tool calls to request it."
            )
        )
        self._record_event("INITIAL_EVIDENCE_COLLECTED", f"Gathered metrics, logs, and traces for {target}.")
        self._transition_to(InvestigationStatus.FORMING_HYPOTHESES)

    def _fast_mode_execute_verification(self, tool_calls: list[ToolCall]) -> None:
        self._transition_to(InvestigationStatus.VERIFYING_HYPOTHESES)
        
        from src.domain.models import ToolResult
        evidence_results = []
        collected_evidence_ids = []
        existing_ids = {e.id for e in self.incident.evidence}
        
        for idx, tool_call in enumerate(tool_calls[:2]): # Max 2 verification calls
            self._record_event("TOOL_EXECUTION", f"Deterministic verification: {tool_call.name}", tool_name=tool_call.name)
            res = self._safe_dispatch(tool_call)
            self.tool_call_count += 1
            
            if isinstance(res, ToolResult):
                for e in res.evidence:
                    if e.id not in existing_ids:
                        self.incident.evidence.append(e)
                        existing_ids.add(e.id)
                        collected_evidence_ids.append(e.id)
                summary_data = res.summary
            else:
                summary_data = res
                
            evidence_results.append(f"--- Verification via {tool_call.name} ---\n{json.dumps(summary_data, default=str)}")
            
        # Do NOT blindly link all verification evidence to all hypotheses.
        # Leave evidence linkage empty — the LLM diagnosis will reference
        # specific evidence IDs if it supports a hypothesis.
            
        self.repository.save_incident(self.incident)
        
        self.messages.append(
            LLMMessage(
                role=MessageRole.USER,
                content="DETERMINISTIC VERIFICATION EVIDENCE:\n" + "\n".join(evidence_results) + "\n\n"
                        "Verification is complete. Based on all evidence, provide:\n"
                        "1. Your final diagnosis using the format 'DIAGNOSIS: <conclusion>'\n"
                        "2. A classification: 'CLASSIFICATION: CONFIRMED' or 'CLASSIFICATION: PLAUSIBLE' or 'CLASSIFICATION: NO_ANOMALY' or 'CLASSIFICATION: INSUFFICIENT'\n"
                        "3. A remediation recommendation using the format 'RECOMMENDATION: <action> | REASON: <reason> | IMPACT: <impact> | RISK: <risk>'\n"
                        "If no anomaly was found, output 'DIAGNOSIS: No significant anomaly detected.' and 'CLASSIFICATION: NO_ANOMALY' and 'RECOMMENDATION: None | REASON: System is healthy | IMPACT: Low | RISK: Low'"
            )
        )
        self._transition_to(InvestigationStatus.CONCLUDING)

    def _step_fast(self, response: LLMResponse) -> None:
        self.messages.append(
            LLMMessage(
                role=MessageRole.ASSISTANT,
                content=response.content or "",
                thinking=response.thinking,
                tool_calls=response.tool_calls,
            )
        )
        
        content = response.content or ""
        
        if self.incident.status == InvestigationStatus.FORMING_HYPOTHESES:
            hypos = []
            for line in content.split("\n"):
                clean = line.strip().lstrip("*-#0123456789. ")
                if "HYPOTHESIS" in clean.upper():
                    h_text = re.sub(r"(?i)^.*?HYPOTHESIS\s*(\d+)?\s*[:\-\.]*\s*", "", clean).strip(" *`_#")
                    if h_text:
                        hypos.append(h_text)
            
            # Fallback to bullet points if no explicit HYPOTHESIS prefix
            if not hypos:
                for line in content.split("\n"):
                    clean = line.strip()
                    if clean.startswith(("-", "*", "1.", "2.", "3.")) and len(clean) > 10:
                        h_text = clean.lstrip("-* 123456789.").strip(" *`_#")
                        if h_text:
                            hypos.append(h_text)
            
            # Fallback to non-empty paragraphs
            if not hypos and content.strip():
                paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
                hypos = [p[:200] for p in paragraphs[:2]]

            if hypos:
                for i, h in enumerate(hypos[:4]):
                    hypo = Hypothesis(id=f"hypo_{int(time.time() * 1000)}_{i}", description=h, status=HypothesisStatus.UNKNOWN, confidence=None)
                    self.incident.hypotheses.append(hypo)
                self.repository.save_incident(self.incident)
                self._record_event("HYPOTHESES_FORMED", f"Formed {len(hypos)} hypotheses.")
                
                tool_calls = response.tool_calls or []
                if tool_calls:
                    self._fast_mode_execute_verification(tool_calls)
                else:
                    # No tool calls returned — ask for ONE verification attempt
                    self._transition_to(InvestigationStatus.VERIFYING_HYPOTHESES)
                    self.messages.append(LLMMessage(
                        role=MessageRole.USER,
                        content="You did not provide any verification tool calls. Please provide exactly ONE tool call to verify your hypotheses. "
                                "Available tools: query_metrics, search_logs, search_traces, get_service_health, get_service_dependencies."
                    ))
            else:
                self.messages.append(LLMMessage(role=MessageRole.USER, content="Please provide hypotheses in the requested format 'HYPOTHESIS: <description>'."))
            return
        
        elif self.incident.status == InvestigationStatus.VERIFYING_HYPOTHESES:
            # Handle delayed verification tool calls
            tool_calls = response.tool_calls or []
            if tool_calls:
                self._fast_mode_execute_verification(tool_calls)
            else:
                # No verification possible — mark as INSUFFICIENT and ask for conclusion
                self.incident.diagnosis_classification = DiagnosisClassification.INSUFFICIENT
                self._record_event("VERIFICATION_SKIPPED", "LLM did not provide verification tool calls.")
                self._transition_to(InvestigationStatus.CONCLUDING)
                self.messages.append(LLMMessage(
                    role=MessageRole.USER,
                    content="Verification could not be performed. Provide your best diagnosis with 'DIAGNOSIS: <conclusion>' and 'CLASSIFICATION: INSUFFICIENT' and "
                            "'RECOMMENDATION: <action> | REASON: <reason> | IMPACT: <impact> | RISK: <risk>'"
                ))
            return
            
        elif self.incident.status in (InvestigationStatus.CONCLUDING, InvestigationStatus.RECOMMENDATION):
            has_diagnosis = any("DIAGNOSIS" in line.upper() for line in content.split("\n"))
            has_recommendation = any("RECOMMENDATION" in line.upper() for line in content.split("\n"))
            
            if has_diagnosis or self.incident.status == InvestigationStatus.CONCLUDING:
                diagnosis = "Unknown"
                for line in content.split("\n"):
                    clean = line.strip().lstrip("*-# ")
                    if "DIAGNOSIS" in clean.upper():
                        diagnosis = re.sub(r"(?i)^.*?DIAGNOSIS\s*(\d+)?\s*[:\-\.]*\s*", "", clean).strip(" *`_#")
                        break
                if diagnosis == "Unknown" and content.strip():
                    diagnosis = content.strip().split("\n")[0][:300]
                    
                self._store_diagnosis(diagnosis, content)
                
                if has_recommendation:
                    self._transition_to(InvestigationStatus.RECOMMENDATION)
                    self._parse_recommendation_and_record(content)
                else:
                    self._transition_to(InvestigationStatus.RECOMMENDATION)
                    self.messages.append(LLMMessage(role=MessageRole.USER, content="Please output a remediation recommendation using the format 'RECOMMENDATION: <action> | REASON: <reason> | IMPACT: <impact> | RISK: <risk>'"))
            elif has_recommendation and self.incident.status == InvestigationStatus.RECOMMENDATION:
                self._parse_recommendation_and_record(content)
            else:
                self.messages.append(LLMMessage(role=MessageRole.USER, content="Please output your final diagnosis using 'DIAGNOSIS: <conclusion>'."))
            return

    def _step_iterative(self, response: LLMResponse) -> None:
        self.messages.append(
            LLMMessage(
                role=MessageRole.ASSISTANT,
                content=response.content or "",
                tool_calls=response.tool_calls,
                thinking=response.thinking,
            )
        )

        if response.finish_reason == FinishReason.TOOL_CALL and response.tool_calls:
            for tool_call in response.tool_calls:
                call_hash = f"{tool_call.name}_{json.dumps(tool_call.arguments, sort_keys=True)}"
                if call_hash in self.tool_call_history:
                    self.duplicate_call_count += 1
                    self.messages.append(
                        LLMMessage(
                            role=MessageRole.TOOL,
                            content=json.dumps({"error": "This exact request has already been executed. Stop repeating and summarize the evidence."}),
                            tool_call_id=tool_call.id,
                            tool_name=tool_call.name,
                        )
                    )
                    continue
                else:
                    self.tool_call_history.add(call_hash)
                    
                self.tool_call_count += 1
                self.useful_tool_calls_in_stage += 1
                self._record_event("TOOL_EXECUTION", f"Executing {tool_call.name}", tool_name=tool_call.name)

                result = self._safe_dispatch(tool_call)

                from src.domain.models import ToolResult
                if isinstance(result, ToolResult):
                    existing_ids = {e.id for e in self.incident.evidence}
                    for e in result.evidence:
                        if e.id not in existing_ids:
                            self.incident.evidence.append(e)
                            existing_ids.add(e.id)
                    self.repository.save_incident(self.incident)
                    summary_content = result.summary
                else:
                    summary_content = result

                self.messages.append(
                    LLMMessage(
                        role=MessageRole.TOOL,
                        content=json.dumps(summary_content, default=str),
                        tool_call_id=tool_call.id,
                        tool_name=tool_call.name,
                    )
                )
            
            if self.incident.status == InvestigationStatus.COLLECTING_EVIDENCE and (self.useful_tool_calls_in_stage >= 2 or self.duplicate_call_count >= 2):
                self._transition_to(InvestigationStatus.FORMING_HYPOTHESES)
                self.messages.append(
                    LLMMessage(
                        role=MessageRole.USER,
                        content="Evidence collection is complete. Stop using tools. Based only on the evidence collected so far, provide at least two competing hypotheses using the format 'HYPOTHESIS: <description>'. If no anomaly is detected, you MUST still output 'HYPOTHESIS: No significant anomaly detected.'",
                    )
                )
            elif self.incident.status == InvestigationStatus.VERIFYING_HYPOTHESES and (self.useful_tool_calls_in_stage >= 2 or self.duplicate_call_count >= 2):
                self._transition_to(InvestigationStatus.CONCLUDING)
                self.messages.append(
                    LLMMessage(
                        role=MessageRole.USER,
                        content="Verification is complete. Stop using tools. Provide your final diagnosis using the format 'DIAGNOSIS: <conclusion>' "
                                "and 'CLASSIFICATION: CONFIRMED/PLAUSIBLE/NO_ANOMALY/INSUFFICIENT' "
                                "and a remediation recommendation using the format 'RECOMMENDATION: <action> | REASON: <reason> | IMPACT: <impact> | RISK: <risk>'. "
                                "If no anomaly was found, output 'DIAGNOSIS: No significant anomaly detected.' and 'CLASSIFICATION: NO_ANOMALY' and 'RECOMMENDATION: None | REASON: System is healthy | IMPACT: Low | RISK: Low'",
                    )
                )
            return

        content = response.content or ""
        
        if self.incident.status == InvestigationStatus.COLLECTING_EVIDENCE:
            self._transition_to(InvestigationStatus.FORMING_HYPOTHESES)
            self.messages.append(LLMMessage(role=MessageRole.USER, content="Evidence collection is complete. Stop using tools. Based only on the evidence collected so far, provide at least two competing hypotheses using the format 'HYPOTHESIS: <description>'. If no anomaly is detected, you MUST still output 'HYPOTHESIS: No significant anomaly detected.'"))
            return

        elif self.incident.status == InvestigationStatus.FORMING_HYPOTHESES:
            hypos = [line.replace("HYPOTHESIS:", "").strip() for line in content.split("\n") if "HYPOTHESIS:" in line]
            if hypos:
                for i, h in enumerate(hypos):
                    hypo = Hypothesis(id=f"hypo_{int(time.time() * 1000)}_{i}", description=h, status=HypothesisStatus.UNKNOWN, confidence=None)
                    self.incident.hypotheses.append(hypo)
                self.repository.save_incident(self.incident)
                self._record_event("HYPOTHESES_FORMED", f"Formed {len(hypos)} hypotheses.")
                
                self._transition_to(InvestigationStatus.VERIFYING_HYPOTHESES)
                self.messages.append(LLMMessage(role=MessageRole.USER, content="Now use tools to gather more evidence to verify or refute these hypotheses. Once verified, output 'DIAGNOSIS: <conclusion>' and 'CLASSIFICATION: CONFIRMED/PLAUSIBLE/NO_ANOMALY/INSUFFICIENT' and a remediation recommendation using the format 'RECOMMENDATION: <action> | REASON: <reason> | IMPACT: <impact> | RISK: <risk>'. If no anomaly was found, output 'DIAGNOSIS: No significant anomaly detected.' and 'CLASSIFICATION: NO_ANOMALY' and 'RECOMMENDATION: None | REASON: System is healthy | IMPACT: Low | RISK: Low'"))
            else:
                self.messages.append(LLMMessage(role=MessageRole.USER, content="Please provide hypotheses in the requested format 'HYPOTHESIS: <description>'."))
            return

        elif self.incident.status == InvestigationStatus.VERIFYING_HYPOTHESES:
            if "DIAGNOSIS:" in content:
                diagnosis_line = [line for line in content.split("\n") if "DIAGNOSIS:" in line]
                diagnosis = diagnosis_line[0].replace("DIAGNOSIS:", "").strip() if diagnosis_line else "Unknown"
                
                self._store_diagnosis(diagnosis, content)
                self._transition_to(InvestigationStatus.CONCLUDING)
                
                if "RECOMMENDATION:" in content:
                    self._transition_to(InvestigationStatus.RECOMMENDATION)
                    self._parse_recommendation_and_record(content)
                else:
                    self._transition_to(InvestigationStatus.RECOMMENDATION)
                    self.messages.append(LLMMessage(role=MessageRole.USER, content="Please output a remediation recommendation using the format 'RECOMMENDATION: <action> | REASON: <reason> | IMPACT: <impact> | RISK: <risk>'"))
            else:
                self.messages.append(LLMMessage(role=MessageRole.USER, content="Please use tools to verify or output 'DIAGNOSIS: <conclusion>'."))
            return

        elif self.incident.status == InvestigationStatus.RECOMMENDATION:
            if "RECOMMENDATION:" in content:
                self._parse_recommendation_and_record(content)
            else:
                self.messages.append(LLMMessage(role=MessageRole.USER, content="Please provide a recommendation in the format 'RECOMMENDATION: <action> | REASON: <reason>'."))
            return

    def _step(self) -> None:
        self.turn_count += 1
        
        tools = self.tool_dispatcher.get_tool_definitions()
        if self.investigation_mode == "fast":
            # In FAST mode, provide tools during FORMING_HYPOTHESES and VERIFYING_HYPOTHESES
            if self.incident.status not in (InvestigationStatus.FORMING_HYPOTHESES, InvestigationStatus.VERIFYING_HYPOTHESES):
                tools = []
        else:
            # In ITERATIVE mode, no tools during FORMING_HYPOTHESES or CONCLUDING
            if self.incident.status in (InvestigationStatus.FORMING_HYPOTHESES, InvestigationStatus.CONCLUDING, InvestigationStatus.RECOMMENDATION):
                tools = []

        request = LLMRequest(
            messages=self.messages,
            tools=tools,
        )

        self._record_event("LLM_CALL", f"Turn {self.turn_count} started in state {self.incident.status.value}.")
        response = self.llm_provider.generate(request)

        if response.finish_reason == FinishReason.ERROR:
            raise RuntimeError(f"LLM Error: {response.error}")

        if self.investigation_mode == "fast":
            self._step_fast(response)
        else:
            self._step_iterative(response)
