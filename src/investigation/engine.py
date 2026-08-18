import json
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
    MessageRole,
    RemediationRecommendation,
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
        max_turns: int = 6,
        max_tool_calls: int = 12,
        max_duration_seconds: int = 180,
    ):
        self.incident = incident
        self.llm_provider = llm_provider
        self.tool_dispatcher = tool_dispatcher
        self.repository = repository

        self.max_turns = max_turns
        self.max_tool_calls = max_tool_calls
        self.max_duration_seconds = max_duration_seconds

        self.messages: list[LLMMessage] = []
        self.turn_count = 0
        self.tool_call_count = 0
        self.start_time = time.time()

        # System prompt setting the constraints
        self.messages.append(
            LLMMessage(
                role=MessageRole.SYSTEM,
                content=(
                    "You are Aegis, an incident investigation AI. "
                    "You must follow a strict process: Collect Evidence -> Form Hypotheses -> Verify Hypotheses -> Conclude -> Recommend. "
                    "Only base your conclusions on evidence gathered from tools. "
                    "Follow the explicit instructions given to you at each step."
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
            self.repository.save_incident(self.incident)
            self._record_event("STATE_TRANSITION", f"Transitioned to {new_status.value}. {reason}")

    def run(self) -> None:
        self._record_event("INVESTIGATION_STARTED", "Engine started.")
        self._transition_to(InvestigationStatus.COLLECTING_EVIDENCE)

        # Initial context
        self.messages.append(
            LLMMessage(
                role=MessageRole.USER,
                content=f"Incident: {self.incident.title}\nDescription: {self.incident.description}\n"
                        f"Please begin by using tools to collect initial evidence about this incident.",
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

    def _step(self) -> None:
        self.turn_count += 1
        request = LLMRequest(
            messages=self.messages,
            tools=self.tool_dispatcher.get_tool_definitions(),
        )

        self._record_event("LLM_CALL", f"Turn {self.turn_count} started in state {self.incident.status.value}.")
        response = self.llm_provider.generate(request)

        if response.finish_reason == FinishReason.ERROR:
            raise RuntimeError(f"LLM Error: {response.error}")

        # Add assistant message to history
        self.messages.append(
            LLMMessage(
                role=MessageRole.ASSISTANT,
                content=response.content or "",
                tool_calls=response.tool_calls
            )
        )

        # Handle tool calls
        if response.finish_reason == FinishReason.TOOL_CALL and response.tool_calls:
            for tool_call in response.tool_calls:
                self.tool_call_count += 1
                self._record_event(
                    "TOOL_EXECUTION", f"Executing {tool_call.name}", tool_name=tool_call.name
                )

                result = self.tool_dispatcher.dispatch(tool_call)

                self.messages.append(
                    LLMMessage(
                        role=MessageRole.TOOL,
                        content=json.dumps(result, default=str),
                        tool_call_id=tool_call.id,
                        tool_name=tool_call.name,
                    )
                )
            return

        # State machine advancement based on LLM output
        content = response.content or ""
        
        if self.incident.status == InvestigationStatus.COLLECTING_EVIDENCE:
            self._transition_to(InvestigationStatus.FORMING_HYPOTHESES)
            self.messages.append(
                LLMMessage(
                    role=MessageRole.USER,
                    content="Evidence collection complete. Please output at least two competing hypotheses using the format 'HYPOTHESIS: <description>'",
                )
            )
            return

        elif self.incident.status == InvestigationStatus.FORMING_HYPOTHESES:
            # Extract hypotheses
            hypos = [line.replace("HYPOTHESIS:", "").strip() for line in content.split("\n") if "HYPOTHESIS:" in line]
            if hypos:
                for i, h in enumerate(hypos):
                    hypo = Hypothesis(
                        id=f"hypo_{int(time.time() * 1000)}_{i}",
                        description=h,
                        status=HypothesisStatus.UNKNOWN,
                        confidence=None
                    )
                    self.incident.hypotheses.append(hypo)
                self.repository.save_incident(self.incident)
                self._record_event("HYPOTHESES_FORMED", f"Formed {len(hypos)} hypotheses.")
                
                self._transition_to(InvestigationStatus.VERIFYING_HYPOTHESES)
                self.messages.append(
                    LLMMessage(
                        role=MessageRole.USER,
                        content="Now use tools to gather more evidence to verify or refute these hypotheses. Once verified, output 'DIAGNOSIS: <conclusion>'",
                    )
                )
            else:
                self.messages.append(LLMMessage(role=MessageRole.USER, content="Please provide hypotheses in the requested format 'HYPOTHESIS: <description>'."))
            return

        elif self.incident.status == InvestigationStatus.VERIFYING_HYPOTHESES:
            if "DIAGNOSIS:" in content:
                diagnosis_line = [line for line in content.split("\n") if "DIAGNOSIS:" in line]
                diagnosis = diagnosis_line[0].replace("DIAGNOSIS:", "").strip() if diagnosis_line else "Unknown"
                
                self._transition_to(InvestigationStatus.CONCLUDING)
                self._record_event("DIAGNOSIS_PRODUCED", diagnosis)
                
                self._transition_to(InvestigationStatus.RECOMMENDATION)
                self.messages.append(
                    LLMMessage(
                        role=MessageRole.USER,
                        content="Please output a remediation recommendation using the format 'RECOMMENDATION: <action> | REASON: <reason> | IMPACT: <impact> | RISK: <risk>'",
                    )
                )
            else:
                self.messages.append(LLMMessage(role=MessageRole.USER, content="Please use tools to verify or output 'DIAGNOSIS: <conclusion>'."))
            return

        elif self.incident.status == InvestigationStatus.RECOMMENDATION:
            if "RECOMMENDATION:" in content:
                # Basic parsing
                rec_str = next(line for line in content.split("\n") if "RECOMMENDATION:" in line)
                parts = [p.strip() for p in rec_str.split("|")]
                action = parts[0].replace("RECOMMENDATION:", "").strip()
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
                )
                self.repository.save_recommendation(self.incident.id, rec)
                self._record_event("RECOMMENDATION_PRODUCED", f"Action: {action}")
                
                self._transition_to(InvestigationStatus.RECORDED)
            else:
                self.messages.append(LLMMessage(role=MessageRole.USER, content="Please provide a recommendation in the format 'RECOMMENDATION: <action> | REASON: <reason>'."))
            return
