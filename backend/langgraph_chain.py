"""
langgraph_chain.py — Multi-Agent LangGraph system.

Agents:
  Supervisor  — deterministic workflow + single LLM tool-router (run_rag vs ticketing)
  RAG Agent   — history check → vector search → generate → verify
  Ticket Agent— preview / create / revise / retrieve / decline

Auxiliary nodes:
  summarise   — condense last bot response
  ack         — positive feedback + clear history
  escalation  — ask about ticket creation after 2 negative-feedback strikes
  decline     — reset ticket/escalation state
"""

import os
import re
import json
import logging
from typing import Any, List, Dict, Optional, Annotated, TypedDict

import prompt
from llm import llm
from utils import create_ticket, retrieve_ticket, extract_json

from langchain_core.messages import BaseMessage, AIMessage, HumanMessage
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from search_documents import search_data

AZURE_OPENAI_MODEL = os.getenv("AZURE_OPENAI_MODEL")
MAX_TURNS = int(os.getenv("MAX_TURNS", 10))
MAX_MESSAGES = MAX_TURNS * 2

FIXED_SUGGESTIONS = [
    "Yes, it worked",
    "No, it didn't work",
    "Summarise this response",
]

# ==================================================================
# STATE
# ==================================================================


class ChatState(TypedDict):
    messages: Annotated[List[BaseMessage], "Chat history"]
    question: str
    original_question: Optional[str]
    needs_rag: bool
    is_ticket: bool
    context: Optional[str]
    answer: Optional[str]
    suggested_questions: Optional[List[str]]
    context_relevant: bool
    # Ticket workflow
    awaiting_ticket_confirmation: bool
    awaiting_ticket_detail_confirmation: bool
    awaiting_ticket_revision: bool
    pending_ticket_details: Optional[Dict]
    ticket_id: Optional[str]
    # Negative-feedback escalation
    negative_feedback_count: int
    tracked_query: Optional[str]
    awaiting_escalation_confirmation: bool
    # Routing
    route_decision: Optional[str]


# ==================================================================
# MEMORY
# ==================================================================


class InMemoryCheckpoint:
    def __init__(self):
        self._store: Dict[str, Dict] = {}
        self.memory_saver = MemorySaver()

    def get_thread_messages(self, thread_id: str) -> List[BaseMessage]:
        if thread_id not in self._store:
            return []
        return self._store[thread_id].get("messages", [])

    def save_thread_state(self, thread_id: str, state: ChatState):
        if thread_id not in self._store:
            self._store[thread_id] = {}
        self._store[thread_id].update(state)

    def get_thread_state(self, thread_id: str) -> Optional[Dict]:
        return self._store.get(thread_id)


checkpoint = InMemoryCheckpoint()

# ==================================================================
# HELPERS
# ==================================================================


def trim_history(messages: List[BaseMessage]) -> List[BaseMessage]:
    if len(messages) <= MAX_MESSAGES:
        return messages
    return messages[-MAX_MESSAGES:]


def build_history(messages: List[BaseMessage]) -> str:
    parts = []
    for msg in messages:
        role = "Assistant" if isinstance(msg, AIMessage) else "User"
        parts.append(f"{role}: {msg.content}")
    return "\n".join(parts)


def _llm_call(messages: list, temperature: float = 0.1, json_mode: bool = False) -> str:
    kwargs: Dict[str, Any] = dict(
        messages=messages,
        temperature=temperature,
        model=AZURE_OPENAI_MODEL,
    )
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    response = llm.chat.completions.create(**kwargs)
    return response.choices[0].message.content.strip()


def extract_tag(text: str, tag: str = "thinking") -> str:
    """Extract or strip a specific XML-style tag from LLM output.

    - tag="thinking" → removes <thinking>…</thinking> and returns the rest.
    - tag="answer"   → returns content inside <answer>…</answer>,
                        falling back to stripping <thinking> if no <answer> found.
    - Any other tag  → returns content inside <{tag}>…</{tag}>,
                        falling back to stripping <thinking>.
    """
    if tag == "thinking":
        return re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.DOTALL).strip()

    m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.DOTALL).strip()


