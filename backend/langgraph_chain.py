import os
import re
import json
import prompt
import logging
from llm import llm
from utils import create_ticket, retrieve_ticket, extract_json

from typing import List, Dict, Tuple, Optional, Annotated, TypedDict
from langchain_core.messages import BaseMessage, AIMessage, HumanMessage
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from search_documents import search_data

AZURE_OPENAI_MODEL = os.getenv("AZURE_OPENAI_MODEL")
MAX_TURNS = int(os.getenv("MAX_TURNS", 10))

ROUTER_SYSTEM_PROMPT = prompt.ROUTER_SYSTEM_PROMPT
RAG_SYSTEM_PROMPT = prompt.RAG_SYSTEM_PROMPT
NON_RAG_SYSTEM_PROMPT = prompt.NON_RAG_SYSTEM_PROMPT
CONTEXT_RELEVANCE_PROMPT = prompt.CONTEXT_RELEVANCE_PROMPT
ANSWER_VERIFICATION_PROMPT = prompt.ANSWER_VERIFICATION_PROMPT
TICKET_INTENT_PROMPT = prompt.TICKET_INTENT_PROMPT
TICKET_DETAILS_INTENT_PROMPT = prompt.TICKET_DETAILS_INTENT_PROMPT
EXTRACT_TICKET_DETAILS_PROMPT = prompt.EXTRACT_TICKET_DETAILS_PROMPT
SUMMARIZE_FOR_TICKET_PROMPT = prompt.SUMMARIZE_FOR_TICKET_PROMPT
REVISE_TICKET_PROMPT = prompt.REVISE_TICKET_PROMPT
INCIDENT_WORKFLOW_MAP = prompt.INCIDENT_WORKFLOW_MAP
CLASSIFICATION_PROMPT = prompt.CLASSIFICATION_PROMPT

MAX_MESSAGES = MAX_TURNS * 2

# ==========================
# STATE
# ==========================


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
    awaiting_ticket_confirmation: bool
    awaiting_ticket_detail_confirmation: bool
    awaiting_ticket_revision: bool
    pending_ticket_details: Optional[Dict]
    ticket_id: Optional[str]


# ==========================
# MEMORY
# ==========================


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

# ==========================
# HELPERS
# ==========================


def trim_history(messages: List[BaseMessage]) -> List[BaseMessage]:
    if len(messages) <= MAX_MESSAGES:
        return messages
    return messages[-MAX_MESSAGES:]


def build_history(messages: List[BaseMessage]) -> str:
    history = ""
    for msg in messages:
        role = "Assistant" if isinstance(msg, AIMessage) else "User"
        history += f"{role}: {msg.content}\n"
    return history


# ==========================
# LLM INTENT CLASSIFIER
# ==========================


def is_ticket_request(question: str, chat_history: List[BaseMessage]) -> bool:
    history_text = build_history(trim_history(chat_history))
    prompt_msg = [
        {
            "role": "system",
            "content": TICKET_INTENT_PROMPT.format(history=history_text),
        },
        {"role": "user", "content": question},
    ]

    response = llm.chat.completions.create(
        messages=prompt_msg, temperature=0.1, model=AZURE_OPENAI_MODEL
    )

    decision = response.choices[0].message.content.strip().upper()
    logging.info(f"Ticket intent decision: {decision}")

    return "YES" in decision


def is_ticket_detail_requested(question: str, chat_history: List[BaseMessage]) -> str:
    history_text = build_history(trim_history(chat_history))
    prompt_msg = [
        {
            "role": "system",
            "content": TICKET_DETAILS_INTENT_PROMPT.format(history=history_text),
        },
        {"role": "user", "content": question},
    ]

    response = llm.chat.completions.create(
        messages=prompt_msg, temperature=0.1, model=AZURE_OPENAI_MODEL
    )

    ticket_id = response.choices[0].message.content.strip()
    logging.info(f"Requesting details about ticket: {ticket_id}")

    if ticket_id.isnumeric() and len(ticket_id) == 6:
        return ticket_id

    return None


