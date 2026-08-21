import json
import os
import time
from datetime import datetime, timezone

from src.domain import (
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
        max_duration_seconds: int = int(os.environ.get("MAXIMUM_INVESTIGATION_DURATION_SECONDS", "210")),
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

        # System prompt setting the constraints
        mode_desc = "FAST MODE: Output precisely what is requested. Be concise." if self.investigation_mode == "fast" else "ITERATIVE MODE: Use tools to gather evidence."
        self.messages.append(
            LLMMessage(
                role=MessageRole.SYSTEM,
                content=(
                    "You are Aegis, an incident investigation AI. "
                    "You must follow a strict process: Collect Evidence -> Form Hypotheses -> Verify Hypotheses -> Conclude -> Recommend. "
                    "Only base your conclusions on evidence gathered. "
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

    def _parse_recommendation_and_record(self, content: str) -> None:
        try:
            rec_str = next(line for line in content.split("\n") if "RECOMMENDATION:" in line)
            parts = [p.strip() for p in rec_str.split("|")]
            action = parts[0].replace("RECOMMENDATION:", "").strip()
        except StopIteration:
            return
            
        reason = "Unknown"
        impact = "Unknown"
        risk = "Unknown"
        
        for p in parts[1:]:
            if p.startswith("REASON:"): reason = p.replace("REASON:", "").strip()
            elif p.startswith("IMPACT:"): impact = p.replace("IMPACT:", "").strip()
            elif p.startswith("RISK:"): risk = p.replace("RISK:", "").strip()
        
        rec = RemediationRecommendation(
            action=action,
            reason=reason,
            expected_impact=impact,
            risk_uncertainty=risk,
            supporting_evidence_ids=[e.id for e in self.incident.evidence]
        )
        self.repository.save_recommendation(self.incident.id, rec)
        self._record_event("RECOMMENDATION_PRODUCED", f"Action: {action}")
        self._transition_to(InvestigationStatus.RECORDED)

    def _fast_mode_collect_evidence(self) -> None:
        evidence_bundle = []
        target = self.incident.affected_service or "unknown-service"
        
        # In FAST mode we query metrics and logs for the target service directly
        res_metrics = self.tool_dispatcher.dispatch(ToolCall(id="initial_m", name="query_metrics", arguments={"query": f"{target} latency"}))
        res_logs = self.tool_dispatcher.dispatch(ToolCall(id="initial_l", name="search_logs", arguments={"query": target}))
        self.tool_call_count += 2

        # Extract real evidence objects and append without duplication
        from src.domain.models import ToolResult
        
        existing_ids = {e.id for e in self.incident.evidence}
        for res in (res_metrics, res_logs):
            if isinstance(res, ToolResult):
                for e in res.evidence:
                    if e.id not in existing_ids:
                        self.incident.evidence.append(e)
                        existing_ids.add(e.id)
                        
        self.repository.save_incident(self.incident)

        m_summary = res_metrics.summary if isinstance(res_metrics, ToolResult) else res_metrics
        l_summary = res_logs.summary if isinstance(res_logs, ToolResult) else res_logs
        
        evidence_bundle.append(f"--- Initial Metrics ---\n{json.dumps(m_summary, default=str)}")
        evidence_bundle.append(f"--- Initial Logs ---\n{json.dumps(l_summary, default=str)}")
        
        self.messages.append(
            LLMMessage(
                role=MessageRole.USER,
                content=f"Incident: {self.incident.title}\nDescription: {self.incident.description}\n"
                        f"Affected Service: {self.incident.affected_service or 'Unknown'}\n\n"
                        "INITIAL EVIDENCE BUNDLE:\n" + "\n".join(evidence_bundle) + "\n\n"
                        "Based ONLY on this initial evidence, provide at least two competing hypotheses using the format 'HYPOTHESIS: <description>'.\n"
                        "You MUST also return a structured tool call for verification. Identify which missing evidence would distinguish the hypotheses, and make up to 2 tool calls to request it."
            )
        )
        self._record_event("INITIAL_EVIDENCE_COLLECTED", "Deterministically gathered metrics and logs.")
        self._transition_to(InvestigationStatus.FORMING_HYPOTHESES)

    def _fast_mode_execute_verification(self, tool_calls: list[ToolCall]) -> None:
        self._transition_to(InvestigationStatus.VERIFYING_HYPOTHESES)
        
        from src.domain.models import ToolResult
        evidence_results = []
        collected_evidence_ids = []
        existing_ids = {e.id for e in self.incident.evidence}
        
        for idx, tool_call in enumerate(tool_calls[:2]): # Max 2 verification calls
            self._record_event("TOOL_EXECUTION", f"Deterministic verification: {tool_call.name}", tool_name=tool_call.name)
            res = self.tool_dispatcher.dispatch(tool_call)
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
            
        # Link collected verification evidence to hypotheses conceptually
        for h in self.incident.hypotheses:
            h.supporting_evidence_ids.extend(collected_evidence_ids)
            
        self.repository.save_incident(self.incident)
        
        self.messages.append(
            LLMMessage(
                role=MessageRole.USER,
                content=f"DETERMINISTIC VERIFICATION EVIDENCE:\n" + "\n".join(evidence_results) + "\n\n"
                        "Verification is complete. Based on all evidence, provide your final diagnosis using the format 'DIAGNOSIS: <conclusion>' "
                        "and a remediation recommendation using the format 'RECOMMENDATION: <action> | REASON: <reason> | IMPACT: <impact> | RISK: <risk>'"
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
            hypos = [line.replace("HYPOTHESIS:", "").strip() for line in content.split("\n") if "HYPOTHESIS:" in line]
            if hypos:
                for i, h in enumerate(hypos):
                    hypo = Hypothesis(id=f"hypo_{int(time.time() * 1000)}_{i}", description=h, status=HypothesisStatus.UNKNOWN, confidence=None)
                    self.incident.hypotheses.append(hypo)
                self.repository.save_incident(self.incident)
                self._record_event("HYPOTHESES_FORMED", f"Formed {len(hypos)} hypotheses.")
                
                tool_calls = response.tool_calls or []
                self._fast_mode_execute_verification(tool_calls)
            else:
                self.messages.append(LLMMessage(role=MessageRole.USER, content="Please provide hypotheses in the requested format 'HYPOTHESIS: <description>'."))
            return
            
        elif self.incident.status in (InvestigationStatus.CONCLUDING, InvestigationStatus.RECOMMENDATION):
            if "DIAGNOSIS:" in content:
                diagnosis_line = [line for line in content.split("\n") if "DIAGNOSIS:" in line]
                diagnosis = diagnosis_line[0].replace("DIAGNOSIS:", "").strip() if diagnosis_line else "Unknown"
                self._record_event("DIAGNOSIS_PRODUCED", diagnosis)
                
                if "RECOMMENDATION:" in content:
                    self._transition_to(InvestigationStatus.RECOMMENDATION)
                    self._parse_recommendation_and_record(content)
                else:
                    self._transition_to(InvestigationStatus.RECOMMENDATION)
                    self.messages.append(LLMMessage(role=MessageRole.USER, content="Please output a remediation recommendation using the format 'RECOMMENDATION: <action> | REASON: <reason> | IMPACT: <impact> | RISK: <risk>'"))
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

                result = self.tool_dispatcher.dispatch(tool_call)

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
                        content="Evidence collection is complete. Stop using tools. Based only on the evidence collected so far, provide at least two competing hypotheses using the format 'HYPOTHESIS: <description>'",
                    )
                )
            elif self.incident.status == InvestigationStatus.VERIFYING_HYPOTHESES and (self.useful_tool_calls_in_stage >= 2 or self.duplicate_call_count >= 2):
                self._transition_to(InvestigationStatus.CONCLUDING)
                self.messages.append(
                    LLMMessage(
                        role=MessageRole.USER,
                        content="Verification is complete. Stop using tools. Provide your final diagnosis using the format 'DIAGNOSIS: <conclusion>' and a remediation recommendation using the format 'RECOMMENDATION: <action> | REASON: <reason> | IMPACT: <impact> | RISK: <risk>'",
                    )
                )
            return

        content = response.content or ""
        
        if self.incident.status == InvestigationStatus.COLLECTING_EVIDENCE:
            self._transition_to(InvestigationStatus.FORMING_HYPOTHESES)
            self.messages.append(LLMMessage(role=MessageRole.USER, content="Evidence collection is complete. Stop using tools. Based only on the evidence collected so far, provide at least two competing hypotheses using the format 'HYPOTHESIS: <description>'"))
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
                self.messages.append(LLMMessage(role=MessageRole.USER, content="Now use tools to gather more evidence to verify or refute these hypotheses. Once verified, output 'DIAGNOSIS: <conclusion>' and a remediation recommendation using the format 'RECOMMENDATION: <action> | REASON: <reason> | IMPACT: <impact> | RISK: <risk>'"))
            else:
                self.messages.append(LLMMessage(role=MessageRole.USER, content="Please provide hypotheses in the requested format 'HYPOTHESIS: <description>'."))
            return

        elif self.incident.status == InvestigationStatus.VERIFYING_HYPOTHESES:
            if "DIAGNOSIS:" in content:
                diagnosis_line = [line for line in content.split("\n") if "DIAGNOSIS:" in line]
                diagnosis = diagnosis_line[0].replace("DIAGNOSIS:", "").strip() if diagnosis_line else "Unknown"
                
                self._transition_to(InvestigationStatus.CONCLUDING)
                self._record_event("DIAGNOSIS_PRODUCED", diagnosis)
                
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
            # In FAST mode, we only provide tools during FORMING_HYPOTHESES for structured verification
            if self.incident.status != InvestigationStatus.FORMING_HYPOTHESES:
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