def extract_decision(text: str, field: str = "DECISION") -> str:
    """Extract a labelled decision from CoT output, e.g. 'DECISION: RAG_NEEDED'."""
    pattern = rf"{field}\s*:\s*(.+)"
    m = re.search(pattern, text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return extract_tag(text, "thinking").strip()


def _classify_fixed_button(question: str) -> Optional[str]:
    """Detect if the user clicked one of the three fixed suggestion buttons."""
    q = question.strip().lower()
    if q in ("yes, it worked", "yes it worked", "it resolved my issue"):
        return "ack"
    if q in ("no, it didn't work", "no it didn't work", "it did not resolve my issue",
             "no, it didn't work", "no it didn't work"):
        return "negative_feedback"
    if q in ("summarise this response", "summarize this response"):
        return "summarise"
    return None


def _classify_yes_no(question: str) -> Optional[str]:
    """Simple yes/no classifier for ticket confirmations."""
    text = question.lower().strip()
    no_patterns = [
        r"\bno\b", r"\bnah\b", r"\bnever mind\b", r"\bno thanks\b",
        r"\bdon't\b", r"\bstop\b", r"\bcancel\b", r"\bdo not\b",
    ]
    yes_patterns = [
        r"\byes\b", r"\bsure\b", r"\bgo ahead\b", r"\bplease create\b",
        r"\byes please\b", r"\bdo it\b", r"\bokay\b", r"\bok\b",
        r"\bsounds good\b", r"\bcreate it\b", r"\bconfirm\b", r"\bproceed\b",
    ]
    for p in no_patterns:
        if re.search(p, text):
            return "no"
    for p in yes_patterns:
        if re.search(p, text):
            return "yes"
    return "other"


# ==================================================================
# LLM TOOL ROUTER (single call: run_rag vs ticketing_* + optional topic)
# ==================================================================


def _parse_json_object(raw: str) -> dict:
    """Best-effort JSON parse for json_mode and occasional extra text."""
    text = raw.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    stripped = extract_tag(text, "thinking")
    if stripped != text:
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return {}
    return {}


def agent_route_tools_decision(
    question: str,
    messages: List[BaseMessage],
    *,
    needs_topic_check: bool,
    tracked_query: Optional[str],
) -> tuple[str, Optional[str], bool]:
    """One LLM call: run_rag vs ticketing tools; optional topic-vs-tracked.

    Replaces separate topic-change, ticket-creation, and ticket-ID retrieval
    classifiers. Returns (route_decision, ticket_id_or_none, topic_is_different).
    """
    history_text = build_history(trim_history(messages))
    if needs_topic_check and tracked_query:
        topic_instruction = (
            f"The user previously gave negative feedback on this tracked issue: {tracked_query}\n"
            'You MUST set "topic_vs_tracked" to "SAME_TOPIC" or "DIFFERENT_TOPIC".\n'
            "Rules: same system/issue follow-ups and feedback on that issue = SAME_TOPIC. "
            "A clearly unrelated new question = DIFFERENT_TOPIC."
        )
    else:
        topic_instruction = 'No topic comparison needed. Set "topic_vs_tracked" to null.'

    system = prompt.AGENT_TOOL_ROUTER_PROMPT.format(
        topic_instruction=topic_instruction,
        history=history_text,
        question=question,
    )
    raw = _llm_call([{"role": "system", "content": system}], json_mode=True)
    data = _parse_json_object(raw)

    tool = str(data.get("tool") or "run_rag").strip().lower().replace("-", "_").replace(" ", "_")
    ticket_id = data.get("ticket_id")
    if ticket_id is not None:
        ticket_id = str(ticket_id).replace("None", "").strip()
        if not (ticket_id.isdigit() and len(ticket_id) == 6):
            ticket_id = None

    topic_raw = data.get("topic_vs_tracked")
    topic_differs = bool(
        needs_topic_check
        and tracked_query
        and topic_raw
        and "DIFFERENT" in str(topic_raw).upper()
    )

    logging.info(
        "Agent tool router: tool=%s ticket_id=%s topic_vs_tracked=%s",
        tool,
        ticket_id,
        topic_raw,
    )

    if "preview" in tool or tool in ("ticketing_preview", "create_ticket", "new_ticket"):
        return "preview_ticket", None, topic_differs
    if ("retrieve" in tool or "lookup" in tool or tool == "ticketing_retrieve") and ticket_id:
        return "retrieve_ticket", ticket_id, topic_differs
    return "rag_agent", None, topic_differs


# ==================================================================
# LLM TOOL CALLS — CoT wrappers
# ==================================================================


def check_history_sufficiency(question: str, chat_history: List[BaseMessage]) -> tuple:
    """Returns (sufficient: bool, rewritten_query: str or None)."""
    history_text = build_history(trim_history(chat_history))
    if not history_text.strip():
        return False, None

    raw = _llm_call([
        {"role": "system", "content": prompt.HISTORY_SUFFICIENCY_PROMPT.format(
            history=history_text, question=question)},
    ])
    decision = extract_decision(raw)
    logging.info("History sufficiency: %s", decision)

    if "SUFFICIENT" in decision and "INSUFFICIENT" not in decision:
        return True, None

    logging.info("History insufficient — rewriting query for RAG.")
    rewrite_raw = _llm_call([
        {"role": "system", "content": prompt.REWRITE_QUERY_PROMPT.format(
            history=history_text, question=question)},
    ])
    rewritten = extract_decision(rewrite_raw, "QUERY")
    if not rewritten:
        rewritten = extract_tag(rewrite_raw, "thinking")
    logging.info("Rewritten query: %s", rewritten)
    return False, rewritten


def search_query_rewrite(question: str, chat_history: List[BaseMessage]) -> str:
    """Rewrite user question into a vector-search query."""
    history_text = ""
    for msg in chat_history:
        role = "User" if msg.type == "human" else "Assistant"
        history_text += f"{role}: {msg.content}\n"

    system = f"""You are a Query Rewriter for an enterprise vector search system.
Rewrite the user's latest message into ONE concise natural-language search query.
Rules:
1. Output a single line query only.
2. Use chat history for essential context (system name, error code, module).
3. Remove conversational filler.

CHAT HISTORY:
{history_text}"""

    raw = _llm_call([
        {"role": "system", "content": system},
        {"role": "user", "content": question},
    ])
    result = extract_tag(raw, "thinking")
    logging.info("Vector search query: %s", result)
    return result


# ==================================================================
# TICKET HELPERS
# ==================================================================


def _build_classification_prompt() -> str:
    mapping_text = json.dumps(prompt.INCIDENT_WORKFLOW_MAP, indent=2)
    return prompt.CLASSIFICATION_PROMPT.format(mapping_text=mapping_text)


def classify_incident(subject: str, description: str) -> dict:
    system_prompt = _build_classification_prompt()
    user_message = f"Classify the following incident ticket:\n\nSUBJECT: {subject}\nDESCRIPTION: {description}"

    raw = _llm_call([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ], json_mode=True)

    try:
        result = json.loads(extract_tag(raw, "thinking"))
    except json.JSONDecodeError:
        cleaned = re.search(r"\{.*\}", raw, re.DOTALL)
        result = json.loads(cleaned.group(0)) if cleaned else {}

    return _validate_against_mapping(result)


def _validate_against_mapping(result: dict) -> dict:
    matched = False
    for entry in prompt.INCIDENT_WORKFLOW_MAP:
        if entry["category"].lower() == result.get("category", "").lower() and (
            entry["sub_category"].lower() == result.get("sub_category", "").lower()
            or entry["sub_category"] == "All"
        ):
            result["technician_group"] = entry["technician_group"]
            result["functions"] = entry["functions"]
            result["validated"] = True
            result["Incident Raise To"] = "Information Management"
            matched = True
            break

    if not matched:
        result["validated"] = False
        result["warning"] = "LLM output did not match any known mapping. Manual review recommended."
    return result


def summarize_for_ticket(chat_history: List[BaseMessage]) -> dict:
    history_text = build_history(chat_history)
    raw = _llm_call([
        {"role": "system", "content": prompt.SUMMARIZE_FOR_TICKET_PROMPT},
        {"role": "user", "content": history_text},
    ])
    try:
        cleaned = extract_tag(raw, "thinking")
        return extract_json(text=cleaned)
    except Exception:
        return {"subject": "Support request", "description": history_text}


def extract_ticket_from_conversation(chat_history: List[BaseMessage]) -> dict:
    """CoT extraction of ticket subject/description from actual user issue."""
    history_text = build_history(chat_history)
    raw = _llm_call([
        {"role": "system", "content": prompt.TICKET_EXTRACTION_COT_PROMPT.format(history=history_text)},
    ])
    try:
        cleaned = extract_tag(raw, "thinking")
        return extract_json(text=cleaned)
    except Exception:
        return summarize_for_ticket(chat_history)


# ==================================================================
# SUPERVISOR NODE
# ==================================================================


def supervisor_node(state: ChatState) -> ChatState:
    question = state["question"]
    messages = state["messages"]

    # ── Handle ticket revision input ──
    if state.get("awaiting_ticket_revision"):
        state["route_decision"] = "revise_ticket"
        return state

    # ── Handle ticket detail confirmation (preview shown, user replied) ──
    if state.get("awaiting_ticket_detail_confirmation"):
        result = _classify_yes_no(question)
        if result == "yes":
            state["route_decision"] = "create_ticket"
        elif result == "no":
            state["route_decision"] = "decline"
            state["awaiting_ticket_detail_confirmation"] = False
            state["pending_ticket_details"] = None
        else:
            state["route_decision"] = "ticket_revision_prompt"
        return state

    # ── Handle ticket confirmation (system suggested ticket) ──
    if state.get("awaiting_ticket_confirmation"):
        result = _classify_yes_no(question)
        if result == "yes":
            state["route_decision"] = "preview_ticket"
        elif result == "no":
            state["route_decision"] = "decline"
        else:
            state["awaiting_ticket_confirmation"] = False
            # Fall through to normal routing
        if result in ("yes", "no"):
            return state

    # ── Handle escalation confirmation (after 2 strikes) ──
    if state.get("awaiting_escalation_confirmation"):
        result = _classify_yes_no(question)
        state["awaiting_escalation_confirmation"] = False
        if result == "yes":
            state["route_decision"] = "preview_ticket"
            state["is_ticket"] = True
            return state
        else:
            state["route_decision"] = "decline"
            state["negative_feedback_count"] = 0
            state["tracked_query"] = None
            return state

    # ── Detect fixed suggestion buttons ──
    button = _classify_fixed_button(question)

    if button == "ack":
        state["route_decision"] = "ack"
        return state

    if button == "summarise":
        state["route_decision"] = "summarise"
        return state

    if button == "negative_feedback":
        count = state.get("negative_feedback_count", 0) + 1
        state["negative_feedback_count"] = count
        logging.info("Negative feedback strike %d", count)

        if count >= 2:
            state["route_decision"] = "escalation"
            return state
        else:
            state["route_decision"] = "rag_agent"
            return state

    # ── Normal routing: one agent call chooses run_rag vs ticketing tools ──
    tracked = state.get("tracked_query")
    needs_topic = bool(tracked and state.get("negative_feedback_count", 0) > 0)
    route_decision, routed_tid, topic_differs = agent_route_tools_decision(
        question,
        messages,
        needs_topic_check=needs_topic,
        tracked_query=tracked,
    )
    if needs_topic and topic_differs:
        logging.info("Topic changed — resetting negative feedback counter.")
        state["negative_feedback_count"] = 0
        state["tracked_query"] = None

    if route_decision == "preview_ticket":
        state["is_ticket"] = True
        state["route_decision"] = "preview_ticket"
        return state
    if route_decision == "retrieve_ticket" and routed_tid:
        state["ticket_id"] = routed_tid
        state["route_decision"] = "retrieve_ticket"
        return state

    state["route_decision"] = "rag_agent"
    return state


def _route_after_supervisor(state: ChatState) -> str:
    return state.get("route_decision", "rag_agent")


# ==================================================================
# RAG AGENT NODE
# ==================================================================


def rag_agent_node(state: ChatState) -> ChatState:
    question = state["question"]
    messages = state["messages"]

    # ── Step 1: Check if history is sufficient ──
    original_question = question
    sufficient, rewritten = check_history_sufficiency(question, messages)

    if sufficient:
        # Generate from history only
        state["context"] = None
    else:
        # Need RAG retrieval
        search_q = rewritten or search_query_rewrite(question, messages)
        if rewritten:
            state["original_question"] = original_question
            question = rewritten

        try:
            results = search_data(query=search_q)
            state["context"] = str(results)
        except Exception as e:
            logging.error("Search failed: %s", e)
            state["context"] = ""

        # Check context relevance
        if state["context"]:
            raw = _llm_call([
                {"role": "system", "content": prompt.CONTEXT_RELEVANCE_PROMPT.format(
                    question=question, context=state["context"])},
            ])
            decision = extract_decision(raw)
            state["context_relevant"] = "RELEVANT" in decision.upper()
            logging.info("Context relevance: %s", decision)
        else:
            state["context_relevant"] = False

    # ── Step 2: Generate response ──
    history_text = build_history(trim_history(messages))
    user_question = state.get("original_question") or state["question"]
    context = state.get("context", "")

    if context:
        system_prompt = prompt.RAG_SYSTEM_PROMPT.format(history=history_text, context=context)
    else:
        system_prompt = prompt.NON_RAG_SYSTEM_PROMPT.format(history=history_text)

    raw = _llm_call([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_question},
    ])
    answer = extract_tag(raw, "answer")
    logging.info("Generated answer (truncated): %s", answer[:200])

    # ── Step 3: Verify answer ──
    verify_raw = _llm_call([
        {"role": "system", "content": prompt.ANSWER_VERIFICATION_PROMPT.format(
            answer=answer, context=context or "", history=history_text)},
    ])
    verification = extract_decision(verify_raw)
    logging.info("Answer verification: %s", verification)

    # ── Step 4: Finalize ──
    state["answer"] = answer
    state["suggested_questions"] = list(FIXED_SUGGESTIONS)

    if not state.get("tracked_query"):
        state["tracked_query"] = state["question"]

    state["messages"].append(HumanMessage(content=state["question"]))
    state["messages"].append(AIMessage(content=answer))
    return state