def classify_user_confirmation(question: str) -> str:
    """Returns 'yes', 'no', or 'other' for three-way confirmation handling."""
    if not question:
        return "other"

    text = question.lower().strip()

    no_patterns = [
        r"\bno\b",
        r"\bnah\b",
        r"\bnever mind\b",
        r"\bno thanks\b",
        r"\bdon't\b",
        r"\bstop\b",
        r"\bcancel\b",
        r"\bdo not\b",
    ]
    yes_patterns = [
        r"\byes\b",
        r"\bsure\b",
        r"\bgo ahead\b",
        r"\bplease create it\b",
        r"\byes please\b",
        r"\bdo it\b",
        r"\bokay\b",
        r"\bok\b",
        r"\baffirmative\b",
        r"\bsounds good\b",
        r"\binterested\b",
        r"\blooks good\b",
        r"\bcreate it\b",
        r"\bconfirm\b",
        r"\bproceed\b",
    ]

    for pattern in no_patterns:
        if re.search(pattern, text):
            return "no"

    for pattern in yes_patterns:
        if re.search(pattern, text):
            return "yes"

    return "other"


# ==========================
# EXTRACT TICKET DETAILS
# ==========================


def extract_ticket_details(
    question: str, chat_history: List[BaseMessage] = None
) -> dict:
    context = ""
    logging.info("Here 1")
    if chat_history:
        context = f"\nChat History:\n{build_history(chat_history)}"

    prompt_msg = [
        {
            "role": "system",
            "content": EXTRACT_TICKET_DETAILS_PROMPT.format(context=context),
        },
        {"role": "user", "content": question},
    ]

    response = llm.chat.completions.create(
        messages=prompt_msg, temperature=0.1, model=AZURE_OPENAI_MODEL
    )

    try:
        return json.loads(response.choices[0].message.content)
    except Exception:
        return {
            "subject": question,
            "description": question,
            "impact": "Low",
            "urgency": "Low",
        }


def summarize_for_ticket(chat_history: List[BaseMessage]) -> dict:
    history_text = build_history(chat_history)
    logging.info("Here 2")
    prompt_msg = [
        {"role": "system", "content": SUMMARIZE_FOR_TICKET_PROMPT},
        {"role": "user", "content": history_text},
    ]

    response = llm.chat.completions.create(
        messages=prompt_msg, temperature=0.1, model=AZURE_OPENAI_MODEL
    )

    try:
        return extract_json(text=response.choices[0].message.content)
    except Exception:
        return {
            "subject": "Support request",
            "description": history_text,
            "impact": "Low",
            "urgency": "Low",
        }


# ==========================
# ROUTER
# ==========================


def determine_if_rag_needed(question: str, chat_history: List[BaseMessage]) -> bool:
    if not chat_history:
        return True

    history_text = build_history(trim_history(chat_history))
    prompt_msg = [
        {
            "role": "system",
            "content": ROUTER_SYSTEM_PROMPT.format(history=history_text),
        },
        {"role": "user", "content": question},
    ]

    response = llm.chat.completions.create(
        messages=prompt_msg, temperature=0.1, model=AZURE_OPENAI_MODEL
    )

    decision = response.choices[0].message.content.strip()
    return "RAG_NEEDED" in decision.upper()


def route_question(state: ChatState) -> ChatState:
    # ===============================
    # HANDLE TICKET REVISION INPUT (user is providing edit instructions)
    # ===============================
    if state.get("awaiting_ticket_revision"):
        # User's message contains the actual edit instructions → revise ticket
        state["is_ticket"] = False
        state["needs_rag"] = False
        return state

    # ===============================
    # HANDLE TICKET DETAIL CONFIRMATION (preview shown)
    # ===============================
    if state.get("awaiting_ticket_detail_confirmation"):
        result = classify_user_confirmation(state["question"])
        logging.info(f"Ticket User Confirmation: {result}")

        if result == "yes":
            state["is_ticket"] = True
            state["needs_rag"] = False
            return state

        elif result == "no":
            state["is_ticket"] = False
            state["awaiting_ticket_detail_confirmation"] = False
            state["pending_ticket_details"] = None
            state["needs_rag"] = False
            return state

        else:
            # User wants to revise (e.g. "I want to update ticket information")
            # Prompt them for details and keep ticket state alive
            state["is_ticket"] = False
            state["needs_rag"] = False
            state["awaiting_ticket_detail_confirmation"] = False
            state["awaiting_ticket_revision"] = True
            state["answer"] = (
                "Sure! Please tell me what you'd like to change "
                "in the ticket details (e.g. subject, description, etc.)."
            )
            state["suggested_questions"] = []
            state["messages"].append(HumanMessage(content=state["question"]))
            state["messages"].append(AIMessage(content=state["answer"]))
            return state

    # ===============================
    # HANDLE TICKET CONFIRMATION (system suggested ticket)
    # ===============================
    if state.get("awaiting_ticket_confirmation"):
        result = classify_user_confirmation(state["question"])

        if result == "yes":
            state["is_ticket"] = True
            state["needs_rag"] = False
            return state

        elif result == "no":
            state["is_ticket"] = False
            state["awaiting_ticket_confirmation"] = False
            state["needs_rag"] = False
            return state

        else:
            state["awaiting_ticket_confirmation"] = False

    # ===============================
    # NORMAL FLOW
    # ===============================
    state["is_ticket"] = is_ticket_request(state["question"], state["messages"])

    if state["is_ticket"]:
        state["needs_rag"] = False
        return state

    state["ticket_id"] = is_ticket_detail_requested(
        state["question"], state["messages"]
    )

    if state["ticket_id"] is not None:
        state["needs_rag"] = False
        return state

    state["needs_rag"] = determine_if_rag_needed(
        state["question"], state["messages"]
    )

    return state


