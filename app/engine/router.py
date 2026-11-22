"""Hybrid Intelligence Router.

Layer 1 — Algorithmic (deterministic, runs first):
  resets, button/list IDs, menu text matches, FAQ keywords, multi-step flows.
Layer 2 — Artificial (LLM fallback):
  anything unmatched goes to the LLM with profile context + history.
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

from app.engine.llm import LLMClient
from app.engine.session import SessionStore

logger = logging.getLogger(__name__)

MenuKind = Literal["text", "buttons", "list"]


@dataclass
class OutgoingMessage:
    """One message the bot should send (rendered by main.py via WhatsAppClient)."""

    kind: MenuKind = "text"
    body: str = ""
    header: Optional[str] = None
    buttons: list[dict[str, str]] = field(default_factory=list)
    list_button_text: str = "Options"
    list_sections: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class BusinessProfile:
    """Plug-and-play industry configuration loaded from profiles/*.json."""

    business_name: str
    industry: str
    system_prompt: str
    greeting: str
    faqs: list[dict[str, Any]]
    main_menu_buttons: list[dict[str, str]]
    menu_actions: dict[str, dict[str, Any]]
    flows: dict[str, dict[str, Any]]

    @classmethod
    def load(cls, profiles_dir: Path, name: str) -> "BusinessProfile":
        """Load `profiles/<name>.json`; raises FileNotFoundError if missing."""
        path = profiles_dir / f"{name}.json"
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            business_name=raw.get("business_name", name),
            industry=raw.get("industry", "general"),
            system_prompt=raw.get("system_prompt", "You are a helpful assistant."),
            greeting=raw.get("greeting", "Hello! How can I help you today?"),
            faqs=raw.get("faqs", []),
            main_menu_buttons=raw.get("main_menu_buttons", []),
            menu_actions=raw.get("menu_actions", {}),
            flows=raw.get("flows", {}),
        )


class Router:
    """Routes each incoming user message to deterministic or LLM handling."""

    HISTORY_CAP = 20  # bounded session history (LLM slices its own smaller window)

    def __init__(
        self,
        profile: BusinessProfile,
        sessions: SessionStore,
        llm: LLMClient,
        reset_keywords: tuple[str, ...] = ("menu", "hi", "hello", "hey", "start", "restart"),
    ) -> None:
        self._profile = profile
        self._sessions = sessions
        self._llm = llm
        self._reset_keywords = reset_keywords

    @property
    def profile(self) -> BusinessProfile:
        """The active business profile."""
        return self._profile

    # -- entry point ------------------------------------------------------

    async def route(
        self,
        session_key: str,
        user_text: str = "",
        interactive_id: Optional[str] = None,
    ) -> list[OutgoingMessage]:
        """Decide the reply for one incoming message (deterministic first)."""
        session = self._sessions.get_or_create(session_key)
        text = (user_text or "").strip()
        lowered = text.lower()
        action_key = (interactive_id or "").strip() or lowered
        logger.info("Route: key=%s text=%r action=%r", session_key, text[:80], action_key[:40])

        # 1. Resets always win (works even mid-flow).
        if lowered in self._reset_keywords:
            session.reset()
            self._sessions.save(session)
            return [self.main_menu("Sure — starting fresh. What would you like to do?")]

        # 2. Active multi-step flow: treat input as the current step's answer.
        if session.active_flow:
            if lowered == "cancel":
                session.reset()
                self._sessions.save(session)
                return [self.main_menu("No problem, I've cancelled that.")]
            return self._advance_flow(session, text)

        # 3. Deterministic menu action (button/list ID, or typed button title).
        action = self._match_menu_action(action_key)
        if action is not None:
            replies = await self._run_menu_action(session, action_key, action)
            self._sessions.save(session)
            return replies

        # 4. FAQ keyword match.
        faq_answer = self._match_faq(lowered)
        if faq_answer is not None:
            self._remember(session, text, faq_answer)
            self._sessions.save(session)
            return [OutgoingMessage(kind="text", body=faq_answer + self._menu_hint_suffix())]

        # 5. LLM fallback with profile context + history.
        reply = await self._llm.generate(
            self._profile.system_prompt, session.history, text, self._menu_hint_inline()
        )
        self._remember(session, text, reply)
        self._sessions.save(session)
        return [OutgoingMessage(kind="text", body=reply)]

    # -- deterministic layer ----------------------------------------------

    def main_menu(self, intro: Optional[str] = None) -> OutgoingMessage:
        """Render the profile's main menu (buttons for ≤3 items, list menu above)."""
        body = f"{intro}\n\n{self._profile.greeting}" if intro else self._profile.greeting
        buttons = self._profile.main_menu_buttons
        if len(buttons) <= 3:
            return OutgoingMessage(
                kind="buttons", body=body, header=self._profile.business_name, buttons=buttons
            )
        return OutgoingMessage(
            kind="list",
            body=body,
            header=self._profile.business_name,
            list_button_text="View options",
            list_sections=[
                {
                    "title": "Main menu",
                    "rows": [
                        {"id": b["id"], "title": b["title"][:24], "description": ""}
                        for b in buttons
                    ],
                }
            ],
        )

    def _match_menu_action(self, action_key: str) -> Optional[dict[str, Any]]:
        """Match a button/list ID, or a typed message equal to a button title."""
        if action_key in self._profile.menu_actions:
            return self._profile.menu_actions[action_key]
        lowered = action_key.lower()
        for button in self._profile.main_menu_buttons:
            if lowered == button.get("title", "").lower():
                return self._profile.menu_actions.get(button["id"])
        return None

    async def _run_menu_action(
        self, session: Any, action_key: str, action: dict[str, Any]
    ) -> list[OutgoingMessage]:
        """Execute a matched deterministic menu action."""
        action_type = action.get("type", "text")
        if action_type == "flow":
            return self._start_flow(session, action.get("flow", ""))
        if action_type == "faq":
            answer = self._faq_answer(action.get("faq", ""))
            self._remember(session, action_key, answer)
            return [OutgoingMessage(kind="text", body=answer + self._menu_hint_suffix())]
        body = action.get("text", "")
        self._remember(session, action_key, body)
        return [OutgoingMessage(kind="text", body=body + self._menu_hint_suffix())]

    def _start_flow(self, session: Any, flow_id: str) -> list[OutgoingMessage]:
        """Begin a multi-step flow; unknown IDs fall back to the main menu."""
        flow = self._profile.flows.get(flow_id)
        if not flow or not flow.get("steps"):
            logger.warning("Unknown or empty flow: %r", flow_id)
            return [self.main_menu("I couldn't start that option.")]
        session.active_flow = flow_id
        session.step_index = 0
        session.data = {}
        first_prompt: str = flow["steps"][0]["prompt"]
        intro = flow.get("intro", "")
        body = f"{intro}\n\n{first_prompt}\n\n_Type *cancel* anytime to stop._" if intro else first_prompt
        return [OutgoingMessage(kind="text", body=body)]

    def _advance_flow(self, session: Any, text: str) -> list[OutgoingMessage]:
        """Store the current step's answer and prompt the next step (or finish)."""
        flow = self._profile.flows.get(session.active_flow or "", {})
        steps: list[dict[str, Any]] = flow.get("steps", [])
        if session.step_index >= len(steps):
            session.reset()  # Defensive: corrupted state returns to menu.
            self._sessions.save(session)
            return [self.main_menu("Something got out of sync — let's start over.")]
        if not text:
            prompt: str = steps[session.step_index]["prompt"]
            return [OutgoingMessage(kind="text", body=f"Please send a text reply.\n\n{prompt}")]

        key: str = steps[session.step_index]["key"]
        session.data[key] = text
        session.step_index += 1

        if session.step_index < len(steps):
            next_prompt: str = steps[session.step_index]["prompt"]
            self._sessions.save(session)
            return [OutgoingMessage(kind="text", body=next_prompt)]

        completion = flow.get("completion_message", "Done! Type *menu* to continue.")
        try:
            completion = completion.format(**session.data)
        except KeyError:
            logger.warning("Flow %r completion template keys mismatch data.", session.active_flow)
        flow_id = session.active_flow or ""
        session.reset()
        self._remember(session, f"[flow:{flow_id}]", completion)
        self._sessions.save(session)
        return [OutgoingMessage(kind="text", body=completion + self._menu_hint_suffix())]

    # -- FAQ helpers -------------------------------------------------------

    def _match_faq(self, lowered_text: str) -> Optional[str]:
        """Return the first FAQ answer whose keyword appears in the text."""
        for faq in self._profile.faqs:
            for keyword in faq.get("keywords", []):
                if keyword.lower() in lowered_text:
                    return str(faq.get("answer", ""))
        return None

    def _faq_answer(self, question: str) -> str:
        """Look up an FAQ answer by (case-insensitive) question title."""
        for faq in self._profile.faqs:
            if faq.get("question", "").lower() == question.lower():
                return str(faq.get("answer", ""))
        return "I couldn't find details on that. Type *menu* to see the options."

    # -- shared helpers ----------------------------------------------------

    def _remember(self, session: Any, user_text: str, assistant_text: str) -> None:
        """Append one exchange to the bounded session history."""
        if user_text:
            session.history.append({"role": "user", "content": user_text[:1000]})
        if assistant_text:
            session.history.append({"role": "assistant", "content": assistant_text[:1000]})
        if len(session.history) > self.HISTORY_CAP:
            del session.history[: -self.HISTORY_CAP]

    def _menu_hint_inline(self) -> str:
        """Short hint of menu options for the LLM/static fallback."""
        titles = [b.get("title", "") for b in self._profile.main_menu_buttons[:3]]
        return ", ".join(t for t in titles if t)

    def _menu_hint_suffix(self) -> str:
        """Nudge back to the menu after a deterministic answer."""
        hint = self._menu_hint_inline()
        return f"\n\n_Type *menu* to see options ({hint})._" if hint else "\n\n_Type *menu* to continue._"