# ==================================================================
# TICKET AGENT NODE
# ==================================================================


def ticket_agent_node(state: ChatState) -> ChatState:
    decision = state.get("route_decision", "")

    if decision == "preview_ticket":
        return _preview_ticket(state)
    if decision == "create_ticket":
        return _create_ticket(state)
    if decision == "revise_ticket":
        return _revise_ticket(state)
    if decision == "retrieve_ticket":
        return _retrieve_ticket(state)
    if decision == "ticket_revision_prompt":
        return _ticket_revision_prompt(state)

    return decline_node(state)


def _preview_ticket(state: ChatState) -> ChatState:
    all_messages = state["messages"] + [HumanMessage(content=state["question"])]
    details = extract_ticket_from_conversation(all_messages)

    result = classify_incident(
        subject=details.get("subject", "Support Request"),
        description=details.get("description", ""),
    )
    details.setdefault("customField", {})["Subcategory"] = result.get("sub_category", "")
    details.setdefault("customField", {})["category"] = result.get("category", "")
    details.setdefault("customField", {})["Incident Raise To"] = result.get("Incident Raise To", "")
    details["technician_group"] = result.get("technician_group", "")

    state["pending_ticket_details"] = details

    subject = details.get("subject", "N/A")
    description = details.get("description", "N/A")
    category = details.get("customField", {}).get("category", "")
    subcategory = details.get("customField", {}).get("Subcategory", "")
    incident_raised_to = details.get("customField", {}).get("Incident Raise To", "")

    state["answer"] = (
        "Here are the ticket details I've generated based on our conversation:\n\n"
        f"**Subject:** {subject}\n\n"
        f"**Description:** {description}\n\n"
        f"**Category:** {category}\n\n"
        f"**Subcategory:** {subcategory}\n\n"
        f"**Incident Raise To:** {incident_raised_to}\n\n"
        "Would you like me to go ahead and create this ticket?"
    )
    state["suggested_questions"] = ["Yes", "No", "I want to update ticket information"]
    state["awaiting_ticket_detail_confirmation"] = True
    state["awaiting_ticket_confirmation"] = False

    state["messages"].append(HumanMessage(content=state["question"]))
    state["messages"].append(AIMessage(content=state["answer"]))
    return state