def route_after_intent(state: ChatState):
    logging.info(
        "awaiting_ticket_detail_confirmation: %s",
        state.get("awaiting_ticket_detail_confirmation"),
    )
    logging.info(
        "awaiting_ticket_confirmation: %s",
        state.get("awaiting_ticket_confirmation"),
    )
    logging.info(
        "awaiting_ticket_revision: %s",
        state.get("awaiting_ticket_revision"),
    )

    # User wants to revise: if answer is already set (Turn A: user said
    # "I want to update" → route_question replied inline), just end.
    # Otherwise (Turn B: user provided actual edit instructions), revise.
    if state.get("awaiting_ticket_revision"):
        if state.get("answer"):
            return "end_turn"
        return "revise_ticket"

    # Ticket detail confirmation (preview was shown, user replied yes/no)
    if state.get("awaiting_ticket_detail_confirmation"):
        if state.get("is_ticket"):
            return "create_ticket"
        else:
            logging.info("Here Decline Ticket")
            return "decline_ticket"

    if state.get("awaiting_ticket_confirmation"):
        return "preview_ticket" if state.get("is_ticket") else "decline_ticket"

    if state.get("ticket_id") is not None:
        return "retrieve_ticket"

    if state.get("is_ticket"):
        return "preview_ticket"

    return "retrieve" if state["needs_rag"] else "generate"


# ==========================
# TICKET NODES
# ==========================


def preview_ticket_node(state: ChatState) -> ChatState:
    print("In preview ticket node")
    all_messages = state["messages"] + [HumanMessage(content=state["question"])]
    details = summarize_for_ticket(all_messages)
    result = classify_incident(
        subject=details.get("subject", "Support Request"),
        description=details.get("description", ""),
    )
    details.setdefault("customField", {})["Subcategory"] = result.get(
        "sub_category", ""
    )
    details.setdefault("customField", {})["category"] = result.get("category", "")
    details.setdefault("customField", {})["Incident Raise To"] = result.get(
        "Incident Raise To", ""
    )

    state["pending_ticket_details"] = details

    subject = details.get("subject", "N/A")
    description = details.get("description", "N/A")
    subcategory = details.get("customField", {}).get("Subcategory", "")
    category = details.get("customField", {}).get("category", "")
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


def _build_classification_prompt() -> str:
    mapping_text = json.dumps(INCIDENT_WORKFLOW_MAP, indent=2)
    return CLASSIFICATION_PROMPT.format(mapping_text=mapping_text)


def classify_incident(
    subject: str,
    description: str,
) -> dict:
    """
    Uses an LLM to classify an incident ticket into the correct
    Category, Sub_Category, and Technician_Group.

    Args:
        subject (str): The incident ticket subject/title.
        description (str): The incident ticket description/body.

    Returns:
        dict: {
            "functions": str,
            "category": str,
            "sub_category": str,
            "technician_group": str,
            "confidence": str,
            "reasoning": str
        }
    """

    system_prompt = _build_classification_prompt()

    user_message = f"""
Classify the following incident ticket:

SUBJECT: {subject}
DESCRIPTION: {description}
"""

    response = llm.chat.completions.create(
        model=AZURE_OPENAI_MODEL,
        temperature=0.1,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    )

    result = json.loads(response.choices[0].message.content)
    result = _validate_against_mapping(result)

    return result


def _validate_against_mapping(result: dict) -> dict:
    """
    Validates LLM output against the known INCIDENT_WORKFLOW_MAP.
    If the returned category/sub_category combo doesn't exist,
    flags it with a warning.
    """
    matched = False
    for entry in INCIDENT_WORKFLOW_MAP:
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
        result["warning"] = (
            "LLM output did not match any known mapping. Manual review recommended."
        )

    return result


