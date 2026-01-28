#!/usr/bin/env python3
"""Intent processor module for Ralph orchestrator.

This module contains the IntentProcessor class which handles user intent
enhancement through the LLM agent.
"""

import re
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .hooks import HookManager
    from .logger import Logger as LoggerType
    from .agents.base import BaseAgent


class IntentProcessor:
    """Handles user intent enhancement through the LLM agent.

    Provides functionality to enhance raw user intent descriptions into
    more detailed specifications suitable for PRD generation.
    """

    def __init__(
        self,
        agent: "BaseAgent",
        hooks: "HookManager",
        logger: "LoggerType",
        template_manager,
        event_class,
        event_type_class,
        enhance_intent_strict: bool = False,
    ):
        """Initialize the intent processor.

        Args:
            agent: The LLM agent to use for enhancement
            hooks: HookManager instance for event emission
            logger: Logger class for output
            template_manager: TemplateManager for rendering prompts
            event_class: Event class for creating events
            event_type_class: EventType enum for event types
            enhance_intent_strict: If True, exit on enhancement failure
        """
        self._agent = agent
        self._hooks = hooks
        self._logger = logger
        self._template_manager = template_manager
        self._event = event_class
        self._event_type = event_type_class
        self._enhance_intent_strict = enhance_intent_strict

    def enhance_intent(self, original_intent: str) -> str:
        """Enhance user intent through the enhancement agent.

        Args:
            original_intent: The original user intent to enhance

        Returns:
            Enhanced intent string, or original intent on failure (unless strict mode)

        Raises:
            SystemExit: If strict mode is enabled and enhancement fails
        """
        if not original_intent or not original_intent.strip():
            self._logger.error("Cannot enhance empty or whitespace-only intent.")
            sys.exit(1)

        self._logger.info("\n🔧 Enhancing intent...", "CYAN")
        self._hooks.emit(self._event(self._event_type.INTENT_ENHANCE_START, phase="enhance_intent"))

        prompt = self._template_manager.render(
            "enhance_intent.txt",
            original_intent=original_intent
        )

        success, stdout, error = self._agent.run(prompt, "ENHANCE_INTENT")

        if not success:
            error_msg = error.message if error else "Unknown error"
            self._logger.warning(f"Intent enhancement failed: {error_msg}")
            self._hooks.emit(self._event(self._event_type.INTENT_ENHANCE_FAILURE, phase="enhance_intent"))

            if self._enhance_intent_strict:
                self._logger.error("Intent enhancement failed in strict mode. Exiting.")
                sys.exit(1)

            self._logger.warning("Falling back to original intent.")
            return original_intent

        enhanced_intent = self._parse_enhanced_intent(stdout, original_intent)

        if not enhanced_intent or not enhanced_intent.strip():
            self._logger.warning("Enhancement agent returned empty response.")
            self._hooks.emit(self._event(self._event_type.INTENT_ENHANCE_FAILURE, phase="enhance_intent"))

            if self._enhance_intent_strict:
                self._logger.error("Intent enhancement returned invalid response in strict mode. Exiting.")
                sys.exit(1)

            self._logger.warning("Falling back to original intent.")
            return original_intent

        self._logger.debug(f"Original intent: {original_intent}", "CYAN")
        self._logger.debug(f"Enhanced intent: {enhanced_intent}", "GREEN")

        self._logger.info("✅ Intent enhanced.", "GREEN")
        self._hooks.emit(self._event(self._event_type.INTENT_ENHANCE_SUCCESS, phase="enhance_intent"))

        return enhanced_intent

    def _parse_enhanced_intent(self, response: str, fallback: str) -> str:
        """Parse the enhanced intent from the agent response.

        Args:
            response: The raw response from the enhancement agent
            fallback: The fallback value if parsing fails

        Returns:
            The parsed enhanced intent or fallback value
        """
        pattern = r'<ENHANCED_INTENT>\s*(.*?)\s*</ENHANCED_INTENT>'
        match = re.search(pattern, response, re.DOTALL)

        if match:
            return match.group(1).strip()

        self._logger.warning("Could not parse enhanced intent from response. Falling back to original.")
        return fallback