def _create_ticket(state: ChatState) -> ChatState:
    details = state.get("pending_ticket_details", {})

    payload = {
        "requesterEmail": "testuser@nuvoco.com",
        "subject": "[Created by Nuvoco AI Agent] " + details.get("subject", "Support Request"),
        "description": details.get("description", ""),
        "impactName": "low",
        "priorityName": "low",
        "urgencyName": "low",
        "statusName": "Open",
        "departmentName": "IT",
        "technicianGroupName": details.get("technician_group", ""),
        "categoryName": details.get("customField", {}).get("category", ""),
        "customField": {
            "Subcategory": details.get("customField", {}).get("Subcategory", ""),
            "Incident Raise To": details.get("customField", {}).get("Incident Raise To", ""),
        },
    }
    logging.info("Create ticket payload: %s", payload)

    ticket = create_ticket(payload)

    if ticket:
        ticket_number = ticket.get("id", ticket.get("name", "N/A"))
        status = ticket.get("statusName", "Open")
        state["answer"] = (
            f"Ticket Created Successfully\n\n"
            f"**Ticket Number**: {ticket_number}\n\n"
            f"**Status**: {status}"
        )
    else:
        state["answer"] = "Sorry, ticket creation **failed**. Please try again later."

    state["suggested_questions"] = []
    state["awaiting_ticket_detail_confirmation"] = False
    state["awaiting_ticket_confirmation"] = False
    state["awaiting_ticket_revision"] = False
    state["awaiting_escalation_confirmation"] = False
    state["pending_ticket_details"] = None
    state["negative_feedback_count"] = 0
    state["tracked_query"] = None

    # Clear history after successful ticket creation
    state["messages"] = []
    state["messages"].append(HumanMessage(content=state["question"]))
    state["messages"].append(AIMessage(content=state["answer"]))
    return state