def create_ticket_node(state: ChatState) -> ChatState:
    details = state.get("pending_ticket_details", {})

    payload = {
        "requesterEmail": "testuser@nuvoco.com",
        "subject": "[Created by Nuvoco AI Agent] "
        + details.get("subject", "Support Request"),
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
            "Incident Raise To": details.get("customField", {}).get(
                "Incident Raise To", ""
            ),
        },
    }
    logging.info(f"Payload: {payload}")

    ticket = create_ticket(payload)

    if ticket:
        state["messages"] = []

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
    state["pending_ticket_details"] = None

    state["messages"].append(HumanMessage(content=state["question"]))
    state["messages"].append(AIMessage(content=state["answer"]))

    return state


def retrieve_ticket_node(state: ChatState) -> ChatState:
    ticket_id = state.get("ticket_id", None)

    try:
        ticket_status = retrieve_ticket(ticket_id=ticket_id)
        if ticket_status:
            state["answer"] = (
                f"The status of ticket {ticket_id} is {ticket_status}"
            )
        else:
            state["answer"] = (
                f"Sorry, it looks like the ticket ID {ticket_id} is wrong "
                "or does not exist"
            )

        state["ticket_id"] = None
        state["messages"].append(HumanMessage(content=state["question"]))
        state["messages"].append(AIMessage(content=state["answer"]))

        return state

    except Exception as e:
        logging.info(f"Error in retrieving ticket data - {e}")


