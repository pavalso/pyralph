#!/usr/bin/env python3
"""QA checklist management for Ralph.

This module contains checklist file operations with corruption detection and recovery:
- QAChecklistError: Base exception for QA checklist operations
- QAChecklistCorruptedError: Raised when checklist file is corrupted
- QAChecklistManager: Manages QA checklist file operations
"""
import datetime
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from ..logger import Logger
from .models import QARequirement

if TYPE_CHECKING:
    from ..prd import PRDManager


class QAChecklistError(Exception):
    """Base exception for QA checklist operations."""
    pass


class QAChecklistCorruptedError(QAChecklistError):
    """Raised when the checklist file is corrupted and cannot be parsed."""
    pass


class QAChecklistManager:
    """Manages QA checklist file operations with corruption detection and recovery.

    The checklist is stored in .ralph/qa-checklist.json and tracks requirement
    fulfillment status across tasks. Supports incremental updates and automatic
    generation from PRD acceptance criteria when the checklist doesn't exist.
    """

    VALID_STATUSES = ("pending", "passed", "failed")

    def __init__(self, checklist_path: Path, prd_manager: Optional['PRDManager'] = None):
        """Initialize the QA checklist manager.

        Args:
            checklist_path: Path to the qa-checklist.json file
            prd_manager: Optional PRDManager instance for generating default checklist
        """
        self._path = checklist_path
        self._prd_manager = prd_manager
        self._requirements: Dict[str, QARequirement] = {}
        self._loaded = False

    @property
    def path(self) -> Path:
        """Get the checklist file path."""
        return self._path

    def exists(self) -> bool:
        """Check if the checklist file exists on disk."""
        return self._path.exists()

    def _validate_checklist_structure(self, data: Any) -> List[QARequirement]:
        """Validate checklist JSON structure and extract requirements.

        Args:
            data: Parsed JSON data

        Returns:
            List of QARequirement objects

        Raises:
            QAChecklistCorruptedError: If structure is invalid
        """
        if not isinstance(data, dict):
            raise QAChecklistCorruptedError("Checklist root must be a JSON object")

        if "requirements" not in data:
            raise QAChecklistCorruptedError("Checklist missing 'requirements' field")

        requirements_data = data["requirements"]
        if not isinstance(requirements_data, list):
            raise QAChecklistCorruptedError("'requirements' must be a list")

        requirements = []
        for i, item in enumerate(requirements_data):
            if not isinstance(item, dict):
                raise QAChecklistCorruptedError(f"Requirement at index {i} is not an object")
            if "id" not in item:
                raise QAChecklistCorruptedError(f"Requirement at index {i} missing 'id' field")
            if "description" not in item:
                raise QAChecklistCorruptedError(f"Requirement at index {i} missing 'description' field")
            if "status" in item and item["status"] not in self.VALID_STATUSES:
                raise QAChecklistCorruptedError(
                    f"Requirement '{item['id']}' has invalid status '{item['status']}'"
                )
            requirements.append(QARequirement.from_dict(item))

        return requirements

    def _backup_corrupted_file(self) -> Optional[Path]:
        """Create a backup of the corrupted checklist file.

        Returns:
            Path to backup file, or None if no backup was created
        """
        if not self._path.exists():
            return None

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = self._path.with_suffix(f".corrupted.{timestamp}.json")
        try:
            shutil.copy2(self._path, backup_path)
            Logger.warning(f"Backed up corrupted checklist to: {backup_path}")
            return backup_path
        except OSError as e:
            Logger.error(f"Failed to backup corrupted checklist: {e}")
            return None

    def _generate_from_prd(self) -> List[QARequirement]:
        """Generate default requirements from PRD acceptance criteria.

        Returns:
            List of QARequirement objects derived from PRD

        Raises:
            QAChecklistError: If PRD manager is not available or PRD is invalid
        """
        if self._prd_manager is None:
            raise QAChecklistError("Cannot generate checklist: no PRD manager provided")

        if not self._prd_manager.exists():
            raise QAChecklistError("Cannot generate checklist: PRD file does not exist")

        try:
            prd_data = self._prd_manager.load()
        except (json.JSONDecodeError, OSError) as e:
            raise QAChecklistError(f"Cannot generate checklist: PRD read error: {e}") from e

        requirements = []
        user_stories = prd_data.get("userStories", [])

        for story in user_stories:
            task_id = story.get("id", "")
            criteria = story.get("acceptanceCriteria", [])

            for idx, criterion in enumerate(criteria):
                req_id = f"{task_id}-AC{idx + 1:02d}"
                requirements.append(QARequirement(
                    id=req_id,
                    description=criterion,
                    status="pending",
                    lastChecked=None,
                    linkedTasks=[]
                ))

        return requirements

    def load(self, auto_create: bool = True) -> Dict[str, QARequirement]:
        """Load checklist from disk with corruption detection.

        Args:
            auto_create: If True, create checklist from PRD when file doesn't exist

        Returns:
            Dictionary mapping requirement IDs to QARequirement objects

        Raises:
            QAChecklistError: If checklist cannot be loaded or created
            FileNotFoundError: If file doesn't exist and auto_create is False
        """
        if not self.exists():
            if auto_create and self._prd_manager is not None:
                Logger.info("QA checklist not found, generating from PRD...")
                requirements = self._generate_from_prd()
                self._requirements = {r.id: r for r in requirements}
                self.save()
                self._loaded = True
                return self._requirements
            raise FileNotFoundError(f"QA checklist not found: {self._path}")

        try:
            content = self._path.read_text(encoding='utf-8')
            data = json.loads(content)
            requirements = self._validate_checklist_structure(data)
            self._requirements = {r.id: r for r in requirements}
            self._loaded = True
            return self._requirements

        except json.JSONDecodeError as e:
            Logger.error(f"Checklist JSON parse error: {e}")
            self._backup_corrupted_file()
            if self._prd_manager is not None:
                Logger.info("Regenerating checklist from PRD...")
                requirements = self._generate_from_prd()
                self._requirements = {r.id: r for r in requirements}
                self.save()
                self._loaded = True
                return self._requirements
            raise QAChecklistCorruptedError(f"Checklist corrupted and no PRD available: {e}") from e

        except QAChecklistCorruptedError:
            self._backup_corrupted_file()
            if self._prd_manager is not None:
                Logger.info("Regenerating checklist from PRD...")
                requirements = self._generate_from_prd()
                self._requirements = {r.id: r for r in requirements}
                self.save()
                self._loaded = True
                return self._requirements
            raise

    def save(self) -> None:
        """Save current requirements to disk.

        Creates parent directories if needed.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "requirements": [r.to_dict() for r in self._requirements.values()]
        }
        self._path.write_text(json.dumps(data, indent=2), encoding='utf-8')

    def get_requirement(self, requirement_id: str) -> Optional[QARequirement]:
        """Get a requirement by ID.

        Args:
            requirement_id: The requirement ID to look up

        Returns:
            QARequirement if found, None otherwise
        """
        if not self._loaded:
            self.load()
        return self._requirements.get(requirement_id)

    def get_all_requirements(self) -> List[QARequirement]:
        """Get all requirements.

        Returns:
            List of all QARequirement objects
        """
        if not self._loaded:
            self.load()
        return list(self._requirements.values())

    def update_requirement(
        self,
        requirement_id: str,
        status: Optional[str] = None,
        linked_task: Optional[str] = None
    ) -> bool:
        """Update a requirement's status and/or link a task.

        Args:
            requirement_id: ID of the requirement to update
            status: New status (pending/passed/failed), or None to keep current
            linked_task: Task ID to add to linkedTasks, or None to skip

        Returns:
            True if requirement was updated, False if not found

        Raises:
            ValueError: If status is not a valid status value
        """
        if not self._loaded:
            self.load()

        if status is not None and status not in self.VALID_STATUSES:
            raise ValueError(f"Invalid status '{status}'. Must be one of: {self.VALID_STATUSES}")

        requirement = self._requirements.get(requirement_id)
        if requirement is None:
            return False

        if status is not None:
            requirement.status = status

        if linked_task is not None and linked_task not in requirement.linkedTasks:
            requirement.linkedTasks.append(linked_task)

        requirement.lastChecked = datetime.datetime.now().isoformat()
        self.save()
        return True

    def add_requirement(self, requirement: QARequirement) -> None:
        """Add a new requirement to the checklist.

        Args:
            requirement: The QARequirement to add

        Raises:
            ValueError: If requirement with same ID already exists
        """
        if not self._loaded:
            self.load()

        if requirement.id in self._requirements:
            raise ValueError(f"Requirement '{requirement.id}' already exists")

        self._requirements[requirement.id] = requirement
        self.save()