def _retrieve_ticket(state: ChatState) -> ChatState:
    ticket_id = state.get("ticket_id")
    try:
        ticket_status = retrieve_ticket(ticket_id=ticket_id)
        if ticket_status:
            state["answer"] = f"The status of ticket {ticket_id} is {ticket_status}"
        else:
            state["answer"] = (
                f"Sorry, it looks like the ticket ID {ticket_id} is wrong or does not exist."
            )
    except Exception as e:
        logging.error("Error retrieving ticket: %s", e)
        state["answer"] = "Sorry, I couldn't retrieve the ticket details. Please try again."

    state["ticket_id"] = None
    state["suggested_questions"] = list(FIXED_SUGGESTIONS)
    state["messages"].append(HumanMessage(content=state["question"]))
    state["messages"].append(AIMessage(content=state["answer"]))
    return state


def _revise_ticket(state: ChatState) -> ChatState:
    current_details = state.get("pending_ticket_details", {})

    raw = _llm_call([
        {"role": "system", "content": prompt.REVISE_TICKET_PROMPT.format(
            subject=current_details.get("subject", ""),
            description=current_details.get("description", ""),
        )},
        {"role": "user", "content": state["question"]},
    ])

    try:
        updated_details = extract_json(text=extract_tag(raw, "thinking"))
    except Exception:
        updated_details = current_details

    result = classify_incident(
        subject=updated_details.get("subject", "Support Request"),
        description=updated_details.get("description", ""),
    )
    updated_details.setdefault("customField", {})["Subcategory"] = result.get("sub_category", "")
    updated_details.setdefault("customField", {})["category"] = result.get("category", "")
    updated_details.setdefault("customField", {})["Incident Raise To"] = result.get("Incident Raise To", "")
    updated_details["technician_group"] = result.get("technician_group", "")

    state["pending_ticket_details"] = updated_details

    subject = updated_details.get("subject", "N/A")
    description = updated_details.get("description", "N/A")
    category = updated_details.get("customField", {}).get("category", "")
    subcategory = updated_details.get("customField", {}).get("Subcategory", "")

    state["answer"] = (
        "Here are the updated ticket details:\n\n"
        f"**Subject:** {subject}\n\n"
        f"**Description:** {description}\n\n"
        f"**Category:** {category}\n\n"
        f"**Subcategory:** {subcategory}\n\n"
        "Would you like me to go ahead and create this ticket?"
    )
    state["suggested_questions"] = ["Yes", "No", "I want to update ticket information"]
    state["awaiting_ticket_detail_confirmation"] = True
    state["awaiting_ticket_revision"] = False

    state["messages"].append(HumanMessage(content=state["question"]))
    state["messages"].append(AIMessage(content=state["answer"]))
    return state