def revise_ticket_node(state: ChatState) -> ChatState:
    current_details = state.get("pending_ticket_details", {})

    prompt_msg = [
        {
            "role": "system",
            "content": REVISE_TICKET_PROMPT.format(
                subject=current_details.get("subject", ""),
                description=current_details.get("description", ""),
                impact="Low",
                urgency=current_details.get("urgency", "Low"),
            ),
        },
        {"role": "user", "content": state["question"]},
    ]

    response = llm.chat.completions.create(
        messages=prompt_msg, temperature=0.1, model=AZURE_OPENAI_MODEL
    )
    try:
        updated_details = extract_json(text=response.choices[0].message.content)
    except Exception:
        updated_details = current_details

    result = classify_incident(
        subject=updated_details.get("subject", "Support Request"),
        description=updated_details.get("description", ""),
    )
    updated_details.setdefault("customField", {})["Subcategory"] = result.get(
        "sub_category", ""
    )
    updated_details.setdefault("customField", {})["category"] = result.get(
        "category", ""
    )
    updated_details.setdefault("customField", {})["Incident Raise To"] = result.get(
        "Incident Raise To", ""
    )

    state["pending_ticket_details"] = updated_details

    subject = updated_details.get("subject", "N/A")
    description = updated_details.get("description", "N/A")
    subcategory = updated_details.get("customField", {}).get("Subcategory", "")
    category = updated_details.get("customField", {}).get("category", "")

    state["answer"] = (
        "Here are the ticket details I've generated based on our conversation:\n\n"
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


def decline_ticket_node(state: ChatState) -> ChatState:
    state["answer"] = "Alright! Let me know if you need any other help."
    state["suggested_questions"] = []
    state["awaiting_ticket_confirmation"] = False
    state["awaiting_ticket_detail_confirmation"] = False
    state["awaiting_ticket_revision"] = False
    state["pending_ticket_details"] = None
    state["messages"] = []
    state["messages"].append(HumanMessage(content=state["question"]))
    state["messages"].append(AIMessage(content=state["answer"]))
    return state


# ==========================
# RAG FLOW
# ==========================


def search_query(question: str, chat_history: List[BaseMessage]) -> str:
    """Create search query for azure ai search."""
    history_text = ""
    for msg in chat_history:
        role = "User" if msg.type == "human" else "Assistant"
        history_text += f"{role}: {msg.content}\n"

    system_prompt = f"""
You are a Query Rewriter for an enterprise vector search system.

Task:
Rewrite the user's latest message into ONE concise natural-language search query to retrieve the most relevant internal documents (HR, Travel, IT, Security, SAP guides/procedures).

Rules:
1. Output MUST be a single line query. No bullets, no quotes, no explanations.
2. Use chat history only to add essential context (policy type, system/app name, SAP module, transaction, error text/code, location).
3. Remove conversational filler (e.g., "please", "as discussed", "above").
4. Prefer specific keywords (SAP MM/FI/SD, Fiori, Kronos, "travel reimbursement policy", "password reset", etc.).
5. If intent is unclear, produce the broadest accurate query based on known context (do
"""
    user_prompt = f"""
====================
CHAT HISTORY:
====================
{history_text}

====================
USER QUERY/RESPONSE:
====================
{question}
"""
    prompt_msg = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    response = llm.chat.completions.create(
        messages=prompt_msg, temperature=0.1, model=AZURE_OPENAI_MODEL
    )
    response = response.choices[0].message.content
    logging.info(f"Vector search query: {response}")
    return response


def retrieve_context(state: ChatState) -> ChatState:
    try:
        state["question"] = search_query(state["question"], state["messages"])
        results = search_data(query=state["question"])
        state["context"] = str(results)
    except Exception as e:
        logging.error(e)
        state["context"] = ""
    return state


def check_history_sufficiency(state: ChatState) -> ChatState:
    """
    Check if conversation history is sufficient to answer the question.
    If not, rewrite the question for RAG retrieval and set needs_rag = True.
    """
    messages = trim_history(state["messages"])
    history_text = build_history(messages)

    if not history_text.strip():
        state["needs_rag"] = True
        return state

    prompt_msg = [
        {
            "role": "system",
            "content": prompt.HISTORY_SUFFICIENCY_PROMPT.format(
                history=history_text, question=state["question"]
            ),
        }
    ]

    response = llm.chat.completions.create(
        messages=prompt_msg, temperature=0.1, model=AZURE_OPENAI_MODEL
    )

    decision = response.choices[0].message.content.strip().upper()
    logging.info(f"History sufficiency check: {decision}")

    if "SUFFICIENT" in decision and "INSUFFICIENT" not in decision:
        state["needs_rag"] = False
        return state

    logging.info("History insufficient. Rewriting query for RAG fallback...")

    rewrite_prompt = [
        {
            "role": "system",
            "content": prompt.REWRITE_QUERY_PROMPT.format(
                history=history_text, question=state["question"]
            ),
        }
    ]

    rewrite_response = llm.chat.completions.create(
        messages=rewrite_prompt, temperature=0.1, model=AZURE_OPENAI_MODEL
    )

    rewritten_query = rewrite_response.choices[0].message.content.strip()
    logging.info(f"Rewritten query for RAG: {rewritten_query}")

    if not state.get("original_question"):
        state["original_question"] = state["question"]
    state["question"] = rewritten_query
    state["needs_rag"] = True

    return state


def check_context_relevance(state: ChatState) -> ChatState:
    if not state.get("context"):
        state["context_relevant"] = False
        return state

    prompt_msg = [
        {
            "role": "system",
            "content": CONTEXT_RELEVANCE_PROMPT.format(
                question=state["question"], context=state["context"]
            ),
        }
    ]

    response = llm.chat.completions.create(
        messages=prompt_msg, temperature=0.1, model=AZURE_OPENAI_MODEL
    )

    decision = response.choices[0].message.content.strip()
    logging.info(f"{decision}: Context")
    state["context_relevant"] = "RELEVANT" in decision.upper()
    return state


def route_after_history_check(state: ChatState):
    """Route after checking history sufficiency."""
    if state["needs_rag"]:
        return "retrieve"
    return "generate"


def generate_response(state: ChatState) -> ChatState:
    messages = trim_history(state["messages"])
    context = state.get("context", "")
    history_text = build_history(messages)
    user_question = state.get("original_question") or state["question"]

    if context:
        system_prompt = RAG_SYSTEM_PROMPT.format(history=history_text, context=context)
    else:
        system_prompt = NON_RAG_SYSTEM_PROMPT.format(history=history_text)

    prompt_msg = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_question},
    ]

    response = llm.chat.completions.create(
        messages=prompt_msg, temperature=0.1, model=AZURE_OPENAI_MODEL
    )

    full_response = response.choices[0].message.content
    main_response, suggested = extract_suggested_questions(full_response)
    logging.info(f"{main_response}: Final Response")

    state["answer"] = main_response
    state["suggested_questions"] = suggested
    print(state["suggested_questions"])
    return state


# ==========================
# VERIFICATION
# ==========================