def _ticket_revision_prompt(state: ChatState) -> ChatState:
    state["answer"] = (
        "Sure! Please tell me what you'd like to change "
        "in the ticket details (e.g. subject, description, etc.)."
    )
    state["suggested_questions"] = []
    state["awaiting_ticket_detail_confirmation"] = False
    state["awaiting_ticket_revision"] = True

    state["messages"].append(HumanMessage(content=state["question"]))
    state["messages"].append(AIMessage(content=state["answer"]))
    return state


# ==================================================================
# AUXILIARY NODES
# ==================================================================


def summarise_node(state: ChatState) -> ChatState:
    """Condense the last bot response into a shorter version."""
    ai_messages = [m for m in state["messages"] if isinstance(m, AIMessage)]
    if not ai_messages:
        state["answer"] = "There's no previous response to summarise."
        state["suggested_questions"] = list(FIXED_SUGGESTIONS)
        state["messages"].append(HumanMessage(content=state["question"]))
        state["messages"].append(AIMessage(content=state["answer"]))
        return state

    last_response = ai_messages[-1].content
    raw = _llm_call([
        {"role": "system", "content": prompt.SUMMARISE_RESPONSE_PROMPT.format(response=last_response)},
    ])
    summary = extract_tag(raw, "answer")

    state["answer"] = summary
    state["suggested_questions"] = list(FIXED_SUGGESTIONS)
    state["messages"].append(HumanMessage(content=state["question"]))
    state["messages"].append(AIMessage(content=summary))
    return state


def ack_node(state: ChatState) -> ChatState:
    """User confirmed the solution worked. Clear history for fresh start."""
    state["answer"] = "Glad to hear that! Let me know if you need any other help."
    state["suggested_questions"] = []
    state["negative_feedback_count"] = 0
    state["tracked_query"] = None
    state["awaiting_escalation_confirmation"] = False

    # Clear conversation history for a fresh start
    state["messages"] = []
    state["messages"].append(HumanMessage(content=state["question"]))
    state["messages"].append(AIMessage(content=state["answer"]))
    return state


def escalation_node(state: ChatState) -> ChatState:
    """After 2 negative-feedback strikes, ask if user wants to create a ticket."""
    state["answer"] = (
        "It seems like the solutions I've provided haven't resolved your issue. "
        "Would you like me to create a support ticket for this?"
    )
    state["suggested_questions"] = ["Yes", "No"]
    state["awaiting_escalation_confirmation"] = True

    state["messages"].append(HumanMessage(content=state["question"]))
    state["messages"].append(AIMessage(content=state["answer"]))
    return state


def decline_node(state: ChatState) -> ChatState:
    """Reset all ticket/escalation state."""
    state["answer"] = "Alright! Let me know if you need any other help."
    state["suggested_questions"] = []
    state["awaiting_ticket_confirmation"] = False
    state["awaiting_ticket_detail_confirmation"] = False
    state["awaiting_ticket_revision"] = False
    state["awaiting_escalation_confirmation"] = False
    state["pending_ticket_details"] = None
    state["negative_feedback_count"] = 0
    state["tracked_query"] = None

    state["messages"] = []
    state["messages"].append(HumanMessage(content=state["question"]))
    state["messages"].append(AIMessage(content=state["answer"]))
    return state


# ==================================================================
# GRAPH
# ==================================================================