def verify_answer(state: ChatState) -> ChatState:
    if not state.get("answer"):
        return state

    history_text = build_history(state["messages"])
    prompt_msg = [
        {
            "role": "system",
            "content": ANSWER_VERIFICATION_PROMPT.format(
                answer=state["answer"],
                context=state.get("context", ""),
                history=history_text,
            ),
        }
    ]

    response = llm.chat.completions.create(
        messages=prompt_msg, temperature=0.1, model=AZURE_OPENAI_MODEL
    )

    decision = "VALID"
    logging.info(f"{decision}: Verify Answer")

    state["messages"].append(HumanMessage(content=state["question"]))
    state["messages"].append(AIMessage(content=state["answer"]))
    return state


# ==========================
# UTIL
# ==========================


def extract_suggested_questions(response: str) -> Tuple[str, List[str]]:
    if (
        "Sorry, I don't have any information regarding this. May I help you with something else?"
        in response
    ):
        return response.strip(), []

    suggested_questions = []
    if "SUGGESTED USER RESPONSES" in response:
        parts = response.split("SUGGESTED USER RESPONSES:")
        main_response = parts[0].strip()
        lines = parts[1].split("\n")
        for line in lines:
            if re.match(r"^\d+\.", line.strip()):
                q: str = re.sub(r"^\d+\.\s*", "", line.strip())
                if q != "":
                    suggested_questions.append(q)
    else:
        main_response = response.strip()

    return main_response, suggested_questions


# ==========================
# GRAPH
# ==========================


def end_turn_node(state: ChatState) -> ChatState:
    """No-op node used when route_question already set the answer inline."""
    return state


def create_langgraph_chain():
    workflow = StateGraph(ChatState)

    workflow.add_node("route", route_question)
    workflow.add_node("check_history", check_history_sufficiency)
    workflow.add_node("preview_ticket", preview_ticket_node)
    workflow.add_node("revise_ticket", revise_ticket_node)
    workflow.add_node("create_ticket", create_ticket_node)
    workflow.add_node("decline_ticket", decline_ticket_node)
    workflow.add_node("retrieve_ticket", retrieve_ticket_node)
    workflow.add_node("end_turn", end_turn_node)
    workflow.add_node("retrieve", retrieve_context)
    workflow.add_node("context_check", check_context_relevance)
    workflow.add_node("generate", generate_response)
    workflow.add_node("verify", verify_answer)

    workflow.set_entry_point("route")
    workflow.add_conditional_edges(
        "route",
        route_after_intent,
        {
            "preview_ticket": "preview_ticket",
            "revise_ticket": "revise_ticket",
            "create_ticket": "create_ticket",
            "decline_ticket": "decline_ticket",
            "retrieve_ticket": "retrieve_ticket",
            "end_turn": "end_turn",
            "retrieve": "retrieve",
            "generate": "check_history",
        },
    )

    workflow.add_conditional_edges(
        "check_history",
        route_after_history_check,
        {
            "retrieve": "retrieve",
            "generate": "generate",
        },
    )

    workflow.add_edge("preview_ticket", END)
    workflow.add_edge("revise_ticket", END)
    workflow.add_edge("create_ticket", END)
    workflow.add_edge("decline_ticket", END)
    workflow.add_edge("end_turn", END)
    workflow.add_edge("retrieve", "context_check")
    workflow.add_edge("context_check", "generate")
    workflow.add_edge("generate", "verify")
    workflow.add_edge("verify", END)

    return workflow.compile(checkpointer=checkpoint.memory_saver)


# ==========================
# CHAT MANAGER
# ==========================


class ThreadedChatManager:

    def __init__(self):
        self.graph = create_langgraph_chain()

    def chat(self, question: str, thread_id: str):
        existing_messages = checkpoint.get_thread_messages(thread_id)
        existing_messages = trim_history(existing_messages)
        prev_state = checkpoint.get_thread_state(thread_id)
        awaiting_confirmation = (
            prev_state.get("awaiting_ticket_confirmation", False)
            if prev_state
            else False
        )
        awaiting_detail_confirmation = (
            prev_state.get("awaiting_ticket_detail_confirmation", False)
            if prev_state
            else False
        )
        awaiting_revision = (
            prev_state.get("awaiting_ticket_revision", False)
            if prev_state
            else False
        )
        pending_details = (
            prev_state.get("pending_ticket_details") if prev_state else None
        )

        ticket_id = prev_state.get("ticket_id", None) if prev_state else None

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
            awaiting_ticket_confirmation=awaiting_confirmation,
            awaiting_ticket_detail_confirmation=awaiting_detail_confirmation,
            awaiting_ticket_revision=awaiting_revision,
            pending_ticket_details=pending_details,
            ticket_id=ticket_id,
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