def create_langgraph_chain():
    workflow = StateGraph(ChatState)

    workflow.add_node("supervisor", supervisor_node)
    workflow.add_node("rag_agent", rag_agent_node)
    workflow.add_node("ticket_agent", ticket_agent_node)
    workflow.add_node("summarise", summarise_node)
    workflow.add_node("ack", ack_node)
    workflow.add_node("escalation", escalation_node)
    workflow.add_node("decline", decline_node)

    workflow.set_entry_point("supervisor")
    workflow.add_conditional_edges(
        "supervisor",
        _route_after_supervisor,
        {
            "rag_agent": "rag_agent",
            "preview_ticket": "ticket_agent",
            "create_ticket": "ticket_agent",
            "revise_ticket": "ticket_agent",
            "retrieve_ticket": "ticket_agent",
            "ticket_revision_prompt": "ticket_agent",
            "decline": "decline",
            "summarise": "summarise",
            "ack": "ack",
            "escalation": "escalation",
        },
    )

    workflow.add_edge("rag_agent", END)
    workflow.add_edge("ticket_agent", END)
    workflow.add_edge("summarise", END)
    workflow.add_edge("ack", END)
    workflow.add_edge("escalation", END)
    workflow.add_edge("decline", END)

    return workflow.compile(checkpointer=checkpoint.memory_saver)


# ==================================================================
# CHAT MANAGER
# ==================================================================


class ThreadedChatManager:
    def __init__(self):
        self.graph = create_langgraph_chain()

    def chat(self, question: str, thread_id: str):
        existing_messages = checkpoint.get_thread_messages(thread_id)
        existing_messages = trim_history(existing_messages)
        prev_state = checkpoint.get_thread_state(thread_id)

        initial_state = ChatState(
            messages=existing_messages.copy(),
            question=question,
            original_question=None,
            needs_rag=False,
            is_ticket=False,
            context=None,
            answer=None,
            suggested_questions=[],
            context_relevant=False,
            awaiting_ticket_confirmation=(
                prev_state.get("awaiting_ticket_confirmation", False) if prev_state else False
            ),
            awaiting_ticket_detail_confirmation=(
                prev_state.get("awaiting_ticket_detail_confirmation", False) if prev_state else False
            ),
            awaiting_ticket_revision=(
                prev_state.get("awaiting_ticket_revision", False) if prev_state else False
            ),
            pending_ticket_details=(
                prev_state.get("pending_ticket_details") if prev_state else None
            ),
            ticket_id=prev_state.get("ticket_id") if prev_state else None,
            negative_feedback_count=(
                prev_state.get("negative_feedback_count", 0) if prev_state else 0
            ),
            tracked_query=(
                prev_state.get("tracked_query") if prev_state else None
            ),
            awaiting_escalation_confirmation=(
                prev_state.get("awaiting_escalation_confirmation", False) if prev_state else False
            ),
            route_decision=None,
        )

        config = {"configurable": {"thread_id": thread_id}}
        result = self.graph.invoke(initial_state, config=config)
        checkpoint.save_thread_state(thread_id, result)

        ai_messages = [m for m in result["messages"] if isinstance(m, AIMessage)]
        if ai_messages:
            return (
                ai_messages[-1].content,
                result.get("suggested_questions", []),
                200,
            )

    def inject_history(self, thread_id: str, messages: list) -> None:
        """
        Seed the in-memory checkpoint with a conversation's stored messages.
        Called once per login session when a user opens an existing conversation.
        """
        existing = checkpoint.get_thread_messages(thread_id)
        if existing:
            return

        lc_messages: List[BaseMessage] = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if not content:
                continue
            if role == "user":
                lc_messages.append(HumanMessage(content=content))
            elif role in ("bot", "assistant"):
                lc_messages.append(AIMessage(content=content))

        if not lc_messages:
            return

        lc_messages = trim_history(lc_messages)
        checkpoint.save_thread_state(
            thread_id,
            {
                "messages": lc_messages,
                "question": "",
                "original_question": None,
                "needs_rag": False,
                "is_ticket": False,
                "context": None,
                "answer": None,
                "suggested_questions": [],
                "context_relevant": False,
                "awaiting_ticket_confirmation": False,
                "awaiting_ticket_detail_confirmation": False,
                "awaiting_ticket_revision": False,
                "pending_ticket_details": None,
                "ticket_id": None,
                "negative_feedback_count": 0,
                "tracked_query": None,
                "awaiting_escalation_confirmation": False,
                "route_decision": None,
            },
        )
        logging.info("Injected %d messages into thread %s.", len(lc_messages), thread_id)
